"""Matching received call signs against STATION.callsign_blacklist.

Each entry is a regular expression that has to match the whole call sign,
without regard to case: "OH2JRB-12" blocks only OH2JRB-12, "OH2JRB-1[0-5]"
blocks OH2JRB-10 to -15, "OH2.*" every call starting with OH2. An entry is
also tried against the call without its SSID, so an entry without one
("OH2JRB") blocks every SSID of that call, as it did before.

Earlier the check was "the call starts with the entry", case-sensitive: an
entry OH2JRB-1 also blocked OH2JRB-12, and a lowercase entry never matched.
"""

import functools
import re

import structlog

from freedata_server import helpers

log = structlog.get_logger("CallsignBlacklist")

# an entry that is a plain call sign, optionally with SSID: the only kind that
# can be compared with a frame that carries just the CRC of the call
PLAIN_CALLSIGN = re.compile(r"[A-Z0-9/]+(?:-[0-9]{1,2})?")


def _entries(entries):
    for entry in entries or []:
        if isinstance(entry, str) and entry.strip():
            yield entry.strip()


def invalid_entries(entries):
    """The entries that are not valid regular expressions (or not text)."""
    bad = [str(entry) for entry in entries or [] if not isinstance(entry, str)]
    for entry in _entries(entries):
        try:
            re.compile(entry)
        except re.error:
            bad.append(entry)
    return bad


@functools.lru_cache(maxsize=256)
def _pattern(entry):
    try:
        return re.compile(entry, re.IGNORECASE)
    except re.error:
        # only from a hand-edited config.ini: POST /config/ refuses it
        log.warning("[Blacklist] Entry is not a valid regular expression, compared as text", entry=entry)
        return re.compile(re.escape(entry), re.IGNORECASE)


def _crc_candidates(entry):
    entry = entry.upper()
    if "-" in entry:
        return [entry]
    return [entry] + [f"{entry}-{ssid}" for ssid in range(16)]


def matching_entry(callsign, entries, callsign_crc=None):
    """The first blacklist entry that matches the call sign, or None.

    callsign_crc (hex string) is used when the call itself is unknown (a
    frame with only the CRC of a call this station has not heard): then only
    plain call sign entries can match.
    """
    call = callsign.strip().upper() if isinstance(callsign, str) and callsign.strip() else None
    base = call.split("-", 1)[0] if call else None
    crc = callsign_crc.lower() if isinstance(callsign_crc, str) else None

    for entry in _entries(entries):
        if call:
            pattern = _pattern(entry)
            if pattern.fullmatch(call) or pattern.fullmatch(base):
                return entry
        elif crc and PLAIN_CALLSIGN.fullmatch(entry.upper()):
            if any(helpers.get_crc_24(c).hex() == crc for c in _crc_candidates(entry)):
                return entry
    return None
