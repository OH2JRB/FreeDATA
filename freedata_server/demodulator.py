from freedata_server import codec2
import copy
import ctypes
import structlog
import threading
from freedata_server.audio_backend import loaded_sounddevice
import itertools
import numpy as np
from freedata_server.audio_buffer import CircularBuffer
from freedata_server.fsk_early import FskEarlyDecoder
from freedata_server.signal_detector import SignalDetector
from freedata_server.config import fsk_modes_enabled

TESTMODE = False


class Demodulator:
    # FSK modes: audio decoded again after a decoded frame, in frame lengths (restart_fsk)
    FSK_REPLAY_FRAMES = 1.5
    # the same FSK frame decoded again within this many frame lengths is dropped: the
    # replay and the early decoder find frames twice. ARQ repeats come later (after
    # their timeout, 20 s or more on data_fsk4_50)
    FSK_DUPLICATE_FRAMES = 2
    # an early decoded FSK frame is passed on this long after its end (fsk_early)
    FSK_RELEASE_MARGIN = 0.2

    MODE_DICT = {}
    # Iterate over the FREEDV_MODE enum members
    for mode in codec2.FREEDV_MODE:
        MODE_DICT[mode.value] = {
            "decode": False,
            "bytes_per_frame": None,
            "bytes_out": None,
            "audio_buffer": None,
            "nin": None,
            "instance": None,
            "state_buffer": [],
            "name": mode.name.upper(),
            "decoding_thread": None,
            # FSK modes only, see restart_fsk()
            "frame_samples": 0,
            "fsk_history": None,
            "fsk_replay": None,
            "fsk_reset": False,
            "retired_instance": None,
            "fsk_early": None,  # FskEarlyDecoder
            "fsk_seen": None,  # payload -> sample time, for dropping duplicates
            "fsk_pending": None,  # early decoded frames waiting for their end
            # frames found but not decoded (rx status 10), see take_decode_failures()
            "decode_failures": 0,
        }

    def __init__(self, ctx):
        self.ctx = ctx
        # each demodulator gets its own copy: its decoding threads replace codec2
        # instances in it (restart_fsk), which must not reach another demodulator
        self.MODE_DICT = copy.deepcopy(Demodulator.MODE_DICT)

        self.log = structlog.get_logger("Demodulator")

        self.shutdown_flag = threading.Event()

        self.AUDIO_FRAMES_PER_BUFFER_RX = 4800
        self.buffer_overflow_counter = [0] * len(codec2.FREEDV_MODE)
        self.is_codec2_traffic_counter = 0
        self.is_codec2_traffic_cooldown = 5

        # Audio Stream object
        self.stream = None

        # init codec2 resampler
        self.resampler = codec2.resampler()

        # transmissions on the channel by their power, for the ARQ sessions
        self.signal_detector = SignalDetector()

        self.init_codec2()

        # enable decoding of signalling modes
        if self.ctx.config_manager.config["EXP"].get("enable_vhf"):
            self.MODE_DICT[codec2.FREEDV_MODE.data_vhf_1.value]["decode"] = True
            self.MODE_DICT[codec2.FREEDV_MODE.signalling.value]["decode"] = True
            self.MODE_DICT[codec2.FREEDV_MODE.signalling_ack.value]["decode"] = True
        else:
            self.MODE_DICT[codec2.FREEDV_MODE.signalling.value]["decode"] = True
            self.MODE_DICT[codec2.FREEDV_MODE.signalling_ack.value]["decode"] = True
            self.MODE_DICT[codec2.FREEDV_MODE.datac4.value]["decode"] = True
            # ARQ sessions fall back to it, signalling included (arq_session.FSK_MODE)
            self.MODE_DICT[codec2.FREEDV_MODE.data_fsk4_50.value]["decode"] = fsk_modes_enabled(self.ctx)
        self.log.info("[MDM] FSK modes " + ("enabled" if fsk_modes_enabled(self.ctx) else "disabled"))

    def init_codec2(self):
        # Open codec2 instances
        for mode in codec2.FREEDV_MODE:
            self.init_codec2_mode(mode.value)

    def init_codec2_mode(self, mode):
        """
        Init codec2 and return some important parameters
        """

        # create codec2 instance
        c2instance = codec2.open_instance(mode)

        # get bytes per frame
        bytes_per_frame = int(codec2.api.freedv_get_bits_per_modem_frame(c2instance) / 8)
        # create byte out buffer
        bytes_out = ctypes.create_string_buffer(bytes_per_frame)
        # set initial frames per burst
        codec2.api.freedv_set_frames_per_burst(c2instance, 1)

        # init audio buffer; it must hold the most samples the mode asks for at
        # once (nin) plus an incoming block, or blocks are dropped as overflow
        # while the decoder waits for more: data_fsk4_50 asks for up to 8160
        buffer_size = max(
            2 * self.AUDIO_FRAMES_PER_BUFFER_RX,
            codec2.api.freedv_get_n_max_modem_samples(c2instance) + self.AUDIO_FRAMES_PER_BUFFER_RX,
        )
        if self.ctx.config_manager.config["EXP"].get("enable_ring_buffer"):
            self.log.debug("[MDM] [buffer]", enable_ring_buffer=True)
            audio_buffer = CircularBuffer(buffer_size)
        else:
            self.log.debug("[MDM] [buffer]", enable_ring_buffer=False)
            audio_buffer = codec2.audio_buffer(buffer_size)

        # get initial nin
        nin = codec2.api.freedv_nin(c2instance)

        # Additional Datac0-specific information - these are not referenced anywhere else.
        # self.signalling_datac0_payload_per_frame = self.signalling_datac0_bytes_per_frame - 2
        # self.signalling_datac0_n_nom_modem_samples = codec2.api.freedv_get_n_nom_modem_samples(
        #     self.signalling_datac0_freedv
        # )
        # self.signalling_datac0_n_tx_modem_samples = codec2.api.freedv_get_n_tx_modem_samples(
        #     self.signalling_datac0_freedv
        # )
        # self.signalling_datac0_n_tx_preamble_modem_samples = (
        #     codec2.api.freedv_get_n_tx_preamble_modem_samples(self.signalling_datac0_freedv)
        # )
        # self.signalling_datac0_n_tx_postamble_modem_samples = (
        #     codec2.api.freedv_get_n_tx_postamble_modem_samples(self.signalling_datac0_freedv)
        # )

        self.MODE_DICT[mode]["instance"] = c2instance
        self.MODE_DICT[mode]["bytes_per_frame"] = bytes_per_frame
        self.MODE_DICT[mode]["bytes_out"] = bytes_out
        self.MODE_DICT[mode]["audio_buffer"] = audio_buffer
        self.MODE_DICT[mode]["nin"] = nin
        self.MODE_DICT[mode]["frame_samples"] = codec2.api.freedv_get_n_tx_modem_samples(c2instance)
        self.MODE_DICT[mode]["fsk_history"] = np.zeros(0, dtype=np.int16)
        self.MODE_DICT[mode]["fsk_replay"] = np.zeros(0, dtype=np.int16)
        if self.is_fsk_mode(mode):
            self.MODE_DICT[mode]["fsk_early"] = FskEarlyDecoder(mode)
            self.MODE_DICT[mode]["fsk_seen"] = {}
            self.MODE_DICT[mode]["fsk_pending"] = []

    @staticmethod
    def is_fsk_mode(mode) -> bool:
        """True for the FSK_LDPC modes (codec2.fsk_configurations)."""
        return mode in codec2.fsk_configurations

    def restart_fsk(self, mode, replay: bool):
        """Opens the codec2 instance of an FSK mode again.

        codec2's FSK_LDPC receiver, once it has decoded a frame, checks for the
        next frame only at the same position and searches again one frame
        length later, so a burst that starts within about a frame of the
        previous one is lost. freedv_set_sync() does not reset it. A new
        instance searches from the start.

        With replay, the last FSK_REPLAY_FRAMES frame lengths of audio are fed to
        the new instance first, so a burst that has already begun is not missed,
        even when codec2 has decoded the frame before it late. Frames this
        decodes twice are dropped (fsk_is_new). Without replay (after
        transmitting) the early decoder forgets its audio as well.

        Runs in the mode's decoding thread only. The replaced instance is
        closed at the next restart, so a pointer another thread has just read
        from MODE_DICT stays valid meanwhile.

        Returns:
            The new codec2 instance.
        """
        data = self.MODE_DICT[mode]
        if data["retired_instance"] is not None:
            codec2.api.freedv_close(data["retired_instance"])
        data["retired_instance"] = data["instance"]

        instance = codec2.open_instance(mode)
        codec2.api.freedv_set_frames_per_burst(instance, 1)
        data["instance"] = instance
        data["nin"] = codec2.api.freedv_nin(instance)
        empty = np.zeros(0, dtype=np.int16)
        data["fsk_replay"] = data["fsk_history"].copy() if replay else empty
        data["fsk_history"] = empty
        data["fsk_reset"] = False
        if not replay:
            data["fsk_early"].reset()
        return instance

    def remember_fsk_audio(self, mode, samples) -> None:
        """Keeps the last FSK_REPLAY_FRAMES frame lengths of audio fed to an FSK mode."""
        data = self.MODE_DICT[mode]
        history = np.concatenate([data["fsk_history"], samples])
        data["fsk_history"] = history[-int(self.FSK_REPLAY_FRAMES * data["frame_samples"]) :]

    def fsk_is_new(self, mode, payload) -> bool:
        """Notes a decoded FSK frame; False if it was decoded within the last
        FSK_DUPLICATE_FRAMES frame lengths of audio already."""
        if self.fsk_seen_recently(mode, payload):
            return False
        data = self.MODE_DICT[mode]
        data["fsk_seen"][payload] = data["fsk_early"].time
        return True

    def fsk_seen_recently(self, mode, payload) -> bool:
        """True if the frame was decoded within the last FSK_DUPLICATE_FRAMES frame
        lengths of audio; forgets older ones, so a later repeat (ARQ) counts as new."""
        data = self.MODE_DICT[mode]
        now = data["fsk_early"].time
        window = self.FSK_DUPLICATE_FRAMES * data["frame_samples"]
        seen = data["fsk_seen"]
        for old in [p for p, t in seen.items() if now - t > window]:
            del seen[old]
        return payload in seen

    def decode_fsk_early(self, mode, live, transmitting) -> None:
        """Feeds new audio to the early decoder of an FSK mode (fsk_early) and
        passes on the frames it has decoded once their audio has ended.

        Args:
            mode: The FSK mode.
            live: The audio new to this mode in this block.
            transmitting: True while this station transmits (no reception).
        """
        data = self.MODE_DICT[mode]
        early = data["fsk_early"]
        if transmitting:
            early.time += len(live)
            early.reset()
        else:
            for frame in early.push(
                live, self.get_fsk_stats, known=lambda payload: self.fsk_seen_recently(mode, payload)
            ):
                if self.fsk_is_new(mode, frame["payload"]):
                    frame["release"] = frame["end"] + int(self.FSK_RELEASE_MARGIN * codec2.api.FREEDV_FS_8000)
                    data["fsk_pending"].append(frame)

        for frame in [f for f in data["fsk_pending"] if f["release"] <= early.time]:
            data["fsk_pending"].remove(frame)
            self.log.debug("[MDM] [demod_audio] FSK frame decoded early", mode_name=data["name"])
            self.ctx.rf_modem.data_queue_received.put({
                "payload": self.unwhiten(mode, frame["payload"]),
                "freedv": data["instance"],
                "bytes_per_frame": data["bytes_per_frame"],
                "snr": frame["snr"],
                "frequency_offset": frame["frequency_offset"],
                "mode_name": data["name"],
            })

    def take_decode_failures(self, modes) -> int:
        """Returns how many frames of these modes were found but could not be decoded
        (rx status 10) since the last call, and starts counting again.

        On a fading path, an auroral one for instance, the OFDM modes often sync on
        a frame and then fail to decode it. ARQSessionIRS uses this to step down.

        Args:
            modes: Mode values.
        """
        failures = 0
        for mode in modes:
            failures += self.MODE_DICT[mode]["decode_failures"]
            self.MODE_DICT[mode]["decode_failures"] = 0
        return failures

    def unwhiten(self, mode, payload) -> bytes:
        """Undoes the whitening of a decoded frame (codec2.whiten); the CRC stays."""
        size = self.MODE_DICT[mode]["bytes_per_frame"] - 2
        return codec2.whiten(mode, payload[:size]) + payload[size:]

    def get_fsk_stats(self, freedv):
        """(SNR, frequency offset) of a codec2 instance, for the early decoder."""
        return self.calculate_snr(freedv), self.get_frequency_offset(freedv)

    def start(self, stream):
        if self.ctx.TESTMODE:
            self.stream = None
        else:
            self.stream = stream

        for mode in self.MODE_DICT:
            # Start decoder threads
            self.MODE_DICT[mode]["decoding_thread"] = threading.Thread(
                target=self.demodulate_audio,
                args=[mode],
                name=self.MODE_DICT[mode]["name"],
                daemon=True,
            )
            self.MODE_DICT[mode]["decoding_thread"].start()

    def get_frequency_offset(self, freedv: ctypes.c_void_p) -> float:
        """
        Ask codec2 for the calculated (audio) frequency offset of the received signal.

        :param freedv: codec2 instance to query
        :type freedv: ctypes.c_void_p
        :return: Offset of audio frequency in Hz
        :rtype: float
        """
        modemStats = codec2.MODEMSTATS()
        codec2.api.freedv_get_modem_extended_stats(freedv, ctypes.byref(modemStats))
        offset = round(modemStats.foff) * (-1)
        return offset

    def demodulate_audio(self, mode) -> int:
        """
        De-modulate supplied audio stream with supplied codec2 instance.
        Decoded audio is placed into `bytes_out`.
        """

        audiobuffer = self.MODE_DICT[mode]["audio_buffer"]
        nin = self.MODE_DICT[mode]["nin"]
        freedv = self.MODE_DICT[mode]["instance"]
        bytes_out = self.MODE_DICT[mode]["bytes_out"]
        bytes_per_frame = self.MODE_DICT[mode]["bytes_per_frame"]
        state_buffer = self.MODE_DICT[mode]["state_buffer"]
        mode_name = self.MODE_DICT[mode]["name"]
        is_fsk = self.is_fsk_mode(mode)

        last_rx_status = 0
        previous_rx_status = 0

        try:
            while self.stream and self.stream.active and not self.shutdown_flag.is_set() or self.ctx.TESTMODE:
                threading.Event().wait(0.01)
                # FSK receivers are reset after transmitting (reset_data_sync)
                if is_fsk and self.MODE_DICT[mode]["fsk_reset"] and not self.ctx.state_manager.isTransmitting():
                    freedv = self.restart_fsk(mode, replay=False)
                    nin = self.MODE_DICT[mode]["nin"]
                # after a decoded FSK frame, the audio of the last frame length is decoded
                # again; its last block is completed with new audio, without a gap
                replay = self.MODE_DICT[mode]["fsk_replay"] if is_fsk else None
                n_replay = min(len(replay), nin) if replay is not None else 0
                n_live = nin - n_replay
                if audiobuffer.nbuffer >= n_live and not self.shutdown_flag.is_set():
                    if n_replay == 0:
                        samples = audiobuffer.buffer[:nin]
                    elif n_live == 0:
                        samples = replay[:nin]
                    else:
                        samples = np.concatenate([replay, audiobuffer.buffer[:n_live]])
                    # demodulate audio
                    transmitting = self.ctx.state_manager.isTransmitting()
                    if not transmitting:
                        nbytes = codec2.api.freedv_rawdatarx(freedv, bytes_out, samples.ctypes)

                        # get current freedata_server states and write to list
                        # 1 trial
                        # 2 sync
                        # 3 trial sync
                        # 6 decoded
                        # 10 error decoding == NACK
                        rx_status = codec2.api.freedv_get_rx_status(freedv)

                        if rx_status not in [0]:
                            self.is_codec2_traffic_counter = self.is_codec2_traffic_cooldown

                            if last_rx_status != rx_status:
                                self.log.debug(
                                    f"[MDM] [DEMOD] [mode={mode_name}] [State: {last_rx_status} >>> {rx_status}]",
                                    sync_flag=codec2.api.rx_sync_flags_to_text[rx_status],
                                )
                                last_rx_status = rx_status

                        # decrement codec traffic counter for making state smoother
                        if self.is_codec2_traffic_counter > 0:
                            self.is_codec2_traffic_counter -= 1
                            self.ctx.state_manager.set_channel_busy_condition_codec2(True)
                        else:
                            self.ctx.state_manager.set_channel_busy_condition_codec2(False)
                        if rx_status == 10:
                            state_buffer.append(rx_status)
                            if previous_rx_status != 10:
                                self.MODE_DICT[mode]["decode_failures"] += 1
                        previous_rx_status = rx_status
                    else:
                        nbytes = 0
                        self.reset_data_sync()
                        # audiobuffer.nbuffer = 0

                    if is_fsk:
                        self.remember_fsk_audio(mode, samples)
                        self.decode_fsk_early(mode, samples[n_replay:], transmitting)
                    if n_replay:
                        self.MODE_DICT[mode]["fsk_replay"] = replay[n_replay:]
                    if n_live:
                        audiobuffer.pop(n_live)
                    nin = codec2.api.freedv_nin(freedv)
                    # a copy: bytes_out is reused by the next decode, which with the
                    # FSK replay can follow right away
                    payload = bytes(bytes_out)
                    if nbytes == bytes_per_frame and is_fsk and not self.fsk_is_new(mode, payload):
                        # decoded early already, or by the replay
                        freedv = self.restart_fsk(mode, replay=True)
                        nin = self.MODE_DICT[mode]["nin"]
                    elif nbytes == bytes_per_frame:
                        self.log.debug(
                            "[MDM] [demod_audio] Pushing received data to received_queue",
                            nbytes=nbytes,
                            mode_name=mode_name,
                        )
                        snr = self.calculate_snr(freedv)
                        self.get_scatter(freedv)

                        item = {
                            "payload": self.unwhiten(mode, payload),
                            "freedv": freedv,
                            "bytes_per_frame": bytes_per_frame,
                            "snr": snr,
                            "frequency_offset": self.get_frequency_offset(freedv),
                            "mode_name": mode_name,
                        }

                        self.ctx.rf_modem.data_queue_received.put(item)

                        state_buffer = []

                        if is_fsk:
                            freedv = self.restart_fsk(mode, replay=True)
                            nin = self.MODE_DICT[mode]["nin"]
        except Exception as e:
            error_message = str(e)
            # we expect this error when shutdown
            if error_message in [
                "PortAudio not initialized [PaErrorCode -10000]",
                "Invalid stream pointer [PaErrorCode -9988]",
            ]:
                return
            else:
                self.log.warning("[MDM] [demod_audio] demod loop ended", mode=mode_name, e=e)
                sd = loaded_sounddevice()
                if sd is not None:
                    sd._terminate()

    def set_frames_per_burst(self, frames_per_burst: int) -> None:
        """
        Configure codec2 to send the configured number of frames per burst.

        :param frames_per_burst: Number of frames per burst requested
        :type frames_per_burst: int
        """
        # Limit frames per burst to acceptable values
        frames_per_burst = max(frames_per_burst, 1)
        frames_per_burst = min(frames_per_burst, 5)

        # FIXME
        frames_per_burst = 1

        for mode in (codec2.FREEDV_MODE.datac1, codec2.FREEDV_MODE.datac3, codec2.FREEDV_MODE.datac4):
            codec2.api.freedv_set_frames_per_burst(self.MODE_DICT[mode.value]["instance"], frames_per_burst)

    def calculate_snr(self, freedv: ctypes.c_void_p) -> float:
        """
        Ask codec2 for data about the received signal and calculate
        the signal-to-noise ratio.

        :param freedv: codec2 instance to query
        :type freedv: ctypes.c_void_p
        :return: Signal-to-noise ratio of the decoded data
        :rtype: float
        """
        try:
            modem_stats_snr = ctypes.c_float()
            modem_stats_sync = ctypes.c_int()

            codec2.api.freedv_get_modem_stats(freedv, ctypes.byref(modem_stats_sync), ctypes.byref(modem_stats_snr))
            modem_stats_snr = modem_stats_snr.value
            modem_stats_sync = modem_stats_sync.value

            snr = round(modem_stats_snr, 1)
            self.log.info("[MDM] calculate_snr: ", snr=snr)
            # snr = np.clip(
            #    snr, -127, 127
            # )  # limit to max value of -128/128 as a possible fix of #188
            return int(snr)
        except Exception as err:
            self.log.error(f"[MDM] calculate_snr: Exception: {err}")
            return 0

    def get_scatter(self, freedv: ctypes.c_void_p) -> None:
        """
        Ask codec2 for data about the received signal and calculate the scatter plot.

        :param freedv: codec2 instance to query
        :type freedv: ctypes.c_void_p
        """

        modemStats = codec2.MODEMSTATS()
        ctypes.cast(
            codec2.api.freedv_get_modem_extended_stats(freedv, ctypes.byref(modemStats)),
            ctypes.c_void_p,
        )

        scatterdata = []
        # original function before itertool
        # for i in range(codec2.MODEM_STATS_NC_MAX):
        #    for j in range(1, codec2.MODEM_STATS_NR_MAX, 2):
        #        # print(f"{modemStats.rx_symbols[i][j]} - {modemStats.rx_symbols[i][j]}")
        #        xsymbols = round(modemStats.rx_symbols[i][j - 1] // 1000)
        #        ysymbols = round(modemStats.rx_symbols[i][j] // 1000)
        #        if xsymbols != 0.0 and ysymbols != 0.0:
        #            scatterdata.append({"x": str(xsymbols), "y": str(ysymbols)})

        for i, j in itertools.product(range(codec2.MODEM_STATS_NC_MAX), range(1, codec2.MODEM_STATS_NR_MAX, 2)):
            # print(f"{modemStats.rx_symbols[i][j]} - {modemStats.rx_symbols[i][j]}")
            xsymbols = round(modemStats.rx_symbols[i][j - 1] // 1000)
            ysymbols = round(modemStats.rx_symbols[i][j] // 1000)
            if xsymbols != 0.0 and ysymbols != 0.0:
                scatterdata.append({"x": str(xsymbols), "y": str(ysymbols)})

        # Send all the data if we have too-few samples, otherwise send a sampling
        if 150 > len(scatterdata) > 0:
            self.ctx.event_manager.send_scatter_change(scatterdata)

        else:
            # only take every tenth data point
            self.ctx.event_manager.send_scatter_change(scatterdata[::10])

    def reset_data_sync(self) -> None:
        """
        reset sync state for modes

        :param frames_per_burst: Number of frames per burst requested
        :type frames_per_burst: int
        """
        for mode in self.MODE_DICT:
            if self.is_fsk_mode(mode):
                # freedv_set_sync() only acts on OFDM modes; the decoding
                # thread opens FSK instances again once transmitting has ended
                self.MODE_DICT[mode]["fsk_reset"] = True
            else:
                codec2.api.freedv_set_sync(self.MODE_DICT[mode]["instance"], 0)

    def set_decode_mode(self, modes_to_decode=None, is_arq_irs=False, is_p2p_connection=False):
        # Reset all modes to not decode
        for m in self.MODE_DICT:
            self.MODE_DICT[m]["decode"] = False

        # signalling is always true
        self.MODE_DICT[codec2.FREEDV_MODE.signalling.value]["decode"] = True

        if self.ctx.config_manager.config["EXP"].get("enable_vhf"):
            self.MODE_DICT[codec2.FREEDV_MODE.data_vhf_1.value]["decode"] = True

        # we only need to decode signalling ack as ISS or within P2P Connection
        if is_arq_irs and not is_p2p_connection:
            self.MODE_DICT[codec2.FREEDV_MODE.signalling_ack.value]["decode"] = False
        else:
            self.MODE_DICT[codec2.FREEDV_MODE.signalling_ack.value]["decode"] = True

        # lowest speed level is always true
        # TODO DO we need this for all states? we only need this on IRS and P2P Connection
        self.MODE_DICT[codec2.FREEDV_MODE.datac4.value]["decode"] = True

        # the FSK speed level is always true as well: an ARQ session can fall back to
        # it at any time, signalling included. It costs less CPU than datac4
        if not self.ctx.config_manager.config["EXP"].get("enable_vhf"):
            self.MODE_DICT[codec2.FREEDV_MODE.data_fsk4_50.value]["decode"] = True

        # Enable specified modes
        if modes_to_decode:
            for mode, decode in modes_to_decode.items():
                if mode in self.MODE_DICT:
                    self.MODE_DICT[mode]["decode"] = decode

        # "Enable FSK modes" off: no FSK, whatever asked for it
        if not fsk_modes_enabled(self.ctx):
            self.MODE_DICT[codec2.FREEDV_MODE.data_fsk4_50.value]["decode"] = False

    def shutdown(self):
        print("shutting down demodulators...")
        self.shutdown_flag.set()
        for mode in self.MODE_DICT:
            self.MODE_DICT[mode]["decoding_thread"].join(3)
