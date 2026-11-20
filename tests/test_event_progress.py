"""EventManager.send_arq_session_progress drops a progress event identical to the
last one of the same session.

The station receiving a transfer reported each burst twice with the same state
and byte count (when it stores the burst's last frame, arq_session_irs.py, and
when it sends the burst ACK), and a burst sent again or a repeated ACK brings
no new bytes. The GUI showed each as another toast.
"""

import queue
import unittest
from types import SimpleNamespace

from freedata_server.event_manager import EventManager


class TestProgressEvents(unittest.TestCase):
    def setUp(self):
        self.q = queue.Queue()
        self.events = EventManager(SimpleNamespace(TESTMODE=False), [self.q])

    def sent(self):
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def progress(self, outbound=False, session_id=7, received=500, state="BURST_REPLY_SENT", level=4, stats=None):
        self.events.send_arq_session_progress(
            outbound, session_id, "AA1AAA-1", received, 1500, state, level, statistics=stats, mode="data_ofdm_2438"
        )

    def test_identical_progress_is_sent_once(self):
        self.progress()
        self.progress()
        self.assertEqual(len(self.sent()), 1)

    def test_statistics_do_not_count(self):
        # the times in the statistics change with every call
        self.progress(stats={"duration": 1.0})
        self.progress(stats={"duration": 1.4})
        self.assertEqual(len(self.sent()), 1)

    def test_new_bytes_state_or_level_are_sent(self):
        self.progress(received=500, state="INFO_ACK_SENT")
        self.progress(received=500, state="BURST_REPLY_SENT")  # the burst ACK after the last frame
        self.progress(received=1000)
        self.progress(received=1000, level=3)
        events = [e["arq-transfer-inbound"] for e in self.sent()]
        self.assertEqual(
            [(e["received_bytes"], e["state"], e["speed_level"]) for e in events],
            [
                (500, "INFO_ACK_SENT", 4),
                (500, "BURST_REPLY_SENT", 4),
                (1000, "BURST_REPLY_SENT", 4),
                (1000, "BURST_REPLY_SENT", 3),
            ],
        )

    def test_back_to_an_earlier_value_is_sent(self):
        # only the last event counts, not all earlier ones
        self.progress(received=500)
        self.progress(received=1000)
        self.progress(received=500)
        self.assertEqual(len(self.sent()), 3)

    def test_sessions_and_directions_are_separate(self):
        self.progress(session_id=7)
        self.progress(session_id=8)
        self.progress(session_id=7, outbound=True)
        self.assertEqual(len(self.sent()), 3)

    def test_new_and_finished_session_forget_the_last_progress(self):
        # P2P connections reuse their session id for every ARQ session
        self.progress()
        self.events.send_arq_session_finished(False, 7, "AA1AAA-1", True, "ENDED")
        self.progress()
        self.events.send_arq_session_new(False, 7, "AA1AAA-1", 1500, "NEW")
        self.progress()
        kinds = [
            e["arq-transfer-inbound"].get("received_bytes", e["arq-transfer-inbound"]["state"]) for e in self.sent()
        ]
        self.assertEqual(kinds, [500, "ENDED", 500, "NEW", 500])


if __name__ == "__main__":
    unittest.main()
