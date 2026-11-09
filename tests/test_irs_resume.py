"""An IRS session that holds data resumes only the same data: session ids
repeat, so a new transfer can get the id of a session the IRS still holds
aborted or failed. On the air (2026-11-09) such a new transfer was told 8428
of its bytes were in, which were another message's."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session_irs import ARQSessionIRS, IRS_State


def held_session():
    """An IRS holding 8428 of 20943 bytes of a stopped transfer."""
    irs = ARQSessionIRS.__new__(ARQSessionIRS)
    irs.id = 134
    irs.dxcall = "AA1AAA-1"
    irs.state = IRS_State.RESUME
    irs.fsk = False
    irs.abort = False
    irs.snr = 13
    irs.speed_level = 4
    irs.frames_per_burst = 1
    irs.dx_snr = []
    irs.total_length = 20943
    irs.total_crc = b"\x11\x22\x33\x44"
    irs.received_bytes = 8428
    irs.received_data = bytearray(b"A" * 8428 + bytes(20943 - 8428))
    irs.pending_frames = {9000: b"x"}
    irs.log = mock.Mock()
    irs.calibrate_speed_settings = mock.Mock()
    irs.frame_factory = mock.Mock()
    irs.launch_transmit_and_wait = mock.Mock()
    irs.set_state_unless_aborted = mock.Mock()
    irs.ctx = types.SimpleNamespace(event_manager=mock.Mock())
    return irs


def info(total_length, total_crc):
    return {"total_length": total_length, "total_crc": total_crc, "snr": 12, "type": 0}


class TestResume(unittest.TestCase):
    def offset_sent(self, irs):
        return irs.frame_factory.build_arq_session_info_ack.call_args.args[1]

    def test_same_data_resumes(self):
        irs = held_session()
        irs.send_info_ack(info(20943, b"\x11\x22\x33\x44"))
        self.assertEqual(self.offset_sent(irs), 8428)
        self.assertEqual(bytes(irs.received_data[:8428]), b"A" * 8428)
        self.assertEqual(irs.pending_frames, {9000: b"x"})

    def test_other_data_under_the_same_id_starts_over(self):
        irs = held_session()
        irs.send_info_ack(info(20942, b"\x55\x66\x77\x88"))
        self.assertEqual(self.offset_sent(irs), 0)
        self.assertEqual(irs.received_bytes, 0)
        self.assertEqual(len(irs.received_data), 20942)
        self.assertEqual(bytes(irs.received_data[:10]), bytes(10))
        self.assertEqual(irs.pending_frames, {})
        self.assertEqual((irs.total_length, irs.total_crc), (20942, b"\x55\x66\x77\x88"))

    def test_same_length_other_crc_starts_over(self):
        irs = held_session()
        irs.send_info_ack(info(20943, b"\x00\x00\x00\x01"))
        self.assertEqual(self.offset_sent(irs), 0)

    def test_new_session_unchanged(self):
        irs = held_session()
        irs.received_bytes = 0
        irs.total_length, irs.total_crc = 0, ""
        irs.send_info_ack(info(500, b"\x01\x02\x03\x04"))
        self.assertEqual(self.offset_sent(irs), 0)
        self.assertEqual(len(irs.received_data), 500)


if __name__ == "__main__":
    unittest.main()
