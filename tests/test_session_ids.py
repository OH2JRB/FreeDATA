"""ARQ sessions and P2P connections never get session id 35, and a P2P
connection's worker thread stops once the connection is disconnected.

An ARQ burst ACK in signalling_ack has no type byte: its first byte is the
session id, and a frame starting with 35 (P2P_CONNECTION_PAYLOAD_ACK) is read
as a P2P payload ACK, so a session with id 35 never got its burst ACKs
(freedata_server/docs/arq-sessions.md, section 2.2)."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import data_frame_factory, p2p_connection
from freedata_server.arq_session import ARQSession
from freedata_server.arq_session_iss import ARQSessionISS
from freedata_server.modem_frametypes import FRAME_TYPE
from freedata_server.p2p_connection import P2PConnection, States

RESERVED = FRAME_TYPE.P2P_CONNECTION_PAYLOAD_ACK.value


def make_ctx():
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(
            config={"STATION": {"mycall": "AA1AAA", "myssid": 1, "mygrid": "JN48ea"}, "MODEM": {"enable_fsk": True}}
        ),
        state_manager=mock.MagicMock(arq_iss_sessions={}, p2p_connection_sessions={}),
        socket_interface_manager=mock.MagicMock(),
        rf_modem=None,
    )


def make_iss(data_crc):
    iss = ARQSessionISS.__new__(ARQSessionISS)
    iss.ctx = make_ctx()
    iss.data_crc = data_crc
    iss.log = mock.Mock()
    return iss


class TestReservedSessionId(unittest.TestCase):
    def test_reserved_id_is_the_p2p_payload_ack_type(self):
        self.assertEqual(ARQSession.RESERVED_SESSION_IDS, (RESERVED,))

    def test_burst_ack_for_id_35_is_misread(self):
        # why the id is reserved: deconstruct reads this burst ACK as another frame
        factory = data_frame_factory.DataFrameFactory(make_ctx())
        ack = factory.build_arq_burst_ack(RESERVED, 1)
        frame = factory.deconstruct(ack, mode_name="SIGNALLING_ACK")
        self.assertEqual(frame["frame_type_int"], FRAME_TYPE.P2P_CONNECTION_PAYLOAD_ACK.value)
        ack = factory.build_arq_burst_ack(RESERVED + 1, 1)
        frame = factory.deconstruct(ack, mode_name="SIGNALLING_ACK")
        self.assertEqual(frame["frame_type_int"], FRAME_TYPE.ARQ_BURST_ACK.value)

    def test_iss_id_from_crc_skips_35(self):
        # the id comes from the CRC's low byte, here 35
        iss = make_iss(bytes([RESERVED, 0, 0, 0]))
        with mock.patch("freedata_server.arq_session_iss.random.randint", side_effect=[RESERVED, 9]):
            self.assertEqual(iss.generate_id(), 9)

    def test_iss_id_from_crc_kept_otherwise(self):
        iss = make_iss(bytes([36, 0, 0, 0]))
        self.assertEqual(iss.generate_id(), 36)

    def test_iss_ids_exhausted(self):
        iss = make_iss(bytes([RESERVED, 0, 0, 0]))
        other = types.SimpleNamespace(data_crc=b"other", state=None)
        iss.ctx.state_manager.arq_iss_sessions = {i: other for i in range(1, 256) if i != RESERVED}
        self.assertIs(iss.generate_id(), False)

    def test_p2p_id_skips_35(self):
        connection = P2PConnection.__new__(P2PConnection)
        connection.ctx = make_ctx()
        with mock.patch.object(p2p_connection.random, "randint", side_effect=[RESERVED, RESERVED, 12]):
            self.assertEqual(connection.generate_id(), 12)


class TestP2PWorkerStops(unittest.TestCase):
    def test_worker_stops_when_disconnected(self):
        connection = P2PConnection(make_ctx(), "AA1AAA-1", "BB2BBB-2")
        self.addCleanup(setattr, connection, "state", States.DISCONNECTED)
        self.assertTrue(connection.worker_thread.is_alive())
        connection.abort_connection()  # dirty disconnect: DISCONNECTED at once
        connection.worker_thread.join(2)
        self.assertFalse(connection.worker_thread.is_alive())

    def test_worker_keeps_running_while_connected(self):
        connection = P2PConnection(make_ctx(), "AA1AAA-1", "BB2BBB-2")
        self.addCleanup(setattr, connection, "state", States.DISCONNECTED)
        connection.state = States.CONNECTED
        threading.Event().wait(0.5)
        self.assertTrue(connection.worker_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
