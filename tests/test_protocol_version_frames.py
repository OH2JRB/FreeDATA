"""CQ, QRV, BEACON and PING carry the sender's ARQ protocol version in what
used to be padding, and received frames record it on the activity the GUI
builds its heard stations list from, so it can mark protocol 1 stations
(which cannot exchange ARQ sessions with this one)."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import constants, data_frame_factory
from freedata_server.frame_handler import FrameHandler


def make_ctx():
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"STATION": {"mycall": "AA1AAA", "myssid": 1, "mygrid": "JN48ea"}}),
        state_manager=mock.Mock(),
    )


class TestProtocolVersionInFrames(unittest.TestCase):
    def setUp(self):
        self.factory = data_frame_factory.DataFrameFactory(make_ctx())

    def frames(self):
        return {
            "CQ": self.factory.build_cq(),
            "QRV": self.factory.build_qrv(5),
            "BEACON": self.factory.build_beacon(True),
            "PING": self.factory.build_ping("BB2BBB-2"),
        }

    def test_frames_carry_the_protocol_version(self):
        for name, frame in self.frames().items():
            with self.subTest(frame=name):
                self.assertEqual(len(frame), self.factory.LENGTH_SIG0_FRAME)
                decoded = self.factory.deconstruct(bytes(frame) + bytes(2))
                self.assertEqual(decoded["protocol_version"], constants.ARQ_PROTOCOL_VERSION)
                self.assertEqual(decoded["origin"], "AA1AAA-1")

    def field_position(self, frame):
        """The byte the protocol_version field takes in this frame."""
        template = self.factory.template_list[frame[0]]
        before = [n for k, n in template.items() if k not in ("frame_length", "protocol_version")]
        return 1 + sum(before)  # after the frame type and the other fields

    def test_frame_from_an_older_version_reads_0(self):
        # an older station sends padding where the field now is
        for name, frame in self.frames().items():
            with self.subTest(frame=name):
                old = bytearray(frame)
                old[self.field_position(frame)] = 0
                decoded = self.factory.deconstruct(bytes(old) + bytes(2))
                self.assertEqual(decoded["protocol_version"], 0)

    def test_old_template_still_decodes_new_frames(self):
        # an older station's template lacks the field: the byte is ignored
        old_factory = data_frame_factory.DataFrameFactory(make_ctx())
        for template in old_factory.template_list.values():
            template.pop("protocol_version", None)
        frame = self.factory.build_beacon(True)
        decoded = old_factory.deconstruct(bytes(frame) + bytes(2))
        self.assertEqual(decoded["origin"], "AA1AAA-1")
        self.assertTrue(decoded["flag"]["AWAY_FROM_KEY"])
        self.assertNotIn("protocol_version", decoded)


class TestSenderProtocolVersion(unittest.TestCase):
    def handler_for(self, frame):
        handler = FrameHandler(make_ctx(), "test")
        handler.details.update({"frame": frame, "snr": 5, "frequency_offset": 0})
        return handler

    def test_versions_by_frame(self):
        cases = [
            ({"frame_type": "BEACON", "protocol_version": 2}, 2),
            ({"frame_type": "CQ", "protocol_version": 0}, 1),  # older station: padding
            ({"frame_type": "QRV", "protocol_version": 0}, 1),
            ({"frame_type": "PING", "protocol_version": 2}, 2),
            ({"frame_type": "ARQ_SESSION_OPEN", "protocol_version": 1}, 1),
            ({"frame_type": "ARQ_SESSION_OPEN_ACK", "version": 2}, 2),
            ({"frame_type": "P2P_CONNECTION_CONNECT"}, None),
            ({"frame_type": "NORM_DATA"}, None),
        ]
        for frame, expected in cases:
            with self.subTest(frame=frame):
                self.assertEqual(self.handler_for(frame).sender_protocol_version(), expected)

    def test_activity_records_the_version(self):
        handler = self.handler_for({"frame_type": "BEACON", "origin": "BB2BBB-2", "protocol_version": 0})
        handler.add_to_activity_list()
        activity = handler.ctx.state_manager.add_activity.call_args.args[0]
        self.assertEqual(activity["protocol_version"], 1)

    def test_activity_without_version(self):
        handler = self.handler_for({"frame_type": "NORM_DATA", "origin": "BB2BBB-2"})
        handler.add_to_activity_list()
        activity = handler.ctx.state_manager.add_activity.call_args.args[0]
        self.assertNotIn("protocol_version", activity)


if __name__ == "__main__":
    unittest.main()
