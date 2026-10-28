"""rigctld PTT: the configured RADIO.ptt_mode becomes the T command's value,
and keying leaves rigctld connected. set_ptt() used to turn the mode into a
plain int, then log its .name: the AttributeError ("'int' object has no
attribute 'name'") marked rigctld disconnected after every key-up."""

import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import rigctld


def make_radio(ptt_mode):
    """A connected rigctld radio with its command channel mocked."""
    radio = rigctld.radio.__new__(rigctld.radio)
    radio.connected = True
    radio.ptt_mode = ptt_mode
    radio.parameters = {"chk_vfo": False, "vfo": None, "ptt": False}
    radio.log = mock.Mock()
    radio.send_command = mock.Mock()
    return radio


class TestRigctldPtt(unittest.TestCase):
    def test_configured_mode_keys_and_stays_connected(self):
        for name, value in (("TX", 1), ("TX_MIC", 2), ("TX_DATA", 3)):
            with self.subTest(ptt_mode=name):
                radio = make_radio(name)
                self.assertTrue(radio.set_ptt(True))
                radio.send_command.assert_called_once_with(f"T {value}")
                self.assertTrue(radio.connected)
                self.assertTrue(radio.parameters["ptt"])
                radio.log.warning.assert_not_called()

    def test_unkey(self):
        radio = make_radio("TX_DATA")
        self.assertTrue(radio.set_ptt(False))
        radio.send_command.assert_called_once_with("T 0")
        self.assertTrue(radio.connected)
        self.assertFalse(radio.parameters["ptt"])

    def test_invalid_mode_falls_back_to_tx(self):
        radio = make_radio("NOT_A_MODE")
        self.assertTrue(radio.set_ptt(True))
        radio.send_command.assert_called_once_with("T 1")
        self.assertTrue(radio.connected)

    def test_command_error_still_disconnects(self):
        radio = make_radio("TX")
        radio.send_command.side_effect = OSError("broken pipe")
        self.assertFalse(radio.set_ptt(True))
        self.assertFalse(radio.connected)

    def test_not_connected(self):
        radio = make_radio("TX")
        radio.connected = False
        self.assertFalse(radio.set_ptt(True))
        radio.send_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
