"""Unit tests for the PSK Reporter uploader: packet layout, deduplication,
the off-by-default state and the API toggle. No network traffic, spots are
handed to a fake transport."""

import asyncio
import json
import struct
import unittest

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server import pskreporter
from freedata_server.pskreporter import PSKReporter, build_packet
from freedata_server.api.modem import get_pskreporter, post_pskreporter

# descriptors copied from https://pskreporter.info/pskdev.html
RECEIVER_TEMPLATE_DOC = bytes.fromhex(
    "00030024999200030001" + "8002FFFF0000768F" + "8004FFFF0000768F" + "8008FFFF0000768F" + "0000"
)
SENDER_OPTION_3_DOC = bytes.fromhex(
    "0002003C99930007"
    + "8001FFFF0000768F"
    + "800500040000768F"
    + "800600010000768F"
    + "800700010000768F"
    + "800AFFFF0000768F"
    + "800B00010000768F"
    + "00960004"
)
SENDER_OPTION_4_DOC = bytes.fromhex(
    "0002004499940008"  # same as option 4 of the docs, but with our own linking value 0x9994
    + "8001FFFF0000768F"
    + "800500040000768F"
    + "800600010000768F"
    + "800700010000768F"
    + "800AFFFF0000768F"
    + "800B00010000768F"
    + "8003FFFF0000768F"
    + "00960004"
)


def parse_sets(packet):
    """Split an IPFIX datagram into (header fields, [(set id, set body)])."""
    version, length, export_time, sequence, domain = struct.unpack(">HHIII", packet[:16])
    sets = []
    offset = 16
    while offset < len(packet):
        set_id, set_length = struct.unpack(">HH", packet[offset : offset + 4])
        sets.append((set_id, packet[offset + 4 : offset + set_length]))
        offset += set_length
    return (version, length, export_time, sequence, domain), sets


def read_string(data, offset):
    size = data[offset]
    return data[offset + 1 : offset + 1 + size].decode(), offset + 1 + size


def parse_sender_records(body, with_locator):
    records = []
    offset = 0
    while offset < len(body) and body[offset] != 0:
        callsign, offset = read_string(body, offset)
        frequency, snr, imd = struct.unpack(">Ibb", body[offset : offset + 6])
        offset += 6
        mode, offset = read_string(body, offset)
        source = body[offset]
        offset += 1
        locator = ""
        if with_locator:
            locator, offset = read_string(body, offset)
        (timestamp,) = struct.unpack(">I", body[offset : offset + 4])
        offset += 4
        records.append((callsign, frequency, snr, imd, mode, source, locator, timestamp))
    return records


class TestPacket(unittest.TestCase):
    def test_templates_match_the_documentation(self):
        self.assertEqual(pskreporter.receiver_template(), RECEIVER_TEMPLATE_DOC)
        self.assertEqual(
            pskreporter.sender_template(pskreporter.SENDER_TEMPLATE_ID, pskreporter.SENDER_FIELDS),
            SENDER_OPTION_3_DOC,
        )
        self.assertEqual(
            pskreporter.sender_template(pskreporter.SENDER_LOCATOR_TEMPLATE_ID, pskreporter.SENDER_LOCATOR_FIELDS),
            SENDER_OPTION_4_DOC,
        )

    def test_packet_layout(self):
        spots = [
            {"callsign": "DJ2LS", "frequency": 14094500, "snr": -7, "locator": "JN48cs", "timestamp": 1700000000},
            {"callsign": "OH2ABC", "frequency": 14094520, "snr": 3, "locator": "", "timestamp": 1700000010},
        ]
        packet = build_packet("OH2JRB", "KP20le", "FreeDATA 0.19.0", spots, 42, 0xDEADBEEF, export_time=1700000100)
        (version, length, export_time, sequence, domain), sets = parse_sets(packet)

        self.assertEqual(version, 10)
        self.assertEqual(length, len(packet))
        self.assertEqual(export_time, 1700000100)
        self.assertEqual(sequence, 42)
        self.assertEqual(domain, 0xDEADBEEF)
        self.assertEqual(len(packet) % 4, 0)
        self.assertEqual([set_id for set_id, _ in sets], [3, 2, 2, 0x9992, 0x9993, 0x9994])

        receiver = dict(sets)[0x9992]
        call, offset = read_string(receiver, 0)
        grid, offset = read_string(receiver, offset)
        software, offset = read_string(receiver, offset)
        self.assertEqual((call, grid, software), ("OH2JRB", "KP20le", "FreeDATA 0.19.0"))

        without_locator = parse_sender_records(dict(sets)[0x9993], with_locator=False)
        with_locator = parse_sender_records(dict(sets)[0x9994], with_locator=True)
        self.assertEqual(without_locator, [("OH2ABC", 14094520, 3, 0, "FREEDATA", 1, "", 1700000010)])
        self.assertEqual(with_locator, [("DJ2LS", 14094500, -7, 0, "FREEDATA", 1, "JN48cs", 1700000000)])

    def test_packet_without_templates(self):
        spots = [{"callsign": "DJ2LS", "frequency": 7053000, "snr": 0, "locator": "", "timestamp": 1}]
        packet = build_packet("OH2JRB", "KP20", "FreeDATA", spots, 0, 1, include_templates=False)
        _, sets = parse_sets(packet)
        self.assertEqual([set_id for set_id, _ in sets], [0x9992, 0x9993])

    def test_normalizing(self):
        self.assertEqual(pskreporter.normalize_callsign("oh2jrb-7"), "OH2JRB")
        self.assertEqual(pskreporter.normalize_locator("kp20LE"), "KP20le")
        self.assertEqual(pskreporter.normalize_locator("------"), "")
        self.assertEqual(pskreporter.normalize_locator(None), "")
        self.assertEqual(pskreporter.clamp_snr(-300), -128)
        self.assertEqual(pskreporter.clamp_snr(12.6), 13)


class TestUploader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.config_manager.config["STATION"]["mycall"] = "OH2JRB"
        cls.ctx.config_manager.config["STATION"]["mygrid"] = "KP20le"

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def setUp(self):
        self.sent = []
        self.reporter = PSKReporter(self.ctx, transport=self.sent.append)

    def uploaded_callsigns(self, packet):
        _, sets = parse_sets(packet)
        records = []
        for set_id, body in sets:
            if set_id == pskreporter.SENDER_TEMPLATE_ID:
                records += parse_sender_records(body, with_locator=False)
            elif set_id == pskreporter.SENDER_LOCATOR_TEMPLATE_ID:
                records += parse_sender_records(body, with_locator=True)
        return sorted(record[0] for record in records)

    def test_off_by_default(self):
        self.assertFalse(self.ctx.pskreporter.enabled)
        self.assertFalse(self.reporter.enabled)
        self.assertFalse(self.reporter.add_spot("DJ2LS-0", -5, 14094500))
        self.assertEqual(self.reporter.flush(now=1000), 0)
        self.assertEqual(self.sent, [])

    def test_off_after_restart_whatever_was_on_before(self):
        self.ctx.pskreporter.set_enabled(True)
        restarted = AppContext(testenv.config_path())
        try:
            self.assertFalse(restarted.pskreporter.enabled)
        finally:
            restarted.shutdown()
            self.ctx.pskreporter.set_enabled(False)

    def test_batches_and_deduplicates(self):
        self.reporter.set_enabled(True)
        self.reporter.add_spot("DJ2LS-0", -5, 14094500, locator="JN48cs")
        self.reporter.add_spot("DJ2LS-1", 2, 14094510)  # same station, other SSID
        self.reporter.add_spot("OH2ABC-0", 1, 14094520)

        self.assertEqual(self.reporter.flush(now=1000), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.uploaded_callsigns(self.sent[0]), ["DJ2LS", "OH2ABC"])
        # the later frame without a locator keeps the known one, with the latest SNR
        records = parse_sender_records(dict(parse_sets(self.sent[0])[1])[0x9994], with_locator=True)
        self.assertEqual(records[0][:3], ("DJ2LS", 14094510, 2))
        self.assertEqual(records[0][6], "JN48cs")

        # nothing new within five minutes, even if stations are heard again
        self.reporter.add_spot("DJ2LS-0", -5, 14094500)
        self.assertEqual(self.reporter.flush(now=1000 + 120), 0)
        self.assertEqual(len(self.sent), 1)

        # after five minutes: the same station on the same band is not repeated within the hour,
        # but a new station or a band change is reported
        self.reporter.add_spot("OH2ABC-0", 1, 7053000)
        self.reporter.add_spot("SM0XYZ-0", 4, 14094500)
        self.assertEqual(self.reporter.flush(now=1000 + 300), 2)
        self.assertEqual(self.uploaded_callsigns(self.sent[1]), ["OH2ABC", "SM0XYZ"])

        # an hour later the unchanged station can be reported again
        self.reporter.add_spot("DJ2LS-0", -5, 14094500)
        self.assertEqual(self.reporter.flush(now=1000 + 3700), 1)

    def test_sequence_counts_records_and_templates_are_resent(self):
        self.reporter.set_enabled(True)
        now = 1000
        for i in range(5):
            self.reporter.add_spot(f"OH{i}AA-0", 0, 14094500)
            self.reporter.flush(now=now)
            now += 300
        sequences = [parse_sets(packet)[0][3] for packet in self.sent]
        self.assertEqual(sequences, [0, 1, 2, 3, 4])
        with_templates = [parse_sets(packet)[1][0][0] == 3 for packet in self.sent]
        self.assertEqual(with_templates, [True, True, True, False, False])

        now = 1000 + 600 + pskreporter.TEMPLATE_INTERVAL  # an hour after the third packet
        self.reporter.add_spot("SM0XYZ-0", 0, 14094500)
        self.reporter.flush(now=now)
        self.assertEqual(parse_sets(self.sent[-1])[1][0][0], 3)

    def test_large_batches_are_split(self):
        self.reporter.set_enabled(True)
        for i in range(pskreporter.MAX_SPOTS_PER_PACKET + 5):
            self.reporter.add_spot(f"OH{i}AA-0", 0, 14094500)
        self.assertEqual(self.reporter.flush(now=1000), pskreporter.MAX_SPOTS_PER_PACKET + 5)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(parse_sets(self.sent[1])[0][3], pskreporter.MAX_SPOTS_PER_PACKET)

    def test_disabling_drops_pending_spots(self):
        self.reporter.set_enabled(True)
        self.reporter.add_spot("DJ2LS-0", -5, 14094500)
        self.reporter.set_enabled(False)
        self.reporter.set_enabled(True)
        self.assertEqual(self.reporter.flush(now=1000), 0)
        self.assertEqual(self.sent, [])

    def test_spots_without_frequency_are_ignored(self):
        self.reporter.set_enabled(True)
        self.assertFalse(self.reporter.add_spot("DJ2LS-0", -5, 0))
        self.assertFalse(self.reporter.add_spot("DJ2LS-0", -5, "---"))

    def test_default_callsign_is_not_reported(self):
        self.ctx.config_manager.config["STATION"]["mycall"] = "AA1AAA"
        try:
            self.reporter.set_enabled(True)
            self.reporter.add_spot("DJ2LS-0", -5, 14094500)
            self.assertEqual(self.reporter.flush(now=1000), 0)
            self.assertEqual(self.sent, [])
        finally:
            self.ctx.config_manager.config["STATION"]["mycall"] = "OH2JRB"


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def call(self, coroutine):
        return json.loads(asyncio.run(coroutine).body)

    def test_toggle(self):
        self.assertEqual(self.call(get_pskreporter(ctx=self.ctx)), {"enabled": False, "pending_spots": 0})
        self.assertTrue(self.call(post_pskreporter({"enabled": True}, ctx=self.ctx))["enabled"])
        self.assertTrue(self.ctx.pskreporter.enabled)
        self.assertTrue(self.call(get_pskreporter(ctx=self.ctx))["enabled"])
        self.assertFalse(self.call(post_pskreporter({"enabled": False}, ctx=self.ctx))["enabled"])
        self.assertFalse(self.ctx.pskreporter.enabled)

    def test_toggle_rejects_non_bool(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(post_pskreporter({"enabled": "yes"}, ctx=self.ctx))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(self.ctx.pskreporter.enabled)

    def test_not_saved_to_config(self):
        asyncio.run(post_pskreporter({"enabled": True}, ctx=self.ctx))
        try:
            for section in self.ctx.config_manager.config.values():
                self.assertNotIn("enable_pskreporter", section)
        finally:
            self.ctx.pskreporter.set_enabled(False)


if __name__ == "__main__":
    unittest.main()
