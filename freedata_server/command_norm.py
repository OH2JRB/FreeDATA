from freedata_server.command import TxCommand
from freedata_server import api_validations
import base64
import threading
from freedata_server.norm.norm_transmission_iss import NormTransmissionISS


class Norm(TxCommand):
    def set_params_from_api(self, apiParams):
        # stored in the form the receiving stations decode from the frame, so
        # both sides compute the same broadcast id (POST /freedata/broadcasts
        # refuses values that cannot be sent)
        self.origin = api_validations.broadcast_address(apiParams["origin"])
        self.domain = api_validations.broadcast_address(apiParams["domain"])
        if self.origin is None or self.domain is None:
            raise ValueError(f"invalid broadcast origin or domain: {apiParams['origin']!r}, {apiParams['domain']!r}")

        # strip data to maximum payload
        self.data = base64.b64decode(apiParams["data"])
        self.data = self.data[: 15 * 26]

        # priority is a 3-bit field (0-7) in the burst flags; the GUI may send
        # it as a string, so coerce it and fall back to 1 if it is invalid
        try:
            self.priority = int(apiParams.get("priority", 1))
        except (TypeError, ValueError):
            self.priority = 1
        if not 0 <= self.priority <= 7:
            self.priority = 1

        self.msgtype = apiParams["type"]
        self.gridsquare = apiParams["gridsquare"]

    def run(self):
        try:
            if not self.ctx.config_manager.config["EXP"]["enable_groupchat"]:
                return False

            self.emit_event()
            self.logger.info(self.log_message())

            # NormTransmissionISS(self.ctx).prepare_and_transmit_data(self.origin, self.domain, self.gridsquare, self.data, self.priority, self.msgtype)

            tx_thread = threading.Thread(
                target=NormTransmissionISS(self.ctx).prepare_and_transmit_data,
                args=(self.origin, self.domain, self.gridsquare, self.data, self.priority, self.msgtype),
                daemon=True,
            )
            tx_thread.start()
            return True

        except Exception as e:
            self.log(f"Error starting NORM transmission: {e}", isWarning=True)

        return False
