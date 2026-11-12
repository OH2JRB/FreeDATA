"""The message queue on a channel that stays busy.

On the air on 2026-11-12 one station heard a steady non-FreeDATA signal, its
channel-busy check never cleared, and a queued message stayed queued for good:
check_for_queued_messages ran only while channel_busy_event was set. A channel
busy only from such signals now holds the queue for QUEUE_BUSY_MAX_WAIT seconds
in a row; codec2 traffic (another FreeDATA station) still holds it."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import schedule_manager
from freedata_server.schedule_manager import ScheduleManager

MESSAGE = {"id": "OH2JRB-12_OH2JRB-13_test", "destination": "OH2JRB-13"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_manager(busy=False, codec2=False):
    state = types.SimpleNamespace(
        channel_busy_event=threading.Event(),
        is_receiving_codec2_signal=lambda: state.codec2,
        getARQ=lambda: False,
        is_modem_running=True,
        codec2=codec2,
    )
    if not busy:
        state.channel_busy_event.set()
    m = ScheduleManager.__new__(ScheduleManager)
    m.ctx = types.SimpleNamespace(state_manager=state)
    m.log = mock.Mock()
    m.queue_busy_since = None
    return m, state


class TestQueueOnBusyChannel(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.queued = MESSAGE
        db = mock.Mock()
        db.return_value.get_first_queued_message.side_effect = lambda: self.queued
        self.send = mock.Mock()
        patches = [
            mock.patch.object(schedule_manager.time, "time", self.clock),
            mock.patch.object(schedule_manager, "DatabaseManagerMessages", db),
            mock.patch.object(schedule_manager.command_message_send, "SendMessageCommand", self.send),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_for(self, m, seconds, step=15):
        """Runs the scheduler check every step seconds; the times it sent."""
        sent = []
        end = self.clock.now + seconds
        while self.clock.now <= end:
            before = self.send.call_count
            m.check_for_queued_messages()
            if self.send.call_count > before:
                sent.append(self.clock.now)
            self.clock.now += step
        return sent

    def test_free_channel_sends_at_once(self):
        m, _ = make_manager()
        m.check_for_queued_messages()
        self.send.assert_called_once()
        self.send.return_value.transmit.assert_called_once()
        m.log.warning.assert_not_called()

    def test_non_freedata_signal_holds_the_queue_for_a_minute(self):
        m, _ = make_manager(busy=True)
        start = self.clock.now
        sent = self.run_for(m, 120)
        self.assertTrue(sent, "a queued message must go out on a channel that never clears")
        self.assertGreaterEqual(sent[0] - start, ScheduleManager.QUEUE_BUSY_MAX_WAIT)
        self.assertLess(sent[0] - start, ScheduleManager.QUEUE_BUSY_MAX_WAIT + 15)
        m.log.warning.assert_called()

    def test_freedata_traffic_holds_the_queue(self):
        m, _ = make_manager(busy=True, codec2=True)
        self.assertEqual(self.run_for(m, 600), [])

    def test_a_free_moment_starts_the_wait_again(self):
        m, state = make_manager(busy=True)
        self.run_for(m, 45)
        state.channel_busy_event.set()  # clears for a moment: sends
        m.check_for_queued_messages()
        self.assertEqual(self.send.call_count, 1)
        state.channel_busy_event.clear()
        self.clock.now += 15
        start = self.clock.now
        sent = self.run_for(m, 45)
        self.assertEqual(sent, [], "the busy wait starts again after the channel was free")
        self.assertIsNotNone(m.queue_busy_since)
        self.assertGreaterEqual(m.queue_busy_since, start)

    def test_codec2_traffic_resets_the_wait(self):
        m, state = make_manager(busy=True)
        self.run_for(m, 45)
        state.codec2 = True
        self.run_for(m, 15)
        state.codec2 = False
        self.assertEqual(self.run_for(m, 45), [])

    def test_empty_queue_logs_nothing(self):
        self.queued = None
        m, _ = make_manager(busy=True)
        self.assertEqual(self.run_for(m, 300), [])
        m.log.warning.assert_not_called()

    def test_arq_session_holds_the_queue(self):
        m, state = make_manager()
        state.getARQ = lambda: True
        self.assertEqual(self.run_for(m, 300), [])


if __name__ == "__main__":
    unittest.main()
