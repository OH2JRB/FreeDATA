# file for handling transmitting data
from freedata_server.norm.norm_transmission import NormTransmission
from freedata_server.norm.norm_transmission import NORMMsgType, NORMMsgPriority
from enum import Enum
import time
from freedata_server.codec2 import FREEDV_MODE

import freedata_server.helpers
from datetime import datetime, timezone, timedelta
import base64
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts


class NORM_ISS_State(Enum):
    NEW = 0
    TRANSMITTING = 1
    ENDED = 2
    FAILED = 3
    ABORTING = 4
    ABORTED = 5


class NormTransmissionISS(NormTransmission):
    MAX_PAYLOAD_SIZE = 26

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx

        if not self.ctx.config_manager.config["EXP"]["enable_groupchat"]:
            return

        self.state = NORM_ISS_State.NEW
        self.log("Initialized")

    def prepare_and_transmit_data(
        self, origin, domain, gridsquare, data, priority=NORMMsgPriority.NORMAL, message_type=NORMMsgType.UNDEFINED
    ):
        self.origin = origin
        self.domain = domain
        self.gridsquare = gridsquare
        self.data = data
        self.priority = priority
        self.message_type = message_type
        self.payload_size = len(data)
        self.timestamp = int(time.time())

        bursts = self.create_data()
        self.add_to_database()
        self.transmit_bursts(bursts)

    def create_data(self):
        try:
            if not self.data:
                self.log("Data is empty", isWarning=True)
                raise ValueError("Data is empty")

            full_data = self.data
            total_bursts = (len(full_data) + self.MAX_PAYLOAD_SIZE - 1) // self.MAX_PAYLOAD_SIZE

            if total_bursts <= 0:
                self.log(
                    f"Invalid burst calculation (data length: {len(full_data)}, max payload: {self.MAX_PAYLOAD_SIZE})",
                    isWarning=True,
                )
                raise ValueError("Invalid burst calculation")

            bursts = []
            for burst_number in range(1, total_bursts + 1):
                offset = (burst_number - 1) * self.MAX_PAYLOAD_SIZE
                payload = full_data[offset : offset + self.MAX_PAYLOAD_SIZE]

                if not payload:
                    self.log(f"Empty payload at burst {burst_number}", isWarning=True)
                    raise ValueError(f"Empty payload at burst {burst_number}")

                burst_info = self.encode_burst_info(burst_number, total_bursts)
                checksum = freedata_server.helpers.get_crc_24(full_data)
                is_last = burst_number == total_bursts
                flags = self.encode_flags(self.message_type, self.priority, is_last)
                burst_frame = self.frame_factory.build_norm_data(
                    origin=self.origin,
                    domain=self.domain,
                    gridsquare=self.gridsquare,
                    timestamp=self.timestamp,
                    burst_info=burst_info,
                    payload_size=len(full_data),
                    payload_data=payload,
                    flag=flags,
                    checksum=checksum,
                )

                self.log(
                    f"Burst {burst_number}/{total_bursts} created (offset={offset}, payload_size={len(payload)}, is_last={is_last})"
                )

                bursts.append(burst_frame)

            self.log(f"All bursts created successfully (total={total_bursts})")
            self.set_burst_labels("burst", list(range(1, total_bursts + 1)), total_bursts)
            return bursts

        except Exception as e:
            self.log(f"Error in create_data: {e}", isWarning=True)
            raise

    def create_repair(self, db_msg_obj: dict, burst_numbers: list[int]):
        try:
            if "payload_data" not in db_msg_obj or "final" not in db_msg_obj["payload_data"]:
                self.log("Missing payload data in db_msg_obj", isWarning=True)
                raise ValueError("Missing payload data")

            data = base64.b64decode(db_msg_obj["payload_data"]["final"])
            total_bursts = db_msg_obj.get("total_bursts")
            priority = db_msg_obj.get("priority")
            msg_type_val = db_msg_obj.get("msg_type")
            message_type = NORMMsgType[msg_type_val] if isinstance(msg_type_val, str) else msg_type_val
            checksum_hex = db_msg_obj.get("checksum")

            # priority 0 is valid, so check it against None instead of truthiness
            if not all([total_bursts, message_type, checksum_hex]) or priority is None:
                self.log("Missing required fields in db_msg_obj", isWarning=True)
                raise ValueError("Missing required fields")

            checksum = bytes.fromhex(checksum_hex)
            self.origin = db_msg_obj["origin"]
            self.domain = db_msg_obj["domain"]
            repair_bursts = []

            for burst_number in burst_numbers:
                if burst_number < 1 or burst_number > total_bursts:
                    self.log(f"Invalid burst number {burst_number}", isWarning=True)
                    raise ValueError(f"Invalid burst number {burst_number}")

                offset = (burst_number - 1) * self.MAX_PAYLOAD_SIZE
                payload = data[offset : offset + self.MAX_PAYLOAD_SIZE]

                if not payload:
                    self.log(f"Empty payload for burst {burst_number}", isWarning=True)
                    raise ValueError(f"Empty payload for burst {burst_number}")

                burst_info = self.encode_burst_info(burst_number, total_bursts)
                is_last = burst_number == total_bursts
                flags = self.encode_flags(message_type, priority, is_last)

                burst_frame = self.frame_factory.build_norm_repair(
                    origin=db_msg_obj["origin"],
                    domain=db_msg_obj["domain"],
                    gridsquare=db_msg_obj["gridsquare"],
                    timestamp=int(db_msg_obj["timestamp"]),
                    burst_info=burst_info,
                    payload_size=len(data),
                    payload_data=payload,
                    flag=flags,
                    checksum=checksum,
                )

                self.log(
                    f"Repair burst {burst_number}/{total_bursts} created (offset={offset}, payload_size={len(payload)}, is_last={is_last})"
                )
                repair_bursts.append(burst_frame)

            self.log(f"All repair bursts created successfully (count={len(repair_bursts)})")
            self.set_burst_labels("repair burst", list(burst_numbers), total_bursts)
            return repair_bursts

        except Exception as e:
            self.log(f"Error in create_repair: {e}", isWarning=True)
            raise

    def set_burst_labels(self, kind, burst_numbers, total_bursts):
        """What transmit_bursts logs for each burst: "<kind> <number>/<total>"."""
        self.burst_kind = kind
        self.burst_numbers = burst_numbers
        self.total_bursts = total_bursts

    def transmit_bursts(self, bursts):
        """Sends the bursts once the channel is clear (wait_for_channel).

        Stops before the next burst when Stop is pressed (stop_all) or an ARQ
        session or P2P connection starts; a broadcast is then sent again by the
        scheduled resend, a repair when the receiver asks again.

        Returns:
            True if every burst was sent.
        """
        try:
            if not bursts:
                self.log("No bursts to transmit", isWarning=True)
                return False

            kind = getattr(self, "burst_kind", "burst")
            numbers = getattr(self, "burst_numbers", None) or list(range(1, len(bursts) + 1))
            total = getattr(self, "total_bursts", None) or len(bursts)

            if getattr(self.ctx, "rf_modem", None) is None:
                self.log(f"Not sent: the modem is not running ({len(bursts)} {kind}s)", isWarning=True)
                return False

            stop_count = self.ctx.state_manager.stop_count
            if not self.wait_for_channel(stop_count):
                self.log(
                    f"Not sent: a session is still running or Stop was pressed ({len(bursts)} {kind}s)", isWarning=True
                )
                return False

            for burst, number in zip(bursts, numbers):
                if self.ctx.state_manager.stop_count != stop_count:
                    self.log(f"Stopped before {kind} {number}/{total}", isWarning=True)
                    return False
                if self.ctx.state_manager.is_in_session():
                    self.log(f"A session started: stopped before {kind} {number}/{total}", isWarning=True)
                    return False
                modem = self.ctx.rf_modem
                if modem is None:  # stopped or restarting, as after a settings change
                    self.log(f"The modem stopped: stopped before {kind} {number}/{total}", isWarning=True)
                    return False
                modem.transmit(FREEDV_MODE.datac4, 1, 200, burst)
                self.log(f"Transmitted {kind} {number}/{total} (size={len(burst)})")

            self.log(f"All {kind}s transmitted successfully")
            return True

        except Exception as e:
            self.log(f"Error in transmit_bursts: {e}", isWarning=True)
            raise

    def add_to_database(self):
        try:
            db = DatabaseManagerBroadcasts(self.ctx)
            checksum = freedata_server.helpers.get_crc_24(self.data).hex()
            broadcast_id = self.create_broadcast_id(int(self.timestamp), self.domain, checksum)

            total_bursts = (len(self.data) + self.MAX_PAYLOAD_SIZE - 1) // self.MAX_PAYLOAD_SIZE

            if total_bursts < 1:
                self.log("No bursts to store in database", isWarning=True)
                return

            for burst_index in range(1, total_bursts + 1):
                offset = (burst_index - 1) * self.MAX_PAYLOAD_SIZE
                payload_data = self.data[offset : offset + self.MAX_PAYLOAD_SIZE]
                if not payload_data:
                    self.log(f"Empty payload at burst index {burst_index}", isWarning=True)
                    continue

                payload_b64 = base64.b64encode(payload_data).decode("ascii")
                nexttransmission_at = datetime.now(timezone.utc) + timedelta(hours=1)
                nexttransmission_at = nexttransmission_at.timestamp()

                expires_at = datetime.now(timezone.utc) + timedelta(days=1)
                expires_at = expires_at.timestamp()

                db.process_broadcast_message(
                    id=broadcast_id,
                    origin=self.origin,
                    timestamp=self.timestamp,
                    burst_index=burst_index,
                    burst_data=payload_b64,
                    total_bursts=total_bursts,
                    checksum=checksum,
                    repairing_callsigns=None,
                    domain=self.domain,
                    gridsquare=self.gridsquare,
                    msg_type=self.message_type.name if hasattr(self.message_type, "name") else str(self.message_type),
                    priority=self.priority.value if hasattr(self.priority, "value") else int(self.priority),
                    received_at=datetime.now(timezone.utc).timestamp(),
                    nexttransmission_at=nexttransmission_at,
                    expires_at=expires_at,
                    is_read=True,
                    direction="transmit",
                    status="assembling",
                )

                self.log(f"Stored burst {burst_index}/{total_bursts} in database (size={len(payload_data)})")

            self.log(f"All {total_bursts} bursts added to database successfully")

        except Exception as e:
            self.log(f"Error in add_to_database: {e}", isWarning=True)
            raise

    def retransmit_data(self, msg):
        self.origin = msg.origin
        self.domain = msg.domain
        self.gridsquare = msg.gridsquare
        self.data = base64.b64decode(msg.payload_data["final"])
        self.priority = msg.priority
        self.message_type = msg.msg_type
        self.payload_size = len(self.data)
        self.timestamp = int(msg.timestamp)

        bursts = self.create_data()
        # self.add_to_database()
        self.transmit_bursts(bursts)
