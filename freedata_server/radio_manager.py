from freedata_server import rigctld
from freedata_server import flrig
from freedata_server import rigdummy
from freedata_server import serial_ptt
import structlog
import threading


class RadioManager:
    log = structlog.get_logger("radio_manager")

    def __init__(self, ctx):
        self.ctx = ctx

        self.radiocontrol = self.ctx.config_manager.config["RADIO"]["control"]

        self.refresh_rate = 1
        self.stop_event = threading.Event()
        self.update_thread = threading.Thread(target=self.update_parameters, daemon=True)
        self._init_rig_control()

    def _init_rig_control(self):
        # Check how we want to control the radio
        if self.radiocontrol in ["rigctld", "rigctld_bundle"]:
            self.radio = rigctld.radio(self.ctx)
        elif self.radiocontrol == "serial_ptt":
            self.radio = serial_ptt.radio(self.ctx)
        elif self.radiocontrol == "flrig":
            self.radio = flrig.radio(self.ctx)
        else:
            self.radio = rigdummy.radio()

        self.update_thread.start()

    def apply(self, action, call, **values):
        """Runs one call that changes the radio and logs it to the server log:
        the action, the values asked for, the radio control in use and the
        outcome. The backends' set_* return True when the radio took the
        change, False when it failed (the backend logs why) and None when the
        backend cannot do it (serial PTT, no radio control). An exception is
        logged and raised again."""
        try:
            result = call()
        except Exception as err:
            self.log.error(
                "[RADIO] Radio control failed", action=action, control=self.radiocontrol, error=str(err), **values
            )
            raise
        if result is False:
            self.log.warning("[RADIO] Radio control failed", action=action, control=self.radiocontrol, **values)
        elif result is None:
            self.log.info("[RADIO] Radio control not supported", action=action, control=self.radiocontrol, **values)
        else:
            self.log.info("[RADIO] Radio control applied", action=action, control=self.radiocontrol, **values)
        return result

    def set_ptt(self, state):
        def key():
            result = self.radio.set_ptt(state)
            # set disabled ptt twice for reducing chance of stuck PTT
            if not state:
                again = self.radio.set_ptt(state)
                if result is False:
                    result = again
            return result

        self.apply("set_ptt", key, ptt=bool(state))

        # send ptt state via socket interface
        try:
            if (
                self.ctx.config_manager.config["SOCKET_INTERFACE"]["enable"]
                and self.ctx.socket_interface_manager.command_server.command_handler
            ):
                self.ctx.socket_interface_manager.command_server.command_handler.socket_respond_ptt(state)
        except Exception as e:
            self.log.warning("[RADIO] Could not send the PTT state to the socket interface", error=str(e))

    def set_tuner(self, state):
        self.apply("set_tuner", lambda: self.radio.set_tuner(state), tuner=state)
        self.ctx.state_manager.set_radio("radio_tuner", state)

    def set_frequency(self, frequency):
        self.apply("set_frequency", lambda: self.radio.set_frequency(frequency), frequency=frequency)
        self.ctx.state_manager.set_radio("radio_frequency", frequency)

    def set_mode(self, mode):
        self.apply("set_mode", lambda: self.radio.set_mode(mode), mode=mode)
        self.ctx.state_manager.set_radio("radio_mode", mode)

    def set_clock(self):
        """Sets the radio's clock to the server's local time, only on the
        user's request. Only rigctld can set it (rigctld.radio.set_clock)."""
        if not hasattr(self.radio, "set_clock"):
            self.log.info("[RADIO] Radio control not supported", action="set_clock", control=self.radiocontrol)
            raise rigctld.RadioClockError(
                "unsupported_backend",
                f"Setting the radio clock needs rigctld radio control, not {self.radiocontrol}",
            )
        try:
            result = self.radio.set_clock()
        except rigctld.RadioClockError as err:
            self.log.warning(
                "[RADIO] Radio control failed",
                action="set_clock",
                control=self.radiocontrol,
                reason=err.reason,
                error=str(err),
            )
            raise
        self.log.info("[RADIO] Radio control applied", action="set_clock", control=self.radiocontrol, **result)
        return result

    def set_rf_level(self, level):
        self.apply("set_rf_level", lambda: self.radio.set_rf_level(level), rf_level=level)
        self.ctx.state_manager.set_radio("radio_rf_level", level)

    def update_parameters(self):
        while not self.stop_event.is_set():
            parameters = self.radio.get_parameters()

            self.ctx.state_manager.set_radio("radio_frequency", parameters["frequency"])
            self.ctx.state_manager.set_radio("radio_mode", parameters["mode"])
            self.ctx.state_manager.set_radio("radio_bandwidth", parameters["bandwidth"])
            self.ctx.state_manager.set_radio("radio_rf_level", parameters["rf"])
            self.ctx.state_manager.set_radio("radio_tuner", parameters["tuner"])

            if self.ctx.state_manager.isTransmitting():
                self.radio_alc = parameters["alc"]
                self.ctx.state_manager.set_radio("radio_swr", parameters["swr"])

            self.ctx.state_manager.set_radio("s_meter_strength", parameters["strength"])
            threading.Event().wait(self.refresh_rate)

    def stop(self):
        self.stop_event.set()
        self.radio.disconnect()
        self.radio.stop_service()
