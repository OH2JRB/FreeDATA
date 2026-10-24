"""SendMessageCommand.transmit() must send a queued message only once, even
when the API and the scheduler start sending it at the same time."""

import threading
import unittest
import unittest.mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server import command_message_send
from freedata_server.message_system_db_messages import DatabaseManagerMessages


class TestSingleMessageSend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.rf_modem = unittest.mock.Mock()  # transmit() needs a modem

    @classmethod
    def tearDownClass(cls):
        cls.ctx.rf_modem = None
        cls.ctx.shutdown()

    def test_concurrent_transmit_sends_once(self):
        command = command_message_send.SendMessageCommand(
            self.ctx, {"destination": "AA1AAA-5", "body": "only once, please"}
        )
        # other tests share the database and may have left queued messages
        queued = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)

        with (
            unittest.mock.patch.object(DatabaseManagerMessages, "get_first_queued_message", return_value=queued),
            unittest.mock.patch.object(command_message_send.np.random, "randint", return_value=0),
            unittest.mock.patch.object(command_message_send, "ARQSessionISS") as iss_class,
        ):
            # like the API command and the scheduler: two commands, same message
            senders = [
                threading.Thread(target=command_message_send.SendMessageCommand(self.ctx, queued).transmit)
                for _ in range(2)
            ]
            for sender in senders:
                sender.start()
            for sender in senders:
                sender.join(30)

        self.assertEqual(iss_class.call_count, 1)
        iss_class.return_value.start.assert_called_once()
        self.assertEqual(self.ctx.state_manager.messages_in_transmit, set())
        sent = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)
        self.assertEqual(sent["status"], "transmitting")


if __name__ == "__main__":
    unittest.main()
