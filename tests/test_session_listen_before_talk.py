"""Listen before talk around the start of an ARQ session.

- state_manager.is_in_session: until the IRS answers ARQ_SESSION_OPEN there is
  no partner traffic, so what is heard counts as traffic on the channel again.
- ARQSessionISS: a retry of ARQ_SESSION_OPEN goes through the channel gate;
  it used to wait on the SignalDetector and then go out anyway.
- P2PConnection.transmit_arq: the session inside a P2P connection starts after
  the channel gate; a busy channel puts the data back at the front of the queue.
"""

import threading
import unittest
import unittest.mock
from types import SimpleNamespace

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import arq_session_iss, p2p_connection
from freedata_server.arq_session_iss import ISS_State
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.state_manager import StateManager


def session(state_name):
    return SimpleNamespace(state=SimpleNamespace(name=state_name))


class TestInSession(unittest.TestCase):
    def state(self, iss=(), irs=(), arq=True):
        state = StateManager.__new__(StateManager)
        state.arq_iss_sessions = {i: session(name) for i, name in enumerate(iss)}
        state.arq_irs_sessions = {i: session(name) for i, name in enumerate(irs)}
        state.p2p_connection_sessions = {}
        state.getARQ = lambda: arq
        return state

    def test_unanswered_open_is_not_a_session(self):
        for name in ("NEW", "OPEN_SENT"):
            with self.subTest(name):
                self.assertFalse(self.state(iss=[name]).is_in_session())

    def test_answered_iss_is_a_session(self):
        for name in ("INFO_SENT", "BURST_SENT"):
            with self.subTest(name):
                self.assertTrue(self.state(iss=[name]).is_in_session())

    def test_running_irs_is_a_session(self):
        self.assertTrue(self.state(irs=["OPEN_ACK_SENT"]).is_in_session())
        # an IRS that runs wins over an ISS left waiting
        self.assertTrue(self.state(iss=["OPEN_SENT"], irs=["BURST_REPLY_SENT"]).is_in_session())

    def test_finished_sessions_left_in_the_registry(self):
        # sessions stay registered for a while after they end
        self.assertFalse(self.state(iss=["OPEN_SENT", "ENDED"], irs=["ENDED"]).is_in_session())
        self.assertTrue(self.state(iss=["ENDED"], irs=[]).is_in_session())

    def test_no_arq(self):
        self.assertFalse(self.state(iss=["INFO_SENT"], arq=False).is_in_session())


def bare_iss(state=ISS_State.OPEN_SENT):
    iss = arq_session_iss.ARQSessionISS.__new__(arq_session_iss.ARQSessionISS)
    iss.ctx = unittest.mock.Mock()
    iss.state = state
    iss.log = unittest.mock.Mock()
    iss.skip_next_clear_channel_wait = False
    return iss


class TestOpenRetryClear(unittest.TestCase):
    def gate(self, result, iss, change_to=None):
        def wait(*args, **kwargs):
            if change_to is not None:
                iss.state = change_to
            return result

        return unittest.mock.patch.object(arq_session_iss.channel_gate, "wait_for_clear_channel", side_effect=wait)

    def test_clear(self):
        iss = bare_iss()
        with self.gate(True, iss):
            self.assertIs(iss.open_retry_clear(lambda: False), True)
        # the gate has just passed: no SignalDetector wait before the retry
        self.assertTrue(iss.skip_next_clear_channel_wait)

    def test_freedata_traffic_fails_the_open(self):
        iss = bare_iss()
        with self.gate(False, iss):
            self.assertIs(iss.open_retry_clear(lambda: False), False)
        self.assertFalse(iss.skip_next_clear_channel_wait)

    def test_answered_or_aborted_while_waiting(self):
        for new_state in (ISS_State.INFO_SENT, ISS_State.ABORTING):
            for result in (True, False):
                with self.subTest(state=new_state, gate=result):
                    iss = bare_iss()
                    with self.gate(result, iss, change_to=new_state):
                        self.assertIsNone(iss.open_retry_clear(lambda: False))

    def test_superseded_while_waiting(self):
        iss = bare_iss()
        with self.gate(True, iss):
            self.assertIsNone(iss.open_retry_clear(lambda: True))


class TestRetryLoop(unittest.TestCase):
    """transmit_wait_and_retry gates the retries of ARQ_SESSION_OPEN, not the first."""

    def run_loop(self, clear, state=ISS_State.OPEN_SENT):
        iss = bare_iss(state)
        iss.fsk = True  # no FSK fallback in the way
        iss.probing = False
        iss.twr_generation = None
        iss.get_signalling_mode = unittest.mock.Mock(return_value=FREEDV_MODE.data_fsk4_50)
        iss.signalling_frames = lambda frame: [frame, frame]
        iss.transmit_frame = unittest.mock.Mock(return_value=True)
        iss.get_response_timeout = unittest.mock.Mock(return_value=0)
        iss.wait_for_answer_on_air = unittest.mock.Mock(return_value=False)
        iss.answer_heard = unittest.mock.Mock(return_value=False)
        iss.transmission_failed = unittest.mock.Mock()
        iss.set_state = unittest.mock.Mock(return_value=True)
        iss.open_retry_clear = unittest.mock.Mock(return_value=clear)
        iss.event_frame_received = threading.Event()
        iss.transmit_wait_and_retry(bytearray(b"open"), 0, 3, mode=FREEDV_MODE.signalling)
        return iss

    def test_retries_go_through_the_gate(self):
        iss = self.run_loop(True)
        self.assertEqual(iss.transmit_frame.call_count, 3)
        self.assertEqual(iss.open_retry_clear.call_count, 2)  # not before the first

    def test_busy_channel_ends_the_session(self):
        iss = self.run_loop(False)
        self.assertEqual(iss.transmit_frame.call_count, 1)
        iss.transmission_failed.assert_called_once()

    def test_moved_on_sends_nothing_more(self):
        iss = self.run_loop(None)
        self.assertEqual(iss.transmit_frame.call_count, 1)
        iss.transmission_failed.assert_not_called()

    def test_frames_inside_the_session_are_not_gated(self):
        iss = self.run_loop(True, state=ISS_State.INFO_SENT)
        iss.open_retry_clear.assert_not_called()
        self.assertEqual(iss.transmit_frame.call_count, 3)


class TestP2PArqStart(unittest.TestCase):
    def connection(self):
        conn = p2p_connection.P2PConnection.__new__(p2p_connection.P2PConnection)
        conn.ctx = unittest.mock.MagicMock()
        conn.is_ISS = True
        conn.destination = "AA1AAA-5"
        conn.session_id = 7
        conn.fsk = False
        conn.p2p_data_tx_queue = p2p_connection.Queue()
        conn.arq_data_type_handler = unittest.mock.Mock()
        conn.arq_data_type_handler.prepare.return_value = (b"data", 0)
        conn.set_state = unittest.mock.Mock()
        conn.log = unittest.mock.Mock()
        return conn

    def test_busy_channel_requeues_the_data_first(self):
        conn = self.connection()
        conn.p2p_data_tx_queue.put(b"second")
        with (
            unittest.mock.patch.object(p2p_connection.channel_gate, "wait_for_clear_channel", return_value=False),
            unittest.mock.patch.object(p2p_connection, "ARQSessionISS") as iss_class,
        ):
            self.assertIsNone(conn.transmit_arq(b"first"))
        iss_class.assert_not_called()
        self.assertEqual(conn.p2p_data_tx_queue.get_nowait(), b"first")
        self.assertEqual(conn.p2p_data_tx_queue.get_nowait(), b"second")
        conn.set_state.assert_called_with(p2p_connection.States.CONNECTED)

    def test_clear_channel_starts_the_session_at_once(self):
        conn = self.connection()
        with (
            unittest.mock.patch.object(
                p2p_connection.channel_gate, "wait_for_clear_channel", return_value=True
            ) as gate,
            unittest.mock.patch.object(p2p_connection, "ARQSessionISS") as iss_class,
        ):
            iss = conn.transmit_arq(b"payload")
        self.assertIs(iss, iss_class.return_value)
        iss.start.assert_called_once()
        self.assertTrue(iss.skip_next_clear_channel_wait)
        self.assertEqual(gate.call_args.kwargs["holdoff_setting"], "cq_reply_holdoff")


if __name__ == "__main__":
    unittest.main()
