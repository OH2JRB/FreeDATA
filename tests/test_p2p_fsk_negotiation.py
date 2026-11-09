"""P2P connections say whether they have the FSK modes on, like ARQ sessions
(test_fsk_negotiation): the CAPS and FSK bits of the P2P flag byte, in the
CONNECT_ACK and in every heartbeat and heartbeat ACK. A connection falls back
to FSK only if the other station has the FSK modes on or does not say (an
older station)."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.p2p_connection import P2PConnection, States


def ctx(fsk=True):
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(
            config={"MODEM": {"enable_fsk": fsk}, "STATION": {"mycall": "AA1AAA", "myssid": 1, "mygrid": "KP20jd"}}
        )
    )


def factory():
    return DataFrameFactory(ctx())


def flags(frame):
    return factory().deconstruct(frame)["flag"]


class TestFrames(unittest.TestCase):
    def test_connect_ack_carries_the_setting(self):
        f = factory()
        for fsk in (True, False):
            got = flags(f.build_p2p_connection_connect_ack("BB1BBB-2", "AA1AAA-1", 7, fsk=fsk))
            self.assertTrue(got["CAPS"])
            self.assertEqual(got["FSK"], fsk)

    def test_connect_ack_from_an_older_station(self):
        # its frame ends with the session id; the byte after it is padding
        got = flags(factory().build_p2p_connection_connect_ack("BB1BBB-2", "AA1AAA-1", 7))
        self.assertFalse(got["CAPS"])

    def test_heartbeats_carry_it_beside_their_flags(self):
        f = factory()
        hb = flags(f.build_p2p_connection_heartbeat(7, flag_has_data=True, fsk=False))
        self.assertEqual((hb["HAS_DATA"], hb["CAPS"], hb["FSK"]), (True, True, False))
        ack = flags(f.build_p2p_connection_heartbeat_ack(7, flag_announce_arq=True, fsk=True))
        self.assertEqual((ack["ANNOUNCE_ARQ"], ack["CAPS"], ack["FSK"]), (True, True, True))

    def test_payload_flags_do_not_say(self):
        frame = factory().build_p2p_connection_payload(
            FREEDV_MODE.data_ofdm_500,
            7,
            1,
            b"x",
            flag_has_data=True,
        )
        self.assertFalse(flags(frame)["CAPS"])


def connection(fsk=True):
    c = P2PConnection.__new__(P2PConnection)
    c.fsk = False
    c.state = States.CONNECTED
    c.session_id = 7
    c.is_ISS = True
    c.is_Master = False
    c.logger = mock.Mock()
    c.ctx = ctx(fsk)
    return c


class TestFallback(unittest.TestCase):
    def test_no_fallback_when_the_other_station_has_fsk_off(self):
        c = connection()
        c.note_peer_fsk({"flag": {"CAPS": True, "FSK": False}})
        self.assertIs(c.peer_fsk, False)
        c.fall_back_to_fsk()
        self.assertFalse(c.fsk)

    def test_fallback_when_it_has_fsk_on(self):
        c = connection()
        c.note_peer_fsk({"flag": {"CAPS": True, "FSK": True}})
        c.fall_back_to_fsk()
        self.assertTrue(c.fsk)

    def test_older_station_as_before(self):
        c = connection()
        c.note_peer_fsk({"flag": {"HAS_DATA": True, "CAPS": False, "FSK": False}})
        self.assertIsNone(c.peer_fsk)
        c.fall_back_to_fsk()
        self.assertTrue(c.fsk)

    def test_frames_without_flags_change_nothing(self):
        c = connection()
        c.peer_fsk = False
        c.note_peer_fsk({"frame_type": "P2P_CONNECTION_DISCONNECT"})
        self.assertIs(c.peer_fsk, False)

    def test_fsk_off_here_still_no_fallback(self):
        c = connection(fsk=False)
        c.note_peer_fsk({"flag": {"CAPS": True, "FSK": True}})
        c.fall_back_to_fsk()
        self.assertFalse(c.fsk)


if __name__ == "__main__":
    unittest.main()
