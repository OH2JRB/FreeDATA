import threading

from freedata_server import channel_gate
from freedata_server import data_frame_factory
from freedata_server import frame_handler


class CQFrameHandler(frame_handler.FrameHandler):
    """Handles received CQ frames.

    This class processes received CQ (Calling Any Station) frames and sends
    a QRV (Ready to Receive) frame as an acknowledgement if the station is
    not currently busy with ARQ. It also checks for queued messages to be
    sent based on the configuration.
    """

    # def should_respond(self):
    #    self.logger.debug(f"Respond to CQ: {self.ctx.config_manager.config['MODEM']['respond_to_cq']}")
    #    return bool(self.ctx.config_manager.config['MODEM']['respond_to_cq'] and not self.ctx.state_manager.getARQ())

    def follow_protocol(self):
        """Processes the received CQ frame.

        This method checks if the modem is currently busy with ARQ. If not,
        it sends a QRV frame as an acknowledgement and checks for queued
        messages to send.
        """

        if self.ctx.state_manager.getARQ():
            return

        self.logger.debug(
            f"[Modem] Responding to request from [{self.details['frame']['origin']}]",
            snr=self.details["snr"],
        )

        # in its own thread: waiting for a clear channel must not hold up the
        # receive worker, which decodes every other frame
        threading.Thread(target=self.send_ack, name="CQ reply", daemon=True).start()
        self.check_for_queued_message()

    def send_ack(self):
        factory = data_frame_factory.DataFrameFactory(self.ctx)
        qrv_frame = factory.build_qrv(self.details["snr"])

        # Several stations may answer the same CQ: wait for a clear channel and
        # a random delay. The CQ itself was just heard, so the FreeDATA holdoff
        # is short here (CHANNEL.cq_reply_holdoff); a reply still not sent
        # after CHANNEL.cq_reply_max_wait s of FreeDATA traffic is dropped.
        if not channel_gate.wait_for_clear_channel(
            self.ctx,
            "CQ reply",
            max_wait_setting="cq_reply_max_wait",
            holdoff_setting="cq_reply_holdoff",
            random_delay_setting="cq_reply_random_delay",
        ):
            return

        self.transmit(qrv_frame)
