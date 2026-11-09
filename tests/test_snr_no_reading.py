"""A frame without an SNR reading: calculate_snr returned 0 on an error, and
cut the reading toward 0 (int), so -0.9 dB read 0; the ARQ speed level took
such a 0 for a real 0 dB. It now rounds, gives None without a reading, and
the frame handler passes None to ARQ sessions and P2P connections (which leave
it out) while the frame's other uses keep 0 as before."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session import ARQSession
from freedata_server.demodulator import Demodulator
from freedata_server.frame_handler import FrameHandler


class TestCalculateSnr(unittest.TestCase):
    def demodulator(self):
        d = Demodulator.__new__(Demodulator)
        d.log = mock.Mock()
        return d

    def stats(self, value):
        def fake(freedv, sync, snr):
            snr._obj.value = value

        return fake

    def test_rounds_instead_of_cutting(self):
        d = self.demodulator()
        for value, expected in ((-0.9, -1), (0.4, 0), (12.6, 13), (-5.5, -6)):
            with mock.patch.object(codec2.api, "freedv_get_modem_stats", side_effect=self.stats(value)):
                self.assertEqual(d.calculate_snr(None), expected, value)

    def test_no_reading_on_error(self):
        d = self.demodulator()
        with mock.patch.object(codec2.api, "freedv_get_modem_stats", side_effect=OSError("no stats")):
            self.assertIsNone(d.calculate_snr(None))


def handler():
    ctx = types.SimpleNamespace(
        config_manager=types.SimpleNamespace(
            config={
                "STATION": {"mycall": "BB1BBB", "myssid": 2, "mygrid": "KP20jd", "enable_callsign_blacklist": False}
            }
        ),
        state_manager=mock.Mock(),
        event_manager=mock.Mock(),
    )
    return FrameHandler(ctx, "TEST")


class TestFrameHandler(unittest.TestCase):
    def test_unknown_snr(self):
        h = handler()
        h.handle({"frame_type": "TEST_FRAME", "frame_type_int": 255}, None, 0, None, 56)
        self.assertEqual(h.details["snr"], 0)  # activity, beacons, PSK Reporter as before
        self.assertIsNone(h.session_snr())

    def test_known_snr(self):
        h = handler()
        h.handle({"frame_type": "TEST_FRAME", "frame_type_int": 255}, -1, 0, None, 56)
        self.assertEqual(h.session_snr(), -1)


class TestSession(unittest.TestCase):
    def test_unknown_snr_left_out_and_histogram_repeats(self):
        s = ARQSession.__new__(ARQSession)
        s.recent_snrs = []
        s.snr_histogram, s.bpm_histogram, s.bps_histogram, s.time_histogram = [], [], [], []
        s.calculate_session_statistics = mock.Mock(return_value={"bytes_per_minute": 0, "bits_per_second": 0})
        for snr in (12, None, 13):
            s.set_details(snr, 0)
            s.update_histograms(0, 100)
        self.assertEqual(s.recent_snrs, [12, 13])
        self.assertEqual(s.snr_histogram, [12, 12, 13])
        self.assertEqual(s.latest_snr(), 12.5)


if __name__ == "__main__":
    unittest.main()
