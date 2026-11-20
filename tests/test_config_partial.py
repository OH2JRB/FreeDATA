"""POST /config accepts only the settings a client changed.

The GUI used to send its whole copy of the config with every change, so a tab
opened before another tab changed the call sign wrote the old call sign back
when its user changed the SSID (2026-11-20). Now a partial config is merged into
the stored one, and the call sign, locator and encryption checks run on the
merged result.
"""

import asyncio
import configparser
import os
import queue
import shutil
import types
import unittest

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.api.config import post_config
from freedata_server.config import CONFIG


class TestPartialConfig(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(testenv.config_path()), "config-partial-test.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.path)
        self.addCleanup(os.remove, self.path)
        self.ctx = types.SimpleNamespace(modem_service=queue.Queue())
        self.ctx.config_manager = CONFIG(self.ctx, self.path)
        # a stored config with a valid station
        full = self.ctx.config_manager.read()
        full["STATION"].update({"mycall": "OH2JRB", "myssid": 13, "mygrid": "KP20le"})
        self.post(full)
        self.drain()

    def post(self, cfg):
        return asyncio.run(post_config(cfg, self.ctx))

    def drain(self):
        while not self.ctx.modem_service.empty():
            self.ctx.modem_service.get()

    def in_file(self, section, key):
        parser = configparser.ConfigParser()
        parser.read(self.path)
        return parser[section][key]

    def test_ssid_alone_keeps_the_call_sign(self):
        result = self.post({"STATION": {"myssid": 7}})
        self.assertEqual((result["STATION"]["mycall"], result["STATION"]["myssid"]), ("OH2JRB", 7))
        self.assertEqual((self.in_file("STATION", "mycall"), self.in_file("STATION", "myssid")), ("OH2JRB", "7"))

    def test_second_tab_does_not_undo_the_first(self):
        # tab A changes the call sign; tab B, opened before, changes only the SSID
        self.post({"STATION": {"mycall": "OH2ABC"}})
        result = self.post({"STATION": {"myssid": 5}})
        self.assertEqual((result["STATION"]["mycall"], result["STATION"]["myssid"]), ("OH2ABC", 5))

    def test_section_without_station_is_accepted(self):
        result = self.post({"GUI": {"distance_unit": "mi"}})
        self.assertEqual(result["GUI"]["distance_unit"], "mi")
        self.assertEqual(result["STATION"]["mycall"], "OH2JRB")
        self.assertTrue(self.ctx.modem_service.empty())  # GUI only: no restart

    def test_modem_setting_restarts(self):
        self.post({"MODEM": {"tx_delay": 77}})
        self.assertEqual(self.ctx.modem_service.get_nowait(), "restart")

    def test_unchanged_value_is_a_no_op(self):
        self.post({"STATION": {"myssid": 13}})
        self.assertTrue(self.ctx.modem_service.empty())

    def test_invalid_call_sign_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            self.post({"STATION": {"mycall": "not a call"}})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.in_file("STATION", "mycall"), "OH2JRB")

    def test_call_sign_is_normalized(self):
        result = self.post({"STATION": {"mycall": "oh2xyz"}})
        self.assertEqual(result["STATION"]["mycall"], "OH2XYZ")

    def test_malformed_body_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            self.post({"STATION": "OH2JRB"})
        self.assertEqual(caught.exception.status_code, 400)

    def test_full_config_still_works(self):
        full = self.ctx.config_manager.read()
        full.pop("FREQUENCIES", None)
        full["STATION"]["myssid"] = 9
        result = self.post(full)
        self.assertEqual((result["STATION"]["mycall"], result["STATION"]["myssid"]), ("OH2JRB", 9))


if __name__ == "__main__":
    unittest.main()
