"""Frequency presets of the GUI's frequency selection: checks, config storage,
moving the list of earlier versions over, schedules and the API."""

import asyncio
import datetime
import json
import os
import queue
import shutil
import tempfile
import types
import unittest

import structlog.testing
from fastapi import FastAPI
from fastapi.testclient import TestClient

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import frequency_list, install_paths
from freedata_server.api import radio as radio_api
from freedata_server.api.config import post_config
from freedata_server.config import CONFIG

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

SCHEDULE = {"days": ["sat", "sun"], "start": "00:00", "end": "00:00", "from": "", "to": ""}


def utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def events(logs):
    return [(entry["log_level"], entry["event"]) for entry in logs]


def write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content if isinstance(content, str) else json.dumps(content))


def entry(frequency, notes="", band="", schedule=None):
    return {"frequency": frequency, "band": band, "notes": notes, "schedule": schedule}


class TempConfig(unittest.TestCase):
    """A config file of its own in a temporary directory, from the template."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_file = os.path.join(self.tmp.name, "config.ini")
        self.user_file = os.path.join(self.tmp.name, "frequencies.json")
        shutil.copyfile(testenv.CONFIG_TEMPLATE, self.config_file)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        ctx = types.SimpleNamespace(modem_service=queue.Queue())
        ctx.config_manager = CONFIG(ctx, self.config_file)
        return ctx.config_manager


class TestDefaultFile(unittest.TestCase):
    def test_default_file_location(self):
        self.assertEqual(frequency_list.DEFAULT_FILE, install_paths.data_dir("frequencies.json"))
        self.assertTrue(os.path.isfile(frequency_list.DEFAULT_FILE))

    def test_default_file_content(self):
        entries = frequency_list.read_file(frequency_list.DEFAULT_FILE)
        self.assertEqual([(e["frequency"], e["notes"], e["band"]) for e in entries], EXPECTED_DEFAULT)

    def test_default_fills_all_slots(self):
        self.assertEqual(len(EXPECTED_DEFAULT), frequency_list.SLOT_COUNT)
        self.assertNotIn(None, frequency_list.default_slots())


class TestParseSlots(unittest.TestCase):
    def test_optional_fields_default_to_empty(self):
        slots = frequency_list.parse_slots([{"frequency": 3573000}])
        self.assertEqual(slots[0], entry(3573000))

    def test_frequency_limit_allowed(self):
        slots = frequency_list.parse_slots([{"frequency": frequency_list.MAX_FREQUENCY}])
        self.assertEqual(slots[0]["frequency"], frequency_list.MAX_FREQUENCY)

    def test_short_list_filled_with_empty_slots(self):
        slots = frequency_list.parse_slots([None, {"frequency": 1}])
        self.assertEqual(len(slots), frequency_list.SLOT_COUNT)
        self.assertEqual(slots[:2], [None, entry(1)])
        self.assertEqual(slots[2:], [None] * (frequency_list.SLOT_COUNT - 2))

    def test_extra_fields_dropped_and_text_trimmed(self):
        slots = frequency_list.parse_slots([{"frequency": 1, "notes": " a ", "band": "b", "x": 1}])
        self.assertEqual(slots[0], entry(1, "a", "b"))

    def test_schedule_normalized(self):
        schedule = {"days": ["Sun", "sat"], "start": "12:00", "end": "11:59", "to": "2026-11-29"}
        slots = frequency_list.parse_slots([{"frequency": 1, "schedule": schedule}])
        self.assertEqual(
            slots[0]["schedule"],
            {"days": ["sat", "sun"], "start": "12:00", "end": "11:59", "from": "", "to": "2026-11-29"},
        )

    def test_invalid(self):
        too_long = "x" * (frequency_list.MAX_NOTES_LENGTH + 1)
        for data in (
            {"frequency": 14093000},
            [{"frequency": 1}] * (frequency_list.SLOT_COUNT + 1),
            [14093000],
            [{"notes": "no frequency"}],
            [{"frequency": "14093000"}],
            [{"frequency": 14093000.5}],
            [{"frequency": 0}],
            [{"frequency": -7053000}],
            [{"frequency": frequency_list.MAX_FREQUENCY + 1}],
            [{"frequency": 10**4000}],
            [{"frequency": True}],
            [{"frequency": 14093000, "notes": 5}],
            [{"frequency": 14093000, "band": None}],
            [{"frequency": 14093000, "notes": too_long}],
            [{"frequency": 1, "schedule": "weekends"}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "days": ["saturday"]}}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "days": "sat"}}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "start": "24:00"}}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "end": "9:00"}}],
            [{"frequency": 1, "schedule": {"days": []}}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "from": "2026-13-01"}}],
            [{"frequency": 1, "schedule": {**SCHEDULE, "from": "2026-11-02", "to": "2026-11-01"}}],
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                frequency_list.parse_slots(data)


class TestScheduleState(unittest.TestCase):
    # 2026-11-21 is a Saturday
    def state(self, schedule, now):
        return frequency_list.schedule_state(frequency_list.parse_schedule(schedule), now)

    def test_no_schedule(self):
        self.assertIsNone(frequency_list.schedule_state(None))

    def test_active_window(self):
        state = self.state({"days": ["sat"], "start": "12:00", "end": "18:00"}, utc(2026, 11, 21, 13, 0))
        self.assertEqual(
            state, {"state": "active", "start": "2026-11-21T12:00:00+00:00", "end": "2026-11-21T18:00:00+00:00"}
        )

    def test_window_end_is_not_active(self):
        state = self.state({"days": ["sat"], "start": "12:00", "end": "18:00"}, utc(2026, 11, 21, 18, 0))
        self.assertEqual(state["state"], "upcoming")
        self.assertEqual(state["start"], "2026-11-28T12:00:00+00:00")

    def test_window_over_midnight_still_active_next_day(self):
        state = self.state({"days": ["fri"], "start": "22:00", "end": "02:00"}, utc(2026, 11, 21, 1, 0))
        self.assertEqual(state["state"], "active")
        self.assertEqual(state["start"], "2026-11-20T22:00:00+00:00")
        self.assertEqual(state["end"], "2026-11-21T02:00:00+00:00")

    def test_equal_times_are_a_full_day(self):
        state = self.state(SCHEDULE, utc(2026, 11, 22, 23, 59))
        self.assertEqual(state["state"], "active")
        self.assertEqual(state["end"], "2026-11-23T00:00:00+00:00")

    def test_every_day_when_no_days(self):
        state = self.state({"days": [], "start": "08:00", "end": "09:00"}, utc(2026, 11, 18, 10, 0))
        self.assertEqual(state["state"], "upcoming")
        self.assertEqual(state["start"], "2026-11-19T08:00:00+00:00")

    def test_one_off_event_ended(self):
        schedule = {"days": [], "start": "00:00", "end": "00:00", "from": "2026-09-26", "to": "2026-09-27"}
        self.assertEqual(self.state(schedule, utc(2026, 11, 18, 10, 0)), {"state": "ended", "start": None, "end": None})

    def test_ended_on_its_last_day(self):
        schedule = {"days": [], "start": "08:00", "end": "09:00", "to": "2026-11-18"}
        self.assertEqual(self.state(schedule, utc(2026, 11, 18, 9, 30))["state"], "ended")

    def test_far_ahead_is_idle(self):
        schedule = {"days": [], "start": "00:00", "end": "00:00", "from": "2027-02-13", "to": "2027-02-14"}
        self.assertEqual(self.state(schedule, utc(2026, 11, 18, 10, 0)), {"state": "idle", "start": None, "end": None})

    def test_days_outside_dates_never_start(self):
        # from Monday to Wednesday, but only on Saturdays
        schedule = {"days": ["sat"], "start": "00:00", "end": "01:00", "from": "2026-11-16", "to": "2026-11-18"}
        self.assertEqual(self.state(schedule, utc(2026, 11, 16, 0, 0))["state"], "ended")

    def test_picker_entries(self):
        slots = frequency_list.parse_slots([None, {"frequency": 1, "schedule": SCHEDULE}, {"frequency": 2}])
        entries = frequency_list.picker_entries(slots, utc(2026, 11, 21, 13, 0))
        self.assertEqual([(e["slot"], e["frequency"]) for e in entries], [(1, 1), (2, 2)])
        self.assertEqual(entries[0]["schedule_state"]["state"], "active")
        self.assertIsNone(entries[1]["schedule_state"])


class TestConfigStorage(TempConfig):
    def test_new_config_gets_default_list(self):
        with structlog.testing.capture_logs() as logs:
            config = self.config()
        self.assertIn(("info", "[FRQ] Frequency presets set to the default list"), events(logs))
        slots = frequency_list.config_slots(config.config)
        self.assertEqual([(s["frequency"], s["notes"], s["band"]) for s in slots], EXPECTED_DEFAULT)

    def test_list_of_earlier_versions_moved_over_once(self):
        write(self.user_file, [{"frequency": 3573000, "notes": "test", "band": "80m"}])
        with structlog.testing.capture_logs() as logs:
            config = self.config()
        self.assertIn(("info", "[FRQ] Frequency presets taken from the frequency list file"), events(logs))
        slots = frequency_list.config_slots(config.config)
        self.assertEqual(slots[0], entry(3573000, "test", "80m"))
        self.assertEqual(slots[1:], [None] * (frequency_list.SLOT_COUNT - 1))
        # from now on the config wins
        write(self.user_file, [{"frequency": 7000000}])
        self.assertEqual(frequency_list.config_slots(self.config().config)[0]["frequency"], 3573000)

    def test_list_longer_than_slots_cut_and_logged(self):
        write(self.user_file, [{"frequency": f} for f in range(1, frequency_list.SLOT_COUNT + 3)])
        with structlog.testing.capture_logs() as logs:
            config = self.config()
        self.assertIn(
            ("warning", "[FRQ] Frequency list file has more entries than slots, the rest is dropped"), events(logs)
        )
        self.assertEqual(frequency_list.config_slots(config.config)[-1]["frequency"], frequency_list.SLOT_COUNT)

    def test_invalid_list_of_earlier_versions_uses_default(self):
        write(self.user_file, "[")
        with structlog.testing.capture_logs() as logs:
            config = self.config()
        self.assertIn(("warning", "[FRQ] Frequency list file not valid, using the default"), events(logs))
        self.assertEqual(frequency_list.config_slots(config.config)[0]["frequency"], EXPECTED_DEFAULT[0][0])

    def test_cleared_slots_and_special_characters_kept(self):
        config = self.config()
        notes = "Ch #26 # 100% %(x)s ; = [x]\nü"
        slots = [None, entry(27265000, notes, "11m", SCHEDULE)]
        frequency_list.save_slots(config, slots)
        expected = slots + [None] * (frequency_list.SLOT_COUNT - 2)
        self.assertEqual(frequency_list.config_slots(config.config), expected)
        # and so does a fresh read of the file
        self.assertEqual(frequency_list.config_slots(self.config().config), expected)

    def test_all_slots_cleared_stays_cleared(self):
        frequency_list.save_slots(self.config(), [])
        self.assertEqual(frequency_list.config_slots(self.config().config), [None] * frequency_list.SLOT_COUNT)

    def test_invalid_config_value_gives_empty_slots(self):
        config = self.config()
        config.config[frequency_list.SECTION][frequency_list.SETTING] = [{"frequency": "x"}]
        with structlog.testing.capture_logs() as logs:
            slots = frequency_list.config_slots(config.config)
        self.assertEqual(slots, [None] * frequency_list.SLOT_COUNT)
        self.assertEqual(events(logs), [("warning", "[FRQ] Frequency presets in the config not valid")])

    def test_check_frequency_list(self):
        config = self.config()
        with structlog.testing.capture_logs() as logs:
            frequency_list.check_frequency_list(config.config)
        self.assertEqual(events(logs), [("info", "[FRQ] Frequency presets loaded")])
        self.assertEqual(logs[0]["filled"], frequency_list.SLOT_COUNT)

    def test_settings_save_leaves_presets_alone(self):
        config = self.config()
        ctx = config.ctx
        frequency_list.save_slots(config, [entry(3573000)])
        stale = config.read()
        stale[frequency_list.SECTION][frequency_list.SETTING] = [entry(7000000)]
        stale["STATION"]["mycall"] = "AA1AAA"
        stale["STATION"]["mygrid"] = "JN48ea"
        asyncio.run(post_config(stale, ctx))
        slots = frequency_list.config_slots(config.read())
        self.assertEqual(slots[0]["frequency"], 3573000)


class TestChangeLog(TempConfig):
    def save(self, slots, **kwargs):
        with structlog.testing.capture_logs() as logs:
            frequency_list.save_slots(self.config_manager, slots, **kwargs)
        return logs

    def setUp(self):
        super().setUp()
        self.config_manager = self.config()
        self.save([entry(3573000, "FT8", "80m"), entry(7053000, "EU / US", "40m")])

    def test_added_changed_cleared(self):
        logs = self.save([None, entry(7074000, "FT8", "40m", SCHEDULE), entry(14080000, "Contest #1", "20m")])
        self.assertEqual(
            [(e["event"], e["slot"], e["old"], e["new"]) for e in logs[:-1]],
            [
                ("[FRQ] Frequency preset cleared", 1, "3.573 MHz 80m 'FT8'", "empty"),
                (
                    "[FRQ] Frequency preset changed",
                    2,
                    "7.053 MHz 40m 'EU / US'",
                    "7.074 MHz 40m 'FT8' sat,sun 00:00-00:00 UTC",
                ),
                ("[FRQ] Frequency preset added", 3, "empty", "14.080 MHz 20m 'Contest #1'"),
            ],
        )
        self.assertEqual(logs[-1]["event"], "[FRQ] Frequency presets saved")
        self.assertEqual((logs[-1]["reason"], logs[-1]["changed"], logs[-1]["filled"]), ("edit", 3, 2))

    def test_nothing_changed(self):
        logs = self.save([entry(3573000, "FT8", "80m"), entry(7053000, "EU / US", "40m")])
        self.assertEqual(events(logs), [("info", "[FRQ] Frequency presets saved")])
        self.assertEqual(logs[0]["changed"], 0)

    def test_moved_slots_logged_as_changes(self):
        logs = self.save([entry(7053000, "EU / US", "40m"), entry(3573000, "FT8", "80m")])
        self.assertEqual(
            [(e["event"], e["slot"]) for e in logs[:-1]],
            [("[FRQ] Frequency preset changed", 1), ("[FRQ] Frequency preset changed", 2)],
        )

    def test_reset_reason(self):
        logs = self.save(frequency_list.default_slots(), reason="reset")
        self.assertEqual(logs[-1]["reason"], "reset")
        self.assertEqual(logs[-1]["changed"], frequency_list.SLOT_COUNT)

    def test_describe_dates_and_every_day(self):
        schedule = {"days": [], "start": "22:00", "end": "02:00", "from": "2026-11-28", "to": ""}
        self.assertEqual(
            frequency_list.describe(entry(50313000, schedule=schedule)),
            "50.313 MHz every day 22:00-02:00 UTC 2026-11-28 to ...",
        )


class TestFrequencyListApi(TempConfig):
    def setUp(self):
        super().setUp()
        self.ctx = types.SimpleNamespace(modem_service=queue.Queue())
        self.ctx.config_manager = CONFIG(self.ctx, self.config_file)
        app = FastAPI()
        app.include_router(radio_api.router, prefix="/radio")
        app.state.ctx = self.ctx
        self.client = TestClient(app)

    def test_get(self):
        body = self.client.get("/radio/frequencies").json()
        self.assertEqual(body["slot_count"], frequency_list.SLOT_COUNT)
        self.assertEqual(len(body["slots"]), frequency_list.SLOT_COUNT)
        self.assertEqual([e["frequency"] for e in body["frequencies"]], [e[0] for e in EXPECTED_DEFAULT])
        self.assertEqual([e["slot"] for e in body["frequencies"]], list(range(frequency_list.SLOT_COUNT)))

    def test_save_and_read_back(self):
        slots = [None, {"frequency": 3573000, "band": "80m", "notes": "FT8", "schedule": SCHEDULE}]
        with structlog.testing.capture_logs() as logs:
            response = self.client.post("/radio/frequencies", json={"slots": slots})
        self.assertEqual(response.status_code, 200)
        # the default list filled all slots: slot 2 changed, the others cleared
        self.assertEqual(
            events(logs),
            [("info", "[FRQ] Frequency preset cleared")]
            + [("info", "[FRQ] Frequency preset changed")]
            + [("info", "[FRQ] Frequency preset cleared")] * (frequency_list.SLOT_COUNT - 2)
            + [("info", "[FRQ] Frequency presets saved")],
        )
        self.assertEqual((logs[-1]["reason"], logs[-1]["changed"], logs[-1]["filled"]), ("edit", 10, 1))
        body = self.client.get("/radio/frequencies").json()
        self.assertIsNone(body["slots"][0])
        self.assertEqual(body["slots"][1]["notes"], "FT8")
        self.assertEqual(len(body["frequencies"]), 1)
        self.assertEqual(body["frequencies"][0]["slot"], 1)
        self.assertIn(body["frequencies"][0]["schedule_state"]["state"], ("active", "upcoming"))

    def test_invalid_save_rejected_and_nothing_written(self):
        before = self.client.get("/radio/frequencies").json()
        for body in ({"slots": [{"frequency": -1}]}, {"slots": "x"}, {}):
            with self.subTest(body=body):
                response = self.client.post("/radio/frequencies", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json()["detail"])
        self.assertEqual(self.client.get("/radio/frequencies").json(), before)

    def test_empty_band_filled_from_band_plan(self):
        slots = [{"frequency": 3573000}, {"frequency": 14093000, "band": "mine"}, {"frequency": 100000000}]
        body = self.client.post("/radio/frequencies", json={"slots": slots}).json()
        self.assertEqual([s["band"] for s in body["slots"][:3]], ["80m", "mine", ""])

    def test_reset(self):
        self.client.post("/radio/frequencies", json={"slots": []})
        self.assertEqual(self.client.get("/radio/frequencies").json()["frequencies"], [])
        body = self.client.post("/radio/frequencies/reset").json()
        self.assertEqual([e["frequency"] for e in body["frequencies"]], [e[0] for e in EXPECTED_DEFAULT])


if __name__ == "__main__":
    unittest.main()
