"""Frequency list of the GUI's frequency selection, read from a JSON file.

The default list ships as data/frequencies.json. A user list can be placed as
frequencies.json next to the config file; it replaces the default. The file is
read on every request, so edits show up the next time the GUI asks for it.

File format: a JSON array of entries, each one
    {"frequency": 14093000, "notes": "EU / US", "band": "20m"}
with the frequency in Hz (a positive integer) and optional notes and band
label (strings).
"""

import json
import os

import structlog

from freedata_server import install_paths

log = structlog.get_logger("frequency_list")

FILE_NAME = "frequencies.json"
DEFAULT_FILE = install_paths.data_dir(FILE_NAME)


def user_file(config_file):
    """Path of the user frequency list: next to the config file."""
    return os.path.join(os.path.dirname(os.path.abspath(config_file)), FILE_NAME)


def parse_entries(data):
    """Check a parsed frequency list and return it with defaults filled in.

    Raises ValueError if the list or any entry is not valid.
    """
    if not isinstance(data, list):
        raise ValueError("the file must hold a JSON array")
    entries = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {index} is not an object")
        frequency = entry.get("frequency")
        # bool is an int subclass, but true/false is not a frequency
        if not isinstance(frequency, int) or isinstance(frequency, bool) or frequency <= 0:
            raise ValueError(f"entry {index}: frequency must be a positive integer in Hz")
        notes = entry.get("notes", "")
        band = entry.get("band", "")
        if not isinstance(notes, str) or not isinstance(band, str):
            raise ValueError(f"entry {index}: notes and band must be strings")
        entries.append({"frequency": frequency, "notes": notes, "band": band})
    return entries


def read_file(path):
    """Read and check one frequency list file."""
    with open(path, encoding="utf-8") as fh:
        return parse_entries(json.load(fh))


def load_frequency_list(config_file, default_file=DEFAULT_FILE):
    """Return (entries, source path) of the frequency list.

    Uses the user file next to the config file if there is one, otherwise the
    default file. A user file that cannot be read or is not valid is logged
    and the default is used instead. If the default cannot be read either,
    the list is empty.
    """
    path = user_file(config_file)
    if os.path.isfile(path):
        try:
            return read_file(path), path
        except (OSError, ValueError) as err:
            log.warning("[FRQ] Frequency list not valid, using the default", path=path, e=err)
    try:
        return read_file(default_file), default_file
    except (OSError, ValueError) as err:
        log.error("[FRQ] Default frequency list not loaded, the list is empty", path=default_file, e=err)
        return [], None


def check_frequency_list(config_file, default_file=DEFAULT_FILE):
    """Read the frequency list once at server start and log which one is used."""
    entries, source = load_frequency_list(config_file, default_file)
    if source is not None and source != user_file(config_file):
        log.info("[FRQ] No user frequency list, using the default", user_file=user_file(config_file))
    if source is not None:
        log.info("[FRQ] Frequency list loaded", path=source, entries=len(entries))
    return source
