"""P2P retry loops stop once the connection is closed or a newer frame takes over.

On the air on 2026-11-12 (on-air test P4) a heartbeat sent just before
DISCONNECT went on retrying every ~32 s in FSK after the connection had closed,
through a modem stop and start, until the server was restarted; the socket
clients were also told DISCONNECTED two and four times."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.p2p_connection import P2PConnection, States


def make_connection(state=States.CONNECTED):
    """A P2P connection with only what the retry loop needs, without a modem."""
    connection = P2PConnection.__new__(P2PConnection)
    connection.fsk = False
    connection.state = state
    connection.session_id = 7
    connection.is_ISS = True
    connection.is_Master = True
    connection.transmission_in_progress = False
    connection.twr_generation = 0
    connection.running_arq_session = None
    connection.ENTIRE_CONNECTION_TIMEOUT = 180
    connection.logger = mock.Mock()
    connection.ctx = mock.Mock()
    connection.ctx.config_manager = types.SimpleNamespace(config={"MODEM": {"enable_fsk": True}})
    connection.session_failed = mock.Mock()
    connection.event_frame_received = mock.Mock()
    return connection


def unanswered(on_wait=None):
    """An event whose waits time out; on_wait(n) runs at the n-th wait."""
    event = mock.Mock()
    calls = []

    def wait(_timeout):
        calls.append(_timeout)
        if on_wait:
            on_wait(len(calls))
        return False

    event.wait.side_effect = wait
    return event


def sent(connection):
    return connection.ctx.rf_modem.transmit.call_count


class TestRetriesStop(unittest.TestCase):
    def run_twr(self, connection, event, retries=10, generation=None):
        args = [b"heartbeat", 10, retries, FREEDV_MODE.signalling]
        if generation is not None:
            args.append(generation)
        with testenv.patch_threading_event("freedata_server.p2p_connection", event):
            connection.transmit_wait_and_retry(*args)

    def test_heartbeat_stops_when_the_connection_closes(self):
        connection = make_connection()

        def close(n):
            if n == 1:
                connection.state = States.DISCONNECTED

        self.run_twr(connection, unanswered(close))
        self.assertEqual(sent(connection), 1, "no heartbeat may go out after DISCONNECTED")
        self.assertFalse(connection.transmission_in_progress)
        connection.session_failed.assert_not_called()

    def test_stops_on_every_closed_state(self):
        for state in (States.DISCONNECTED, States.ABORTED, States.FAILED):
            connection = make_connection(state)
            self.run_twr(connection, unanswered())
            self.assertEqual(sent(connection), 0, state.name)

    def test_a_newer_frame_takes_over(self):
        # the DISCONNECT is launched while a heartbeat waits for its answer
        connection = make_connection()
        connection.twr_generation = 1

        def disconnect_launched(n):
            if n == 1:
                connection.state = States.DISCONNECTING
                connection.twr_generation += 1

        self.run_twr(connection, unanswered(disconnect_launched), generation=1)
        self.assertEqual(sent(connection), 1)
        # the newer loop owns the flag now
        self.assertTrue(connection.transmission_in_progress)
        connection.session_failed.assert_not_called()

    def test_unanswered_frame_still_retries_while_open(self):
        connection = make_connection()
        connection.ctx.config_manager.config["MODEM"]["enable_fsk"] = False
        self.run_twr(connection, unanswered(), retries=3)
        self.assertEqual(sent(connection), 3)
        connection.session_failed.assert_called_once()

    def test_disconnect_retries_still_end_the_connection(self):
        connection = make_connection(States.DISCONNECTING)
        connection.ctx.config_manager.config["MODEM"]["enable_fsk"] = False
        self.run_twr(connection, unanswered(), retries=3)
        self.assertEqual(sent(connection), 3)
        self.assertIs(connection.state, States.DISCONNECTED)

    def test_launch_twr_passes_a_new_generation(self):
        connection = make_connection()
        with mock.patch("freedata_server.p2p_connection.threading.Thread") as thread:
            connection.launch_twr(b"a", 5, 3, FREEDV_MODE.signalling)
            connection.launch_twr(b"b", 5, 3, FREEDV_MODE.signalling)
        generations = [c.kwargs["args"][-1] for c in thread.call_args_list]
        self.assertEqual(generations, [1, 2])
        self.assertEqual(connection.twr_generation, 2)


class TestDisconnectedReportedOnce(unittest.TestCase):
    def setUp(self):
        self.connection = make_connection()
        self.connection.frame_factory = mock.Mock()
        self.connection.launch_twr_irs = mock.Mock()
        self.handler = self.connection.ctx.socket_interface_manager.command_server.command_handler

    def test_repeated_disconnect(self):
        self.connection.received_disconnect({})
        self.connection.received_disconnect({})
        self.handler.socket_respond_disconnected.assert_called_once()
        # every DISCONNECT is still answered: the other side may have missed the ACK
        self.assertEqual(self.connection.launch_twr_irs.call_count, 2)

    def test_repeated_disconnect_ack(self):
        self.connection.state = States.DISCONNECTING
        self.connection.received_disconnect_ack({})
        self.connection.received_disconnect_ack({})
        self.handler.socket_respond_disconnected.assert_called_once()


if __name__ == "__main__":
    unittest.main()
