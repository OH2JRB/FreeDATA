"""Data from the TCP data port goes to the P2P session it is for: the active
session of the command socket, or the only session there is
(freedata_server/docs/arq-sessions.md, socket interface)."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.socket_interface import DataSocket


def make_handler(sessions, active=None):
    handler = DataSocket.__new__(DataSocket)
    handler.ctx = types.SimpleNamespace(state_manager=types.SimpleNamespace(p2p_connection_sessions=sessions))
    command_handler = types.SimpleNamespace(session=active)
    handler.server = types.SimpleNamespace(
        socket_interface_manager=types.SimpleNamespace(
            command_server=types.SimpleNamespace(command_handler=command_handler)
        )
    )
    handler.log = mock.Mock()
    return handler


def session(session_id):
    return types.SimpleNamespace(session_id=session_id)


class TestDataPortTarget(unittest.TestCase):
    def test_active_session(self):
        a, b = session(1), session(2)
        self.assertEqual(make_handler({1: a, 2: b}, active=b).get_target_sessions(), [b])

    def test_only_session(self):
        a = session(1)
        self.assertEqual(make_handler({1: a}).get_target_sessions(), [a])

    def test_several_sessions_and_none_active(self):
        handler = make_handler({1: session(1), 2: session(2)})
        self.assertEqual(handler.get_target_sessions(), [])
        handler.log.assert_called_once()

    def test_no_session(self):
        self.assertEqual(make_handler({}).get_target_sessions(), [])

    def test_active_session_no_longer_registered(self):
        a = session(1)
        self.assertEqual(make_handler({1: a}, active=session(5)).get_target_sessions(), [a])


if __name__ == "__main__":
    unittest.main()
