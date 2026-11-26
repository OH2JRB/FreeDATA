"""STATION.respond_to_cq decides whether a received CQ is answered with QRV.

The setting was shown in the GUI ("Respond to CQ callings with a QRV reply")
but never read: the check in CQFrameHandler was commented out (and looked in
the MODEM section), so a station always answered. Now it is read at every CQ,
and changing it saves without a modem restart. Checking for messages queued to
the calling station (auto repeat) does not depend on it.
"""

import asyncio
import os
import queue
import shutil
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import frame_handler_cq
from freedata_server.api.config import post_config
from freedata_server.config import CONFIG


def make_handler(station, arq_running=False):
    handler = object.__new__(frame_handler_cq.CQFrameHandler)
    handler.ctx = types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"STATION": station}),
        state_manager=types.SimpleNamespace(getARQ=lambda: arq_running),
    )
    handler.details = {"frame": {"origin": "AA1AAA-1"}, "snr": 5}
    handler.logger = mock.Mock()
    handler.check_for_queued_message = mock.Mock()
    return handler


class TestCQReply(unittest.TestCase):
    def run_cq(self, station, arq_running=False):
        handler = make_handler(station, arq_running)
        with mock.patch.object(frame_handler_cq.threading, "Thread") as thread:
            handler.follow_protocol()
        replies = [c for c in thread.call_args_list if c.kwargs.get("target") == handler.send_ack]
        return len(replies), handler.check_for_queued_message.call_count

    def test_on_answers(self):
        self.assertEqual(self.run_cq({"respond_to_cq": True}), (1, 1))

    def test_off_does_not_answer_but_still_checks_the_queue(self):
        self.assertEqual(self.run_cq({"respond_to_cq": False}), (0, 1))

    def test_missing_setting_answers(self):
        # configs written before the setting existed: the old behaviour
        self.assertEqual(self.run_cq({}), (1, 1))

    def test_nothing_during_an_arq_session(self):
        self.assertEqual(self.run_cq({"respond_to_cq": True}, arq_running=True), (0, 0))


class TestNoRestart(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(testenv.config_path()), "config-cq-test.ini")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.path)
        self.addCleanup(os.remove, self.path)
        self.ctx = types.SimpleNamespace(modem_service=queue.Queue())
        self.ctx.config_manager = CONFIG(self.ctx, self.path)
        full = self.ctx.config_manager.read()
        full["STATION"].update({"mycall": "OH2JRB", "myssid": 13, "mygrid": "KP20le"})
        asyncio.run(post_config(full, self.ctx))
        while not self.ctx.modem_service.empty():
            self.ctx.modem_service.get()

    def test_switch_saves_without_restart(self):
        result = asyncio.run(post_config({"STATION": {"respond_to_cq": False}}, self.ctx))
        self.assertIs(result["STATION"]["respond_to_cq"], False)
        self.assertTrue(self.ctx.modem_service.empty())


if __name__ == "__main__":
    unittest.main()
