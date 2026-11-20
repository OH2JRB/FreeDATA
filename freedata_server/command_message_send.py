from freedata_server.command import TxCommand
from freedata_server.arq_session_iss import ARQSessionISS
from freedata_server.message_p2p import MessageP2P, encryption_wanted
from freedata_server.arq_data_type_handler import ARQ_SESSION_TYPES
from freedata_server.message_system_db_messages import DatabaseManagerMessages
from freedata_server import encryption
from freedata_server.bandplan import on_amateur_band
from freedata_server import channel_gate


class SendMessageCommand(TxCommand):
    """Command to send a P2P message using an ARQ transfer session"""

    # transmit() goes through the channel gate itself, right before the session
    # starts: run()'s gate would come before the message checks, and its decision
    # would be stale by the time the session opens (on the air 2026-11-19, test
    # G2: a message keyed up 26 s after its last check, over another station)
    LISTEN_BEFORE_TALK = False
    # the scheduler sends queued messages on its own thread: it waits for
    # FreeDATA traffic only this long and leaves the message queued for its next try
    SCHEDULER_MAX_WAIT_SETTING = "beacon_max_wait"

    def __init__(self, ctx, apiParams: dict = {}, from_scheduler=False):
        self.from_scheduler = from_scheduler
        super().__init__(ctx, apiParams)

    def set_params_from_api(self, apiParams):
        """Sets parameters from the API request.

        This method creates a MessageP2P object from the API parameters,
        and adds the message to the database with the status 'queued'.
        A message that is already in the database (the scheduler passes the
        queued message it found) is not added again.

        Args:
            apiParams (dict): A dictionary containing the API parameters.
        """
        origin = f"{self.ctx.config_manager.config['STATION']['mycall']}-{self.ctx.config_manager.config['STATION']['myssid']}"
        self.message = MessageP2P.from_api_params(origin, apiParams)
        if "id" in apiParams and DatabaseManagerMessages(self.ctx).get_message_by_id(self.message.id):
            return

        # an encrypted station without a key: hold the message until the key is entered
        status = "queued"
        if self.key_missing(self.message.destination):
            status = "key_required"

        DatabaseManagerMessages(self.ctx).add_message(
            self.message.to_dict(),
            statistics={},
            direction="transmit",
            status=status,
            frequency=self.ctx.state_manager.radio_frequency,
        )
        if status == "key_required":
            self.ctx.event_manager.send_encryption_key_required(self.message.destination, "send")

    def key_missing(self, destination):
        """True if messages to destination are to be encrypted, but its key is unknown."""
        return encryption_wanted(self.ctx, destination) and self.ctx.station_keys.key_for(destination) is None

    def blocked_on_amateur_band(self, encrypt, message_id, destination):
        """True if an encrypted message may not be sent on the current frequency.

        Encrypted data is not allowed on the amateur bands. The message is set to
        failed, so it does not block the queue; the event manager tells the GUI
        and logs it.
        """
        frequency = self.ctx.state_manager.radio_frequency
        if not encrypt or not on_amateur_band(frequency, self.ctx.iaru_region()):
            return False
        DatabaseManagerMessages(self.ctx).update_message(message_id, update_data={"status": "failed"})
        self.ctx.event_manager.send_encryption_amateur_band(destination, frequency)
        return True

    def transmit(self):
        """Transmits the first queued message using ARQ.

        This method retrieves the first queued message from the database,
        prepares the data using the ARQ data type handler, creates an
        ARQSessionISS, and starts the transmission. It includes error
        handling and logging.

        Args:
            modem: The modem object.
        """
        if self.ctx.state_manager.getARQ():
            self.log("Modem busy, waiting until ready...")
            return

        if not self.ctx.rf_modem:
            self.log("Modem not running...", isWarning=True)
            return

        # a Stop while this waits for a free channel ends it (stop_all)
        stop_count = self.ctx.state_manager.stop_count
        first_queued_message = DatabaseManagerMessages(self.ctx).get_first_queued_message()
        if not first_queued_message:
            self.log("No queued message in database.")
            return

        # the API and the scheduler can both get here for the same message
        message_id = first_queued_message["id"]
        if not self.ctx.state_manager.claim_message_transmit(message_id):
            self.log(f"Message {message_id} is already being sent")
            return
        try:
            self.log(f"Queued message found: {first_queued_message['id']}")
            # DatabaseManagerMessages(self.ctx.event_manager).update_message(first_queued_message["id"], update_data={'status': 'transmitting'}, frequency=self.ctx.state_manager.radio_frequency)
            message_dict = DatabaseManagerMessages(self.ctx).get_message_by_id(first_queued_message["id"])
            message = MessageP2P.from_api_params(message_dict["origin"], message_dict)

            # the key may be gone since the message was queued (e.g. after a server restart)
            if self.key_missing(message.destination):
                self.log(f"No encryption key for {message.destination}, holding message", isWarning=True)
                DatabaseManagerMessages(self.ctx).update_message(
                    first_queued_message["id"], update_data={"status": "key_required"}
                )
                self.ctx.event_manager.send_encryption_key_required(message.destination, "send")
                return
            encrypt = encryption_wanted(self.ctx, message.destination)
            if self.blocked_on_amateur_band(encrypt, message_id, message.destination):
                return

            # Convert JSON string to bytes (using UTF-8 encoding)
            payload = message.to_payload().encode("utf-8")
            json_bytearray = bytearray(payload)
            session_type = ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm if encrypt else ARQ_SESSION_TYPES.p2pmsg_zlib
            try:
                data, data_type = self.arq_data_type_handler.prepare(json_bytearray, session_type, message.destination)
            except encryption.KeyMissingError:
                # key removed while we waited for a free channel
                DatabaseManagerMessages(self.ctx).update_message(
                    first_queued_message["id"], update_data={"status": "key_required"}
                )
                self.ctx.event_manager.send_encryption_key_required(message.destination, "send")
                return

            # listen before talk (channel_gate), as late as possible: the session
            # opens right after it. FreeDATA traffic that goes on, or a Stop,
            # leaves the message queued
            max_wait = self.SCHEDULER_MAX_WAIT_SETTING if self.from_scheduler else self.CHANNEL_MAX_WAIT_SETTING
            if not channel_gate.wait_for_clear_channel(
                self.ctx, self.get_name(), max_wait_setting=max_wait, stop_count=stop_count
            ):
                self.log("Channel not free, message stays queued", isWarning=True)
                return
            # Stop was pressed while we waited: the message is aborted already
            if self.ctx.state_manager.stop_count != stop_count:
                self.log("Stopped while waiting for a free channel, not sending", isWarning=True)
                return
            # another ARQ session may have started while we waited; the message stays queued
            if self.ctx.state_manager.getARQ():
                self.log("Modem busy, waiting until ready...")
                return
            # the radio may have been tuned while we waited
            if self.blocked_on_amateur_band(encrypt, message_id, message.destination):
                return

            # If we came until here, we are setting status to transmitting, otherwise it stays in queued
            DatabaseManagerMessages(self.ctx).update_message(
                first_queued_message["id"],
                update_data={"status": "transmitting"},
                frequency=self.ctx.state_manager.radio_frequency,
            )
            # destination must come from the same (queued) message as the payload,
            # which is not necessarily self.message
            iss = ARQSessionISS(self.ctx, message.destination, data, data_type)
            # the gate has just passed: the first ARQ_SESSION_OPEN goes out at once,
            # its retries wait for the channel as before
            iss.skip_next_clear_channel_wait = True
            self.ctx.state_manager.register_arq_iss_session(iss)
            iss.start()
        except Exception as e:
            self.log(f"Error starting ARQ session: {e}", isWarning=True)
        finally:
            # once it is "transmitting" it is no longer the first queued message
            self.ctx.state_manager.release_message_transmit(message_id)
