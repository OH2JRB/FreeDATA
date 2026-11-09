"""Test frames go out in datac4, which every station decodes, and a station
that receives one handles it: before, they went out in data_ofdm_500, decoded
only inside an ARQ session, and a received one had no template or handler, so
the other station never saw it (on the air 2026-11-09)."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server import command_test
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.frame_dispatcher import DISPATCHER
from freedata_server.modem_frametypes import FRAME_TYPE


def factory():
    return DataFrameFactory(ctx())


def ctx(vhf=False):
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(
            config={
                "EXP": {"enable_vhf": vhf},
                "STATION": {"mycall": "BB1BBB", "myssid": 2, "mygrid": "KP20jd", "enable_callsign_blacklist": False},
            }
        ),
        state_manager=mock.Mock(),
        event_manager=mock.Mock(),
    )


class TestSend(unittest.TestCase):
    def test_sent_in_datac4(self):
        command = command_test.TestCommand.__new__(command_test.TestCommand)
        command.ctx = ctx()
        self.assertEqual(command.get_tx_mode(), codec2.FREEDV_MODE.datac4)
        command.ctx = ctx(vhf=True)
        self.assertEqual(command.get_tx_mode(), codec2.FREEDV_MODE.data_vhf_1)


class TestReceive(unittest.TestCase):
    def test_received_frame_is_read_and_handled(self):
        frame = factory().build_test("datac4")
        self.assertEqual(len(frame), codec2.get_bytes_per_frame(codec2.FREEDV_MODE.datac4.value) - 2)
        deconstructed = factory().deconstruct(frame, mode_name="DATAC4")
        self.assertEqual(deconstructed["frame_type"], "TEST_FRAME")

        entry = DISPATCHER.FRAME_HANDLER[FRAME_TYPE.TEST_FRAME.value]
        c = ctx()
        handler = entry["class"](c, entry["name"])
        self.assertTrue(handler.handle(deconstructed, 12, 0, None, len(frame) + 2))
        event = c.event_manager.broadcast.call_args.args[0]
        self.assertEqual((event["received"], event["snr"]), ("TEST_FRAME", "12"))
        c.state_manager.add_activity.assert_called_once()


if __name__ == "__main__":
    unittest.main()
