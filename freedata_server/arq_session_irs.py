import threading
from freedata_server import arq_session
from freedata_server import codec2
from freedata_server import helpers
from freedata_server import stats
from freedata_server.modem_frametypes import FRAME_TYPE
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.config import fsk_modes_enabled
from enum import Enum
import time
from freedata_server.p2p_connection import States as P2PStates


class IRS_State(Enum):
    """Enumeration representing the states of an IRS (Information Receiving Station) ARQ session.

    This enumeration defines the various states that an ARQ session on the
    Information Receiving Station (IRS) side can transition through during
    the data transfer process.

    Attributes:
        NEW: Initial state of a new session.
        OPEN_ACK_SENT: State after sending an acknowledgement to the session open request.
        INFO_ACK_SENT: State after sending an acknowledgement to the session information frame.
        BURST_REPLY_SENT: State after sending an acknowledgement to a burst data frame.
        ENDED: State after successfully receiving all data and confirming the checksum.
        FAILED: State after a session failure, such as a timeout or CRC mismatch.
        ABORTED: State after the session is explicitly aborted.
        RESUME: State allowing transmission resumption after a waiting period. This state is set after a period longer than TIMEOUT_DATA to ensure clean states.
    """

    NEW = 0
    OPEN_ACK_SENT = 1
    INFO_ACK_SENT = 2
    BURST_REPLY_SENT = 3
    ENDED = 4
    FAILED = 5
    ABORTED = 6
    RESUME = 7  # State, which allows resuming of a transmission - will be set after some waiting time, higher than TIMEOUT_DATA for ensuring clean states


class ARQSessionIRS(arq_session.ARQSession):
    """Manages an ARQ session on the Information Receiving Station (IRS) side.

    This class extends the base ARQSession class and implements the specific
    logic and state transitions for the IRS during an ARQ data transfer.
    """

    TIMEOUT_CONNECT = 55  # 14.2
    TIMEOUT_DATA = 90
    # On FSK the ISS's attempts take far longer (two 5.44 s copies of a signalling
    # frame and up to 32 s of waiting, about 43 s; a burst and its wait up to
    # 70 s), so the IRS waits this many times as long before it gives up
    FSK_TIMEOUT_FACTOR = 4

    # frames found but not decoded (Demodulator.take_decode_failures) in the
    # current mode since the last answer, after which the IRS steps down: to
    # datac4, or from datac4 to FSK. It then keeps the level at datac4 or below
    # for STEP_DOWN_HOLD answers, whatever the SNR says
    DECODE_FAILURES_TO_STEP_DOWN = 2
    STEP_DOWN_HOLD = 3

    # A signal at least this share of the expected burst's frame long, with
    # nothing decoded DECODE_ALLOWANCE seconds after its end: on a path that
    # defeats OFDM (30 Hz of Doppler spread) the OFDM modes do not even sync,
    # so only the SignalDetector shows that the burst came. The IRS then falls
    # back to FSK and answers in FSK (answer_undecodable_burst)
    HEARD_FRAME_SHARE = 0.6
    DECODE_ALLOWANCE = 1.0
    DETECTION_INTERVAL = 0.2

    STATE_TRANSITION = {
        IRS_State.NEW: {
            FRAME_TYPE.ARQ_SESSION_OPEN.value: "send_open_ack",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
        IRS_State.OPEN_ACK_SENT: {
            FRAME_TYPE.ARQ_SESSION_OPEN.value: "send_open_ack",
            FRAME_TYPE.ARQ_SESSION_INFO.value: "send_info_ack",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
        IRS_State.INFO_ACK_SENT: {
            FRAME_TYPE.ARQ_SESSION_INFO.value: "send_info_ack",
            FRAME_TYPE.ARQ_BURST_FRAME.value: "receive_data",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
        IRS_State.BURST_REPLY_SENT: {
            FRAME_TYPE.ARQ_BURST_FRAME.value: "receive_data",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
        IRS_State.ENDED: {
            FRAME_TYPE.ARQ_BURST_FRAME.value: "receive_data",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
        IRS_State.FAILED: {
            FRAME_TYPE.ARQ_BURST_FRAME.value: "receive_data",
            # the ISS still trying after this side gave up (a slow FSK path): start over
            FRAME_TYPE.ARQ_SESSION_INFO.value: "send_info_ack",
            # the ISS aborting after this side gave up
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
            # FRAME_TYPE.ARQ_SESSION_OPEN.value: 'send_open_ack',
        },
        IRS_State.ABORTED: {
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
            # FRAME_TYPE.ARQ_SESSION_OPEN.value: 'send_open_ack',
            # FRAME_TYPE.ARQ_SESSION_INFO.value: 'send_info_ack',
            # FRAME_TYPE.ARQ_BURST_FRAME.value: 'receive_data',
        },
        IRS_State.RESUME: {
            FRAME_TYPE.ARQ_SESSION_OPEN.value: "send_open_ack",
            FRAME_TYPE.ARQ_BURST_FRAME.value: "receive_data",
            FRAME_TYPE.ARQ_STOP.value: "send_stop_ack",
        },
    }

    def __init__(self, ctx, dxcall: str, session_id: int):
        """Initializes a new ARQ session on the Information Receiving Station (IRS) side.

        This method initializes an ARQSessionIRS object, setting up the session
        parameters and state for the IRS. It inherits from the arq_session.ARQSession
        class and adds IRS-specific attributes.

        Args:
            config (dict): The configuration dictionary.
            modem: The modem object.
            dxcall (str): The DX call sign.
            session_id (int): The unique ID of the session.
        """
        super().__init__(ctx, dxcall)
        self.ctx = ctx
        self.id = session_id
        self.dxcall = dxcall
        self.version = self.ctx.constants.ARQ_PROTOCOL_VERSION
        self.is_IRS = True

        self.state = IRS_State.NEW
        self.state_enum = IRS_State  # needed for access State enum from outside

        # instance of p2p connection
        self.running_p2p_connection = None
        try:
            self.running_p2p_connection = self.ctx.state_manager.get_p2p_connection_session(self.id)
            if self.running_p2p_connection is not None:
                # register arq session in p2p connection
                self.running_p2p_connection.running_arq_session = self
                self.running_p2p_connection.set_state(P2PStates.ARQ_SESSION)
        except Exception as _:
            self.log("Error getting p2p connection session")

        self.statistics = stats.stats(self.ctx)

        self.step_down_hold = 0
        # failures counted before this session do not count
        self.take_decode_failures()

        self.type_byte = None
        self.total_length = 0
        self.total_crc = ""
        self.received_data = None
        self.received_bytes = 0
        self.received_crc = None
        # CRC of the data which has already been handed over to the data type handler.
        # Used for avoiding a duplicate delivery if the ISS repeats its last burst.
        self.delivered_crc = None

        self.maximum_bandwidth = 0

        self.abort = False

        # FSK bursts (several frames): frames after a lost one by offset, and the
        # timer that answers a burst whose last frame is lost
        self.pending_frames = {}
        self.burst_ack_timer = None
        self.burst_ack_lock = threading.RLock()
        self.burst_id = 0
        self.burst_frame_size = 0
        # a frame came again, so the last answer was lost: the next one goes out
        # twice on FSK (answer_frames)
        self.answer_lost = False

    def all_data_received(self):
        """Checks if all data has been received.

        This method compares the total expected length of the data with the
        number of bytes received so far.

        Returns:
            bool: True if all data has been received, False otherwise.
        """
        print(f"{self.total_length} vs {self.received_bytes}")
        return self.total_length == self.received_bytes

    def final_crc_matches(self) -> bool:
        """Checks if the final CRC matches the received data.

        This method calculates the CRC32 checksum of the received data and
        compares it with the expected CRC received from the sender.

        Returns:
            bool: True if the CRC matches, False otherwise.
        """
        return self.total_crc == helpers.get_crc_32(bytes(self.received_data)).hex()

    def transmit_and_wait(self, frame, timeout, mode):
        """Transmits a frame and waits for a response.

        This method transmits the given frame using the specified mode and waits
        for a response event within the given timeout period. If no response is
        received and the session is not aborted or failed, it logs a timeout
        message and calls the transmission_failed method.

        Args:
            frame: The frame to be transmitted.
            timeout: The timeout period in seconds.
            mode: The FreeDV mode to use for transmission.
        """
        self.event_frame_received.clear()
        self.transmit_frame(frame, mode)
        self.log(f"Waiting {timeout} seconds...")
        since = time.monotonic()
        deadline = since + (timeout * self.FSK_TIMEOUT_FACTOR if self.fsk else timeout)
        while not self.event_frame_received.wait(self.DETECTION_INTERVAL):
            if time.monotonic() >= deadline:
                if self.state not in [IRS_State.ABORTED, IRS_State.FAILED]:
                    self.log("Timeout waiting for ISS. Session failed.")
                    self.transmission_failed()
                return
            if self.burst_heard_not_decoded(since):
                if fsk_modes_enabled(self.ctx):
                    self.answer_undecodable_burst()
                since = time.monotonic()

    def burst_heard_not_decoded(self, since) -> bool:
        """True if a burst came after since but was not decoded.

        Args:
            since: time.monotonic() at the end of the own transmission.
        """
        if (
            self.fsk
            or self.received_data is None
            or self.state
            not in [
                IRS_State.INFO_ACK_SENT,
                IRS_State.BURST_REPLY_SENT,
            ]
        ):
            return False
        detector = self.get_signal_detector()
        if detector is None:
            return False
        frame_duration = codec2.get_frame_duration(self.get_mode_by_speed_level(self.speed_level).value)
        now = time.monotonic()
        return any(
            end - start >= self.HEARD_FRAME_SHARE * frame_duration and now - end >= self.DECODE_ALLOWANCE
            for start, end in detector.segments(since)
        )

    def answer_undecodable_burst(self):
        """Falls back to FSK and answers the burst that came but could not be decoded.

        The FSK burst ACK carries the bytes received so far, so the ISS sends
        them again, in FSK: it follows the IRS there as the ACK arrives in FSK.
        Its burst id is the one after the last burst decoded, the one that came.
        """
        with self.burst_ack_lock:
            self.log("A burst came but could not be decoded: falling back to FSK", isWarning=True)
            self.fall_back_to_fsk()
            self.burst_id = (self.burst_id + 1) % 256
            ack = self.build_burst_ack(flag_abort=self.abort)
            self.set_state(IRS_State.BURST_REPLY_SENT)
            self.transmit_frame(ack, mode=self.FSK_MODE)

    def launch_transmit_and_wait(self, frame, timeout, mode):
        """Launches the transmit_and_wait method in a separate thread.

        This method creates and starts a new daemon thread to execute the
        transmit_and_wait method, allowing the main thread to continue
        without blocking.

        Args:
            frame: The frame to be transmitted.
            timeout: The timeout period in seconds.
            mode: The FreeDV mode to use for transmission.
        """
        thread_wait = threading.Thread(target=self.transmit_and_wait, args=[frame, timeout, mode], daemon=True)
        thread_wait.start()

    def send_open_ack(self, open_frame):
        """Sends an acknowledgement to the ARQ session open request.

        This method handles the reception of an ARQ_SESSION_OPEN frame.
        It negotiates the maximum transmission bandwidth, checks protocol
        version compatibility, and sends an ARQ_SESSION_OPEN_ACK frame.

        Args:
            open_frame: The received ARQ_SESSION_OPEN frame.

        Returns:
            Tuple[None, None]: Returns None for both data and type_byte as this method doesn't handle data.
        """
        # check for maximum bandwidth. If ISS bandwidth is higher than own, then use own
        if open_frame["maximum_bandwidth"] > self.ctx.config_manager.config["MODEM"]["maximum_bandwidth"]:
            self.maximum_bandwidth = self.ctx.config_manager.config["MODEM"]["maximum_bandwidth"]
        else:
            self.maximum_bandwidth = open_frame["maximum_bandwidth"]
        self.log(f"Negotiated transmission bandwidth {self.maximum_bandwidth}Hz")

        self.ctx.event_manager.send_arq_session_new(False, self.id, self.dxcall, 0, self.state.name)

        if open_frame["protocol_version"] not in [self.protocol_version]:
            self.abort = True
            self.log("Protocol version mismatch! Setting disconnect flag!", isWarning=True)
            self.set_state(IRS_State.ABORTED)

        ack_frame = self.frame_factory.build_arq_session_open_ack(
            self.id, self.dxcall, self.version, self.snr, flag_abort=self.abort
        )

        self.launch_transmit_and_wait(
            self.signalling_frames(ack_frame), self.TIMEOUT_CONNECT, mode=self.get_signalling_mode()
        )
        if not self.abort:
            self.set_state(IRS_State.OPEN_ACK_SENT)
        return None, None

    def send_info_ack(self, info_frame):
        """Sends an acknowledgement to the ARQ session information frame.

        This method handles the reception of an ARQ_SESSION_INFO frame.
        It extracts session details, calibrates speed settings, and sends
        an ARQ_SESSION_INFO_ACK frame.

        Args:
            info_frame: The received ARQ_SESSION_INFO frame.

        Returns:
            Tuple[None, None]: Returns None for both data and type_byte as this method doesn't handle data.
        """
        # Get session info from ISS
        if self.received_bytes == 0:
            self.received_data = bytearray(info_frame["total_length"])
            self.pending_frames = {}
        self.total_length = info_frame["total_length"]
        self.total_crc = info_frame["total_crc"]
        self.dx_snr.append(info_frame["snr"])
        self.type_byte = info_frame["type"]

        self.calibrate_speed_settings()

        self.log(f"New transfer of {self.total_length} bytes, received_bytes: {self.received_bytes}")
        self.ctx.event_manager.send_arq_session_new(False, self.id, self.dxcall, self.total_length, self.state.name)

        info_ack = self.frame_factory.build_arq_session_info_ack(
            self.id,
            self.received_bytes,
            self.snr,
            self.speed_level,
            self.frames_per_burst,
            flag_abort=self.abort,
        )
        self.launch_transmit_and_wait(
            self.signalling_frames(info_ack), self.TIMEOUT_CONNECT, mode=self.get_signalling_mode()
        )
        if not self.abort:
            self.set_state(IRS_State.INFO_ACK_SENT)
        return None, None

    def process_incoming_data(self, frame):
        """Processes incoming data from a received frame.

        This method handles incoming data frames, checking for offset
        mismatches, extracting the relevant data portion, and updating the
        received data buffer. It also logs progress and sends ARQ session
        progress events.

        Args:
            frame: The received data frame.

        Returns:
            bool: True if the data was processed successfully, False otherwise.
        """
        if frame["offset"] > self.received_bytes:
            # a frame of an FSK burst after a lost one: keep it until the gap is
            # filled, since the data is stored in a row (the ISS continues
            # from the offset in the ACK)
            self.log(f"Keeping data at offset {frame['offset']} until {self.received_bytes} has arrived")
            if frame["offset"] in self.pending_frames:
                # held already: the ISS sends it again (the last frame of a burst
                # after a lost ACK), so it did not get the last answer
                self.answer_lost = True
            self.pending_frames[frame["offset"]] = frame["data"]
            return True

        end = frame["offset"] + min(len(frame["data"]), self.total_length - frame["offset"])
        if end <= self.received_bytes:
            # a frame received before, repeated in the next FSK burst (sent from
            # the offset of the gap) or after a lost ACK: same data, nothing to do
            self.log(f"Already received data up to {end}")
            # the ISS sends it again: it did not get the last answer
            self.answer_lost = True
            return True

        if frame["offset"] != self.received_bytes:
            # TODO: IF WE HAVE AN OFFSET BECAUSE OF A SPEED LEVEL CHANGE FOR EXAMPLE,
            # TODO: WE HAVE TO DISCARD THE LAST BYTES, BUT NOT returning False!!
            # CASE: ACK is going lost.
            self.log(
                f"Discarding data offset: Offset = {frame['offset']} | Already received: {self.received_bytes}",
                isWarning=True,
            )
            self.received_bytes = frame["offset"]

            # return False

        # update p2p connection timeout
        if self.running_p2p_connection:
            try:
                self.running_p2p_connection.last_data_timestamp = time.time()
                self.running_p2p_connection.running_arq_session = self
                self.running_p2p_connection.set_state(P2PStates.ARQ_SESSION)
            except Exception as e:
                self.log(f"Error handling P2P states: {e}")

        remaining_data_length = self.total_length - self.received_bytes
        self.log(f"Remaining data: {remaining_data_length}", isWarning=True)
        # Is this the last data part?
        if remaining_data_length < len(frame["data"]):
            # we only want the remaining length, not the entire frame data
            data_part = frame["data"][:remaining_data_length]
        else:
            # we want the entire frame data
            data_part = frame["data"]

        self.received_data[frame["offset"] :] = data_part
        # self.received_bytes += len(data_part)
        self.received_bytes = len(self.received_data)

        # frames kept from after a gap that now follow on
        pending = self.pending_frames.pop(self.received_bytes, None)
        if pending is not None:
            return self.process_incoming_data({"offset": self.received_bytes, "data": pending})
        self.pending_frames = {
            offset: data for offset, data in self.pending_frames.items() if offset > self.received_bytes
        }

        self.log(f"Received {self.received_bytes}/{self.total_length} bytes")
        self.ctx.event_manager.send_arq_session_progress(
            False,
            self.id,
            self.dxcall,
            self.received_bytes,
            self.total_length,
            self.state.name,
            self.speed_level,
            self.calculate_session_statistics(self.received_bytes, self.total_length),
        )

        return True

    def receive_data(self, burst_frame):
        """Receives and processes a burst data frame.

        This method processes the incoming burst data frame, sends acknowledgements,
        updates session state, and handles session completion or failure.

        Args:
            burst_frame: The received ARQ_BURST_FRAME.

        Returns:
            Tuple[bytearray, int]: The received data and its type byte if the transmission is successful,
                                    or None, None in case of failure or incomplete transmission.
        """
        # Data has already been delivered - the ISS probably missed our final ACK and repeats
        # its last burst. Repeat the final ACK, but don't deliver the data a second time.
        if self.delivered_crc is not None and self.delivered_crc == self.total_crc:
            self.log("Data already received and delivered, repeating final ACK", isWarning=True)
            with self.burst_ack_lock:
                # it answers this burst (FSK burst ACKs carry its id), at its end
                self.burst_id = burst_frame["burst_id"]
                self.answer_lost = True
                self.answer_at_burst_end(burst_frame, self.send_final_ack)
            return None, None

        # No receive buffer, e.g. after a failed transmission. The ISS has to re-open the session.
        if self.received_data is None:
            self.log("Ignoring burst frame, no active transfer (session needs to be re-opened)", isWarning=True)
            return None, None

        with self.burst_ack_lock:
            # for the FSK burst ACK: which burst, and the data size of its frames
            self.burst_id = burst_frame["burst_id"]
            self.burst_frame_size = len(burst_frame["data"])
            self.process_incoming_data(burst_frame)
            # update statistics
            self.update_histograms(self.received_bytes, self.total_length)

            if not self.all_data_received():
                self.answer_at_burst_end(burst_frame, self.send_burst_ack)
                return None, None

            # the final ACK, also at the end of the burst: the frame that completes
            # the data may be one of the first (one that fills a gap)
            crc_matches = self.final_crc_matches()
            self.answer_at_burst_end(burst_frame, lambda: self.send_final_ack(checksum=crc_matches))

        if crc_matches:
            self.log("All data received successfully!")
            self.session_ended = time.time()
            self.set_state(IRS_State.ENDED)
            self.delivered_crc = self.total_crc

            session_stats = self.calculate_session_statistics(self.received_bytes, self.total_length)
            self.ctx.event_manager.send_arq_session_finished(
                False,
                self.id,
                self.dxcall,
                True,
                self.state.name,
                data=self.received_data,
                statistics=session_stats,
            )

            return self.received_data, self.type_byte
        else:
            self.log("CRC fail at the end of transmission!", isWarning=True)
            self.log(
                f"Failed Data [Is real:{len(self.received_data)}/Is calculated:{self.received_bytes}/Should:{self.total_length}]:{self.received_data}",
                isWarning=True,
            )
            return self.transmission_failed()

    def take_decode_failures(self) -> int:
        """Frames of the current and previous speed level's modes that were found
        but not decoded since the last call (Demodulator.take_decode_failures)."""
        demodulator = getattr(self.ctx.rf_modem, "demodulator", None)
        if demodulator is None:
            return 0
        modes = {self.get_mode_by_speed_level(level).value for level in (self.speed_level, self.previous_speed_level)}
        return demodulator.take_decode_failures(modes)

    def build_burst_ack(self, **flags):
        """Builds an ARQ burst ACK at the current speed level.

        In FSK it carries its frame type and the bytes received in a row: only
        signalling_ack frames go without.

        Args:
            **flags: flag_final, flag_checksum, flag_abort for build_arq_burst_ack.
        """
        if not self.fsk:
            return self.frame_factory.build_arq_burst_ack(self.id, self.speed_level, **flags)
        # frames held after the gap: bit i for the frame i + 1 frames after it
        frames_received = 0
        if self.burst_frame_size:
            for i in range(16):
                if self.received_bytes + (i + 1) * self.burst_frame_size in self.pending_frames:
                    frames_received |= 1 << i
        return self.frame_factory.build_arq_burst_ack(
            self.id,
            self.speed_level,
            offset=self.received_bytes,
            burst_id=self.burst_id,
            frames_received=frames_received,
            **flags,
        )

    def answer_at_burst_end(self, burst_frame, send):
        """Answers a burst after its last frame: at once if this frame is the last,
        else from a timer (schedule_burst_ack), which a later frame of the burst
        replaces. The other station cannot hear while it still transmits.

        Args:
            burst_frame: The ARQ_BURST_FRAME just received.
            send: The answer, send_burst_ack or send_final_ack.
        """
        number, total = self.frame_factory.decode_burst_info(burst_frame["burst_info"])
        if number < total:
            frame_duration = codec2.get_frame_duration(self.get_mode_by_speed_level(self.speed_level).value)
            self.schedule_burst_ack((total - number + 1) * frame_duration, send)
        else:
            self.cancel_burst_ack()
            send()

    def send_final_ack(self, checksum=True):
        """Sends the final burst ACK: all data received, with or without a matching CRC."""
        ack = self.build_burst_ack(flag_final=True, flag_checksum=checksum)
        self.transmit_frame(self.answer_frames(ack), mode=self.get_signalling_mode(FREEDV_MODE.signalling_ack))
        self.log("Final ACK sent")

    def answer_frames(self, ack):
        """A burst ACK as it goes out: on FSK twice in one transmission when the last
        answer was lost (a frame came again), else once."""
        frames = [ack] * self.FSK_SIGNALLING_COPIES if self.fsk and self.answer_lost else ack
        self.answer_lost = False
        return frames

    def schedule_burst_ack(self, delay, send=None):
        """Answers after delay seconds, unless the burst's last frame comes first.

        An FSK burst has several frames. The IRS answers after the last one; the
        timer answers instead if that one is lost. A frame is decoded up to a
        frame length after its end, so delay is one frame length more than the
        frames still to come.

        Args:
            delay (float): Seconds until the answer.
            send: The answer; send_burst_ack by default.
        """
        self.cancel_burst_ack()
        self.burst_ack_timer = threading.Timer(delay, send or self.send_burst_ack)
        self.burst_ack_timer.daemon = True
        self.burst_ack_timer.start()

    def cancel_burst_ack(self):
        """Stops a pending burst ACK timer."""
        if self.burst_ack_timer is not None:
            self.burst_ack_timer.cancel()
            self.burst_ack_timer = None

    def send_burst_ack(self):
        """Acknowledges a burst: the bytes received in a row and the next speed level.

        Called after the last frame of a burst, or by the timer of
        schedule_burst_ack() when the last frame is lost.
        """
        with self.burst_ack_lock:
            self.cancel_burst_ack()
            # the session may have ended or failed meanwhile; with all data in, the
            # final ACK answers (receive_data), not this one: a timer that fired
            # while the last frame came would send a second, non-final ACK
            if (
                self.received_data is None
                or self.all_data_received()
                or self.state
                in [
                    IRS_State.ENDED,
                    IRS_State.FAILED,
                    IRS_State.ABORTED,
                ]
            ):
                return
            self.calibrate_speed_settings()
            ack = self.build_burst_ack(flag_abort=self.abort)

            self.set_state(IRS_State.BURST_REPLY_SENT)
            self.ctx.event_manager.send_arq_session_progress(
                False,
                self.id,
                self.dxcall,
                self.received_bytes,
                self.total_length,
                self.state.name,
                self.speed_level,
                statistics=self.calculate_session_statistics(self.received_bytes, self.total_length),
            )

            self.launch_transmit_and_wait(
                self.answer_frames(ack), self.TIMEOUT_DATA, mode=self.get_signalling_mode(FREEDV_MODE.signalling_ack)
            )

    def calibrate_speed_settings(self, burst_frame=None):
        """Calibrates the speed settings for the ARQ session based on SNR and bandwidth.

        This method determines the appropriate speed level for the session based on the
        current SNR and available bandwidth. It also sets the appropriate decode
        modes for the modem, ensuring smooth transitions between speed levels.

        Args:
            burst_frame (optional): The received burst frame, used to get the sender's speed level. Defaults to None.

        Returns:
            int: The calculated speed level.
        """
        _received_speed_level = burst_frame["speed_level"] if burst_frame else 0

        # the ISS has fallen back to FSK after failures; the SNR cannot tell
        # when the OFDM modes would work again, so stay (the demodulator always
        # decodes the FSK mode)
        if self.fsk:
            self.speed_level = self.FSK_LEVEL
            return self.speed_level

        latest_snr = self.snr if self.snr else -10
        appropriate_speed_level = self.get_appropriate_speed_level(latest_snr, self.maximum_bandwidth)

        # frames that arrived but could not be decoded: a fading channel, which the
        # SNR does not show. Step down, and to FSK from datac4
        datac4_level = self.FSK_LEVEL + 1
        if self.take_decode_failures() >= self.DECODE_FAILURES_TO_STEP_DOWN:
            if self.speed_level <= datac4_level and fsk_modes_enabled(self.ctx):
                self.log("OFDM frames fail to decode at datac4, falling back to FSK", isWarning=True)
                self.fall_back_to_fsk()
                return self.speed_level
            self.log("OFDM frames fail to decode, stepping down to datac4", isWarning=True)
            self.step_down_hold = self.STEP_DOWN_HOLD
        if self.step_down_hold > 0:
            self.step_down_hold -= 1
            appropriate_speed_level = min(appropriate_speed_level, datac4_level)
        modes_to_decode = {}

        # Log the latest SNR, current, appropriate speed levels, and the previous speed level
        self.log(
            f"Latest SNR: {latest_snr}, Current Speed Level: {self.speed_level}, Appropriate Speed Level: {appropriate_speed_level}, Previous Speed Level: {self.previous_speed_level}",
            isWarning=True,
        )

        # Adjust the speed level by one step towards the appropriate level, if needed
        # if appropriate_speed_level > self.speed_level and self.speed_level < len(self.SPEED_LEVEL_DICT) - 1:
        #    # we need to ensure, the received data is equal to our speed level before changing it
        #    if received_speed_level == self.speed_level:
        #        self.speed_level += 1
        # elif appropriate_speed_level < self.speed_level and self.speed_level > 0:
        #    #if received_speed_level == self.speed_level:
        #    #    self.speed_level -= 1

        # Always decode the current mode
        current_mode = self.get_mode_by_speed_level(self.speed_level).value
        modes_to_decode[current_mode] = True

        # Update previous speed level
        if self.previous_speed_level != self.speed_level:
            self.previous_speed_level = self.speed_level  # Update the previous speed level

        # Ensure, previous mode is decoded as well
        previous_mode = self.get_mode_by_speed_level(self.previous_speed_level).value
        modes_to_decode[previous_mode] = True

        # Ensure, appropriate mode is decoded as well
        appropriate_mode = self.get_mode_by_speed_level(appropriate_speed_level).value
        modes_to_decode[appropriate_mode] = True

        self.log(f"Modes to Decode: {list(modes_to_decode.keys())}", isWarning=True)
        # Apply the new decode mode based on the updated and previous speed levels
        self.ctx.rf_modem.demodulator.set_decode_mode(modes_to_decode)

        # finally update the speed level to the appropriate one
        self.speed_level = appropriate_speed_level

        return self.speed_level

    def abort_transmission(self):
        """Aborts the ARQ transmission.

        This method sets the abort flag to True, signaling that the
        transmission should be terminated.
        """
        self.log("Aborting transmission... setting abort flag")
        self.abort = True

    def push_statistics(self, session_stats):
        """Pushes session statistics to the stats API in a background thread.

        The push is a blocking HTTP request, so it must not run on the
        frame dispatching thread.

        Args:
            session_stats (dict): The session statistics to push.
        """
        threading.Thread(
            target=self.statistics.push,
            args=[self.state.name, session_stats, self.dxcall],
            daemon=True,
        ).start()

    def send_stop_ack(self, stop_frame):
        """Sends a stop acknowledgement and finalizes the session.

        This method handles the reception of an ARQ_STOP frame by sending an
        ARQ_STOP_ACK, setting the session state to ABORTED, and sending session
        finished events. It also pushes session statistics if enabled.

        Args:
            stop_frame: The received ARQ_STOP frame.

        Returns:
            Tuple[None, None]: Returns None for both data and type_byte as this method doesn't handle data.
        """
        stop_ack = self.frame_factory.build_arq_stop_ack(self.id)
        # in signalling, not signalling_ack: its 3-byte frames carry no frame
        # type and are read as ARQ_BURST_ACKs (DataFrameFactory.deconstruct)
        self.launch_transmit_and_wait(
            self.signalling_frames(stop_ack),
            self.TIMEOUT_CONNECT,
            mode=self.get_signalling_mode(FREEDV_MODE.signalling),
        )
        self.set_state(IRS_State.ABORTED)
        self.ctx.state_manager.setARQ(False)
        session_stats = self.calculate_session_statistics(self.received_bytes, self.total_length)

        self.ctx.event_manager.send_arq_session_finished(
            False, self.id, self.dxcall, False, self.state.name, statistics=session_stats
        )
        if self.ctx.config_manager.config["STATION"]["enable_stats"]:
            self.push_statistics(session_stats)

        return None, None

    def transmission_failed(self, irs_frame=None):
        """Handles transmission failures.

        This method is called when a transmission fails. It sets the session
        state to FAILED, logs the failure, sends session finished events,
        pushes session statistics if enabled, and disables ARQ.

        Args:
            irs_frame (optional): The received IRS frame. Defaults to None.

        Returns:
            Tuple[None, None]: Returns None for both data and type_byte.
        """
        # final function for failed transmissions
        self.session_ended = time.time()
        self.set_state(IRS_State.FAILED)
        self.log("Transmission failed!")
        # self.ctx.rf_modem.demodulator.set_decode_mode()
        session_stats = self.calculate_session_statistics(self.received_bytes, self.total_length)
        self.cancel_burst_ack()
        self.received_data = None
        self.received_bytes = 0
        self.pending_frames = {}
        self.ctx.event_manager.send_arq_session_finished(
            False, self.id, self.dxcall, False, self.state.name, statistics=session_stats
        )
        if self.ctx.config_manager.config["STATION"]["enable_stats"]:
            self.push_statistics(session_stats)

        self.ctx.state_manager.setARQ(False)
        return None, None

    def transmission_aborted(self):
        """Handles the abortion of the transmission.

        This method is called when the transmission is aborted. It sets the
        session state to ABORTED, logs the abortion, sends session finished
        events, and disables ARQ.

        Returns:
            Tuple[None, None]: Returns None for both data and type_byte.
        """
        if self.state not in [IRS_State.ABORTED, IRS_State.ENDED]:
            self.log("session aborted")
            self.session_ended = time.time()
            self.set_state(IRS_State.ABORTED)
            self.cancel_burst_ack()
            # break actual retries
            self.event_frame_received.set()

            # self.ctx.rf_modem.demodulator.set_decode_mode()
            self.ctx.event_manager.send_arq_session_finished(
                False,
                self.id,
                self.dxcall,
                False,
                self.state.name,
                statistics=self.calculate_session_statistics(self.received_bytes, self.total_length),
            )
            self.ctx.state_manager.setARQ(False)
        return None, None
