"""Radio control actions reach the server log: what was asked, the values,
the radio control in use and whether it worked. Every change goes through
RadioManager.apply; rigctld checks its "RPRT" reply to tell success from
failure, and a read that keeps failing on every poll is logged once."""

import asyncio
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import structlog.testing
from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import flrig, rigctld, rigdummy, serial_ptt
from freedata_server.api import radio as radio_api
from freedata_server.radio_manager import RadioManager
from freedata_server.rigctld import RadioClockError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_manager(radio, control="rigctld"):
    manager = RadioManager.__new__(RadioManager)
    manager.radio = radio
    manager.radiocontrol = control
    manager.ctx = mock.Mock()
    manager.ctx.config_manager.config = {"SOCKET_INTERFACE": {"enable": False}}
    return manager


def radio_logs(logs):
    return [entry for entry in logs if entry["event"].startswith("[RADIO]")]


def make_rigctld(*replies):
    radio = rigctld.radio.__new__(rigctld.radio)
    radio.connected = True
    radio.ptt_mode = "TX"
    radio.parameters = {"chk_vfo": False, "vfo": None, "ptt": False, "mode": "USB"}
    radio.poll_errors = {}
    radio.log = mock.Mock()
    radio.send_command = mock.Mock(side_effect=list(replies))
    return radio


class TestRadioManagerLogging(unittest.TestCase):
    def test_applied(self):
        radio = mock.Mock(**{f"{name}.return_value": True for name in ("set_frequency", "set_mode", "set_rf_level")})
        manager = make_manager(radio)
        with structlog.testing.capture_logs() as logs:
            manager.set_frequency(14093000)
            manager.set_mode("PKTUSB")
            manager.set_rf_level(50)
        frequency, mode, rf_level = radio_logs(logs)
        for entry in (frequency, mode, rf_level):
            self.assertEqual(entry["event"], "[RADIO] Radio control applied")
            self.assertEqual(entry["log_level"], "info")
            self.assertEqual(entry["control"], "rigctld")
        self.assertEqual((frequency["action"], frequency["frequency"]), ("set_frequency", 14093000))
        self.assertEqual((mode["action"], mode["mode"]), ("set_mode", "PKTUSB"))
        self.assertEqual((rf_level["action"], rf_level["rf_level"]), ("set_rf_level", 50))
        manager.ctx.state_manager.set_radio.assert_any_call("radio_frequency", 14093000)

    def test_failed(self):
        manager = make_manager(mock.Mock(**{"set_tuner.return_value": False}))
        with structlog.testing.capture_logs() as logs:
            manager.set_tuner(True)
        (entry,) = radio_logs(logs)
        self.assertEqual(entry["event"], "[RADIO] Radio control failed")
        self.assertEqual(entry["log_level"], "warning")
        self.assertEqual((entry["action"], entry["tuner"]), ("set_tuner", True))

    def test_not_supported(self):
        manager = make_manager(rigdummy.radio(), "disabled")
        with structlog.testing.capture_logs() as logs:
            manager.set_frequency(7050000)
            manager.set_ptt(True)
        for entry in radio_logs(logs):
            self.assertEqual(entry["event"], "[RADIO] Radio control not supported")
            self.assertEqual(entry["log_level"], "info")
            self.assertEqual(entry["control"], "disabled")
        self.assertEqual([e["action"] for e in radio_logs(logs)], ["set_frequency", "set_ptt"])

    def test_exception_is_logged_and_raised(self):
        manager = make_manager(mock.Mock(**{"set_mode.side_effect": OSError("port gone")}))
        with structlog.testing.capture_logs() as logs:
            with self.assertRaises(OSError):
                manager.set_mode("USB")
        (entry,) = radio_logs(logs)
        self.assertEqual(entry["log_level"], "error")
        self.assertEqual(entry["error"], "port gone")
        self.assertEqual(entry["mode"], "USB")

    def test_unkey_is_sent_twice_and_logged_once(self):
        radio = mock.Mock(**{"set_ptt.side_effect": [False, True]})
        manager = make_manager(radio)
        with structlog.testing.capture_logs() as logs:
            manager.set_ptt(False)
        self.assertEqual(radio.set_ptt.call_count, 2)
        (entry,) = radio_logs(logs)
        # the second unkey worked
        self.assertEqual(entry["event"], "[RADIO] Radio control applied")
        self.assertEqual((entry["action"], entry["ptt"]), ("set_ptt", False))

    def test_key(self):
        radio = mock.Mock(**{"set_ptt.return_value": True})
        with structlog.testing.capture_logs() as logs:
            make_manager(radio).set_ptt(True)
        radio.set_ptt.assert_called_once_with(True)
        (entry,) = radio_logs(logs)
        self.assertEqual((entry["log_level"], entry["ptt"]), ("info", True))

    def test_clock(self):
        radio = mock.Mock(**{"set_clock.return_value": {"set_to": "t", "radio_clock": "r"}})
        with structlog.testing.capture_logs() as logs:
            make_manager(radio).set_clock()
        (entry,) = radio_logs(logs)
        self.assertEqual(entry["event"], "[RADIO] Radio control applied")
        self.assertEqual((entry["action"], entry["set_to"], entry["radio_clock"]), ("set_clock", "t", "r"))

    def test_clock_failed(self):
        radio = mock.Mock(**{"set_clock.side_effect": RadioClockError("not_supported", "no clock")})
        with structlog.testing.capture_logs() as logs:
            with self.assertRaises(RadioClockError):
                make_manager(radio).set_clock()
        (entry,) = radio_logs(logs)
        self.assertEqual((entry["log_level"], entry["reason"]), ("warning", "not_supported"))

    def test_clock_unsupported_backend(self):
        with structlog.testing.capture_logs() as logs:
            with self.assertRaises(RadioClockError):
                make_manager(rigdummy.radio(), "disabled").set_clock()
        (entry,) = radio_logs(logs)
        self.assertEqual(entry["event"], "[RADIO] Radio control not supported")


class TestRigctldSetResult(unittest.TestCase):
    SETTERS = (
        ("set_frequency", (14093000,), "F 14093000"),
        ("set_mode", ("PKTUSB",), "M PKTUSB 0"),
        ("set_bandwidth", (2400,), "M USB 2400"),
        ("set_rf_level", (50,), "L RFPOWER 0.5"),
        ("set_tuner", (True,), "U TUNER 1"),
        ("set_ptt", (True,), "T 1"),
    )

    def test_rprt_0_is_success(self):
        for name, args, command in self.SETTERS:
            with self.subTest(name):
                radio = make_rigctld("RPRT 0")
                self.assertTrue(getattr(radio, name)(*args))
                radio.send_command.assert_called_once_with(command, raw=True, priority=True)
                radio.log.warning.assert_not_called()
                self.assertTrue(radio.connected)

    def test_error_reply_is_failure(self):
        for reply in ("RPRT -1", None, ""):
            for name, args, command in self.SETTERS:
                with self.subTest(name, reply=reply):
                    radio = make_rigctld(reply)
                    self.assertFalse(getattr(radio, name)(*args))
                    radio.log.warning.assert_called_once()
                    self.assertEqual(radio.log.warning.call_args.kwargs, {"command": command, "reply": reply})

    def test_failed_set_keeps_the_last_value(self):
        radio = make_rigctld("RPRT -9")
        radio.parameters["frequency"] = 7050000
        radio.set_frequency(14093000)
        self.assertEqual(radio.parameters["frequency"], 7050000)


class TestRigctldPollErrors(unittest.TestCase):
    def test_repeated_error_logged_once(self):
        radio = make_rigctld("garbage", "garbage", "14093000", "garbage")
        for _ in range(2):
            radio.get_frequency()
        self.assertEqual(radio.log.warning.call_count, 1)
        self.assertEqual(radio.log.debug.call_count, 1)
        # works again, then fails: logged again
        radio.get_frequency()
        self.assertEqual(radio.parameters["frequency"], 14093000)
        radio.get_frequency()
        self.assertEqual(radio.log.warning.call_count, 2)

    def test_a_different_error_is_logged(self):
        radio = make_rigctld()
        radio.poll_failed("SWR", ValueError("a"))
        radio.poll_failed("SWR", ValueError("b"))
        radio.poll_failed("ALC", ValueError("b"))
        self.assertEqual(radio.log.warning.call_count, 3)


class TestOtherBackendResults(unittest.TestCase):
    def test_flrig_not_connected(self):
        radio = flrig.radio.__new__(flrig.radio)
        radio.connected = False
        radio.parameters = {}
        for name, args in (
            ("set_frequency", (1,)),
            ("set_mode", ("USB",)),
            ("set_bandwidth", (1,)),
            ("set_rf_level", (1,)),
            ("set_tuner", (True,)),
            ("set_ptt", (False,)),
        ):
            with self.subTest(name):
                self.assertIs(getattr(radio, name)(*args), False)

    def test_flrig_connected(self):
        radio = flrig.radio.__new__(flrig.radio)
        radio.connected = True
        radio.parameters = {}
        radio.logger = mock.Mock()
        radio.server = mock.Mock()
        radio.server.rig.get_maxpwr.return_value = "100"
        self.assertIs(radio.set_ptt(False), True)
        self.assertIs(radio.set_rf_level(50), True)
        radio.server.rig.set_power.assert_called_once_with(50)
        radio.server.rig.set_mode.side_effect = OSError("gone")
        self.assertIs(radio.set_mode("USB"), False)

    def test_serial_ptt(self):
        radio = serial_ptt.radio.__new__(serial_ptt.radio)
        radio.parameters = {"ptt": False}
        radio.log = mock.Mock()
        radio.serial_comport = "COM9"
        radio.serial_rts = "ON"
        radio.serial_dtr = "OFF"
        radio.serial_connection = mock.Mock()
        self.assertIs(radio.set_ptt(True), True)
        self.assertTrue(radio.serial_connection.rts)
        self.assertFalse(radio.serial_connection.dtr)
        self.assertIs(radio.set_ptt(False), True)
        radio.serial_connection = None
        self.assertIs(radio.set_ptt(True), False)
        self.assertIsNone(radio.set_frequency(14093000))


class TestTuneRoute(unittest.TestCase):
    def test_stop(self):
        ctx = types.SimpleNamespace(rf_modem=mock.Mock())
        with structlog.testing.capture_logs() as logs:
            asyncio.run(radio_api.post_radio_tune({"enable_tuning": False}, ctx))
        ctx.rf_modem.stop_sine.assert_called_once()
        (entry,) = radio_logs(logs)
        self.assertEqual((entry["action"], entry["enable"], entry["log_level"]), ("tune", False, "info"))

    def test_modem_not_running(self):
        ctx = types.SimpleNamespace(state_manager=types.SimpleNamespace(is_modem_running=False))
        with structlog.testing.capture_logs() as logs:
            with self.assertRaises(HTTPException):
                asyncio.run(radio_api.post_radio_tune({"enable_tuning": True}, ctx))
        (entry,) = radio_logs(logs)
        self.assertEqual((entry["action"], entry["log_level"]), ("tune", "warning"))


class TestRadioControlLogFile(unittest.TestCase):
    def test_written_to_log_file(self):
        # setup_logging configures logging for the whole process, so run it in its own
        script = (
            "import sys\n"
            "from unittest import mock\n"
            "from freedata_server.log_handler import setup_logging\n"
            "from freedata_server.radio_manager import RadioManager\n"
            "setup_logging(sys.argv[1], 'INFO')\n"
            "manager = RadioManager.__new__(RadioManager)\n"
            "manager.ctx = mock.Mock()\n"
            "manager.radiocontrol = 'rigctld'\n"
            "manager.radio = mock.Mock(**{'set_frequency.return_value': True, 'set_mode.return_value': False})\n"
            "manager.set_frequency(14093000)\n"
            "manager.set_mode('PKTUSB')\n"
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
        self.assertIn("[RADIO] Radio control applied", logged)
        self.assertIn("frequency=14093000", logged)
        self.assertIn("[RADIO] Radio control failed", logged)
        self.assertIn("mode=PKTUSB", logged)


if __name__ == "__main__":
    unittest.main()
