"""Small fixes: flrig only reports connected once flrig answers and polls
frequency and mode once per cycle, codec2's CRC16 has declared ctypes types,
and P2P connection data without a session id reaches no connection."""

import binascii
import threading
import time
import types
import unittest
from unittest import mock
from xmlrpc.server import SimpleXMLRPCServer

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2, flrig
from freedata_server.arq_data_type_handler import ARQDataTypeHandler


class FakeFlrig:
    """XML-RPC server answering the calls flrig.radio makes, counting each."""

    def __init__(self):
        self.calls = {}
        self.server = SimpleXMLRPCServer(("127.0.0.1", 0), logRequests=False, allow_none=True)
        self.port = self.server.server_address[1]
        answers = {
            "main.get_version": "2.0",
            "rig.get_vfo": "14074000",
            "rig.get_mode": "USB",
            "rig.get_power": 50,
            "rig.get_maxpwr": 100,
            "rig.get_ptt": 0,
            "rig.get_swrmeter": 1,
            "rig.get_smeter": 5,
        }
        for name, answer in answers.items():
            self.server.register_function(self.counted(name, answer), name)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def counted(self, name, answer):
        def call():
            self.calls[name] = self.calls.get(name, 0) + 1
            return answer

        return call

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def make_flrig(port):
    ctx = types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"FLRIG": {"ip": "127.0.0.1", "port": port}}),
        state_manager=mock.Mock(),
    )
    radio = flrig.radio(ctx)
    radio.poll_interval = 0.1
    return radio


def wait_for(condition, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class TestFlrig(unittest.TestCase):
    def test_not_connected_without_flrig(self):
        fake = FakeFlrig()
        port = fake.port
        fake.close()
        radio = make_flrig(port)
        self.addCleanup(radio.close_rig)
        self.assertFalse(radio.connect())
        self.assertFalse(radio.get_status())
        radio.ctx.state_manager.set_radio.assert_called_with("radio_status", False)

    def test_connects_and_polls_once_per_cycle(self):
        fake = FakeFlrig()
        self.addCleanup(fake.close)
        radio = make_flrig(fake.port)
        self.addCleanup(radio.close_rig)
        self.assertTrue(wait_for(lambda: fake.calls.get("rig.get_smeter", 0) >= 2))
        self.assertTrue(radio.get_status())
        self.assertEqual(radio.get_parameters()["frequency"], "14074000")
        # the S-meter is read last in a cycle, so after n full cycles the
        # frequency and mode have been read n or n + 1 times
        smeter = fake.calls["rig.get_smeter"]
        self.assertLessEqual(fake.calls["rig.get_vfo"], smeter + 1)
        self.assertLessEqual(fake.calls["rig.get_mode"], smeter + 1)


class TestCodec2Crc16(unittest.TestCase):
    def test_crc16_types_declared(self):
        self.assertEqual(codec2.api.freedv_gen_crc16.restype, codec2.ctypes.c_ushort)
        self.assertIsNotNone(codec2.api.freedv_gen_crc16.argtypes)

    def test_crc16_is_ccitt(self):
        for data in (bytes(range(30)), bytes(510), b"\xff" * 14):
            self.assertEqual(codec2.api.freedv_gen_crc16(data, len(data)), binascii.crc_hqx(data, 0xFFFF))


class TestP2PConnectionSessions(unittest.TestCase):
    def setUp(self):
        self.connection = mock.Mock()
        ctx = mock.Mock()
        ctx.state_manager.p2p_connection_sessions = {7: self.connection}
        self.handler = ARQDataTypeHandler(ctx)

    def test_known_id_returns_its_connection(self):
        self.assertEqual(self.handler.get_p2p_connection_sessions(7), [self.connection])

    def test_unknown_id_returns_none(self):
        self.assertEqual(self.handler.get_p2p_connection_sessions(8), [])

    def test_missing_id_returns_none(self):
        self.assertEqual(self.handler.get_p2p_connection_sessions(None), [])
        self.assertEqual(self.handler.get_p2p_connection_sessions(), [])


if __name__ == "__main__":
    unittest.main()
