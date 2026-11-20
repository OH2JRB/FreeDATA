"""A message goes through listen before talk right before its ARQ session opens.

On the air on 2026-11-19 (test G2) a message's session keyed up over another
station's ping: SendMessageCommand passed the channel gate in run(), then waited
on its own (a random wait, a 10 s codec2 check), and the ISS waited up to 12 s
more on the SignalDetector before its first ARQ_SESSION_OPEN and then sent
anyway. Now the gate runs in transmit() just before the session is created,
and the first open frame goes out without the SignalDetector wait
(a P2P connection's ISS, which has no gate, keeps it).
"""

import unittest
import unittest.mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server import arq_session, command_message_send
from freedata_server.message_system_db_messages import DatabaseManagerMessages


class TestMessageGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.rf_modem = unittest.mock.Mock()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.rf_modem = None
        cls.ctx.shutdown()

    def new_message(self, body):
        """A queued message, deleted after the test: the test stations share the
        message database, and a message left queued would be sent by later tests
        as their first queued message."""
        command = command_message_send.SendMessageCommand(self.ctx, {"destination": "AA1AAA-5", "body": body})
        self.addCleanup(DatabaseManagerMessages(self.ctx).delete_message, command.message.id, allow_queued=True)
        return command

    def send(self, gate_result=True, from_scheduler=False, order=None):
        command = self.new_message("gate")
        queued = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)
        order = order if order is not None else []

        def gate(*args, **kwargs):
            order.append("gate")
            return gate_result

        def iss(*args, **kwargs):
            order.append("iss")
            session = unittest.mock.Mock()
            session.skip_next_clear_channel_wait = False
            session.start.side_effect = lambda: order.append(("start", session.skip_next_clear_channel_wait))
            return session

        with (
            unittest.mock.patch.object(DatabaseManagerMessages, "get_first_queued_message", return_value=queued),
            unittest.mock.patch.object(command_message_send, "encryption_wanted", return_value=False),
            unittest.mock.patch.object(command_message_send.SendMessageCommand, "key_missing", return_value=False),
            unittest.mock.patch.object(
                command_message_send.channel_gate, "wait_for_clear_channel", side_effect=gate
            ) as gate_mock,
            unittest.mock.patch.object(command_message_send, "ARQSessionISS", side_effect=iss) as iss_class,
        ):
            sender = command_message_send.SendMessageCommand(self.ctx, queued, from_scheduler=from_scheduler)
            sender.arq_data_type_handler = unittest.mock.Mock()
            sender.arq_data_type_handler.prepare.side_effect = lambda *a: order.append("prepare") or (b"data", 0)
            sender.transmit()
        status = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)["status"]
        return gate_mock, iss_class, status, order

    def test_gate_runs_right_before_the_session(self):
        gate, iss_class, status, order = self.send()
        # the first open goes out without the SignalDetector wait
        self.assertEqual(order, ["prepare", "gate", "iss", ("start", True)])
        self.assertEqual(status, "transmitting")
        _, kwargs = gate.call_args
        self.assertEqual(kwargs["max_wait_setting"], "freedata_max_wait")
        self.assertEqual(kwargs["stop_count"], self.ctx.state_manager.stop_count)

    def test_scheduler_waits_shorter(self):
        gate, _, _, _ = self.send(from_scheduler=True)
        self.assertEqual(gate.call_args.kwargs["max_wait_setting"], "beacon_max_wait")

    def test_busy_channel_leaves_the_message_queued(self):
        gate, iss_class, status, _ = self.send(gate_result=False)
        gate.assert_called_once()
        iss_class.assert_not_called()
        self.assertEqual(status, "queued")
        self.assertEqual(self.ctx.state_manager.messages_in_transmit, set())

    def test_run_does_not_gate_twice(self):
        command = self.new_message("run")
        with (
            unittest.mock.patch.object(
                command_message_send.channel_gate, "wait_for_clear_channel", return_value=True
            ) as gate,
            unittest.mock.patch.object(command_message_send.SendMessageCommand, "transmit") as transmit,
        ):
            command.run()
        transmit.assert_called_once()
        gate.assert_not_called()


class TestFirstOpenFrameNotHeld(unittest.TestCase):
    """transmit_frame skips the SignalDetector wait once after skip_next_clear_channel_wait."""

    def session(self):
        session = arq_session.ARQSession.__new__(arq_session.ARQSession)
        session.ctx = unittest.mock.Mock()
        session.log = unittest.mock.Mock()
        session.wait_for_clear_channel = unittest.mock.Mock()
        session.get_mode_by_speed_level = unittest.mock.Mock(return_value="datac4")
        session.speed_level = 0
        return session

    def test_skips_once_then_waits_again(self):
        session = self.session()
        session.skip_next_clear_channel_wait = True
        session.transmit_frame(bytearray(b"open"), mode="datac13")
        session.wait_for_clear_channel.assert_not_called()
        self.assertFalse(session.skip_next_clear_channel_wait)
        session.transmit_frame(bytearray(b"retry"), mode="datac13")
        session.wait_for_clear_channel.assert_called_once()
        self.assertEqual(session.ctx.rf_modem.transmit.call_count, 2)

    def test_waits_by_default(self):
        session = self.session()
        session.transmit_frame(bytearray(b"burst"), mode="datac13")
        session.wait_for_clear_channel.assert_called_once()

    def test_iss_start_keeps_the_wait(self):
        # a P2P connection starts its ISS without a gate: start() must not skip the wait
        from freedata_server import arq_session_iss

        iss = arq_session_iss.ARQSessionISS.__new__(arq_session_iss.ARQSessionISS)
        iss.ctx = unittest.mock.MagicMock()
        iss.ctx.config_manager.config = {"MODEM": {"maximum_bandwidth": 2438}}
        iss.id = 1
        iss.dxcall = "AA1AAA-5"
        iss.total_length = 10
        iss.protocol_version = 2
        iss.state = arq_session_iss.ISS_State.NEW
        iss.frame_factory = unittest.mock.Mock()
        iss.set_state = unittest.mock.Mock(return_value=True)
        iss.launch_twr = unittest.mock.Mock()
        with (
            unittest.mock.patch.object(arq_session_iss, "high_rate_modes_enabled", return_value=False),
            unittest.mock.patch.object(arq_session_iss, "fsk_modes_enabled", return_value=False),
        ):
            iss.start()
        iss.launch_twr.assert_called_once()
        self.assertFalse(iss.skip_next_clear_channel_wait)


if __name__ == "__main__":
    unittest.main()
