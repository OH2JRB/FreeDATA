from freedata_server.command import TxCommand


class BeaconCommand(TxCommand):
    """Command for transmitting beacon frames.

    This command builds and transmits beacon frames, indicating the station's
    status (away from key or not).
    """

    # the scheduler thread runs it: on a channel with FreeDATA traffic, skip
    # this beacon rather than hold the scheduler; the next one comes anyway
    CHANNEL_MAX_WAIT_SETTING = "beacon_max_wait"

    def build_frame(self):
        """Builds a beacon frame.

        This method retrieves the station's "away from key" status from the
        state manager and uses it to build a beacon frame.

        Returns:
            bytearray: The built beacon frame.
        """
        beacon_state = self.ctx.state_manager.is_away_from_key
        return self.frame_factory.build_beacon(beacon_state)
