"""The SNR the IRS chooses the speed level by: the median of the last three
frames' readings. "self.snr or -10" made a 0 dB reading -10 dB, and a single
frame whose SNR codec2 put near 0 among 12 dB frames (data_ofdm_250_hr after
datac4 traffic, on the air 2026-11-08) dropped the session to the lowest
level for a burst."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session import ARQSession
from freedata_server.arq_session_irs import ARQSessionIRS


def session():
    s = ARQSession.__new__(ARQSession)
    s.recent_snrs = []
    return s


class TestLatestSnr(unittest.TestCase):
    def test_default_before_any_frame(self):
        self.assertEqual(session().latest_snr(), -10)

    def test_zero_is_a_reading(self):
        s = session()
        s.set_details(0, 0)
        self.assertEqual(s.latest_snr(), 0)

    def test_one_stray_reading_does_not_count(self):
        s = session()
        for snr in (12, 13, 0):
            s.set_details(snr, 0)
        self.assertEqual(s.latest_snr(), 12)

    def test_a_lasting_drop_counts(self):
        s = session()
        for snr in (12, 3, 2):
            s.set_details(snr, 0)
        self.assertEqual(s.latest_snr(), 3)

    def test_only_the_last_three(self):
        s = session()
        for snr in (20, 20, 20, 5, 6, 7):
            s.set_details(snr, 0)
        self.assertEqual(s.latest_snr(), 6)

    def test_missing_readings_are_skipped(self):
        s = session()
        for snr in (12, None, "n/a", 13):
            s.set_details(snr, 0)
        self.assertEqual(s.recent_snrs, [12, 13])
        self.assertEqual(s.latest_snr(), 12.5)


class TestIrsLevel(unittest.TestCase):
    def test_250_limit_keeps_level_5_through_a_zero_reading(self):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.fsk = False
        irs.high_rate = True
        irs.high_rate_hold = 0
        irs.speed_level = irs.previous_speed_level = irs.last_burst_level = 5
        irs.maximum_bandwidth = 250
        irs.step_down_hold = 0
        irs.recent_snrs = []
        irs.log = mock.Mock()
        demodulator = mock.Mock(**{"take_decode_failures.return_value": 0})
        irs.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(
                config={"MODEM": {"maximum_bandwidth": 250, "enable_fsk": True, "enable_high_rate": True}}
            ),
            state_manager=types.SimpleNamespace(channel_busy_slot=[False] * 5),
            rf_modem=types.SimpleNamespace(demodulator=demodulator),
        )
        for snr in (12, 13, 0):
            irs.set_details(snr, 0)
        self.assertEqual(irs.calibrate_speed_settings(), 5)


if __name__ == "__main__":
    unittest.main()
