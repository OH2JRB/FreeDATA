"""A session's retry thread when the modem is gone (stopped or restarting, as
after a settings change): it called None.transmit and died with an
AttributeError ("PytestUnhandledThreadExceptionWarning ... transmit_wait_and_retry"
in about one full test run in three). The session now ends through its usual
failure path instead."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session import ARQSession
from freedata_server.arq_session_irs import ARQSessionIRS
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.p2p_connection import P2PConnection, States


def ctx_without_modem():
    return types.SimpleNamespace(
        rf_modem=None, config_manager=types.SimpleNamespace(config={"MODEM": {"enable_fsk": True}})
    )


class TestArq(unittest.TestCase):
    def test_transmit_frame_reports_it(self):
        s = ARQSession.__new__(ARQSession)
        s.ctx = ctx_without_modem()
        s.log = mock.Mock()
        self.assertIs(s.transmit_frame(b"x", FREEDV_MODE.signalling), False)
        s.log.assert_called_once()

    def test_iss_retry_thread_ends_the_session(self):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.ctx = ctx_without_modem()
        iss.state = ISS_State.INFO_SENT
        iss.fsk = False
        iss.probing = False
        iss.speed_level = 1
        iss.log = mock.Mock()
        iss.transmission_failed = mock.Mock()
        event = mock.Mock()
        with testenv.patch_threading_event("freedata_server.arq_session_iss", event):
            iss.transmit_wait_and_retry(b"frame", 1.0, ARQSessionISS.RETRIES_DATA, "auto", isARQBurst=True)
        iss.transmission_failed.assert_called_once()
        event.wait.assert_not_called()

    def test_irs_answer_ends_the_session(self):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.ctx = ctx_without_modem()
        irs.log = mock.Mock()
        irs.event_frame_received = threading.Event()
        irs.transmission_failed = mock.Mock()
        irs.transmit_and_wait(b"ack", 0.1, FREEDV_MODE.signalling_ack)
        irs.transmission_failed.assert_called_once()


class TestP2p(unittest.TestCase):
    def connection(self):
        c = P2PConnection.__new__(P2PConnection)
        c.ctx = ctx_without_modem()
        c.fsk = False
        c.state = States.CONNECTING
        c.session_id = 1
        c.is_ISS = True
        c.is_Master = False
        c.transmission_in_progress = False
        c.logger = mock.Mock()
        c.session_failed = mock.Mock()
        c.event_frame_received = threading.Event()
        return c

    def test_retry_thread_ends_the_connection(self):
        c = self.connection()
        c.transmit_wait_and_retry(b"x" * 14, 0.1, 3, FREEDV_MODE.signalling)
        c.session_failed.assert_called_once()
        self.assertFalse(c.transmission_in_progress)

    def test_irs_answer_ends_the_connection(self):
        c = self.connection()
        c.transmit_and_wait_irs(b"x" * 14, 0.1, FREEDV_MODE.signalling)
        c.session_failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
