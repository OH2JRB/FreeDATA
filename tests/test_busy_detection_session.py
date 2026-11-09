"""Busy-slot detection during an ARQ session or P2P connection: the traffic
then is the other station's own frames, which counted as busy until a
receiver synced on them, and a slot stayed busy twice as long as it was
counted, so the speed level chosen after each burst saw a busy channel (on the
air 2026-11-08). audio.calculate_fft counts no new traffic then."""

import queue
import types
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import audio
from freedata_server.state_manager import StateManager

CENTRE = 2


def tone(hz=1500, n=800):
    t = np.arange(n) / 8000
    rng = np.random.default_rng(1)
    return (8000 * np.sin(2 * np.pi * hz * t) + rng.normal(0, 50, n)).astype(np.int16)


def states(in_session):
    s = mock.Mock()
    s.isTransmitting.return_value = False
    s.is_receiving_codec2_signal.return_value = False
    s.is_in_session.return_value = in_session
    return s


class TestFft(unittest.TestCase):
    def setUp(self):
        audio.SLOT_DELAY[:] = [0] * len(audio.SLOT_DELAY)
        audio.CHANNEL_BUSY_DELAY = 0
        self.addCleanup(lambda: audio.SLOT_DELAY.__setitem__(slice(None), [0] * len(audio.SLOT_DELAY)))

    def slots(self, in_session):
        s = states(in_session)
        audio.calculate_fft(tone(), queue.Queue(), s)
        return s.set_channel_slot_busy.call_args.args[0], s

    def test_traffic_counts_outside_a_session(self):
        slots, s = self.slots(False)
        self.assertTrue(slots[CENTRE])
        s.set_channel_busy_condition_traffic.assert_called_with(True)

    def test_no_new_traffic_counted_in_a_session(self):
        slots, s = self.slots(True)
        self.assertFalse(any(slots))
        s.set_channel_busy_condition_traffic.assert_called_with(False)

    def test_marks_from_before_the_session_fade(self):
        self.slots(False)
        held = audio.SLOT_DELAY[CENTRE]
        self.assertGreater(held, 0)
        for _ in range(held):
            slots, _ = self.slots(True)
        self.assertFalse(slots[CENTRE])


class Session:
    def __init__(self, state_name):
        self.state = types.SimpleNamespace(name=state_name)


class TestIsInSession(unittest.TestCase):
    def manager(self):
        return StateManager(queue.Queue())

    def test_idle(self):
        self.assertFalse(self.manager().is_in_session())

    def test_arq_session(self):
        m = self.manager()
        m.setARQ(True)
        self.assertTrue(m.is_in_session())
        m.setARQ(False)
        self.assertFalse(m.is_in_session())

    def test_p2p_connection(self):
        m = self.manager()
        m.p2p_connection_sessions = {1: Session("DISCONNECTED"), 2: Session("FAILED")}
        self.assertFalse(m.is_in_session())
        m.p2p_connection_sessions[3] = Session("CONNECTED")
        self.assertTrue(m.is_in_session())


if __name__ == "__main__":
    unittest.main()
