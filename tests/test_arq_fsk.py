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
    def test_burst_ack_in_fsk_carries_its_frame_type(self):
        ctx = types.SimpleNamespace()
        ctx.config_manager = CONFIG(ctx, testenv.config_path())
        factory = DataFrameFactory(ctx)
        ack = factory.build_arq_burst_ack(7, 0, flag_final=True, flag_checksum=True, with_frame_type=True)
        frame = factory.deconstruct(bytes(ack) + bytes(2), mode_name="DATA_FSK4_50")
        self.assertEqual(frame["frame_type_int"], FRAME_TYPE.ARQ_BURST_ACK.value)
        self.assertEqual(frame["session_id"], 7)
        self.assertEqual(frame["speed_level"], 0)
        self.assertTrue(frame["flag"]["FINAL"])
        self.assertTrue(frame["flag"]["CHECKSUM"])
        # DATAC14 burst ACKs stay without it
        self.assertEqual(len(factory.build_arq_burst_ack(7, 0)), 3)


class SessionOnFadingPath:
    """Two stations in test mode; the channel drops the OFDM frames lost() picks."""

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
        self.sent = []  # (station, mode, frame type) of every frame sent
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
                mode, frame = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=0.2)
            except queue.Empty:
                continue
            frame = bytes(frame) + bytes(2)  # CRC
            # DATAC14 burst ACKs carry no frame type
            frame_type = FRAME_TYPE.ARQ_BURST_ACK.value if len(frame) == 5 else frame[0]
            self.sent.append((station, mode, frame_type))
            if mode is not FSK and self.lost(frame_type):
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
        return [m for s, m, t in self.sent if t == frame_type.value and station in (None, s)]


class TestAuroralPath(SessionOnFadingPath, unittest.TestCase):
    def lost(self, frame_type):
        return True  # no OFDM frame gets through, signalling included

    def test_session_moves_to_fsk(self):
        payload = np.random.default_rng(1).bytes(100)
        self.assertTrue(self.transfer(payload))
        self.assertEqual(self.received(), payload)

        modes = [mode for _, mode, _ in self.sent]
        # two OPEN attempts in DATAC13, then everything in FSK, from both sides
        self.assertEqual(modes[:2], [FREEDV_MODE.signalling] * 2)
        self.assertTrue(all(mode is FSK for mode in modes[2:]), modes)
        self.assertIn(FSK, self.frames(FRAME_TYPE.ARQ_SESSION_OPEN_ACK, "IRS"))
        self.assertEqual(len(self.frames(FRAME_TYPE.ARQ_BURST_FRAME)), 5)  # 23 bytes each at the FSK level


class TestDataBurstsFail(SessionOnFadingPath, unittest.TestCase):
    def lost(self, frame_type):
        return frame_type == FRAME_TYPE.ARQ_BURST_FRAME.value  # signalling gets through, OFDM data does not

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


if __name__ == "__main__":
    unittest.main()
