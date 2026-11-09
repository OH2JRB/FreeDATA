"""A saved audio device that ALSA numbers differently now: its id covers the
name, which holds the card number ("(hw:1,0)"), so after a USB reconnect or a
reboot with other USB audio the radio's codec came back as another card and
its id was not found. find_renumbered_device finds it under the new number."""

import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import audio


def devices(*entries):
    return [{"name": n, "hostapi": 0, "max_input_channels": i, "max_output_channels": o} for n, i, o in entries]


class FakeSoundDevice:
    def __init__(self, devs):
        self.devs = devs

    def query_devices(self, device=None, kind=None):
        return self.devs

    def query_hostapis(self, index):
        return {"name": "ALSA"}


def saved_id(name):
    return audio.device_crc({"name": name, "hostapi": 0})


class TestRenumbered(unittest.TestCase):
    def lookup(self, crc, devs, is_input=True):
        with mock.patch.object(audio, "get_sounddevice", return_value=FakeSoundDevice(devs)):
            return audio.get_device_index_from_crc(crc, is_input)

    def test_found_under_its_new_card_number(self):
        crc = saved_id("USB Audio CODEC: - (hw:1,0)")
        devs = devices(("HDA Intel PCH: HDMI 0 (hw:0,3)", 0, 8), ("USB Audio CODEC: - (hw:2,0)", 2, 2))
        self.assertEqual(self.lookup(crc, devs), (1, "USB Audio CODEC: - (hw:2,0)"))
        self.assertEqual(self.lookup(crc, devs, is_input=False), (1, "USB Audio CODEC: - (hw:2,0)"))

    def test_unchanged_id_still_direct(self):
        crc = saved_id("USB Audio CODEC: - (hw:1,0)")
        devs = devices(("USB Audio CODEC: - (hw:1,0)", 2, 2))
        self.assertEqual(self.lookup(crc, devs), (0, "USB Audio CODEC: - (hw:1,0)"))

    def test_two_codecs_are_not_guessed_between(self):
        crc = saved_id("USB Audio CODEC: - (hw:1,0)")
        devs = devices(("USB Audio CODEC: - (hw:2,0)", 2, 2), ("USB Audio CODEC: - (hw:3,0)", 2, 2))
        self.assertIsNone(self.lookup(crc, devs))

    def test_other_device_number_is_not_matched(self):
        crc = saved_id("USB Audio CODEC: - (hw:1,0)")
        devs = devices(("USB Audio CODEC: - (hw:2,1)", 2, 2))
        self.assertIsNone(self.lookup(crc, devs))

    def test_device_gone(self):
        crc = saved_id("USB Audio CODEC: - (hw:1,0)")
        self.assertIsNone(self.lookup(crc, devices(("HDA Intel PCH: HDMI 0 (hw:0,3)", 0, 8))))


if __name__ == "__main__":
    unittest.main()
