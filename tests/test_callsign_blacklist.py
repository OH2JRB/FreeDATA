"""STATION.callsign_blacklist: entries are case-insensitive regular expressions.

Before, an entry blocked every call that started with it, case-sensitively:
on air (2026-11-28) an entry OH2JRB-1 also blocked OH2JRB-12, and an entry
oh2jrb-12 blocked nothing. Now an entry has to match the whole call (or the
call without its SSID, so an entry without SSID still covers every SSID), case
does not matter, and POST /config/ refuses an entry that is not a valid
regular expression.
"""

import asyncio
import os
import queue
import shutil
import types
import unittest
from unittest import mock

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import callsign_blacklist, frame_handler, helpers
from freedata_server.api.config import post_config
from freedata_server.config import CONFIG

match = callsign_blacklist.matching_entry


class TestMatching(unittest.TestCase):
    def blocked(self, call, *entries, crc=None):
        return match(call, list(entries), crc) is not None

    def test_exact_call(self):
        self.assertTrue(self.blocked("OH2JRB-12", "OH2JRB-12"))

    def test_ssid_entry_is_not_a_prefix(self):
        # the on-air finding: OH2JRB-1 must not block OH2JRB-12
        self.assertFalse(self.blocked("OH2JRB-12", "OH2JRB-1"))
        self.assertTrue(self.blocked("OH2JRB-1", "OH2JRB-1"))

    def test_entry_without_ssid_covers_every_ssid(self):
        for call in ("OH2JRB", "OH2JRB-0", "OH2JRB-12"):
            self.assertTrue(self.blocked(call, "OH2JRB"), call)
        self.assertFalse(self.blocked("OH2JRBX-1", "OH2JRB"))
        self.assertFalse(self.blocked("XOH2JRB-1", "OH2JRB"))

    def test_case_does_not_matter(self):
        self.assertTrue(self.blocked("OH2JRB-12", "oh2jrb-12"))
        self.assertTrue(self.blocked("oh2jrb-12", "OH2JRB-12"))
        self.assertTrue(self.blocked("OH2JRB-12", "oh2.*"))

    def test_regular_expressions(self):
        self.assertTrue(self.blocked("OH2JRB-13", "OH2JRB-1[0-5]"))
        self.assertFalse(self.blocked("OH2JRB-16", "OH2JRB-1[0-5]"))
        self.assertTrue(self.blocked("OH1DDC-1", "OH.*"))
        self.assertFalse(self.blocked("DL1ABC-1", "OH.*"))
        self.assertTrue(self.blocked("DL1ABC-1", "OH.*", "DL1A(BC|XY)"))
        # anchors are allowed but not needed
        self.assertTrue(self.blocked("OH2JRB-12", "^OH2JRB-12$"))

    def test_partial_match_is_not_enough(self):
        self.assertFalse(self.blocked("OH2JRB-12", "JRB"))
        self.assertFalse(self.blocked("OH2JRB-12", "OH2"))

    def test_the_first_matching_entry_is_returned(self):
        self.assertEqual(match("OH2JRB-12", ["DL1ABC", "oh2.*", "OH2JRB"]), "oh2.*")

    def test_blank_and_non_text_entries_are_skipped(self):
        self.assertIsNone(match("OH2JRB-12", ["", "  ", None, 5]))
        self.assertEqual(match("OH2JRB-12", [" OH2JRB-12 "]), "OH2JRB-12")

    def test_empty_list_or_unknown_call(self):
        self.assertIsNone(match("OH2JRB-12", []))
        self.assertIsNone(match("OH2JRB-12", None))
        self.assertIsNone(match(None, ["OH2JRB-12"]))

    def test_invalid_expression_is_compared_as_text(self):
        # only possible from a hand-edited config.ini
        self.assertTrue(self.blocked("OH2JRB-12", "OH2JRB-12", "OH2[JRB"))
        self.assertFalse(self.blocked("OH2JRB-12", "OH2[JRB"))
        self.assertTrue(self.blocked("OH2[JRB", "oh2[jrb"))

    def test_crc_only_frame(self):
        # a frame with only the CRC of a call this station has not heard
        crc = helpers.get_crc_24("OH2JRB-12").hex()
        self.assertTrue(self.blocked(None, "oh2jrb-12", crc=crc))
        self.assertTrue(self.blocked(None, "OH2JRB", crc=crc))
        self.assertFalse(self.blocked(None, "OH2JRB-1", crc=crc))
        # a regular expression cannot be compared with a CRC
        self.assertFalse(self.blocked(None, "OH2.*", crc=crc))
        self.assertFalse(self.blocked(None, "OH2JRB-12", crc=None))


class TestInvalidEntries(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(callsign_blacklist.invalid_entries(["OH2JRB", "OH.*", "", "DL1(A|B)C"]), [])
        self.assertEqual(callsign_blacklist.invalid_entries(None), [])

    def test_invalid(self):
        self.assertEqual(callsign_blacklist.invalid_entries(["OH2JRB", "OH2[JRB", "(", 5]), ["5", "OH2[JRB", "("])


class TestFrameHandler(unittest.TestCase):
    def handle(self, frame, entries, enabled=True):
        handler = object.__new__(frame_handler.FrameHandler)
        handler.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(
                config={"STATION": {"enable_callsign_blacklist": enabled, "callsign_blacklist": entries}}
            ),
        )
        handler.details = {}
        handler.logger = mock.Mock()
        for name in ("log", "add_to_heard_stations", "add_to_activity_list", "emit_event", "follow_protocol"):
            setattr(handler, name, mock.Mock())
        with (
            mock.patch.object(frame_handler, "DatabaseManagerStations"),
            mock.patch.object(frame_handler, "DatabaseManager") as db,
        ):
            db.return_value.get_callsign_by_checksum.return_value = None
            result = handler.handle(dict(frame), 5, 0, None, 0)
        return result, handler.follow_protocol.called

    def test_blocked_frame_is_not_processed(self):
        self.assertEqual(self.handle({"origin": "OH2JRB-12", "frame_type": "PING"}, ["oh2jrb-1[0-9]"]), (False, False))

    def test_other_call_is_processed(self):
        self.assertEqual(self.handle({"origin": "OH2JRB-12", "frame_type": "PING"}, ["OH2JRB-1"]), (True, True))

    def test_switched_off(self):
        self.assertEqual(
            self.handle({"origin": "OH2JRB-12", "frame_type": "PING"}, ["OH2JRB-12"], enabled=False), (True, True)
        )

    def test_unknown_crc_does_not_crash(self):
        # the old check called get_crc_24(None) for a frame whose origin CRC
        # was not in the database
        crc = helpers.get_crc_24("OH2JRB-12").hex()
        self.assertEqual(self.handle({"origin_crc": crc, "frame_type": "PING_ACK"}, ["OH2JRB-12"]), (False, False))
        other = helpers.get_crc_24("DL1ABC-1").hex()
        self.assertEqual(self.handle({"origin_crc": other, "frame_type": "PING_ACK"}, ["OH2JRB-12"]), (True, True))


class TestPostConfig(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(testenv.config_path()), "config-blacklist-test.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.path)
        self.addCleanup(os.remove, self.path)
        self.ctx = types.SimpleNamespace(modem_service=queue.Queue())
        self.ctx.config_manager = CONFIG(self.ctx, self.path)
        full = self.ctx.config_manager.read()
        full["STATION"].update({"mycall": "OH1DDC", "myssid": 1, "mygrid": "KP20le"})
        asyncio.run(post_config(full, self.ctx))

    def post(self, station):
        return asyncio.run(post_config({"STATION": station}, self.ctx))

    def test_regular_expressions_are_saved(self):
        result = self.post({"callsign_blacklist": ["oh2jrb-1[0-5]", "DL.*"]})
        self.assertEqual(result["STATION"]["callsign_blacklist"], ["oh2jrb-1[0-5]", "DL.*"])

    def test_invalid_expression_is_refused(self):
        with self.assertRaises(HTTPException) as cm:
            self.post({"callsign_blacklist": ["OH2JRB", "OH2[JRB"]})
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("OH2[JRB", cm.exception.detail["error"])
        self.assertEqual(self.ctx.config_manager.read()["STATION"]["callsign_blacklist"], [])

    def test_other_settings_save_with_an_invalid_entry_already_stored(self):
        # an entry put in config.ini by hand does not block other changes
        cfg = self.ctx.config_manager.read()
        cfg["STATION"]["callsign_blacklist"] = ["OH2[JRB"]
        self.ctx.config_manager.write(cfg)
        result = self.post({"respond_to_cq": False})
        self.assertIs(result["STATION"]["respond_to_cq"], False)


if __name__ == "__main__":
    unittest.main()
