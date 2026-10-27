"""helpers.check_callsign matches a CRC-24 against a callsign with each SSID
of a list, and its own SSID, without changing the list (callers pass the
config's STATION.ssid_list)."""

import unittest

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import helpers


def crc(callsign):
    return helpers.get_crc_24(callsign).hex()


class TestCheckCallsign(unittest.TestCase):
    def test_ssid_list_not_changed(self):
        ssid_list = [0, 2, 3]
        for _ in range(3):
            helpers.check_callsign("AA1AAA-1", crc("XX9XXX-1"), ssid_list)
            helpers.check_callsign("AA1AAA-1", crc("AA1AAA-1"), ssid_list)
        self.assertEqual(ssid_list, [0, 2, 3])

    def test_matches_ssid_from_list(self):
        self.assertEqual(helpers.check_callsign("AA1AAA-1", crc("AA1AAA-3"), [0, 3]), [True, "AA1AAA-3"])

    def test_matches_own_ssid_not_in_list(self):
        self.assertEqual(helpers.check_callsign("AA1AAA-7", crc("AA1AAA-7"), [0, 1]), [True, "AA1AAA-7"])
        self.assertEqual(helpers.check_callsign("AA1AAA-7", crc("AA1AAA-7"), []), [True, "AA1AAA-7"])

    def test_string_ssids(self):
        self.assertEqual(helpers.check_callsign("AA1AAA-1", crc("AA1AAA-4"), ["4"]), [True, "AA1AAA-4"])

    def test_callsign_without_ssid(self):
        self.assertEqual(helpers.check_callsign("AA1AAA", crc("AA1AAA"), []), [True, "AA1AAA"])
        self.assertEqual(helpers.check_callsign("AA1AAA", crc("AA1AAA-0"), []), [True, "AA1AAA-0"])

    def test_no_match(self):
        self.assertEqual(helpers.check_callsign("AA1AAA-1", crc("AA1AAA-5"), [0, 1]), [False, b""])
        self.assertEqual(helpers.check_callsign("AA1AAA-1", crc("BB2BBB-1"), [0, 1]), [False, b""])


if __name__ == "__main__":
    unittest.main()
