"""ARQ sessions fall back to the HF FSK mode data_fsk4_50 (speed level 0) after
failures: signalling after unanswered OFDM attempts, data after failed bursts
at datac4, and then both sides stay on it. The ISS waits longer for answers to
FSK frames, which codec2 decodes up to one frame length after their end (see
freedata_server/docs/fading-channels.md, section 8)."""

import base64
import queue
import threading
import types
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session_irs import ARQSessionIRS
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.command_arq_raw import ARQRawCommand
from freedata_server.config import CONFIG
from freedata_server.context import AppContext
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.modem_frametypes import FRAME_TYPE

FREEDV_MODE = codec2.FREEDV_MODE
FSK = FREEDV_MODE.data_fsk4_50
DATAC4_LEVEL = 1
# added to the wait after an FSK frame: the IRS decodes it late (5.44 s), and
# answers in FSK (5.44 s on the air, decoded up to 5.44 s late)
FSK_ROUND_TRIP = 3 * 5.44


def make_iss(speed_level=DATAC4_LEVEL):
    """An ISS with only what transmit_wait_and_retry needs, without a modem."""
    iss = ARQSessionISS.__new__(ARQSessionISS)
    iss.speed_level = speed_level
    iss.fsk = False
    iss.state = ISS_State.INFO_SENT
    iss.confirmed_bytes = 0
    iss.log = mock.Mock()
    iss.transmit_frame = mock.Mock()
    iss.send_data = mock.Mock()
    return iss


def answer_after(failures):
    """A threading.Event stand-in whose wait() times out `failures` times, then is answered."""
    event = mock.Mock()
    event.wait.side_effect = [False] * failures + [True]
    return event


class TestDecodeDelay(unittest.TestCase):
    def test_ofdm_modes_decode_at_once(self):
        for mode in (
            FREEDV_MODE.signalling,
            FREEDV_MODE.signalling_ack,
            FREEDV_MODE.datac4,
            FREEDV_MODE.data_ofdm_2438,
        ):
            self.assertEqual(codec2.get_decode_delay(mode.value), 0.0, mode.name)

    def test_fsk_modes_decode_a_frame_later(self):
        # measured: up to 5.1 s for data_fsk4_50 and 1.3 s for data_vhf_1
        self.assertAlmostEqual(codec2.get_decode_delay(FSK.value), 5.44)
        self.assertAlmostEqual(codec2.get_decode_delay(FREEDV_MODE.data_vhf_1.value), 1.36)


class TestSpeedLevels(unittest.TestCase):
    def test_fsk_is_the_lowest_level(self):
        levels = ARQSessionISS.SPEED_LEVEL_DICT
        self.assertIs(levels[ARQSessionISS.FSK_LEVEL]["mode"], FSK)
        self.assertEqual(ARQSessionISS.FSK_LEVEL, min(levels))
        self.assertIs(levels[DATAC4_LEVEL]["mode"], FREEDV_MODE.datac4)

    def test_snr_never_chooses_fsk(self):
        iss = make_iss()
        iss.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(config={"MODEM": {"maximum_bandwidth": 0}}),
            state_manager=types.SimpleNamespace(channel_busy_slot=[False] * 5),
        )
        self.assertEqual(iss.get_appropriate_speed_level(-30), DATAC4_LEVEL)
        self.assertEqual(iss.get_appropriate_speed_level(20), 4)


class TestResponseTimeout(unittest.TestCase):
    def test_ofdm_timeouts_unchanged(self):
        iss = make_iss()
        self.assertEqual(iss.get_response_timeout(ARQSessionISS.TIMEOUT_TRANSFER), ARQSessionISS.TIMEOUT_TRANSFER)
        self.assertEqual(
            iss.get_response_timeout(ARQSessionISS.TIMEOUT_CONNECT_ACK, FREEDV_MODE.signalling),
            ARQSessionISS.TIMEOUT_CONNECT_ACK,
        )

    def test_fsk_speed_level_waits_longer(self):
        iss = make_iss(ARQSessionISS.FSK_LEVEL)
        self.assertAlmostEqual(
            iss.get_response_timeout(ARQSessionISS.TIMEOUT_TRANSFER), ARQSessionISS.TIMEOUT_TRANSFER + FSK_ROUND_TRIP
        )
        self.assertAlmostEqual(
            iss.get_response_timeout(ARQSessionISS.TIMEOUT_CONNECT_ACK, FSK),
            ARQSessionISS.TIMEOUT_CONNECT_ACK + FSK_ROUND_TRIP,
        )

    def test_transmit_wait_and_retry_uses_it(self):
        iss = make_iss()
        event = answer_after(0)
        with mock.patch("freedata_server.arq_session_iss.threading.Event", return_value=event):
            iss.transmit_wait_and_retry(b"frame", ARQSessionISS.TIMEOUT_CONNECT_ACK, 1, FSK)
        iss.transmit_frame.assert_called_once_with(b"frame", FSK)
        self.assertAlmostEqual(event.wait.call_args.args[0], ARQSessionISS.TIMEOUT_CONNECT_ACK + FSK_ROUND_TRIP)

    def test_burst_of_several_frames(self):
        # sent in one transmission; the IRS may answer a frame length late if
        # it misses the last frame (its ACK timer)
        iss = make_iss(ARQSessionISS.FSK_LEVEL)
        event = answer_after(0)
        burst = [b"1", b"2", b"3", b"4"]
        with mock.patch("freedata_server.arq_session_iss.threading.Event", return_value=event):
            iss.transmit_wait_and_retry(burst, ARQSessionISS.TIMEOUT_TRANSFER, 1, "auto", isARQBurst=True)
        iss.transmit_frame.assert_called_once_with(burst, "auto")
        self.assertAlmostEqual(event.wait.call_args.args[0], ARQSessionISS.TIMEOUT_TRANSFER + FSK_ROUND_TRIP + 5.44)


class TestFallback(unittest.TestCase):
    def run_twr(self, iss, failures, mode, retries, is_burst=False):
        event = answer_after(failures)
        with mock.patch("freedata_server.arq_session_iss.threading.Event", return_value=event):
            iss.transmit_wait_and_retry(b"frame", 1.0, retries, mode, isARQBurst=is_burst)
        sent = [c.args[1] for c in iss.transmit_frame.call_args_list]
        waits = [c.args[0] for c in event.wait.call_args_list]
        return sent, waits

    def test_signalling_falls_back_to_fsk(self):
        iss = make_iss()
        sent, waits = self.run_twr(iss, 2, FREEDV_MODE.signalling, ARQSessionISS.RETRIES_CONNECT)
        self.assertEqual(sent, [FREEDV_MODE.signalling, FREEDV_MODE.signalling, FSK])
        self.assertEqual(waits[:2], [1.0, 1.0])
        self.assertAlmostEqual(waits[2], 1.0 + FSK_ROUND_TRIP)
        self.assertTrue(iss.fsk)
        self.assertEqual(iss.speed_level, ARQSessionISS.FSK_LEVEL)

    def test_answered_signalling_stays_ofdm(self):
        iss = make_iss()
        sent, _ = self.run_twr(iss, 1, FREEDV_MODE.signalling, ARQSessionISS.RETRIES_CONNECT)
        self.assertEqual(sent, [FREEDV_MODE.signalling] * 2)
        self.assertFalse(iss.fsk)

    def test_bursts_step_down_to_datac4_then_fsk(self):
        iss = make_iss(speed_level=4)
        self.run_twr(iss, 2, "auto", ARQSessionISS.RETRIES_DATA, is_burst=True)
        self.assertEqual(iss.speed_level, DATAC4_LEVEL)
        self.assertFalse(iss.fsk)
        iss.send_data.assert_called_once()

        iss.send_data.reset_mock()
        iss.transmit_frame.reset_mock()
        self.run_twr(iss, 2, "auto", ARQSessionISS.RETRIES_DATA, is_burst=True)
        self.assertEqual(iss.speed_level, ARQSessionISS.FSK_LEVEL)
        self.assertTrue(iss.fsk)
        iss.send_data.assert_called_once()

    def test_fsk_bursts_keep_trying(self):
        iss = make_iss(ARQSessionISS.FSK_LEVEL)
        iss.fsk = True
        self.run_twr(iss, 3, "auto", ARQSessionISS.RETRIES_DATA, is_burst=True)
        iss.send_data.assert_not_called()
        self.assertEqual(iss.transmit_frame.call_count, 4)

    def test_iss_stays_on_fsk(self):
        iss = make_iss(ARQSessionISS.FSK_LEVEL)
        iss.fsk = True
        iss.update_speed_level({"speed_level": 4})
        self.assertEqual(iss.speed_level, ARQSessionISS.FSK_LEVEL)

    def test_irs_answers_fsk_at_fsk_level(self):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.fsk = False
        irs.speed_level = 4
        irs.log = mock.Mock()
        irs.fall_back_to_fsk()
        self.assertEqual(irs.calibrate_speed_settings(), ARQSessionIRS.FSK_LEVEL)
        self.assertIs(irs.get_signalling_mode(FREEDV_MODE.signalling_ack), FSK)
        self.assertTrue(irs.is_fsk_frame({"mode_name": "DATA_FSK4_50"}))
        self.assertFalse(irs.is_fsk_frame({"mode_name": "SIGNALLING_ACK"}))


class TestFskBurstAck(unittest.TestCase):
    def test_burst_ack_in_fsk_carries_type_and_offset(self):
        ctx = types.SimpleNamespace()
        ctx.config_manager = CONFIG(ctx, testenv.config_path())
        factory = DataFrameFactory(ctx)
        ack = factory.build_arq_burst_ack(
            7, 0, flag_final=True, flag_checksum=True, offset=46, burst_id=200, frames_received=0b101
        )
        frame = factory.deconstruct(bytes(ack) + bytes(2), mode_name="DATA_FSK4_50")
        self.assertEqual(frame["frame_type_int"], FRAME_TYPE.ARQ_BURST_ACK.value)
        self.assertEqual(frame["session_id"], 7)
        self.assertEqual(frame["speed_level"], 0)
        self.assertEqual(frame["offset"], 46)
        self.assertEqual(frame["burst_id"], 200)
        self.assertEqual(frame["frames_received"], 0b101)
        self.assertTrue(frame["flag"]["FINAL"])
        self.assertTrue(frame["flag"]["CHECKSUM"])
        # DATAC14 burst ACKs stay without
        self.assertEqual(len(factory.build_arq_burst_ack(7, 0)), 3)


class TestSelectiveRepeats(unittest.TestCase):
    def make_iss(self):
        iss = make_iss(ARQSessionISS.FSK_LEVEL)
        iss.fsk = True
        iss.burst_id = 5
        iss.state = ISS_State.BURST_SENT
        iss.total_length = 1000
        iss.fsk_frames_per_burst = 4
        iss.last_burst = {"offsets": [0, 22, 44, 66], "size": 22}
        iss.irs_holds = set()
        return iss

    def ack(self, offset, frames_received=0, burst_id=5):
        return {
            "frame_type_int": FRAME_TYPE.ARQ_BURST_ACK.value,
            "offset": offset,
            "burst_id": burst_id,
            "frames_received": frames_received,
        }

    def test_frames_held_after_the_gap(self):
        iss = self.make_iss()
        iss.adapt_fsk_burst(self.ack(22, 0b11))  # holds 44 and 66
        self.assertEqual(iss.irs_holds, {44, 66})
        self.assertEqual(iss.fsk_frames_per_burst, 4)  # 1 of 4 lost: unchanged

    def test_more_frames_after_a_clean_burst(self):
        iss = self.make_iss()
        iss.adapt_fsk_burst(self.ack(88))
        self.assertEqual(iss.fsk_frames_per_burst, 5)
        iss.fsk_frames_per_burst = ARQSessionISS.FSK_FRAMES_PER_BURST_MAX
        iss.adapt_fsk_burst(self.ack(88))
        self.assertEqual(iss.fsk_frames_per_burst, ARQSessionISS.FSK_FRAMES_PER_BURST_MAX)

    def test_fewer_frames_after_losses(self):
        iss = self.make_iss()
        iss.adapt_fsk_burst(self.ack(22))  # 3 of 4 lost
        self.assertEqual(iss.fsk_frames_per_burst, 2)
        iss.adapt_fsk_burst(self.ack(0))
        self.assertEqual(iss.fsk_frames_per_burst, ARQSessionISS.FSK_FRAMES_PER_BURST_MIN)

    def test_stale_ack_ignored(self):
        iss = self.make_iss()
        self.assertTrue(iss.is_stale_frame(self.ack(22, burst_id=4)))
        self.assertFalse(iss.is_stale_frame(self.ack(22, burst_id=5)))
        # DATAC14 ACKs carry no burst id
        self.assertFalse(iss.is_stale_frame({"frame_type_int": FRAME_TYPE.ARQ_BURST_ACK.value}))

    def test_irs_reports_frames_held(self):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.fsk = True
        irs.id = 9
        irs.speed_level = 0
        irs.received_bytes = 22
        irs.burst_id = 5
        irs.burst_frame_size = 22
        irs.pending_frames = {44: b"", 88: b""}
        irs.frame_factory = mock.Mock()
        irs.build_burst_ack()
        kwargs = irs.frame_factory.build_arq_burst_ack.call_args.kwargs
        self.assertEqual((kwargs["offset"], kwargs["burst_id"]), (22, 5))
        self.assertEqual(kwargs["frames_received"], 0b101)  # 44 = gap + 1 frame, 88 = gap + 3 frames


class SessionOnFadingPath:
    """Two stations in test mode; the channel drops the frames lost() picks."""

    @classmethod
    def setUpClass(cls):
        cls.ctx_ISS = AppContext(testenv.config_path())
        cls.ctx_ISS.TESTMODE = True
        cls.ctx_ISS.startup()
        cls.ctx_IRS = AppContext(testenv.config_path())
        cls.ctx_IRS.TESTMODE = True
        cls.ctx_IRS.startup()

    @classmethod
    def tearDownClass(cls):
        cls.ctx_IRS.shutdown()
        cls.ctx_ISS.shutdown()

    def setUp(self):
        # short OFDM timeouts; FSK frames add their decoding delay to them
        for name in ("TIMEOUT_CONNECT_ACK", "TIMEOUT_TRANSFER"):
            patcher = mock.patch.object(ARQSessionISS, name, 0.5)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = []  # (station, mode, frame type, frame) of every frame sent
        self.running = True
        self.channels = [
            threading.Thread(target=self.channel, args=[self.ctx_ISS, self.ctx_IRS, "ISS"], daemon=True),
            threading.Thread(target=self.channel, args=[self.ctx_IRS, self.ctx_ISS, "IRS"], daemon=True),
        ]
        for channel in self.channels:
            channel.start()

    def tearDown(self):
        self.running = False
        for channel in self.channels:
            channel.join(3)

    def channel(self, ctx_a, ctx_b, station):
        while self.running:
            try:
                mode, frames = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=0.2)
            except queue.Empty:
                continue
            # a burst of several frames is one transmission
            for frame in frames if isinstance(frames, list) else [frames]:
                frame = bytes(frame) + bytes(2)  # CRC
                # DATAC14 burst ACKs carry no frame type
                frame_type = FRAME_TYPE.ARQ_BURST_ACK.value if len(frame) == 5 else frame[0]
                self.sent.append((station, mode, frame_type, frame))
                if self.lost(mode, frame_type):
                    continue
                # the demodulator names a frame's mode like this
                ctx_b.service_manager.frame_dispatcher.process_data(
                    frame, None, len(frame), 15, 0, mode_name=mode.name.upper()
                )

    def transfer(self, payload):
        params = {"dxcall": "AA1AAA-1", "data": base64.b64encode(payload), "type": "raw"}
        ARQRawCommand(self.ctx_ISS, params).run()
        while True:
            outbound = self.ctx_ISS.TESTMODE_EVENTS.get(timeout=120).get("arq-transfer-outbound", {})
            if "success" in outbound:
                return outbound["success"]

    def received(self):
        while True:
            inbound = self.ctx_IRS.TESTMODE_EVENTS.get(timeout=10).get("arq-transfer-inbound", {})
            if inbound.get("success"):
                return base64.b64decode(inbound["data"])

    def frames(self, frame_type, station=None):
        """Modes of the frames of a type sent, in order."""
        return [m for s, m, t, _ in self.sent if t == frame_type.value and station in (None, s)]


class TestAuroralPath(SessionOnFadingPath, unittest.TestCase):
    def lost(self, mode, frame_type):
        return mode is not FSK  # no OFDM frame gets through, signalling included

    def test_session_moves_to_fsk(self):
        payload = np.random.default_rng(1).bytes(100)
        self.assertTrue(self.transfer(payload))
        self.assertEqual(self.received(), payload)

        modes = [mode for _, mode, _, _ in self.sent]
        # two OPEN attempts in DATAC13, then everything in FSK, from both sides
        self.assertEqual(modes[:2], [FREEDV_MODE.signalling] * 2)
        self.assertTrue(all(mode is FSK for mode in modes[2:]), modes)
        self.assertIn(FSK, self.frames(FRAME_TYPE.ARQ_SESSION_OPEN_ACK, "IRS"))
        # 21 bytes per frame at the FSK level, 4 frames in the first burst: 2 bursts, 2 ACKs
        self.assertEqual(len(self.frames(FRAME_TYPE.ARQ_BURST_FRAME)), 5)
        self.assertEqual(len(self.frames(FRAME_TYPE.ARQ_BURST_ACK)), 2)


class TestDataBurstsFail(SessionOnFadingPath, unittest.TestCase):
    def lost(self, mode, frame_type):
        # signalling gets through, OFDM data does not
        return mode is not FSK and frame_type == FRAME_TYPE.ARQ_BURST_FRAME.value

    def test_bursts_step_down_to_fsk(self):
        payload = np.random.default_rng(2).bytes(60)
        self.assertTrue(self.transfer(payload))
        self.assertEqual(self.received(), payload)

        bursts = self.frames(FRAME_TYPE.ARQ_BURST_FRAME)
        # the SNR-chosen level twice, datac4 twice, then FSK
        self.assertEqual(bursts[:4], [FREEDV_MODE.data_ofdm_2438] * 2 + [FREEDV_MODE.datac4] * 2)
        self.assertTrue(bursts[4:] and all(mode is FSK for mode in bursts[4:]), bursts)
        # session signalling went in OFDM; the burst ACKs followed the bursts to FSK
        self.assertEqual(self.frames(FRAME_TYPE.ARQ_SESSION_OPEN), [FREEDV_MODE.signalling])
        acks = self.frames(FRAME_TYPE.ARQ_BURST_ACK, "IRS")
        self.assertTrue(acks and all(mode is FSK for mode in acks), acks)


class TestLostFskFrames(SessionOnFadingPath, unittest.TestCase):
    """On an auroral path, FSK frames are lost as well: one inside a burst, and
    the last one of the next burst, which the IRS's ACK timer covers."""

    LOST_BURST_FRAMES = {1, 7}  # counting the FSK burst frames sent from 0

    def setUp(self):
        super().setUp()
        self.fsk_burst_frames = 0

    def lost(self, mode, frame_type):
        if mode is not FSK:
            return True
        if frame_type != FRAME_TYPE.ARQ_BURST_FRAME.value:
            return False
        self.fsk_burst_frames += 1
        return self.fsk_burst_frames - 1 in self.LOST_BURST_FRAMES

    def test_bursts_continue_from_the_gap(self):
        payload = np.random.default_rng(3).bytes(150)  # 8 frames of 21 bytes
        self.assertTrue(self.transfer(payload))
        self.assertEqual(self.received(), payload)

        offsets = [int.from_bytes(f[3:7], "big") for s, m, t, f in self.sent if t == FRAME_TYPE.ARQ_BURST_FRAME.value]
        acks = [f for s, m, t, f in self.sent if t == FRAME_TYPE.ARQ_BURST_ACK.value]
        acked = [(int.from_bytes(f[4:8], "big"), int.from_bytes(f[9:11], "big")) for f in acks]
        # burst 1 loses 21: the IRS keeps 42 and 63 and asks for 21, saying it
        # holds the two frames after it. Burst 2 sends 21 and new frames only,
        # so the IRS has up to 126, but loses its last frame (126): the timer
        # answers. Burst 3 finishes.
        self.assertEqual(offsets, [0, 21, 42, 63, 21, 84, 105, 126, 126, 147])
        self.assertEqual(acked, [(21, 0b11), (126, 0), (150, 0)])
        # each ACK answers the burst before it
        self.assertEqual([f[8] for f in acks], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
