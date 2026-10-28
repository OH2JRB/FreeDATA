"""P2P connections between two stations in test mode: connecting, data in both
directions as single payload frames, larger data in an ARQ session inside the
connection, and disconnecting (freedata_server/docs/arq-sessions.md, section 7).

The socket interface is replaced by a mock on both stations, so the test sees
what the connection reports to its socket clients without opening ports
(test_socket_data_port.py covers the data port itself)."""

import queue
import threading
import time
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import helpers
from freedata_server.command_p2p_connection import P2PConnectionCommand
from freedata_server.context import AppContext
from freedata_server.frame_handler import FrameHandler
from freedata_server.p2p_connection import States

# Both stations use the test config's station callsign AA1AAA. The IRS's
# socket client set MYCALL to another callsign, and the connection, and the
# ARQ session that carries larger data inside it, are addressed to that.
ISS_CALL = "AA1AAA-1"
IRS_CALL = "BB2BBB-2"


def make_socket_interface(callsigns=None):
    """A stand-in for SocketInterfaceHandler: socket_interface_callsigns are
    the callsigns set with the MYCALL command, which a CONNECT may address."""
    socket_interface = mock.Mock()
    socket_interface.socket_interface_callsigns = callsigns
    return socket_interface


def wait_for(condition, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


class TestP2PConnection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx_ISS = cls.start_station()
        cls.ctx_IRS = cls.start_station()

        cls.channels_running = True
        cls.channels = [
            threading.Thread(target=cls.channel_worker, args=[cls.ctx_ISS, cls.ctx_IRS], daemon=True),
            threading.Thread(target=cls.channel_worker, args=[cls.ctx_IRS, cls.ctx_ISS], daemon=True),
        ]
        for channel in cls.channels:
            channel.start()

    @classmethod
    def start_station(cls):
        ctx = AppContext(testenv.config_path())
        ctx.TESTMODE = True
        testenv.start(ctx)
        # the service manager starts the modem, then sets the socket interface
        # from the config: replace it only after that
        if not wait_for(lambda: ctx.rf_modem is not None, 10):
            raise RuntimeError("modem did not start")
        time.sleep(0.5)
        return ctx

    @classmethod
    def tearDownClass(cls):
        cls.channels_running = False
        for channel in cls.channels:
            channel.join()
        cls.ctx_IRS.shutdown()
        cls.ctx_ISS.shutdown()

    @classmethod
    def channel_worker(cls, ctx_a, ctx_b):
        """Delivers every frame station A transmits to station B."""
        while cls.channels_running:
            try:
                mode, frames = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=0.5)
            except queue.Empty:
                continue
            # an ARQ burst is a list of frames sent back to back
            for frame in frames if isinstance(frames, list) else [frames]:
                frame = bytes(frame) + bytes(2)  # the CRC16 the modulator appends
                # signalling_ack frames carry no type byte; the dispatcher knows
                # them by the mode they were received in
                mode_name = "SIGNALLING_ACK" if len(frame) == 5 else mode
                ctx_b.service_manager.frame_dispatcher.process_data(frame, None, len(frame), 15, 0, mode_name=mode_name)

    def setUp(self):
        self.ctx_ISS.socket_interface_manager = make_socket_interface()
        self.ctx_IRS.socket_interface_manager = make_socket_interface([IRS_CALL])
        self.iss = None
        self.irs = None
        self.addCleanup(self.remove_sessions)

    def remove_sessions(self):
        for ctx, session in ((self.ctx_ISS, self.iss), (self.ctx_IRS, self.irs)):
            if session is None:
                continue
            if session.state not in (States.DISCONNECTED, States.FAILED):
                session.abort_connection()
            ctx.state_manager.p2p_connection_sessions.pop(session.session_id, None)

    def command_handler(self, ctx):
        return ctx.socket_interface_manager.command_server.command_handler

    def data_handler(self, ctx):
        return ctx.socket_interface_manager.data_server.data_handler

    def received_by(self, ctx):
        """The data the station passed to its data socket client so far."""
        return b"".join(c.args[0] for c in self.data_handler(ctx).send_data_to_client.call_args_list)

    def connect(self):
        command = P2PConnectionCommand(self.ctx_ISS, {"origin": ISS_CALL, "destination": IRS_CALL})
        self.iss = command.run()
        self.assertTrue(self.iss)
        self.assertTrue(
            wait_for(lambda: self.iss.session_id in self.ctx_IRS.state_manager.p2p_connection_sessions, 15),
            "the IRS never opened the connection",
        )
        self.irs = self.ctx_IRS.state_manager.p2p_connection_sessions[self.iss.session_id]
        self.assertTrue(wait_for(lambda: self.iss.state is States.CONNECTED, 15), self.iss.state)
        self.assertIs(self.irs.state, States.CONNECTED)

    def disconnect(self):
        self.iss.disconnect()
        self.assertTrue(wait_for(lambda: self.iss.state is States.DISCONNECTED, 15), self.iss.state)
        self.assertIs(self.irs.state, States.DISCONNECTED)
        self.command_handler(self.ctx_ISS).socket_respond_disconnected.assert_called()
        self.command_handler(self.ctx_IRS).socket_respond_disconnected.assert_called()

    def test_connect_and_disconnect(self):
        self.connect()
        self.assertTrue(self.iss.is_ISS)
        self.assertFalse(self.irs.is_ISS)
        self.assertEqual(self.irs.origin, ISS_CALL)
        self.assertEqual(self.irs.destination, IRS_CALL)
        self.command_handler(self.ctx_ISS).socket_respond_connected.assert_called_once_with(ISS_CALL, IRS_CALL, 2300)
        self.command_handler(self.ctx_IRS).socket_respond_connected.assert_called_once_with(ISS_CALL, IRS_CALL, 2300)
        self.disconnect()

    def test_payload_both_directions(self):
        self.connect()
        # 11 bytes or less compressed go in a signalling frame, up to 32 in datac4
        for data in (b"hi", b"HELLO WORLD, THIS IS A TEST"):
            with self.subTest(direction="ISS to IRS", data=data):
                before = self.received_by(self.ctx_IRS)
                self.iss.p2p_data_tx_queue.put(data)
                self.assertTrue(wait_for(lambda: self.received_by(self.ctx_IRS) == before + data, 20))
                self.assertTrue(wait_for(lambda: self.iss.state is States.CONNECTED, 15), self.iss.state)

        # the IRS sends once a heartbeat from the ISS hands it the turn
        data = b"answer from the IRS"
        self.irs.p2p_data_tx_queue.put(data)
        self.assertTrue(wait_for(lambda: self.received_by(self.ctx_ISS) == data, 30))
        self.disconnect()

    def test_large_payload_in_arq_session(self):
        self.connect()
        # random data doesn't compress: too large for a payload frame
        data = np.random.bytes(300)
        self.iss.p2p_data_tx_queue.put(data)
        self.assertTrue(wait_for(lambda: self.received_by(self.ctx_IRS) == data, 60), self.iss.state)
        self.assertTrue(wait_for(lambda: self.iss.state is States.CONNECTED, 15), self.iss.state)
        self.assertIsNone(self.iss.running_arq_session)
        self.assertNotIn(self.iss.session_id, self.ctx_ISS.state_manager.arq_iss_sessions)
        self.assertNotIn(self.iss.session_id, self.ctx_IRS.state_manager.arq_irs_sessions)
        self.disconnect()


class TestArqOpenForMycall(unittest.TestCase):
    """An ARQ_SESSION_OPEN to a MYCALL callsign is accepted only for the ARQ
    session of a P2P connection, which has the connection's id."""

    def is_for_me(self, destination, session_id, mycall=(IRS_CALL,), p2p_sessions=(7,)):
        ctx = mock.Mock()
        ctx.config_manager.config = {"STATION": {"mycall": "AA1AAA", "myssid": 1, "ssid_list": [0, 1, 2]}}
        ctx.socket_interface_manager = make_socket_interface(list(mycall))
        ctx.state_manager.p2p_connection_sessions = dict.fromkeys(p2p_sessions)
        handler = FrameHandler(ctx, "test")
        handler.details["frame"] = {
            "frame_type": "ARQ_SESSION_OPEN",
            "destination_crc": helpers.get_crc_24(destination).hex(),
            "session_id": session_id,
        }
        return handler.is_frame_for_me()

    def test_station_callsign(self):
        self.assertTrue(self.is_for_me("AA1AAA-2", session_id=99))

    def test_mycall_in_p2p_connection(self):
        self.assertTrue(self.is_for_me(IRS_CALL, session_id=7))

    def test_mycall_without_p2p_connection(self):
        self.assertFalse(self.is_for_me(IRS_CALL, session_id=99))

    def test_other_callsign_in_p2p_connection(self):
        self.assertFalse(self.is_for_me("CC3CCC-1", session_id=7))

    def test_no_mycall_set(self):
        self.assertFalse(self.is_for_me(IRS_CALL, session_id=7, mycall=()))


if __name__ == "__main__":
    unittest.main()
