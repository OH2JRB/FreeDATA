"""Encryption events must reach the server log (stdout and the log file set up
by log_handler), and key material must never be logged."""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest

import structlog.testing

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server.api.freedata import (
    patch_station_encryption,
    put_station_encryption_key,
    delete_station_encryption_key,
    put_common_encryption_key,
    delete_common_encryption_key,
)

STATION_HEX_KEY = "cc" * 32
COMMON_PASSPHRASE = "correct horse battery staple"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestEncryptionLogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.message_system_db_manager.initialize_default_values()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def encryption_logs(self, logs):
        return [entry for entry in logs if entry["event"].startswith("[ENCRYPTION]")]

    def assert_no_key_material(self, logs):
        for entry in logs:
            text = repr(entry)
            self.assertNotIn(STATION_HEX_KEY, text)
            self.assertNotIn(COMMON_PASSPHRASE, text)

    def test_key_and_setting_changes_are_logged(self):
        callsign = "LG1AAA-0"
        with structlog.testing.capture_logs() as logs:
            asyncio.run(patch_station_encryption(callsign, {"enabled": True}, self.ctx))
            asyncio.run(put_station_encryption_key(callsign, {"key": STATION_HEX_KEY}, self.ctx))
            asyncio.run(delete_station_encryption_key(callsign, self.ctx))
            asyncio.run(patch_station_encryption(callsign, {"enabled": False}, self.ctx))
            asyncio.run(put_common_encryption_key({"key": COMMON_PASSPHRASE}, self.ctx))
            asyncio.run(delete_common_encryption_key(self.ctx))
            with self.assertRaises(Exception):
                asyncio.run(put_station_encryption_key(callsign, {"key": "not a key"}, self.ctx))

        events = [entry["event"] for entry in self.encryption_logs(logs)]
        for expected in (
            "[ENCRYPTION] Encryption on",
            "[ENCRYPTION] Key entered",
            "[ENCRYPTION] Key removed from memory",
            "[ENCRYPTION] Encryption off",
            "[ENCRYPTION] Common key entered",
            "[ENCRYPTION] Common key removed from memory",
            "[ENCRYPTION] Invalid key rejected",
        ):
            self.assertIn(expected, events)
        entered = next(e for e in logs if e["event"] == "[ENCRYPTION] Key entered")
        self.assertEqual(entered["callsign"], callsign)
        self.assert_no_key_material(logs)

    def test_gui_events_are_logged(self):
        with structlog.testing.capture_logs() as logs:
            self.ctx.event_manager.send_encryption_key_required("LG2AAA-0", "send")
            self.ctx.event_manager.send_encryption_amateur_band("LG2AAA-0", 14093000)
        key_required, amateur_band = self.encryption_logs(logs)
        self.assertEqual(key_required["log_level"], "warning")
        self.assertEqual(key_required["reason"], "send")
        self.assertEqual(amateur_band["log_level"], "warning")
        self.assertEqual(amateur_band["frequency"], 14093000)
        self.assertEqual(amateur_band["iaru_region"], "all")


class TestEncryptionLogHandler(unittest.TestCase):
    def test_written_to_stdout_and_log_file(self):
        # setup_logging configures logging for the whole process, so run it in its own
        script = (
            "import sys\n"
            "from unittest import mock\n"
            "from freedata_server.log_handler import setup_logging\n"
            "from freedata_server.event_manager import EventManager\n"
            "setup_logging(sys.argv[1], 'INFO')\n"
            "ctx = mock.Mock(TESTMODE=False)\n"
            "ctx.iaru_region.return_value = 1\n"
            "EventManager(ctx, []).send_encryption_amateur_band('LG3AAA-0', 7050000)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_file = os.path.join(tmp, "freedata.log")
            result = subprocess.run(
                [sys.executable, "-c", script, log_file],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(log_file, encoding="utf-8") as fh:
                logged = fh.read()
        for output in (result.stdout, logged):
            self.assertIn("[ENCRYPTION] Encrypted message not sent", output)
            self.assertIn("LG3AAA-0", output)
            self.assertIn("7050000", output)


if __name__ == "__main__":
    unittest.main()
