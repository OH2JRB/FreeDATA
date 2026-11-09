from freedata_server.command import TxCommand
from freedata_server.codec2 import FREEDV_MODE


class TestCommand(TxCommand):
    """Command for transmitting test frames.

    This command builds and transmits test frames, in datac4: every station
    decodes it (Demodulator.set_decode_mode), so the other station logs and
    shows the test frame with its SNR. data_ofdm_500, used before, is decoded
    only inside an ARQ session, so a test frame came through unseen.
    """

    def build_frame(self):
        """Builds a test frame.

        This method uses the frame factory to build a test frame using the
        specified FreeDV mode.

        Returns:
            bytearray: The built test frame.
        """
        return self.frame_factory.build_test(self.get_tx_mode().name)

    def get_tx_mode(self):
        """Returns the transmission mode for test frames.

        datac4, or data_vhf_1 with EXP.enable_vhf.

        Returns:
            codec2.FREEDV_MODE: The FreeDV mode for test frames.
        """
        if self.ctx.config_manager.config["EXP"].get("enable_vhf"):
            mode = FREEDV_MODE.data_vhf_1
        else:
            mode = FREEDV_MODE.datac4

        return mode
