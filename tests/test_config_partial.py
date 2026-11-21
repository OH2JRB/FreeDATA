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
from unittest import mock

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


class TestConfigChangedEvent(unittest.TestCase):
    """POST /config/ announces a change, so open GUI tabs reload the config."""

    def setUp(self):
        self.path = os.path.join(os.path.dirname(testenv.config_path()), "config-event-test.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.path)
        self.addCleanup(os.remove, self.path)
        self.events = []
        self.ctx = types.SimpleNamespace(
            modem_service=queue.Queue(),
            event_manager=types.SimpleNamespace(config_changed=lambda: self.events.append("changed")),
        )
        self.ctx.config_manager = CONFIG(self.ctx, self.path)
        full = self.ctx.config_manager.read()
        full["STATION"].update({"mycall": "OH2JRB", "myssid": 13, "mygrid": "KP20le"})
        asyncio.run(post_config(full, self.ctx))
        self.events.clear()

    def test_change_is_announced(self):
        asyncio.run(post_config({"STATION": {"myssid": 3}}, self.ctx))
        self.assertEqual(self.events, ["changed"])

    def test_no_change_is_not_announced(self):
        asyncio.run(post_config({"STATION": {"myssid": 13}}, self.ctx))
        self.assertEqual(self.events, [])

    def test_refused_change_is_not_announced(self):
        with self.assertRaises(HTTPException):
            asyncio.run(post_config({"STATION": {"mycall": "not a call"}}, self.ctx))
        self.assertEqual(self.events, [])


class TestTempLogSwitch(unittest.TestCase):
    """STATION.enable_log_to_temp starts or stops the temp-directory log at once,
    without a modem restart, and the state event shows the file."""

    def setUp(self):
        self.path = os.path.join(os.path.dirname(testenv.config_path()), "config-templog-test.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.path)
        self.addCleanup(os.remove, self.path)
        self.state = {}
        self.ctx = types.SimpleNamespace(
            modem_service=queue.Queue(),
            state_manager=types.SimpleNamespace(set=lambda key, value: self.state.__setitem__(key, value)),
        )
        self.ctx.config_manager = CONFIG(self.ctx, self.path)
        full = self.ctx.config_manager.read()
        full["STATION"].update({"mycall": "OH2JRB", "myssid": 13, "mygrid": "KP20le"})
        asyncio.run(post_config(full, self.ctx))
        while not self.ctx.modem_service.empty():
            self.ctx.modem_service.get()
        patcher = mock.patch(
            "freedata_server.api.config.set_temp_logging",
            side_effect=lambda on: "/tmp/freedata_server_x.log" if on else "",
        )
        self.switch = patcher.start()
        self.addCleanup(patcher.stop)

    def test_template_has_it_off(self):
        self.assertIs(self.ctx.config_manager.read()["STATION"]["enable_log_to_temp"], False)

    def test_on_then_off(self):
        asyncio.run(post_config({"STATION": {"enable_log_to_temp": True}}, self.ctx))
        self.assertEqual(self.state, {"temp_log_file": "/tmp/freedata_server_x.log"})
        asyncio.run(post_config({"STATION": {"enable_log_to_temp": False}}, self.ctx))
        self.assertEqual(self.state, {"temp_log_file": ""})
        self.assertEqual([c.args for c in self.switch.call_args_list], [(True,), (False,)])
        self.assertTrue(self.ctx.modem_service.empty())  # no modem restart

    def test_stored_for_later_runs(self):
        asyncio.run(post_config({"STATION": {"enable_log_to_temp": True}}, self.ctx))
        parser = configparser.ConfigParser()
        parser.read(self.path)
        self.assertTrue(parser.getboolean("STATION", "enable_log_to_temp"))

    def test_other_settings_leave_it_alone(self):
        asyncio.run(post_config({"STATION": {"myssid": 4}}, self.ctx))
        self.switch.assert_not_called()
        self.assertEqual(self.ctx.modem_service.get_nowait(), "restart")


class TestEventShape(unittest.TestCase):
    def test_config_changed_event(self):
        from freedata_server.event_manager import EventManager

        q = queue.Queue()
        EventManager(types.SimpleNamespace(TESTMODE=False), [q]).config_changed()
        self.assertEqual(q.get_nowait(), {"type": "config", "config": "changed"})


if __name__ == "__main__":
    unittest.main()
