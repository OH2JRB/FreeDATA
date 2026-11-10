# base class for norm transmission


import structlog
import threading
import time
from enum import IntEnum
import hashlib
import numpy as np
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.data_frame_factory import DataFrameFactory


class NORMMsgType(IntEnum):
    UNDEFINED = 0
    MESSAGE = 1  # Generic text/data message
    POSITION = 2  # GPS or grid locator info
    SITREP = 3  # Situation report
    METAR = 4  # METAR Message


class NORMMsgPriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3
    ALERT = 4
    EMERGENCY = 5


class NormTransmission:
    # before keying up: a random delay of 0 to RANDOM_DELAY_MAX s (several
    # stations answering the same frame spread out), then up to BUSY_WAIT s for
    # the channel to be free, as CQ answers and raw ARQ sends do
    RANDOM_DELAY_MAX = 5
    BUSY_WAIT = 5
    # a broadcast waits up to SESSION_WAIT s for a running ARQ session or P2P
    # connection to end; it would otherwise key up in the middle of it
    SESSION_WAIT = 300
    SESSION_POLL = 1
    # Broadcasts held back by a session are released when it ends, on both of
    # its stations at once: on the air (2026-11-10) random delays of 0 and 1 s
    # sent both together. After a session the delay is spread over
    # RANDOM_DELAY_AFTER_SESSION s instead.
    RANDOM_DELAY_AFTER_SESSION = 15
    # The channel counts as free when neither codec2 traffic nor the FFT traffic
    # check (any signal, seen within a fraction of a second, while codec2 needs
    # a sync first) reports it busy. Once free, it is heard for LISTEN_TIME s
    # more before keying up, to notice a station that has just started.
    LISTEN_TIME = 0.5
    # A busy channel is waited for up to BUSY_WAIT s, but up to
    # BUSY_WAIT_FREEDATA s while another FreeDATA station is heard (codec2
    # traffic within the last FREEDATA_GAP s): a broadcast of 3 bursts takes
    # about 17 s, one of 15 bursts about 90 s, and on the air (2026-11-10) the
    # second station keyed up after 5 s during the first one's last burst.
    BUSY_WAIT_FREEDATA = 90
    FREEDATA_GAP = 3
    BUSY_POLL = 0.25

    def __init__(self, ctx):
        self.logger = structlog.get_logger(type(self).__name__)
        self.ctx = ctx

        if not self.ctx.config_manager.config["EXP"]["enable_groupchat"]:
            return

        self.origin = None
        self.domain = None

        self.frame_factory = DataFrameFactory(self.ctx)

    def log(self, message, isWarning=False):
        """Logs a message with session context.

        Logs a message, including the class name, session ID, and current state,
        using the appropriate log level (warning or info).

        Args:
            message: The message to be logged.
            isWarning: A boolean indicating whether the message should be logged as a warning.
        """
        msg = f"[{type(self).__name__}][origin={self.origin},domain={self.domain}][state={self.state.name}]: {message}"
        logger = self.logger.warn if isWarning else self.logger.info
        logger(msg)

    def set_state(self, state):
        self.last_state_change_timestamp = time.time()
        if self.state == state:
            self.log(f"{type(self).__name__} state {self.state.name} unchanged.")
        else:
            self.log(
                f"{type(self).__name__} state change from {self.state.name} to {state.name} at {self.last_state_change_timestamp}"
            )
        self.state = state

    def on_frame_received(self, frame):
        """Handles received frames based on the current session state.

        This method processes incoming frames, triggering state transitions and
        data handling based on the frame type and current session state.
        It logs received frame types and ignores unknown state transitions.

        Args:
            frame: The received frame.
        """
        self.event_frame_received.set()
        self.log(f"Received {frame['frame_type']}")
        frame_type = frame["frame_type_int"]
        if self.state in self.STATE_TRANSITION and frame_type in self.STATE_TRANSITION[self.state]:
            action_name = self.STATE_TRANSITION[self.state][frame_type]
            received_data, type_byte = getattr(self, action_name)(frame)

            if isinstance(received_data, bytearray) and isinstance(type_byte, int):
                self.arq_data_type_handler.dispatch(
                    type_byte, received_data, self.update_histograms(len(received_data), len(received_data))
                )
            return

        self.log(f"Ignoring unknown transition from state {self.state.name} with frame {frame['frame_type']}")

    def encode_flags(self, msg_type, priority, is_last):
        """
        Encodes message type, priority and 'last burst' flag into a single byte.

        Supports msg_type as Enum or string (e.g., "MESSAGE").
        """
        # Convert msg_type
        if isinstance(msg_type, str):
            try:
                msg_type_enum = NORMMsgType[msg_type.upper()]
            except KeyError:
                raise ValueError(f"Invalid msg_type string: '{msg_type}'")
        elif isinstance(msg_type, NORMMsgType):
            msg_type_enum = msg_type
        else:
            raise TypeError("msg_type must be NORMMsgType or str")

        msg_type_int = int(msg_type_enum)

        # Convert priority
        if isinstance(priority, IntEnum):
            priority_int = int(priority)
        else:
            priority_int = int(priority)

        assert 0 <= msg_type_int <= 15, "msg_type must be 0–15"
        assert 0 <= priority_int <= 7, "priority must be 0–7"

        return ((1 if is_last else 0) << 7) | ((msg_type_int & 0x0F) << 3) | (priority_int & 0x07)

    def decode_flags(self, flags):
        """
        Decodes a flags byte into (is_last, msg_type, priority).

        Bit layout:
        Bit 7   → is_last
        Bits 6–3 → msg_type (0–15)
        Bits 2–0 → priority (0–7)

        Returns:
            is_last (bool),
            msg_type (NORMMsgType),
            priority (int)
        """
        is_last = bool((flags >> 7) & 0x01)
        msg_type_int = (flags >> 3) & 0x0F
        priority = flags & 0x07

        try:
            msg_type = NORMMsgType(msg_type_int)
        except ValueError:
            msg_type = NORMMsgType.UNDEFINED  # Fallback bei unbekanntem Typ

        return is_last, msg_type, priority

    def encode_burst_info(self, burst_number, total_bursts):
        return ((burst_number & 0x0F) << 4) | (total_bursts & 0x0F)

    def decode_burst_info(self, burst_info):
        burst_number = (burst_info >> 4) & 0x0F
        burst_total = burst_info & 0x0F
        return burst_number, burst_total

    def create_broadcast_id(self, timestamp: int, domain: str, checksum: str, length: int = 10) -> str:
        """
        Creates a deterministic broadcast ID using BLAKE2b.

        Args:
            timestamp (int): UNIX timestamp (int).
            domain (str): Domain/context string.
            checksum (str): Checksum string.
            length (int): Number of hex characters in result (max 128).

        Returns:
            str: Broadcast ID, e.g., 'bc_8d12fa3b4e'
        """
        base_str = f"{timestamp}:{domain}:{checksum}".encode("utf-8")
        digest = hashlib.blake2b(base_str, digest_size=length // 2).hexdigest()  # 1 hex char = 4 bits
        return f"{digest}"

    def wait_for_channel(self, stop_count, session_wait=None):
        """Waits until this station may key up for NORM traffic.

        Waits for a running ARQ session or P2P connection to end (up to
        session_wait s, default SESSION_WAIT), then a random delay (up to
        RANDOM_DELAY_MAX s, or RANDOM_DELAY_AFTER_SESSION s when it waited for
        a session), then up to BUSY_WAIT s for a free channel (wait_for_free_channel).

        Returns:
            True to send now; False if a session is still running or Stop was
            pressed (stop_count changed) meanwhile.
        """
        state = self.ctx.state_manager
        deadline = time.time() + (self.SESSION_WAIT if session_wait is None else session_wait)
        held = state.is_in_session()
        if held:
            self.logger.info("[NORM] Waiting for the running session to end")
        while state.is_in_session():
            if state.stop_count != stop_count or time.time() >= deadline:
                return False
            threading.Event().wait(self.SESSION_POLL)

        spread = self.RANDOM_DELAY_AFTER_SESSION if held else self.RANDOM_DELAY_MAX
        random_delay = float(np.random.uniform(0, spread))
        self.logger.info(f"[NORM] Random delay before transmit: {random_delay:.1f}s")
        threading.Event().wait(random_delay)

        self.wait_for_free_channel()  # sends anyway when the channel stays busy

        return state.stop_count == stop_count and not state.is_in_session()

    def wait_for_free_channel(self):
        """Waits for the channel to be free and stay free for LISTEN_TIME s.

        Free = state_manager.channel_busy_event set: no codec2 traffic and no
        traffic seen by the FFT check (audio.calculate_fft). A busy channel is
        waited for up to BUSY_WAIT s, or up to BUSY_WAIT_FREEDATA s while
        another FreeDATA station is heard.

        Returns:
            True if it is free; False if it was still busy after the wait.
        """
        state = self.ctx.state_manager
        start = time.time()
        last_freedata = None
        started_logged = False  # the busy state flickers during another station's bursts: log it once
        while True:
            if state.channel_busy_event.wait(self.BUSY_POLL):
                threading.Event().wait(self.LISTEN_TIME)
                if state.channel_busy_event.is_set():
                    return True
                if not started_logged:
                    self.logger.info("[NORM] Another station started: waiting for the channel")
                    started_logged = True
            now = time.time()
            if state.is_receiving_codec2_signal():
                if last_freedata is None:
                    self.logger.info("[NORM] Another FreeDATA station is sending: waiting for it to finish")
                last_freedata = now
            hearing_freedata = last_freedata is not None and now - last_freedata < self.FREEDATA_GAP
            if now - start >= (self.BUSY_WAIT_FREEDATA if hearing_freedata else self.BUSY_WAIT):
                self.logger.info(f"[NORM] Channel still busy after {now - start:.0f}s: sending anyway")
                return False

    def create_and_transmit_nack_burst(self, origin, id, burst_numbers):
        """Asks the sender of broadcast id for its missing bursts.

        Returns:
            True if the NACK was sent; False if a session was running or Stop
            was pressed, in which case the next scheduled check asks again.
        """
        if getattr(self.ctx, "rf_modem", None) is None:
            self.logger.info(f"[NORM] NACK for {id} not sent: the modem is not running")
            return False
        if not self.wait_for_channel(self.ctx.state_manager.stop_count, session_wait=0):
            self.logger.info(f"[NORM] NACK for {id} not sent: a session is running or Stop was pressed")
            return False
        burst = self.frame_factory.build_norm_nack(origin, id, burst_numbers)
        modem = self.ctx.rf_modem
        if modem is None:  # stopped while waiting for the channel
            self.logger.info(f"[NORM] NACK for {id} not sent: the modem stopped")
            return False
        modem.transmit(FREEDV_MODE.datac4, 1, 200, burst)
        self.logger.info(f"[NORM] NACK sent for {id}, missing bursts {list(burst_numbers)}")
        return True
