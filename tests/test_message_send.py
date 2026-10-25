"""SendMessageCommand.transmit() must send a queued message only once, even
when the API and the scheduler start sending it at the same time."""

import itertools
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


class TestEncryptedSendOnAmateurBand(unittest.TestCase):
    """Encrypted messages must not be sent while the radio is on an amateur band."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.rf_modem = unittest.mock.Mock()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.rf_modem = None
        cls.ctx.state_manager.radio_frequency = 0
        cls.ctx.shutdown()

    def transmit(self, frequency, encrypt=True):
        self.ctx.state_manager.radio_frequency = frequency
        command = command_message_send.SendMessageCommand(self.ctx, {"destination": "AA1AAA-5", "body": "secret"})
        queued = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)
        with (
            unittest.mock.patch.object(DatabaseManagerMessages, "get_first_queued_message", return_value=queued),
            unittest.mock.patch.object(command_message_send, "encryption_wanted", return_value=encrypt),
            unittest.mock.patch.object(command_message_send.SendMessageCommand, "key_missing", return_value=False),
            unittest.mock.patch.object(command_message_send.np.random, "randint", return_value=0),
            # skip the 10 s free channel check
            unittest.mock.patch.object(command_message_send.time, "time", side_effect=itertools.count(step=100)),
            unittest.mock.patch.object(self.ctx.event_manager, "send_encryption_amateur_band") as event,
            unittest.mock.patch.object(command_message_send, "ARQSessionISS") as iss_class,
        ):
            sender = command_message_send.SendMessageCommand(self.ctx, queued)
            sender.arq_data_type_handler = unittest.mock.Mock()
            sender.arq_data_type_handler.prepare.return_value = (b"data", 0)
            sender.transmit()
        status = DatabaseManagerMessages(self.ctx).get_message_by_id(command.message.id)["status"]
        return iss_class, event, status

    def test_encrypted_on_amateur_band_is_not_sent(self):
        iss_class, event, status = self.transmit(14093000)
        iss_class.assert_not_called()
        event.assert_called_once_with("AA1AAA-5", 14093000)
        self.assertEqual(status, "failed")

    def test_encrypted_outside_amateur_bands_is_sent(self):
        iss_class, event, status = self.transmit(15000000)
        iss_class.return_value.start.assert_called_once()
        event.assert_not_called()
        self.assertEqual(status, "transmitting")

    def test_plain_on_amateur_band_is_sent(self):
        iss_class, event, status = self.transmit(14093000, encrypt=False)
        iss_class.return_value.start.assert_called_once()
        event.assert_not_called()
        self.assertEqual(status, "transmitting")

    def test_radio_event_flags_amateur_band(self):
        self.ctx.state_manager.radio_frequency = 7050000
        self.assertTrue(self.ctx.state_manager.get_radio_status()["radio_amateur_band"])
        self.ctx.state_manager.radio_frequency = 15000000
        self.assertFalse(self.ctx.state_manager.get_radio_event(True)["radio_amateur_band"])


if __name__ == "__main__":
    unittest.main()
