"""IARU regional band plans, read from data/iaru_bandplan.json.

Lookup helpers; on_amateur_band() keeps encrypted messages off the amateur
bands (command_message_send.py). Band plans are
voluntary guidance and national rules can be stricter, so a hit here does not
mean a frequency is legal for a given station.
"""

import functools
import json
import os

import structlog

log = structlog.get_logger("bandplan")

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


def _base_dir():
    """Directory that holds data/.

    From source or a pip install, data/ is in this package's directory. A
    Nuitka standalone build puts this module in <dist>/freedata_server/ but the
    --include-data-dir=data=data files in <dist>/data/, so go up one level.
    (__compiled__.containing_dir is the directory that holds the .dist folder,
    one level above <dist>, so it cannot be used for files inside the build.)
    """
    try:
        __compiled__  # type: ignore[name-defined]  # noqa: B018
    except NameError:
        return _MODULE_DIR
    return os.path.dirname(_MODULE_DIR)


BANDPLAN_FILE = os.path.join(_base_dir(), "data", "iaru_bandplan.json")


@functools.cache
def load_bandplan(path=BANDPLAN_FILE):
    """Return the parsed band plan file."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check_bandplan(path=BANDPLAN_FILE):
    """Read the band plan once at server start and log the result.

    It is otherwise read only when a frequency is known, so a missing file
    would first show as a failing radio status or message send.
    """
    try:
        load_bandplan(path)
    except (OSError, ValueError) as err:
        log.error("[BND] Band plan not loaded, the amateur band check will fail", path=path, e=err)
        return False
    log.info("[BND] Band plan loaded", path=path)
    return True


def get_bands(region):
    """Return the band list of an IARU region (1, 2 or 3)."""
    return load_bandplan()["regions"][str(region)]["bands"]


def find_band(region, frequency_hz):
    """Return the band containing frequency_hz, or None."""
    for band in get_bands(region):
        if band["start_hz"] <= frequency_hz <= band["end_hz"]:
            return band
    return None


def find_segments(region, frequency_hz):
    """Return all band plan segments containing frequency_hz.

    Some source plans have overlapping rows, so this can return more than one segment.
    """
    band = find_band(region, frequency_hz)
    if band is None:
        return []
    return [seg for seg in band["segments"] if seg["start_hz"] <= frequency_hz < seg["end_hz"]]


def fits_in_segment(region, low_hz, high_hz, mode="digimodes"):
    """True when low_hz..high_hz lies inside a single segment listing mode
    (or all_modes) whose max bandwidth allows it."""
    for seg in find_segments(region, low_hz):
        if high_hz > seg["end_hz"]:
            continue
        if mode not in seg["modes"] and "all_modes" not in seg["modes"]:
            continue
        if seg["max_bandwidth_hz"] is not None and high_hz - low_hz > seg["max_bandwidth_hz"]:
            continue
        return True
    return False


def on_amateur_band(frequency_hz, region=0):
    """True when frequency_hz is inside an amateur band of the IARU region
    (1, 2 or 3). Region 0, the default, checks all three regions.

    Returns False for a missing or unreadable frequency.
    """
    try:
        frequency_hz = int(float(frequency_hz))
    except (TypeError, ValueError):
        return False
    regions = [region] if region in (1, 2, 3) else load_bandplan()["regions"]
    return any(find_band(r, frequency_hz) is not None for r in regions)
