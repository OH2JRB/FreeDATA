"""P2P connections fall back to the HF FSK mode data_fsk4_50 like ARQ sessions:
after two unanswered attempts of a frame, and when a frame arrives in FSK (see
freedata_server/docs/arq-sessions.md, section 4.1)."""

import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.p2p_connection import P2PConnection, States

FSK = FREEDV_MODE.data_fsk4_50
FSK_ROUND_TRIP = 3 * 5.44


def make_connection():
    """A P2P connection with only what the retry loop needs, without a modem."""
    connection = P2PConnection.__new__(P2PConnection)
    connection.fsk = False
    connection.state = States.CONNECTING
    connection.session_id = 1
    connection.is_ISS = True
    connection.is_Master = False
    connection.transmission_in_progress = False
    connection.logger = mock.Mock()
    connection.ctx = mock.Mock()
    connection.session_failed = mock.Mock()
    connection.event_frame_received = mock.Mock()
    return connection


def answer_after(failures):
    event = mock.Mock()
    event.wait.side_effect = [False] * failures + [True]
    return event


class TestP2PFskFallback(unittest.TestCase):
    def run_twr(self, connection, failures, frame=b"x" * 14, mode=FREEDV_MODE.signalling, retries=3):
        event = answer_after(failures)
        with mock.patch("freedata_server.p2p_connection.threading.Event", return_value=event):
            connection.transmit_wait_and_retry(frame, 5, retries, mode)
        modes = [c.args[0] for c in connection.ctx.rf_modem.transmit.call_args_list]
        waits = [c.args[0] for c in event.wait.call_args_list]
        return modes, waits

    def test_unanswered_frames_go_in_fsk(self):
        connection = make_connection()
        modes, waits = self.run_twr(connection, 2)
        self.assertEqual(modes, [FREEDV_MODE.signalling, FREEDV_MODE.signalling, FSK])
        self.assertEqual(waits[:2], [5, 5])
        self.assertAlmostEqual(waits[2], 5 + FSK_ROUND_TRIP)
        self.assertTrue(connection.fsk)
        connection.session_failed.assert_not_called()

    def test_fsk_gets_its_own_attempts(self):
        # 3 retries: 2 in OFDM, then at least RETRIES_FSK in FSK
        connection = make_connection()
        modes, _ = self.run_twr(connection, 4)
        self.assertEqual(modes.count(FSK), 3)

    def test_too_large_for_fsk_stays(self):
        connection = make_connection()
        modes, _ = self.run_twr(connection, 3, frame=b"x" * 50, mode=FREEDV_MODE.datac4)
        self.assertNotIn(FSK, modes)
        self.assertFalse(connection.fsk)
        connection.session_failed.assert_called_once()

    def test_follows_a_frame_in_fsk(self):
        connection = make_connection()
        connection.STATE_TRANSITION = {}
        connection.on_frame_received({
            "frame_type": "P2P_CONNECTION_HEARTBEAT",
            "frame_type_int": 0,
            "mode_name": "DATA_FSK4_50",
        })
        self.assertTrue(connection.fsk)
        # answers, sent as signalling_ack or signalling, now go in FSK
        connection.transmit_frame(b"ack", FREEDV_MODE.signalling_ack)
        self.assertIs(connection.ctx.rf_modem.transmit.call_args.args[0], FSK)


if __name__ == "__main__":
    unittest.main()
