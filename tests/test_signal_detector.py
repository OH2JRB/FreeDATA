"""The SignalDetector finds transmissions by their power, and ARQ sessions use
it where codec2 reports nothing: on hl_disturbed the OFDM modes do not even
sync on a frame (see freedata_server/docs/arq-sessions.md, section 4.1)."""

import os
import sys
import threading
import time
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session_irs import ARQSessionIRS, IRS_State
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.signal_detector import SignalDetector

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import channel_sim as cs  # noqa: E402

FREEDV_MODE = codec2.FREEDV_MODE
FS = 8000


def noise(seconds, level=300.0, seed=0):
    return np.random.default_rng(seed).standard_normal(int(seconds * FS)) * level


def heard(detector, signal_seconds, end, quiet_seconds=3.0, level=3000.0):
    """Quiet audio, then a signal of signal_seconds ending at time end."""
    detector.push(noise(quiet_seconds), now=end - signal_seconds)
    detector.push(noise(signal_seconds, level, seed=1), now=end)


class TestSignalDetector(unittest.TestCase):
    def test_frames_on_a_disturbed_path(self):
        # an OFDM burst that datac4 cannot decode on hl_disturbed is found to the block
        mode = FREEDV_MODE.data_ofdm_2438
        modulator = cs._modulator(vhf=False)
        tx = codec2.open_instance(mode.value)
        size = codec2.api.freedv_get_bits_per_modem_frame(tx) // 8 - 2
        rng = np.random.default_rng(1)
        burst = np.frombuffer(modulator.create_burst(mode, 1, 0, bytearray(rng.bytes(size))), dtype=np.int16)
        audio = np.concatenate([np.zeros(3 * FS, np.int16), burst, np.zeros(2 * FS, np.int16)])
        rx = cs.apply_channel(audio, cs.CHANNELS["hl_disturbed"], 10, rng)
        detector = SignalDetector()
        detector.push(rx, now=100.0)
        start = 100.0 - len(rx) / FS
        found = detector.segments()
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][0] - start, 3.0, delta=0.15)
        self.assertAlmostEqual(found[0][1] - start, 3.0 + len(burst) / FS, delta=0.15)

    def test_nothing_in_noise(self):
        detector = SignalDetector()
        detector.push(noise(60) * (1 + 0.1 * np.sin(np.arange(60 * FS) / FS / 7)), now=100.0)
        self.assertEqual(detector.segments(), [])
        self.assertFalse(detector.receiving(now=100.0))

    def test_own_transmissions_left_out(self):
        detector = SignalDetector()
        detector.push(noise(3), now=97.0)
        detector.push(noise(2, 3000.0), transmitting=True, now=99.0)
        detector.push(noise(1), now=100.0)
        self.assertEqual(detector.segments(), [])

    def test_receiving_and_since(self):
        detector = SignalDetector()
        heard(detector, 2.0, end=100.0)
        self.assertTrue(detector.receiving(now=100.1))
        self.assertFalse(detector.receiving(now=101.0))
        self.assertEqual(len(detector.segments(since=99.0)), 1)
        self.assertEqual(detector.segments(since=100.5), [])

    def test_no_floor_without_audio(self):
        self.assertEqual(SignalDetector().segments(), [])


class TestIrsHearsUndecodableBurst(unittest.TestCase):
    def make_irs(self, detector):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.fsk = False
        irs.id = 3
        irs.speed_level = irs.previous_speed_level = 4  # data_ofdm_2438, 5.5 s frames
        irs.state = IRS_State.BURST_REPLY_SENT
        irs.received_data = bytearray(10)
        irs.received_bytes = 10
        irs.burst_id = 7
        irs.burst_frame_size = 0
        irs.pending_frames = {}
        irs.abort = False
        irs.log = mock.Mock()
        irs.transmit_frame = mock.Mock()
        irs.burst_ack_lock = threading.RLock()
        irs.frame_factory = mock.Mock()
        irs.ctx = mock.Mock()
        irs.ctx.rf_modem.demodulator.signal_detector = detector
        return irs

    def test_burst_heard_but_not_decoded(self):
        detector = SignalDetector()
        now = time.monotonic()
        heard(detector, 5.0, end=now - 1.5)
        irs = self.make_irs(detector)
        self.assertTrue(irs.burst_heard_not_decoded(since=now - 8))
        # not when the signal ended before the IRS's own transmission
        self.assertFalse(irs.burst_heard_not_decoded(since=now - 1))

    def test_too_short_or_too_recent(self):
        detector = SignalDetector()
        now = time.monotonic()
        heard(detector, 1.0, end=now - 1.5)  # a DATAC13 frame, not a 5.5 s burst
        self.assertFalse(self.make_irs(detector).burst_heard_not_decoded(since=now - 8))
        detector = SignalDetector()
        heard(detector, 5.0, end=now - 0.2)  # the decoder may still deliver it
        self.assertFalse(self.make_irs(detector).burst_heard_not_decoded(since=now - 8))

    def test_answer_in_fsk(self):
        irs = self.make_irs(SignalDetector())
        irs.answer_undecodable_burst()
        self.assertTrue(irs.fsk)
        self.assertEqual(irs.speed_level, ARQSessionIRS.FSK_LEVEL)
        kwargs = irs.frame_factory.build_arq_burst_ack.call_args.kwargs
        self.assertEqual((kwargs["offset"], kwargs["burst_id"]), (10, 8))  # the burst after the last decoded
        self.assertIs(irs.transmit_frame.call_args.kwargs["mode"], FREEDV_MODE.data_fsk4_50)


class TestIssHearsAnswer(unittest.TestCase):
    def make_iss(self, detector):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.speed_level = 4
        iss.fsk = False
        iss.probing = False
        iss.state = ISS_State.BURST_SENT
        iss.confirmed_bytes = 0
        iss.log = mock.Mock()
        iss.transmit_frame = mock.Mock()
        iss.send_data = mock.Mock()
        iss.event_frame_received = threading.Event()
        iss.ctx = mock.Mock()
        iss.ctx.rf_modem.demodulator.signal_detector = detector
        return iss

    def test_waits_while_the_answer_is_on_the_air(self):
        detector = SignalDetector()
        now = time.monotonic()
        heard(detector, 0.5, end=now)  # the IRS's FSK answer has begun
        iss = self.make_iss(detector)
        threading.Timer(0.5, iss.event_frame_received.set).start()  # and gets decoded
        self.assertTrue(iss.wait_for_answer_on_air(since=now - 1))

    def test_no_wait_when_nothing_on_the_air(self):
        detector = SignalDetector()
        detector.push(noise(5), now=time.monotonic())
        started = time.monotonic()
        self.assertFalse(self.make_iss(detector).wait_for_answer_on_air(since=started - 1))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_undecodable_answer_moves_to_fsk(self):
        detector = SignalDetector()
        iss = self.make_iss(detector)
        event = mock.Mock()

        def wait(timeout):
            # meanwhile the IRS answers in DATAC14, which is heard but not decoded
            if not detector.segments():
                heard(detector, 0.7, end=time.monotonic())
            time.sleep(min(timeout, 0.05))
            return False

        event.wait.side_effect = wait
        with testenv.patch_threading_event("freedata_server.arq_session_iss", event):
            iss.transmit_wait_and_retry([b"burst"], 1.0, 25, "auto", isARQBurst=True)
        self.assertTrue(iss.fsk)
        self.assertEqual(iss.transmit_frame.call_count, 1)  # not repeated in OFDM
        self.assertTrue(iss.send_data.call_args.kwargs["fallback"])


if __name__ == "__main__":
    unittest.main()
