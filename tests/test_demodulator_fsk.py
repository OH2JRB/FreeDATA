"""The demodulator restarts codec2's FSK receiver after every decoded frame and
after transmitting, so FSK bursts that follow each other closely are not lost
(see freedata_server/docs/fading-channels.md, section 7)."""

import os
import queue
import sys
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.demodulator import Demodulator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import channel_sim as cs  # noqa: E402

FSK = codec2.FREEDV_MODE.data_vhf_1


def fsk_audio(payloads, gap_ms=500, snr_db=20, seed=1):
    """data_vhf_1 bursts of one frame each, with noise, as int16 samples."""
    modulator = cs._modulator(vhf=True)
    audio = [np.zeros(cs.FS // 2, dtype=np.int16)]
    for payload in payloads:
        audio.append(np.frombuffer(modulator.create_burst(FSK, 1, gap_ms, bytearray(payload)), dtype=np.int16))
    rng = np.random.default_rng(seed)
    return cs.to_int16(cs.apply_channel(np.concatenate(audio), cs.CHANNELS["awgn"], snr_db, rng))


class TestFskRestart(unittest.TestCase):
    def setUp(self):
        self.transmitting = False
        state_manager = mock.Mock()
        state_manager.isTransmitting.side_effect = lambda: self.transmitting
        self.ctx = types.SimpleNamespace(
            TESTMODE=True,
            config_manager=types.SimpleNamespace(config={"EXP": {"enable_vhf": True, "enable_ring_buffer": False}}),
            state_manager=state_manager,
            rf_modem=types.SimpleNamespace(data_queue_received=queue.Queue()),
            event_manager=mock.Mock(),
        )
        self.demod = Demodulator(self.ctx)
        self.data = self.demod.MODE_DICT[FSK.value]
        self.thread = threading.Thread(target=self.demod.demodulate_audio, args=[FSK.value], daemon=True)
        self.thread.start()

    def tearDown(self):
        self.ctx.TESTMODE = False
        self.demod.shutdown_flag.set()
        self.thread.join(5)

    def feed(self, samples, chunk=2000):
        buffer = self.data["audio_buffer"]
        for start in range(0, len(samples), chunk):
            part = samples[start : start + chunk]
            while buffer.nbuffer + len(part) > buffer.size:
                time.sleep(0.002)
            buffer.push(part)

    def drain(self):
        """Feed noise for two frame lengths, then wait until all audio is decoded."""
        rng = np.random.default_rng(9)
        self.feed((rng.standard_normal(2 * self.data["frame_samples"]) * 300).astype(np.int16))
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.data["audio_buffer"].nbuffer < self.data["nin"] and len(self.data["fsk_replay"]) == 0:
                break
            time.sleep(0.01)
        time.sleep(0.1)

    def decoded(self, payloads):
        received = []
        while not self.ctx.rf_modem.data_queue_received.empty():
            item = self.ctx.rf_modem.data_queue_received.get()
            received.append(bytes(item["payload"])[: len(payloads[0])])
        return received

    def make_payloads(self, n, seed=2):
        rng = np.random.default_rng(seed)
        size = self.data["bytes_per_frame"] - 2
        return [rng.integers(0, 256, size, dtype=np.uint8).tobytes() for _ in range(n)]

    def test_close_bursts_decode(self):
        # without the restart about half of such bursts are lost (simulator:
        # 6 of 10). With it 7 of these 8 decode: codec2 decodes a frame up to a bit more than a frame length
        # after its end, so replaying one frame length can start just after the
        # next burst has begun (here burst 3), see fading-channels.md section 7
        payloads = self.make_payloads(8)
        self.feed(fsk_audio(payloads, gap_ms=500))
        self.drain()
        received = self.decoded(payloads)
        self.assertEqual(len(received), len(set(received)))  # no frame twice
        self.assertTrue(set(received) <= set(payloads))
        self.assertGreaterEqual(len(received), 7)

    def test_reset_after_transmitting(self):
        ofdm = codec2.FREEDV_MODE.datac1.value
        ofdm_instance = self.demod.MODE_DICT[ofdm]["instance"]
        fsk_instance = self.data["instance"]

        self.transmitting = True
        self.demod.reset_data_sync()
        self.assertTrue(self.data["fsk_reset"])
        time.sleep(0.1)
        # not while transmitting
        self.assertIs(self.data["instance"], fsk_instance)

        self.transmitting = False
        deadline = time.time() + 5
        while self.data["fsk_reset"] and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.data["fsk_reset"])
        self.assertIsNot(self.data["instance"], fsk_instance)
        self.assertIs(self.demod.MODE_DICT[ofdm]["instance"], ofdm_instance)

        # the new receiver decodes
        payloads = self.make_payloads(2, seed=3)
        self.feed(fsk_audio(payloads))
        self.drain()
        self.assertEqual(sorted(self.decoded(payloads)), sorted(payloads))

    def test_history_keeps_one_frame(self):
        frame_samples = self.data["frame_samples"]
        self.assertGreater(frame_samples, 0)
        self.demod.remember_fsk_audio(FSK.value, np.arange(frame_samples + 100, dtype=np.int16))
        history = self.data["fsk_history"]
        self.assertEqual(len(history), frame_samples)
        self.assertEqual(history[-1], frame_samples + 99)

    def test_only_fsk_modes_restart(self):
        self.assertTrue(Demodulator.is_fsk_mode(FSK.value))
        self.assertFalse(Demodulator.is_fsk_mode(codec2.FREEDV_MODE.datac4.value))


if __name__ == "__main__":
    unittest.main()
