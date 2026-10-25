"""Tests that Explorer publishing and PSK Reporter uploads are switched off,
and stay off, while encryption is in use (for a station or with the common key)."""

import asyncio
import copy
import json
import queue
import unittest
from unittest import mock

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server.message_p2p import encryption_in_use
from freedata_server.api.config import post_config
from freedata_server.api.freedata import patch_station_encryption
from freedata_server.api.modem import get_pskreporter, post_pskreporter


def body(response):
    return json.loads(response.body)


class TestEncryptionDisablesReporting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.TESTMODE = True
        cls.ctx.message_system_db_manager.initialize_default_values()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def setUp(self):
        self.addCleanup(self.reset)
        self.reset()

    def reset(self):
        for callsign in ("ER1AAA-0", "ER2AAA-0"):
            asyncio.run(patch_station_encryption(callsign, {"enabled": False}, self.ctx))
        self.write_config(enable_common_key=False, enable_explorer=False, enable_stats=False)
        self.ctx.pskreporter.set_enabled(False)

    def write_config(self, **values):
        config = {"STATION": {}, "MESSAGES": {}}
        for key, value in values.items():
            section = "MESSAGES" if key == "enable_common_key" else "STATION"
            config[section][key] = value
        self.ctx.config_manager.write(config)

    def config_with(self, **values):
        config = copy.deepcopy(self.ctx.config_manager.read())
        for key, value in values.items():
            section = "MESSAGES" if key == "enable_common_key" else "STATION"
            config[section][key] = value
        return config

    def post_config(self, config):
        # keep the config change from restarting the modem (and its scheduler) in this test
        with mock.patch.object(self.ctx, "modem_service", queue.Queue()):
            return asyncio.run(post_config(config, self.ctx))

    def reporting(self):
        station = self.ctx.config_manager.config["STATION"]
        return station["enable_explorer"], station["enable_stats"], self.ctx.pskreporter.status()["enabled"]

    def enable_reporting(self):
        self.write_config(enable_explorer=True, enable_stats=True)
        asyncio.run(post_pskreporter({"enabled": True}, self.ctx))
        self.assertEqual(self.reporting(), (True, True, True))

    def test_station_encryption_switches_reporting_off(self):
        self.enable_reporting()
        asyncio.run(patch_station_encryption("ER1AAA-0", {"enabled": True}, self.ctx))
        self.assertTrue(encryption_in_use(self.ctx))
        self.assertEqual(self.reporting(), (False, False, False))
        self.assertTrue(body(asyncio.run(get_pskreporter(self.ctx)))["blocked_by_encryption"])

    def test_common_key_setting_switches_reporting_off(self):
        self.enable_reporting()
        updated = self.post_config(self.config_with(enable_common_key=True))
        self.assertTrue(updated["MESSAGES"]["enable_common_key"])
        self.assertEqual(self.reporting(), (False, False, False))

    def test_reporting_locked_while_encryption_is_on(self):
        asyncio.run(patch_station_encryption("ER1AAA-0", {"enabled": True}, self.ctx))
        asyncio.run(patch_station_encryption("ER2AAA-0", {"enabled": True}, self.ctx))

        with self.assertRaises(HTTPException) as cm:
            asyncio.run(post_pskreporter({"enabled": True}, self.ctx))
        self.assertEqual(cm.exception.status_code, 409)
        # switching it off is always allowed
        self.assertFalse(body(asyncio.run(post_pskreporter({"enabled": False}, self.ctx)))["enabled"])

        # Explorer can't be switched on through the config either
        updated = self.post_config(self.config_with(enable_explorer=True, enable_stats=True))
        self.assertFalse(updated["STATION"]["enable_explorer"])
        self.assertFalse(updated["STATION"]["enable_stats"])

        # still locked while one station has encryption on
        asyncio.run(patch_station_encryption("ER1AAA-0", {"enabled": False}, self.ctx))
        self.assertTrue(encryption_in_use(self.ctx))

        # all encryption off: reporting can be switched on again
        asyncio.run(patch_station_encryption("ER2AAA-0", {"enabled": False}, self.ctx))
        self.assertFalse(encryption_in_use(self.ctx))
        self.assertTrue(body(asyncio.run(post_pskreporter({"enabled": True}, self.ctx)))["enabled"])
        updated = self.post_config(self.config_with(enable_explorer=True))
        self.assertTrue(updated["STATION"]["enable_explorer"])

    def test_turning_station_encryption_off_does_not_switch_reporting_on(self):
        asyncio.run(patch_station_encryption("ER1AAA-0", {"enabled": True}, self.ctx))
        asyncio.run(patch_station_encryption("ER1AAA-0", {"enabled": False}, self.ctx))
        self.assertEqual(self.reporting(), (False, False, False))
        self.assertFalse(body(asyncio.run(get_pskreporter(self.ctx)))["blocked_by_encryption"])

    def test_startup_switches_explorer_off(self):
        """A config with Explorer on (e.g. edited by hand) is corrected at startup."""
        self.write_config(enable_common_key=True, enable_explorer=True, enable_stats=True)
        with (
            mock.patch.object(self.ctx, "modem_service", queue.Queue()),
            mock.patch.object(self.ctx.websocket_manager, "startWorkerThreads"),
        ):
            self.ctx.startup()
        self.assertEqual(self.reporting(), (False, False, False))


if __name__ == "__main__":
    unittest.main()
