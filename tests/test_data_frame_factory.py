import unittest

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.config import CONFIG
from freedata_server import helpers
from freedata_server.modem_frametypes import FRAME_TYPE


# Dummy Context with config
class DummyCtx:
    def __init__(self, config_path):
        self.config_manager = CONFIG(self, config_path)


class TestDataFrameFactory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Setup minimal context and config
        cls.ctx = DummyCtx(testenv.config_path())
        cls.ctx.config_manager.config["STATION"]["mycall"] = "T1CALL"
        cls.ctx.config_manager.config["STATION"]["mygrid"] = "AB12aa"

        cls.factory = DataFrameFactory(cls.ctx)

    def test_build_beacon(self):
        beacon_frame = self.factory.build_beacon()
        beacon_data = self.factory.deconstruct(beacon_frame)
        self.assertEqual(beacon_data["origin"], self.factory.myfullcall.upper())
        self.assertEqual(beacon_data["gridsquare"], self.factory.mygrid.upper())

    def test_build_ping(self):
        dxcall = "DJ2LS-3"
        ping_frame = self.factory.build_ping(dxcall)
        ping_data = self.factory.deconstruct(ping_frame)
        self.assertEqual(ping_data["origin"], self.factory.myfullcall)
        self.assertEqual(ping_data["destination_crc"], helpers.get_crc_24(dxcall).hex())

    def test_build_arq_connect(self):
        dxcall = "DJ2LS-4"
        session_id = 123
        frame = self.factory.build_arq_session_open(dxcall, session_id, 1700, 1)
        frame_data = self.factory.deconstruct(frame)
        self.assertEqual(frame_data["origin"], self.factory.myfullcall)
        self.assertEqual(frame_data["session_id"], session_id)

    def test_build_cq(self):
        frame = self.factory.build_cq()
        frame_data = self.factory.deconstruct(frame)
        self.assertEqual(frame_data["origin"], self.factory.myfullcall)
        self.assertEqual(frame_data["gridsquare"], self.factory.mygrid.upper())

    def test_build_burst_data_frames(self):
        session_id = 123
        offset = 40
        payload = b"Hello World!"
        frame = self.factory.build_arq_burst_frame(FREEDV_MODE.datac3, session_id, offset, payload, 0)
        frame_data = self.factory.deconstruct(frame)
        self.assertEqual(frame_data["session_id"], session_id)
        self.assertEqual(frame_data["offset"], offset)
        extracted = frame_data["data"][: len(payload)]
        self.assertEqual(extracted, payload)
        self.assertEqual(self.factory.decode_burst_info(frame_data["burst_info"]), (1, 1))

        frame = self.factory.build_arq_burst_frame(
            FREEDV_MODE.datac3, session_id, offset, payload, 0, burst_number=3, burst_total=4
        )
        self.assertEqual(self.factory.decode_burst_info(self.factory.deconstruct(frame)["burst_info"]), (3, 4))

        # Check overflow condition
        oversized_payload = payload * 1000
        with self.assertRaises(OverflowError):
            self.factory.build_arq_burst_frame(FREEDV_MODE.datac3, session_id, offset, oversized_payload, 0)

    def test_available_payload(self):
        available = self.factory.get_available_data_payload_for_mode(FRAME_TYPE.ARQ_BURST_FRAME, FREEDV_MODE.datac3)
        # 128 bytes less 2 CRC, 1 type, 1 session id, 1 speed level, 4 offset, 1 burst info, 1 burst id
        self.assertEqual(available, 117)

    def test_arq_session_frames_carry_the_snr(self):
        open_ack = self.factory.build_arq_session_open_ack(1, "AA1AAA-0", 1, 7.5)
        info = self.factory.build_arq_session_info(1, 100, bytes(4), -3.2, 0)
        info_ack = self.factory.build_arq_session_info_ack(1, 0, 4, 1, 1)
        self.assertEqual(self.factory.deconstruct(open_ack)["snr"], 7.5)
        self.assertEqual(self.factory.deconstruct(info)["snr"], -3.2)
        self.assertEqual(self.factory.deconstruct(info_ack)["snr"], 4)
        # before the session has measured one (ARQSession.snr starts as a list)
        self.assertEqual(
            self.factory.deconstruct(self.factory.build_arq_session_info(1, 100, bytes(4), [], 0))["snr"], 0
        )
        # one byte holds -12.7 to 12.7 dB
        self.assertEqual(self.factory.deconstruct(self.factory.build_arq_session_open_ack(1, "A", 1, 30))["snr"], 12.7)


if __name__ == "__main__":
    unittest.main()
