import threading

from freedata_server.modem_frametypes import FRAME_TYPE as FR
from freedata_server import frame_handler
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.norm.norm_transmission_irs import NormTransmissionIRS
from freedata_server.norm.norm_transmission_iss import NormTransmissionISS


class NORMFrameHandler(frame_handler.FrameHandler):
    def follow_protocol(self):
        frame = self.details["frame"]

        if frame["frame_type_int"] == FR.NORM_DATA.value:
            NormTransmissionIRS(self.ctx, frame)

        elif frame["frame_type_int"] == FR.NORM_REPAIR.value:
            NormTransmissionIRS(self.ctx, frame)

        elif frame["frame_type_int"] == FR.NORM_NACK.value:
            self.answer_nack(frame)
        else:
            self.logger.warning("DISCARDING FRAME", frame=frame)
            return

    def answer_nack(self, frame):
        """Sends the bursts a receiver asks for, if the broadcast is our own.

        The repair waits for a clear channel (NormTransmission.wait_for_channel),
        so it runs in its own thread rather than holding up the receive worker.
        """
        try:
            broadcast_id = frame["id"].decode("utf-8")
            db = DatabaseManagerBroadcasts(self.ctx)
            broadcast = db.get_broadcast_per_id(broadcast_id)
            if broadcast is None:
                self.logger.debug("[NORM] NACK for a broadcast we do not have", id=broadcast_id)
                return
            # only repair broadcasts we have sent ourselves, not ones we only received
            if broadcast.get("direction") != "transmit":
                self.logger.info(
                    "[NORM] ignoring NACK for broadcast not originated by us",
                    id=broadcast["id"],
                    nack_origin=frame.get("origin"),
                )
                return
            # repairs and scheduled resends share the attempts counter and its limit
            if (broadcast["attempts"] or 0) >= db.MAX_ATTEMPTS:
                self.logger.info("[NORM] NACK not answered: maximum attempts reached", id=broadcast["id"])
                return
            db.increment_attempts(broadcast["id"])
            self.logger.info(
                "[NORM] NACK received",
                id=broadcast["id"],
                nack_origin=frame.get("origin"),
                missing=frame["burst_numbers"],
            )
            iss = NormTransmissionISS(self.ctx)
            repair_bursts = iss.create_repair(broadcast, frame["burst_numbers"])
            threading.Thread(target=iss.transmit_bursts, args=(repair_bursts,), name="NORM repair", daemon=True).start()
        except Exception as e:
            self.logger.warning("[NORM] could not answer NACK", error=str(e))
