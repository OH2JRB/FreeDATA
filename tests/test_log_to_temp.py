"""The optional temp-directory log file: switch, file name, control characters
stripped, console logging kept."""

import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from freedata_server.constants import LOG_TO_TEMP_ENV_VAR
from freedata_server.log_handler import (
    log_to_temp_requested,
    strip_control_characters,
    temp_log_filename,
    temp_log_in_config,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestStripControlCharacters(unittest.TestCase):
    def test_removes_ansi_colours(self):
        self.assertEqual(strip_control_characters("\x1b[32minfo\x1b[0m done"), "info done")

    def test_removes_osc_and_short_escapes(self):
        self.assertEqual(strip_control_characters("\x1b]0;title\x07a\x1b]8;;x\x1b\\b\x1bMc"), "abc")

    def test_removes_other_control_characters_keeps_newline_and_tab(self):
        self.assertEqual(strip_control_characters("a\x00b\rc\x07d\x7fe\x85f\n\tg"), "abcdef\n\tg")

    def test_keeps_non_ascii_text(self):
        self.assertEqual(strip_control_characters("Järvenpää ✓ 73"), "Järvenpää ✓ 73")


class TestTempLogFilename(unittest.TestCase):
    def test_named_by_date_and_time_in_temp_dir(self):
        name = temp_log_filename(datetime.datetime(2026, 11, 17, 9, 5, 3))
        self.assertEqual(os.path.dirname(name), tempfile.gettempdir())
        self.assertEqual(os.path.basename(name), "freedata_server_20261117_090503.log")


class TestLogToTempHandler(unittest.TestCase):
    def test_console_and_both_files_written(self):
        # setup_logging configures logging for the whole process, so run it in its own
        script = (
            "import sys, structlog\n"
            "from freedata_server.log_handler import setup_logging\n"
            "setup_logging(sys.argv[1], 'INFO', temp_filename=sys.argv[2])\n"
            "structlog.get_logger('test').info('\\x1b[31mred\\x1b[0m text\\x07', station='OH2ABC\\r')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            log_file = os.path.join(tmp, "freedata.log")
            temp_file = os.path.join(tmp, "freedata_server_20261117_090503.log")
            result = subprocess.run(
                [sys.executable, "-c", script, log_file, temp_file],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("red", result.stdout)
            self.assertIn("\x07", result.stdout)  # console output is left as it was
            with open(log_file, encoding="utf-8") as fh:
                self.assertIn("red", fh.read())
            # read in text mode: the native line ending (CRLF on Windows) is kept
            with open(temp_file, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("red text", content)
            self.assertIn("OH2ABC", content)
            self.assertIn("Logging also to temp file", content)
            self.assertEqual(strip_control_characters(content), content)


class TestLogToTempSwitch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(LOG_TO_TEMP_ENV_VAR, None)

    def test_off_by_default(self):
        self.assertFalse(log_to_temp_requested([]))

    def test_command_line_switch(self):
        self.assertTrue(log_to_temp_requested(["--log-to-temp"]))

    def test_other_arguments_ignored(self):
        self.assertTrue(log_to_temp_requested(["--something", "x", "--log-to-temp"]))
        self.assertFalse(log_to_temp_requested(["--something"]))

    def test_environment_variable(self):
        for value, expected in (("1", True), ("Yes", True), ("true", True), ("0", False), ("", False)):
            os.environ[LOG_TO_TEMP_ENV_VAR] = value
            self.assertEqual(log_to_temp_requested([]), expected, value)


class TestTempLogInConfig(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.ini")
            if text is not None:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
            return temp_log_in_config(path)

    def test_on_and_off(self):
        self.assertTrue(self.read("[STATION]\nenable_log_to_temp = True\n"))
        self.assertFalse(self.read("[STATION]\nenable_log_to_temp = False\n"))

    def test_missing_or_broken_is_off(self):
        self.assertFalse(self.read(None))
        self.assertFalse(self.read("[STATION]\nmycall = OH2JRB\n"))
        self.assertFalse(self.read("[STATION]\nenable_log_to_temp = maybe\n"))
        self.assertFalse(self.read("not an ini file"))


class TestSwitchAtRuntime(unittest.TestCase):
    def test_start_stop_start(self):
        # set_temp_logging changes the logging of the whole process, so run it in its own
        script = (
            "import sys, structlog\n"
            "from freedata_server.log_handler import setup_logging, set_temp_logging, temp_log_path\n"
            "setup_logging('', 'INFO')\n"
            "log = structlog.get_logger('test')\n"
            "assert temp_log_path() == ''\n"
            "log.info('before')\n"
            "assert set_temp_logging(True, sys.argv[1]) == sys.argv[1]\n"
            "assert set_temp_logging(True) == sys.argv[1]  # already on: same file\n"
            "log.info('while on')\n"
            "assert set_temp_logging(False) == ''\n"
            "assert temp_log_path() == ''\n"
            "log.info('after')\n"
            "assert set_temp_logging(True, sys.argv[2]) == sys.argv[2]\n"
            "log.info('second file')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "first.log")
            second = os.path.join(tmp, "second.log")
            result = subprocess.run(
                [sys.executable, "-c", script, first, second],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(first, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("while on", content)
            self.assertIn("Logging to temp file stopped", content)
            self.assertNotIn("before", content)
            self.assertNotIn("after", content)
            with open(second, encoding="utf-8") as fh:
                self.assertIn("second file", fh.read())


if __name__ == "__main__":
    unittest.main()
