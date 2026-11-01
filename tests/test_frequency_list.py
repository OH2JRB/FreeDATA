"""Frequency list of the GUI's frequency selection: file loading, fallback and API."""

import json
import os
import tempfile
import unittest
from unittest import mock

import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import frequency_list, install_paths
from freedata_server.api import radio as radio_api
from freedata_server.context import AppContext

# the presets that were hard-coded in the GUI before the list moved to a file
EXPECTED_DEFAULT = [
    (50616000, "EU / US", "6m"),
    (50308000, "US", "6m"),
    (28093000, "EU / US", "10m"),
    (27265000, "Ch 26", "11m"),
    (27245000, "Ch 25", "11m"),
    (24908000, "EU / US", "12m"),
    (21093000, "EU / US", "15m"),
    (18106000, "EU / US", "17m"),
    (14093000, "EU / US", "20m"),
    (7053000, "EU / US", "40m"),
]


def events(logs):
    return [(entry["log_level"], entry["event"]) for entry in logs]


def write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content if isinstance(content, str) else json.dumps(content))


class TestDefaultFile(unittest.TestCase):
    def test_default_file_location(self):
        self.assertEqual(frequency_list.DEFAULT_FILE, install_paths.data_dir("frequencies.json"))
        self.assertTrue(os.path.isfile(frequency_list.DEFAULT_FILE))

    def test_default_file_content(self):
        entries = frequency_list.read_file(frequency_list.DEFAULT_FILE)
        self.assertEqual([(e["frequency"], e["notes"], e["band"]) for e in entries], EXPECTED_DEFAULT)


class TestParseEntries(unittest.TestCase):
    def test_optional_fields_default_to_empty(self):
        self.assertEqual(
            frequency_list.parse_entries([{"frequency": 3573000}]),
            [{"frequency": 3573000, "notes": "", "band": ""}],
        )

    def test_extra_fields_dropped(self):
        entries = frequency_list.parse_entries([{"frequency": 1, "notes": "a", "band": "b", "x": 1}])
        self.assertEqual(entries, [{"frequency": 1, "notes": "a", "band": "b"}])

    def test_invalid(self):
        for data in (
            {"frequency": 14093000},
            [14093000],
            [{"notes": "no frequency"}],
            [{"frequency": "14093000"}],
            [{"frequency": 14093000.5}],
            [{"frequency": 0}],
            [{"frequency": -7053000}],
            [{"frequency": True}],
            [{"frequency": 14093000, "notes": 5}],
            [{"frequency": 14093000, "band": None}],
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                frequency_list.parse_entries(data)


class TestLoadFrequencyList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_file = os.path.join(self.tmp.name, "config.ini")
        self.user_file = os.path.join(self.tmp.name, "frequencies.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_user_file_next_to_config(self):
        self.assertEqual(frequency_list.user_file(self.config_file), self.user_file)

    def test_missing_user_file_uses_default(self):
        entries, source = frequency_list.load_frequency_list(self.config_file)
        self.assertEqual(source, frequency_list.DEFAULT_FILE)
        self.assertEqual(len(entries), len(EXPECTED_DEFAULT))

    def test_user_file_replaces_default(self):
        write(self.user_file, [{"frequency": 3573000, "notes": "test", "band": "80m"}])
        entries, source = frequency_list.load_frequency_list(self.config_file)
        self.assertEqual(source, self.user_file)
        self.assertEqual(entries, [{"frequency": 3573000, "notes": "test", "band": "80m"}])

    def test_invalid_user_file_falls_back_and_logs(self):
        for content in ("[", '{"frequency": 1}', '[{"frequency": "x"}]'):
            with self.subTest(content=content):
                write(self.user_file, content)
                with structlog.testing.capture_logs() as logs:
                    entries, source = frequency_list.load_frequency_list(self.config_file)
                self.assertEqual(source, frequency_list.DEFAULT_FILE)
                self.assertEqual(len(entries), len(EXPECTED_DEFAULT))
                self.assertEqual(events(logs), [("warning", "[FRQ] Frequency list not valid, using the default")])

    def test_no_readable_file_gives_empty_list(self):
        missing_default = os.path.join(self.tmp.name, "data", "frequencies.json")
        with structlog.testing.capture_logs() as logs:
            self.assertEqual(frequency_list.load_frequency_list(self.config_file, missing_default), ([], None))
        self.assertEqual(events(logs), [("error", "[FRQ] Default frequency list not loaded, the list is empty")])

    def test_check_frequency_list(self):
        with structlog.testing.capture_logs() as logs:
            self.assertEqual(frequency_list.check_frequency_list(self.config_file), frequency_list.DEFAULT_FILE)
        self.assertEqual(
            events(logs),
            [("info", "[FRQ] No user frequency list, using the default"), ("info", "[FRQ] Frequency list loaded")],
        )
        write(self.user_file, [{"frequency": 3573000}])
        with structlog.testing.capture_logs() as logs:
            self.assertEqual(frequency_list.check_frequency_list(self.config_file), self.user_file)
        self.assertEqual(logs[0]["path"], self.user_file)
        self.assertEqual(logs[0]["entries"], 1)


class TestFrequencyListApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        app = FastAPI()
        app.include_router(radio_api.router, prefix="/radio")
        app.state.ctx = cls.ctx
        cls.client = TestClient(app)
        cls.user_file = frequency_list.user_file(cls.ctx.config_manager.config_name)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def tearDown(self):
        if os.path.exists(self.user_file):
            os.remove(self.user_file)

    def test_default_list(self):
        response = self.client.get("/radio/frequencies")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "default")
        self.assertEqual([e["frequency"] for e in body["frequencies"]], [e[0] for e in EXPECTED_DEFAULT])

    def test_user_list_read_on_every_request(self):
        write(self.user_file, [{"frequency": 3573000, "notes": "test", "band": "80m"}])
        body = self.client.get("/radio/frequencies").json()
        self.assertEqual(
            body, {"frequencies": [{"frequency": 3573000, "notes": "test", "band": "80m"}], "source": "user"}
        )
        write(self.user_file, "not json")
        with structlog.testing.capture_logs() as logs:
            body = self.client.get("/radio/frequencies").json()
        self.assertEqual(body["source"], "default")
        self.assertEqual(events(logs), [("warning", "[FRQ] Frequency list not valid, using the default")])

    def test_no_list(self):
        with mock.patch.object(frequency_list, "read_file", side_effect=OSError("unreadable")):
            body = self.client.get("/radio/frequencies").json()
        self.assertEqual(body, {"frequencies": [], "source": "none"})


if __name__ == "__main__":
    unittest.main()
