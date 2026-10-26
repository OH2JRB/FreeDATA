"""The "Enable FSK modes" switch (MODEM.enable_fsk) through the config API: a
change is logged, saved to the config file and restarts the modem."""

import asyncio
import configparser
import copy
import queue
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.api import config as api_config
from freedata_server.context import AppContext


class TestFskSwitchApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.TESTMODE = True
        cls.original = copy.deepcopy(cls.ctx.config_manager.read())

    @classmethod
    def tearDownClass(cls):
        cls.ctx.config_manager.write(cls.original)
        cls.ctx.shutdown()

    def post(self, enabled):
        config = copy.deepcopy(self.ctx.config_manager.read())
        config["MODEM"]["enable_fsk"] = enabled
        modem_service = queue.Queue()
        with (
            mock.patch.object(self.ctx, "modem_service", modem_service),
            mock.patch.object(api_config, "modem_log") as log,
        ):
            updated = asyncio.run(api_config.post_config(config, self.ctx))
        return updated, log, modem_service

    def saved(self):
        parser = configparser.ConfigParser()
        parser.read(self.ctx.config_manager.config_name)
        return parser.getboolean("MODEM", "enable_fsk")

    def test_switching_logs_saves_and_restarts(self):
        self.post(True)
        for enabled, text in ((False, "disabled"), (True, "enabled")):
            updated, log, modem_service = self.post(enabled)
            self.assertIs(updated["MODEM"]["enable_fsk"], enabled)
            self.assertIs(self.saved(), enabled)
            log.info.assert_called_once_with(f"[MODEM] FSK modes {text}")
            self.assertEqual(modem_service.get_nowait(), "restart")

    def test_no_log_without_a_change(self):
        self.post(True)
        config = copy.deepcopy(self.ctx.config_manager.read())
        config["MODEM"]["tx_delay"] = 100 if config["MODEM"]["tx_delay"] != 100 else 50
        with (
            mock.patch.object(self.ctx, "modem_service", queue.Queue()),
            mock.patch.object(api_config, "modem_log") as log,
        ):
            asyncio.run(api_config.post_config(config, self.ctx))
        log.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
