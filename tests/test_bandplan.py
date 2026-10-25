"""Sanity checks for the bundled IARU band plan data and its lookup helpers."""

import unittest

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import bandplan


class TestBandplanData(unittest.TestCase):
    def test_metadata(self):
        data = bandplan.load_bandplan()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(sorted(data["regions"]), ["1", "2", "3"])
        for region in data["regions"].values():
            self.assertTrue(region["sources"])
            for source in region["sources"]:
                self.assertTrue(source["url"].startswith("https://"))
                self.assertTrue(source["version"])

    def test_segments_inside_band_edges(self):
        known_modes = set(bandplan.load_bandplan()["mode_tags"])
        for region in ("1", "2", "3"):
            for band in bandplan.get_bands(region):
                self.assertLess(band["start_hz"], band["end_hz"])
                self.assertTrue(band["segments"], band["name"])
                for seg in band["segments"]:
                    self.assertLessEqual(band["start_hz"], seg["start_hz"], (region, band["name"], seg))
                    self.assertLess(seg["start_hz"], seg["end_hz"], (region, band["name"], seg))
                    self.assertLessEqual(seg["end_hz"], band["end_hz"], (region, band["name"], seg))
                    self.assertTrue(set(seg["modes"]) <= known_modes, seg["modes"])

    def test_hf_bands_present(self):
        for region in ("1", "2", "3"):
            names = {band["name"] for band in bandplan.get_bands(region)}
            for name in ("160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m"):
                self.assertIn(name, names)


class TestBandplanLookup(unittest.TestCase):
    def test_find_band(self):
        self.assertEqual(bandplan.find_band(1, 14_100_000)["name"], "20m")
        self.assertEqual(bandplan.find_band(1, 70_200_000)["name"], "4m")
        self.assertIsNone(bandplan.find_band(2, 70_200_000))  # 4 m is a Region 1 band only
        self.assertEqual(bandplan.find_band(2, 7_250_000)["name"], "40m")
        self.assertIsNone(bandplan.find_band(1, 7_250_000))  # 40 m ends at 7.2 MHz in Region 1
        self.assertIsNone(bandplan.find_band(3, 11_000_000))

    def test_find_segments(self):
        segments = bandplan.find_segments(1, 14_105_000)
        self.assertEqual(len(segments), 1)
        self.assertIn("digimodes", segments[0]["modes"])
        self.assertTrue(segments[0]["acds"])

    def test_fits_in_segment(self):
        # 2.3 kHz wide data signal in the Region 1 20 m all-mode digimode segment
        self.assertTrue(bandplan.fits_in_segment(1, 14_105_000, 14_107_300))
        # too wide for the 500 Hz narrow band digimode segment
        self.assertFalse(bandplan.fits_in_segment(1, 14_080_000, 14_082_300))
        # CW only
        self.assertFalse(bandplan.fits_in_segment(1, 14_010_000, 14_010_100))
        # beacon segment
        self.assertFalse(bandplan.fits_in_segment(1, 14_099_500, 14_100_000))


if __name__ == "__main__":
    unittest.main()
