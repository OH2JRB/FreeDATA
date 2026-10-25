"""Checks of the HF fading channel simulator in tools/channel_sim.py, used to
measure the modes on auroral paths (tools/fading_measure.py)."""

import os
import sys
import unittest

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import channel_sim as cs  # noqa: E402

FS = cs.FS


def measured_spread(taps):
    """Two-sided Doppler spread (2 sigma) from the power spectrum of the taps."""
    psd = np.abs(np.fft.fft(taps)) ** 2
    freqs = np.fft.fftfreq(len(taps), 1 / FS)
    return 2 * np.sqrt(np.sum(psd * freqs**2) / np.sum(psd))


class TestChannelModel(unittest.TestCase):
    def test_fading_taps(self):
        rng = np.random.default_rng(3)
        for spread in (1.0, 10.0, 30.0):
            taps = cs.fading_taps(FS * 60, spread, rng)
            self.assertAlmostEqual(np.mean(np.abs(taps) ** 2), 1.0, places=6)
            self.assertAlmostEqual(measured_spread(taps) / spread, 1.0, delta=0.15)
        np.testing.assert_array_equal(cs.fading_taps(100, 0, rng), np.ones(100))

    def test_snr_in_3khz(self):
        rng = np.random.default_rng(4)
        t = np.arange(FS * 10) / FS
        x = 1000 * np.sin(2 * np.pi * 1500 * t)
        for snr in (0, 10):
            noise = cs.apply_channel(x, cs.CHANNELS["awgn"], snr, rng) - x
            noise_3k = np.mean(noise**2) * 3000 / (FS / 2)
            self.assertAlmostEqual(10 * np.log10(np.mean(x**2) / noise_3k), snr, delta=0.2)

    def test_fading_keeps_mean_power(self):
        rng = np.random.default_rng(5)
        t = np.arange(FS * 60) / FS
        x = 1000 * np.sin(2 * np.pi * 1500 * t)
        y = cs.apply_channel(x, cs.CHANNELS["hl_moderate"], 100, rng)
        self.assertAlmostEqual(np.mean(y**2) / np.mean(x**2), 1.0, delta=0.15)

    def test_doppler_shift(self):
        rng = np.random.default_rng(6)
        t = np.arange(FS * 2) / FS
        x = np.sin(2 * np.pi * 1000 * t)
        y = cs.apply_channel(x, cs.CHANNELS["awgn"], 100, rng, doppler_shift_hz=50)
        freqs = np.fft.rfftfreq(len(y), 1 / FS)
        self.assertAlmostEqual(freqs[np.argmax(np.abs(np.fft.rfft(y)))], 1050, delta=1)


class TestModesThroughChannel(unittest.TestCase):
    def test_clean_channel_decodes(self):
        # the ARQ signalling mode and the lowest ARQ speed level
        for mode in (cs.codec2.FREEDV_MODE.signalling_ack, cs.codec2.FREEDV_MODE.datac4):
            result = cs.run_mode(mode, cs.CHANNELS["awgn"], 20, frames=3)
            self.assertEqual(result.frames_ok, 3, mode.name)

    def test_fsk_candidate_decodes(self):
        candidate = next(c for c in cs.FSK_CANDIDATES if c.name == "fsk4_100")
        self.assertEqual(candidate.bandwidth_hz, 500)
        result = cs.run_mode(candidate, cs.CHANNELS["awgn"], 20, frames=3, continuous=True)
        self.assertEqual(result.frames_ok, 3)
        self.assertEqual(result.payload_bytes, 30)


if __name__ == "__main__":
    unittest.main()
