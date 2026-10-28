"""Setting the radio's clock on request: rigctld's set_clock with the server's
local time and UTC offset, the read-back with get_clock, the errors, the
backends that cannot do it, and POST /radio/clock."""

import asyncio
import json
import threading
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import rigctld, rigdummy
from freedata_server.api import radio as radio_api
from freedata_server.radio_manager import RadioManager
from freedata_server.rigctld import RadioClockError

NOW = datetime(2026, 10, 28, 14, 5, 30, tzinfo=timezone(timedelta(hours=2)))


def make_rigctld(*replies, connected=True):
    radio = rigctld.radio.__new__(rigctld.radio)
    radio.connected = connected
    radio.log = mock.Mock()
    radio.send_command = mock.Mock(side_effect=list(replies))
    return radio


class TestClockString(unittest.TestCase):
    def test_offsets(self):
        cases = [
            ((2, 0), "2026-10-28T14:05:30+0200"),
            ((0, 0), "2026-10-28T14:05:30+0000"),
            ((-3, -30), "2026-10-28T14:05:30-0330"),
            ((5, 45), "2026-10-28T14:05:30+0545"),
            ((-10, 0), "2026-10-28T14:05:30-1000"),
        ]
        for (hours, minutes), expected in cases:
            with self.subTest(expected=expected):
                now = NOW.replace(tzinfo=timezone(timedelta(hours=hours, minutes=minutes)))
                self.assertEqual(rigctld.radio.clock_string(now), expected)

    def test_hamlib_format_position(self):
        # Hamlib's set_clock looks for the offset sign at index 19
        self.assertIn(rigctld.radio.clock_string(NOW)[19], "+-")

    def test_rprt_code(self):
        self.assertEqual(rigctld.radio.rprt_code("RPRT 0"), 0)
        self.assertEqual(rigctld.radio.rprt_code("RPRT -11"), -11)
        self.assertIsNone(rigctld.radio.rprt_code("2026-10-28T14:05:30.000+02:00"))
        self.assertIsNone(rigctld.radio.rprt_code(None))


class TestSendCommandRaw(unittest.TestCase):
    def make_radio(self, reply):
        radio = rigctld.radio.__new__(rigctld.radio)
        radio.connected = True
        radio.log = mock.Mock()
        radio.await_response = threading.Event()
        radio.await_response.set()
        radio.connection = mock.Mock(**{"recv.return_value": reply})
        return radio

    def test_raw_keeps_the_result_code(self):
        radio = self.make_radio(b"RPRT -11\n")
        self.assertEqual(radio.send_command("\\set_clock x", raw=True), "RPRT -11")
        radio.connection.sendall.assert_called_once_with(b"\\set_clock x\n")

    def test_without_raw_a_result_code_is_none(self):
        self.assertIsNone(self.make_radio(b"RPRT 0\n").send_command("\\set_clock x"))


class TestRigctldSetClock(unittest.TestCase):
    def test_sets_and_reads_back(self):
        radio = make_rigctld("RPRT 0", "2026-10-28T14:05:30.000+02:00")
        result = radio.set_clock(NOW)
        self.assertEqual(result, {"set_to": "2026-10-28T14:05:30+0200", "radio_clock": "2026-10-28T14:05:30.000+02:00"})
        self.assertEqual(
            radio.send_command.call_args_list,
            [
                mock.call("\\set_clock 2026-10-28T14:05:30+0200", raw=True),
                mock.call("\\get_clock", raw=True),
            ],
        )

    def test_uses_the_server_local_time_by_default(self):
        radio = make_rigctld("RPRT 0", "x")
        result = radio.set_clock()
        expected = rigctld.radio.clock_string(datetime.now().astimezone())
        self.assertEqual(result["set_to"][:16], expected[:16])  # same minute
        self.assertEqual(result["set_to"][19:], expected[19:])  # same UTC offset

    def test_readback_error(self):
        radio = make_rigctld("RPRT 0", "RPRT -11")
        self.assertIsNone(radio.set_clock(NOW)["radio_clock"])

    def test_radio_without_clock(self):
        radio = make_rigctld("RPRT -11")
        with self.assertRaises(RadioClockError) as caught:
            radio.set_clock(NOW)
        self.assertEqual(caught.exception.reason, "not_supported")
        self.assertEqual(radio.send_command.call_count, 1)  # no read-back

    def test_rigctld_error(self):
        for reply in ("RPRT -1", None, ""):
            with self.subTest(reply=reply):
                with self.assertRaises(RadioClockError) as caught:
                    make_rigctld(reply).set_clock(NOW)
                self.assertEqual(caught.exception.reason, "failed")

    def test_not_connected(self):
        radio = make_rigctld(connected=False)
        with self.assertRaises(RadioClockError) as caught:
            radio.set_clock(NOW)
        self.assertEqual(caught.exception.reason, "not_connected")
        radio.send_command.assert_not_called()


class TestRadioManagerSetClock(unittest.TestCase):
    def make_manager(self, radio, control):
        manager = RadioManager.__new__(RadioManager)
        manager.radio = radio
        manager.radiocontrol = control
        return manager

    def test_rigctld(self):
        radio = mock.Mock(**{"set_clock.return_value": {"set_to": "t"}})
        self.assertEqual(self.make_manager(radio, "rigctld").set_clock(), {"set_to": "t"})

    def test_other_backends(self):
        with self.assertRaises(RadioClockError) as caught:
            self.make_manager(rigdummy.radio(), "disabled").set_clock()
        self.assertEqual(caught.exception.reason, "unsupported_backend")


class TestRadioClockRoute(unittest.TestCase):
    def call(self, radio_manager):
        ctx = types.SimpleNamespace(radio_manager=radio_manager)
        return asyncio.run(radio_api.post_radio_clock(ctx))

    def test_success(self):
        manager = mock.Mock(**{"set_clock.return_value": {"set_to": "a", "radio_clock": "b"}})
        response = self.call(manager)
        self.assertEqual(json.loads(response.body), {"set_to": "a", "radio_clock": "b"})

    def test_errors(self):
        cases = {"not_connected": 409, "unsupported_backend": 409, "not_supported": 422, "failed": 500}
        for reason, status in cases.items():
            with self.subTest(reason=reason):
                manager = mock.Mock(**{"set_clock.side_effect": RadioClockError(reason, "message")})
                with self.assertRaises(HTTPException) as caught:
                    self.call(manager)
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.detail, {"error": "message"})

    def test_no_radio_manager(self):
        with self.assertRaises(HTTPException) as caught:
            self.call(None)
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
