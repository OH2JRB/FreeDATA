"""Frequency presets of the GUI's frequency selection, kept in the config file.

The presets are FREQUENCIES.presets in config.ini: a JSON list of SLOT_COUNT
slots. A slot is either null (empty, cleared by the user) or an entry

    {"frequency": 14093000, "band": "20m", "notes": "EU / US", "schedule": null}

with the frequency in Hz (a positive integer), a band label and notes
(strings), and an optional schedule: when the frequency is in use, for
example during a contest. The schedule is for information only; nothing
tunes the radio by it.

    {"days": ["sat", "sun"], "start": "00:00", "end": "00:00",
     "from": "2026-09-26", "to": "2026-09-27"}

- days: the UTC weekdays a window starts on; empty means every day
- start, end: UTC "HH:MM"; an end at or before the start ends the next day,
  so start == end is a 24 h window
- from, to: optional ISO dates ("" for none) limiting the days a window may
  start on; without them the schedule repeats every week

When the config has no presets yet, they are filled once from the user's
frequencies.json next to the config file (the list format of earlier
versions), or from the default list shipped as data/frequencies.json.
"""

import datetime
import json
import os
import re

import structlog

from freedata_server import bandplan, install_paths

log = structlog.get_logger("frequency_list")

FILE_NAME = "frequencies.json"
DEFAULT_FILE = install_paths.data_dir(FILE_NAME)

SECTION = "FREQUENCIES"
SETTING = "presets"
SLOT_COUNT = 10
# a plausibility limit: the amateur bands of the ITU table end at 250 GHz
MAX_FREQUENCY = 300_000_000_000
MAX_BAND_LENGTH = 20
MAX_NOTES_LENGTH = 200
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# how far ahead a window counts as "upcoming"
UPCOMING_DAYS = 8

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def user_file(config_file):
    """Path of the user frequency list of earlier versions: next to the config file."""
    return os.path.join(os.path.dirname(os.path.abspath(config_file)), FILE_NAME)


def _string(entry, key, index, max_length):
    value = entry.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"slot {index}: {key} must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"slot {index}: {key} is longer than {max_length} characters")
    return value


def _date(value, index, key):
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError(f"slot {index}: schedule {key} must be a date YYYY-MM-DD")
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"slot {index}: schedule {key} must be a date YYYY-MM-DD") from None


def parse_schedule(schedule, index=0):
    """Check a schedule and return it normalized, or None for no schedule."""
    if schedule is None:
        return None
    if not isinstance(schedule, dict):
        raise ValueError(f"slot {index}: schedule must be an object or null")
    days = schedule.get("days", [])
    if not isinstance(days, list) or not all(isinstance(day, str) for day in days):
        raise ValueError(f"slot {index}: schedule days must be a list of weekdays")
    days = {day.strip().lower() for day in days}
    if not days <= set(DAYS):
        raise ValueError(f"slot {index}: schedule days must be of {', '.join(DAYS)}")
    times = {}
    for key in ("start", "end"):
        value = schedule.get(key)
        if not isinstance(value, str) or not _TIME.match(value.strip()):
            raise ValueError(f"slot {index}: schedule {key} must be a UTC time HH:MM")
        times[key] = value.strip()
    first = _date(schedule.get("from"), index, "from")
    last = _date(schedule.get("to"), index, "to")
    if first and last and first > last:
        raise ValueError(f"slot {index}: schedule from is after to")
    return {
        "days": [day for day in DAYS if day in days],
        "start": times["start"],
        "end": times["end"],
        "from": first,
        "to": last,
    }


def parse_entry(entry, index=0):
    """Check one preset and return it with defaults filled in."""
    if not isinstance(entry, dict):
        raise ValueError(f"slot {index} is not an object")
    frequency = entry.get("frequency")
    # bool is an int subclass, but true/false is not a frequency
    if not isinstance(frequency, int) or isinstance(frequency, bool) or frequency <= 0:
        raise ValueError(f"slot {index}: frequency must be a positive integer in Hz")
    if frequency > MAX_FREQUENCY:
        raise ValueError(f"slot {index}: frequency must be at most {MAX_FREQUENCY} Hz (300 GHz)")
    return {
        "frequency": frequency,
        "band": _string(entry, "band", index, MAX_BAND_LENGTH),
        "notes": _string(entry, "notes", index, MAX_NOTES_LENGTH),
        "schedule": parse_schedule(entry.get("schedule"), index),
    }


def parse_slots(data):
    """Check a list of slots and return exactly SLOT_COUNT of them.

    null is an empty slot. A shorter list is filled up with empty slots; a
    longer one is an error, so nothing is dropped without notice.
    Raises ValueError if the list or any slot is not valid.
    """
    if not isinstance(data, list):
        raise ValueError("the presets must be a list")
    if len(data) > SLOT_COUNT:
        raise ValueError(f"at most {SLOT_COUNT} presets")
    slots = [None if entry is None else parse_entry(entry, index) for index, entry in enumerate(data)]
    return slots + [None] * (SLOT_COUNT - len(slots))


def parse_entries(data):
    """Check a frequency list file of earlier versions (a list of entries)."""
    if not isinstance(data, list):
        raise ValueError("the file must hold a JSON array")
    return [parse_entry(entry, index) for index, entry in enumerate(data)]


def read_file(path):
    """Read and check one frequency list file."""
    with open(path, encoding="utf-8") as fh:
        return parse_entries(json.load(fh))


def _to_slots(entries):
    entries = entries[:SLOT_COUNT]
    return entries + [None] * (SLOT_COUNT - len(entries))


def default_slots(default_file=DEFAULT_FILE):
    """The shipped default list as slots, or all empty if it cannot be read."""
    try:
        return _to_slots(read_file(default_file))
    except (OSError, ValueError) as err:
        log.error("[FRQ] Default frequency list not loaded", path=default_file, e=err)
        return [None] * SLOT_COUNT


def initial_slots(config_file, default_file=DEFAULT_FILE):
    """The presets for a config that has none yet.

    From the user's frequencies.json of earlier versions if there is a valid
    one, otherwise the shipped default. Entries beyond SLOT_COUNT are dropped
    (logged).
    """
    path = user_file(config_file)
    if os.path.isfile(path):
        try:
            entries = read_file(path)
        except (OSError, ValueError) as err:
            log.warning("[FRQ] Frequency list file not valid, using the default", path=path, e=err)
        else:
            if len(entries) > SLOT_COUNT:
                log.warning(
                    "[FRQ] Frequency list file has more entries than slots, the rest is dropped",
                    path=path,
                    entries=len(entries),
                    slots=SLOT_COUNT,
                )
            log.info("[FRQ] Frequency presets taken from the frequency list file", path=path)
            return _to_slots(entries)
    log.info("[FRQ] Frequency presets set to the default list")
    return default_slots(default_file)


def config_slots(config):
    """The presets from a read config, checked; all empty if they are not valid."""
    presets = (config or {}).get(SECTION, {}).get(SETTING, [])
    try:
        return parse_slots(presets)
    except ValueError as err:
        log.warning("[FRQ] Frequency presets in the config not valid", e=err)
        return [None] * SLOT_COUNT


def band_name(frequency, region=0):
    """Name of the amateur band holding the frequency ("20m"), from the IARU
    band plan of the region, or of any region for 0; "" if none."""
    try:
        regions = [region] if region in (1, 2, 3) else (1, 2, 3)
        for r in regions:
            band = bandplan.find_band(r, frequency)
            if band is not None:
                return band["name"]
    except (OSError, ValueError, KeyError) as err:
        log.warning("[FRQ] Band plan not available for the band label", e=err)
    return ""


def describe(entry):
    """One preset as text for the log: "14.093 MHz 20m 'EU / US' sat,sun 00:00-00:00 UTC"."""
    if entry is None:
        return "empty"
    parts = [re.sub(r"0{1,3}$", "", f"{entry['frequency'] / 1e6:.6f}") + " MHz"]
    if entry["band"]:
        parts.append(entry["band"])
    if entry["notes"]:
        parts.append(repr(entry["notes"]))
    schedule = entry["schedule"]
    if schedule is not None:
        days = ",".join(schedule["days"]) or "every day"
        parts.append(f"{days} {schedule['start']}-{schedule['end']} UTC")
        if schedule["from"] or schedule["to"]:
            parts.append(f"{schedule['from'] or '...'} to {schedule['to'] or '...'}")
    return " ".join(parts)


def log_changes(old, new, reason):
    """Log each slot that differs between old and new (slot numbers from 1,
    as in the GUI), then a summary line. Returns the number of changed slots."""
    changed = 0
    for index, (before, after) in enumerate(zip(old, new)):
        if before == after:
            continue
        changed += 1
        if before is None:
            kind = "added"
        elif after is None:
            kind = "cleared"
        else:
            kind = "changed"
        log.info(f"[FRQ] Frequency preset {kind}", slot=index + 1, old=describe(before), new=describe(after))
    log.info(
        "[FRQ] Frequency presets saved",
        reason=reason,
        changed=changed,
        filled=sum(slot is not None for slot in new),
    )
    return changed


def save_slots(config_manager, slots, region=0, reason="edit"):
    """Check the slots and write them to the config file. An empty band label
    is filled in from the band plan of the IARU region. Each changed slot is
    logged (log_changes), with `reason` ("edit", "reset") in the summary.

    Returns the slots as written. Raises ValueError if they are not valid,
    OSError if the config could not be written.
    """
    slots = parse_slots(slots)
    for slot in slots:
        if slot is not None and not slot["band"]:
            slot["band"] = band_name(slot["frequency"], region)
    old = config_slots(config_manager.config)
    if not config_manager.write({SECTION: {SETTING: slots}}):
        raise OSError("the config file could not be written")
    log_changes(old, slots, reason)
    return slots


def _window(day, schedule):
    def at(time):
        return datetime.datetime.combine(day, datetime.time.fromisoformat(time), tzinfo=datetime.timezone.utc)

    start, end = at(schedule["start"]), at(schedule["end"])
    if end <= start:
        end += datetime.timedelta(days=1)
    return start, end


def _starts_on(day, schedule):
    if schedule["days"] and DAYS[day.weekday()] not in schedule["days"]:
        return False
    if schedule["from"] and day.isoformat() < schedule["from"]:
        return False
    if schedule["to"] and day.isoformat() > schedule["to"]:
        return False
    return True


def schedule_state(schedule, now=None):
    """Where a schedule is at, at `now` (an aware datetime, default the current time).

    Returns None for no schedule, otherwise {"state", "start", "end"}:
    - active: a window is open now; start and end are that window's
    - upcoming: the next window starts within UPCOMING_DAYS
    - ended: the schedule has a last day and its last window is over
    - idle: none of these (a first day further ahead than UPCOMING_DAYS)
    start and end are ISO UTC times, or None.
    """
    if schedule is None:
        return None
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    today = now.date()
    # from yesterday: a window that started then may still be open
    for offset in range(-1, UPCOMING_DAYS + 1):
        day = today + datetime.timedelta(days=offset)
        if not _starts_on(day, schedule):
            continue
        start, end = _window(day, schedule)
        if end > now:
            state = "active" if start <= now else "upcoming"
            return {"state": state, "start": start.isoformat(), "end": end.isoformat()}
    # no window open or ahead: over for good if no day after the searched ones is left
    if schedule["to"] and schedule["to"] <= (today + datetime.timedelta(days=UPCOMING_DAYS)).isoformat():
        return {"state": "ended", "start": None, "end": None}
    return {"state": "idle", "start": None, "end": None}


def picker_entries(slots, now=None):
    """The filled slots for the frequency selection, in slot order, each with
    its slot index and schedule state."""
    return [
        {**entry, "slot": index, "schedule_state": schedule_state(entry["schedule"], now)}
        for index, entry in enumerate(slots)
        if entry is not None
    ]


def check_frequency_list(config):
    """Log the presets once at server start."""
    slots = config_slots(config)
    log.info("[FRQ] Frequency presets loaded", filled=sum(slot is not None for slot in slots), slots=SLOT_COUNT)
    return slots
