"""The real-time ARQ session simulator (tools/arq_session_sim.py): its audio bus
and streaming channel, and one short session on a clean channel."""

import os
import sys
import unittest

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import arq_session_sim as sim  # noqa: E402
import channel_sim as cs  # noqa: E402


class TestStreamChannel(unittest.TestCase):
    def test_every_transmission_has_the_same_power(self):
        channel = sim.StreamChannel(cs.CHANNELS["awgn"], np.random.default_rng(1), 10)
        for level in (100.0, 5000.0):
            x = np.random.default_rng(2).standard_normal(8000) * level
            y = channel.apply(x, 0)
            self.assertAlmostEqual(np.sqrt(np.mean(y**2)), sim.TX_RMS, delta=1)

    def test_fading_goes_on_between_transmissions(self):
        # the same gains at the same sample time, whichever transmission it is in
        channel = sim.StreamChannel(cs.CHANNELS["hl_disturbed"], np.random.default_rng(3), 30)
        tone = np.sin(2 * np.pi * 1500 * np.arange(16000) / sim.FS)
        whole = channel.apply(tone, 8000)
        second_half = channel.apply(tone[8000:], 16000)
        middle = slice(2000, 6000)  # away from the edges of the analytic signal
        np.testing.assert_allclose(whole[8000:][middle], second_half[middle], atol=0.05 * sim.TX_RMS)

    def test_mean_power_over_fading(self):
        channel = sim.StreamChannel(cs.CHANNELS["hl_moderate"], np.random.default_rng(4), 120)
        x = np.random.default_rng(5).standard_normal(sim.FS * 100)
        y = channel.apply(x, 0)
        self.assertAlmostEqual(np.mean(y**2) / sim.TX_RMS**2, 1.0, delta=0.2)


class TestAudioBus(unittest.TestCase):
    def test_delivery(self):
        bus = sim.AudioBus(cs.CHANNELS["awgn"], 10, seed=1, seconds=10)
        heard = {"a": [], "b": []}
        bus.add_station("a", heard["a"].append)
        bus.add_station("b", heard["b"].append)
        bus.send("a", np.full(2 * sim.BLOCK, 1000, dtype=np.int16))
        # three blocks, without the pacing
        for _ in range(3):
            self._step(bus)
        a = np.concatenate(heard["a"]).astype(float)
        b = np.concatenate(heard["b"]).astype(float)
        # the sender does not hear itself; the other station from the next block on
        self.assertLess(np.std(a), 2 * bus.noise_sigma)
        self.assertLess(np.std(b[: sim.BLOCK]), 2 * bus.noise_sigma)
        self.assertAlmostEqual(np.mean(b[sim.BLOCK : 3 * sim.BLOCK]), sim.TX_RMS, delta=0.3 * sim.TX_RMS)
        # noise for 10 dB SNR in 3 kHz
        expected = np.sqrt(sim.TX_RMS**2 / 10 * (sim.FS / 2) / 3000)
        self.assertAlmostEqual(np.std(a), expected, delta=0.1 * expected)

    @staticmethod
    def _step(bus):
        """One block of AudioBus.run."""
        bus.running = True
        original = sim.time.sleep
        sim.time.sleep = lambda s: setattr(bus, "running", False)
        try:
            bus.run()
        finally:
            sim.time.sleep = original


class TestSession(unittest.TestCase):
    def test_clean_channel(self):
        # real time: about 20 s
        result = sim.run_session("awgn", 10, 100, max_seconds=120)
        self.assertTrue(result["success"], result)
        self.assertGreater(result["bps"], 0)


if __name__ == "__main__":
    unittest.main()
