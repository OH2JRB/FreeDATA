"""
PSK Reporter uploader.

Reports stations FreeDATA hears to https://pskreporter.info with the mode
"FREEDATA", using the UDP/IPFIX protocol described in
https://pskreporter.info/pskdev.html.

Reporting is a runtime-only switch: it is off whenever the server starts and
is never written to the config file.
"""

import random
import socket
import struct
import threading
import time

import structlog

log = structlog.get_logger("pskreporter")

HOST = "report.pskreporter.info"
PORT = 4739

MODE = "FREEDATA"

# upload rules from pskdev.html
UPLOAD_INTERVAL = 300  # at most one datagram every five minutes
REPEAT_INTERVAL = 3600  # report an unchanged station at most once per hour
TEMPLATE_INTERVAL = 3600  # resend the record format descriptors at least once per hour
TEMPLATE_STARTUP_PACKETS = 3  # always send descriptors in the first datagrams, UDP is lossy
MAX_SPOTS_PER_PACKET = 30  # keeps a datagram well below a typical MTU

# PSK Reporter enterprise number and field ids
ENTERPRISE = 30351
SENDER_CALLSIGN = 1
RECEIVER_CALLSIGN = 2
SENDER_LOCATOR = 3
RECEIVER_LOCATOR = 4
FREQUENCY = 5
SNR = 6
IMD = 7
DECODER_SOFTWARE = 8
MODE_FIELD = 10
INFORMATION_SOURCE = 11
FLOW_START_SECONDS = 150  # standard IANA field, not an enterprise one

VARIABLE_LENGTH = 0xFFFF
INFORMATION_SOURCE_AUTOMATIC = 1

# template ids, the "linking values" of pskdev.html
RECEIVER_TEMPLATE_ID = 0x9992
SENDER_TEMPLATE_ID = 0x9993  # without sender locator
SENDER_LOCATOR_TEMPLATE_ID = 0x9994  # with sender locator

RECEIVER_FIELDS = [
    (RECEIVER_CALLSIGN, VARIABLE_LENGTH),
    (RECEIVER_LOCATOR, VARIABLE_LENGTH),
    (DECODER_SOFTWARE, VARIABLE_LENGTH),
]
SENDER_FIELDS = [
    (SENDER_CALLSIGN, VARIABLE_LENGTH),
    (FREQUENCY, 4),
    (SNR, 1),
    (IMD, 1),
    (MODE_FIELD, VARIABLE_LENGTH),
    (INFORMATION_SOURCE, 1),
]
SENDER_LOCATOR_FIELDS = SENDER_FIELDS + [(SENDER_LOCATOR, VARIABLE_LENGTH)]


def pad4(data: bytes) -> bytes:
    """Null-pad data to a multiple of four bytes."""
    return data + b"\x00" * (-len(data) % 4)


def pack_string(value: str) -> bytes:
    """Pack a string as a length byte followed by its UTF-8 bytes."""
    encoded = value.encode("utf-8")[:254]
    return bytes([len(encoded)]) + encoded


def field_specifier(field_id: int, length: int) -> bytes:
    if field_id == FLOW_START_SECONDS:
        return struct.pack(">HH", field_id, length)
    return struct.pack(">HHI", 0x8000 | field_id, length, ENTERPRISE)


def sender_template(template_id: int, fields: list) -> bytes:
    """Template set (set id 2) for sender records; flowStartSeconds goes last."""
    body = struct.pack(">HH", template_id, len(fields) + 1)
    body += b"".join(field_specifier(field_id, length) for field_id, length in fields)
    body += field_specifier(FLOW_START_SECONDS, 4)
    return struct.pack(">HH", 2, len(body) + 4) + body


def receiver_template() -> bytes:
    """Options template set (set id 3) for the receiver record."""
    body = struct.pack(">HHH", RECEIVER_TEMPLATE_ID, len(RECEIVER_FIELDS), 1)
    body += b"".join(field_specifier(field_id, length) for field_id, length in RECEIVER_FIELDS)
    body = pad4(body)
    return struct.pack(">HH", 3, len(body) + 4) + body


def data_set(set_id: int, records: bytes) -> bytes:
    """Wrap records in a data set header, null-padded to four bytes."""
    records = pad4(records)
    return struct.pack(">HH", set_id, len(records) + 4) + records


def normalize_callsign(callsign: str) -> str:
    """Strip the FreeDATA SSID, PSK Reporter wants the plain callsign."""
    return str(callsign).strip().upper().split("-")[0]


def normalize_locator(locator) -> str:
    """Return a valid Maidenhead locator (sub-square lowercase) or an empty string."""
    locator = str(locator or "").strip()
    if len(locator) not in (4, 6, 8) or not locator[:2].isalpha() or not locator[2:4].isdigit():
        return ""
    if len(locator) >= 6 and not locator[4:6].isalpha():
        return ""
    return locator[:4].upper() + locator[4:6].lower() + locator[6:]


def clamp_snr(snr) -> int:
    return max(-128, min(127, int(round(float(snr)))))


def build_packet(
    receiver_callsign: str,
    receiver_locator: str,
    software: str,
    spots: list,
    sequence: int,
    observation_domain: int,
    include_templates: bool = True,
    export_time: int = None,
) -> bytes:
    """Build one IPFIX datagram.

    Each spot is a dict with callsign, frequency (Hz), snr, timestamp and
    an optional locator (empty string if unknown).
    """
    body = b""
    if include_templates:
        body += receiver_template()
        body += sender_template(SENDER_TEMPLATE_ID, SENDER_FIELDS)
        body += sender_template(SENDER_LOCATOR_TEMPLATE_ID, SENDER_LOCATOR_FIELDS)

    receiver = pack_string(receiver_callsign) + pack_string(receiver_locator) + pack_string(software)
    body += data_set(RECEIVER_TEMPLATE_ID, receiver)

    without_locator = b""
    with_locator = b""
    for spot in spots:
        record = pack_string(spot["callsign"])
        record += struct.pack(">I", int(spot["frequency"]))
        record += struct.pack(">bb", clamp_snr(spot["snr"]), 0)
        record += pack_string(MODE)
        record += struct.pack(">B", INFORMATION_SOURCE_AUTOMATIC)
        if spot.get("locator"):
            with_locator += record + pack_string(spot["locator"]) + struct.pack(">I", int(spot["timestamp"]))
        else:
            without_locator += record + struct.pack(">I", int(spot["timestamp"]))
    if without_locator:
        body += data_set(SENDER_TEMPLATE_ID, without_locator)
    if with_locator:
        body += data_set(SENDER_LOCATOR_TEMPLATE_ID, with_locator)

    if export_time is None:
        export_time = int(time.time())
    header = struct.pack(
        ">HHIII",
        10,
        len(body) + 16,
        export_time,
        sequence & 0xFFFFFFFF,
        observation_domain & 0xFFFFFFFF,
    )
    return header + body


class PSKReporter:
    """Collects heard stations and uploads them to PSK Reporter in batches."""

    def __init__(self, ctx, transport=None):
        """
        Args:
            ctx: The application context.
            transport: Optional callable taking the datagram bytes, used
                instead of the UDP socket (tests).
        """
        self.ctx = ctx
        self.transport = transport
        self.enabled = False  # runtime only, always off at server start
        self.lock = threading.Lock()
        self.pending = {}  # callsign -> latest spot since the last upload
        self.reported = {}  # callsign -> (monotonic time, band) of the last upload
        self.sequence = 0
        self.observation_domain = random.getrandbits(32)
        self.packets_sent = 0
        self.last_template_time = None
        self.last_upload_time = None
        self.sock = None

    def set_enabled(self, enabled: bool):
        with self.lock:
            self.enabled = bool(enabled)
            if not self.enabled:
                self.pending.clear()
        log.info("[PSKREPORTER] reporting", enabled=self.enabled)
        # in the /states stream, so an open GUI shows the switch as it is (for
        # example off again after a server restart)
        state_manager = getattr(self.ctx, "state_manager", None)
        if state_manager is not None:
            state_manager.set("is_pskreporter_enabled", self.enabled)

    def status(self) -> dict:
        with self.lock:
            return {"enabled": self.enabled, "pending_spots": len(self.pending)}

    def add_spot(self, callsign, snr, frequency, locator=None, timestamp=None):
        """Queue a heard station for the next upload; ignored while disabled."""
        if not self.enabled:
            return False
        try:
            frequency = int(float(frequency))
            snr = clamp_snr(snr)
        except (TypeError, ValueError):
            return False
        callsign = normalize_callsign(callsign)
        if not callsign or frequency <= 0:
            return False
        spot = {
            "callsign": callsign,
            "frequency": frequency,
            "snr": snr,
            "locator": normalize_locator(locator),
            "timestamp": int(timestamp if timestamp is not None else time.time()),
        }
        with self.lock:
            if not self.enabled:
                return False
            previous = self.pending.get(callsign)
            # keep a known locator if a later frame of the same station has none
            if previous and not spot["locator"]:
                spot["locator"] = previous["locator"]
            self.pending[callsign] = spot
        return True

    def take_spots(self, now: float) -> list:
        """Return the spots due for upload and forget the rest of the batch.

        A station is skipped if it was uploaded within REPEAT_INTERVAL on the
        same band.
        """
        with self.lock:
            spots = []
            for callsign, spot in self.pending.items():
                band = spot["frequency"] // 1_000_000
                last = self.reported.get(callsign)
                if last and now - last[0] < REPEAT_INTERVAL and last[1] == band:
                    continue
                spots.append(spot)
                self.reported[callsign] = (now, band)
            self.pending.clear()
            # drop entries that can no longer block a report
            self.reported = {
                callsign: last for callsign, last in self.reported.items() if now - last[0] < REPEAT_INTERVAL
            }
        return spots

    def flush(self, now: float = None):
        """Upload pending spots, at most once every UPLOAD_INTERVAL seconds."""
        if not self.enabled:
            return 0
        if now is None:
            now = time.monotonic()
        if self.last_upload_time is not None and now - self.last_upload_time < UPLOAD_INTERVAL:
            return 0

        station = self.ctx.config_manager.config["STATION"]
        mycall = normalize_callsign(station["mycall"])
        if mycall in ["AA1AAA", "XX1XXX"]:
            log.info("[PSKREPORTER] not reporting with the default callsign")
            return 0

        spots = self.take_spots(now)
        if not spots:
            return 0
        self.last_upload_time = now

        mygrid = normalize_locator(station["mygrid"])
        software = f"FreeDATA {self.ctx.constants.MODEM_VERSION}"
        sent = 0
        for i in range(0, len(spots), MAX_SPOTS_PER_PACKET):
            chunk = spots[i : i + MAX_SPOTS_PER_PACKET]
            include_templates = (
                self.packets_sent < TEMPLATE_STARTUP_PACKETS
                or self.last_template_time is None
                or now - self.last_template_time >= TEMPLATE_INTERVAL
            )
            packet = build_packet(
                mycall,
                mygrid,
                software,
                chunk,
                self.sequence,
                self.observation_domain,
                include_templates=include_templates,
            )
            try:
                self.send(packet)
            except OSError as e:
                log.warning("[PSKREPORTER] upload failed", e=e)
                break
            self.packets_sent += 1
            self.sequence += len(chunk)
            if include_templates:
                self.last_template_time = now
            sent += len(chunk)
        log.info("[PSKREPORTER] spots uploaded", spots=sent)
        return sent

    def send(self, packet: bytes):
        if self.transport is not None:
            self.transport(packet)
            return
        if self.sock is None:
            # one socket for the whole session, PSK Reporter wants a constant source port
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.sendto(packet, (HOST, PORT))
