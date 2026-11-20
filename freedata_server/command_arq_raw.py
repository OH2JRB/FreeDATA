from freedata_server.command import TxCommand
from freedata_server import api_validations
import base64
from freedata_server.arq_session_iss import ARQSessionISS
from freedata_server.arq_data_type_handler import ARQ_SESSION_TYPES


class ARQRawCommand(TxCommand):
    """Command for transmitting raw data via ARQ.

    This command handles the transmission of raw data using the ARQ protocol.
    It prepares the data, creates an ARQ session, and starts the
    transmission.
    """

    def set_params_from_api(self, apiParams):
        """Sets parameters for the command from the API request.

        This method extracts parameters such as dxcall, data type, and raw
        data from the provided API parameters dictionary. It validates the
        dxcall and sets default values if necessary.

        Args:
            apiParams (dict): A dictionary containing the API parameters.
        """
        self.dxcall = apiParams["dxcall"]
        if not api_validations.validate_freedata_callsign(self.dxcall):
            self.dxcall = f"{self.dxcall}-0"

        try:
            self.type = ARQ_SESSION_TYPES[apiParams["type"]]
        except KeyError:
            self.type = ARQ_SESSION_TYPES.raw

        self.data = base64.b64decode(apiParams["data"])

    def run(self):
        """Executes the ARQ raw data transmission command.

        This method prepares the data for transmission, creates an ARQ session,
        and starts the transmission process. It waits for a clear channel first
        (wait_for_clear_channel, listen before talk); the session's first
        ARQ_SESSION_OPEN then goes out at once. Errors during session startup
        are logged and return False.

        Args:
            event_queue (Queue): The event queue for emitting events.
            modem: The modem object for transmission.

        Returns:
            ARQSessionISS or bool: The ARQSessionISS object if the session
            starts successfully, False otherwise.
        """
        try:
            self.emit_event()
            self.logger.info(self.log_message())

            if not self.wait_for_clear_channel():
                return False

            prepared_data, type_byte = self.arq_data_type_handler.prepare(self.data, self.type)

            iss = ARQSessionISS(self.ctx, self.dxcall, prepared_data, type_byte)
            # the gate has just passed: the first ARQ_SESSION_OPEN goes out at once,
            # its retries wait for the channel as before
            iss.skip_next_clear_channel_wait = True
            if iss.id:
                self.ctx.state_manager.register_arq_iss_session(iss)
                iss.start()
                return iss
        except Exception as e:
            self.log(f"Error starting ARQ session: {e}", isWarning=True)

        return False
