"""The early FSK decoder (freedata_server/fsk_early.py) passes FSK frames on
right after their end instead of up to a frame length later, and never before
their end (see freedata_server/docs/fading-channels.md, section 8)."""

import os
import sys
import unittest

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.fsk_early import FskEarlyDecoder

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import channel_sim as cs  # noqa: E402
import test_demodulator_fsk as tdf  # noqa: E402

HF_FSK = codec2.FREEDV_MODE.data_fsk4_50


def bursts(payloads, frames_per_burst, gap_ms=500):
    """FSK bursts of frames back to back; returns the audio and each frame's end."""
    modulator = cs._modulator(vhf=False)
    frame_samples = codec2.get_frame_duration(HF_FSK.value) * cs.FS
    audio = [np.zeros(4000, dtype=np.int16)]
    ends = []
    position = 4000
    for i in range(0, len(payloads), frames_per_burst):
        frames = [bytearray(p) for p in payloads[i : i + frames_per_burst]]
        burst = np.frombuffer(modulator.create_burst(HF_FSK, 1, gap_ms, frames), dtype=np.int16)
        ends += [position + (k + 1) * frame_samples for k in range(len(frames))]
        audio.append(burst)
        position += len(burst)
    audio.append(np.zeros(int(2 * frame_samples), dtype=np.int16))
    return np.concatenate(audio), ends


class TestFskEarlyDecoder(unittest.TestCase):
    def run_decoder(self, channel, snr, payloads, frames_per_burst=2):
        audio, ends = bursts(payloads, frames_per_burst)
        rx = cs.to_int16(cs.apply_channel(audio, cs.CHANNELS[channel], snr, np.random.default_rng(1)))
        decoder = FskEarlyDecoder(HF_FSK.value)
        found = {}
        for start in range(0, len(rx), 1600):
            for frame in decoder.push(rx[start : start + 1600], lambda freedv: (0, 0), known=found.__contains__):
                found[frame["payload"]] = (frame["end"], decoder.time)
        # the decoder hands out the frames as decoded, still whitened (codec2.whiten)
        found = {codec2.whiten(HF_FSK.value, k[:-2]): v for k, v in found.items()}
        results = []
        for payload, end in zip(payloads, ends):
            hit = [v for k, v in found.items() if k == payload]
            if hit:
                located, decoded_at = hit[0]
                results.append(((located - end) / cs.FS, (max(decoded_at, located) - end) / cs.FS))
        self.assertTrue(all(k in payloads for k in found))  # no wrong frame
        return results

    def payloads(self, n, seed=1):
        rng = np.random.default_rng(seed)
        return [rng.integers(0, 256, 30, dtype=np.uint8).tobytes() for _ in range(n)]

    def test_frames_found_at_their_end(self):
        results = self.run_decoder("awgn", 0, self.payloads(6))
        self.assertEqual(len(results), 6)
        for error, passed_on in results:
            self.assertLess(abs(error), 0.01)  # end located within 10 ms
            self.assertLess(passed_on, 1.1)  # the regular receiver: up to 5.4 s

    def test_auroral_path(self):
        # most, not all, frames: the regular receiver gets the rest
        results = self.run_decoder("hl_moderate", 10, self.payloads(6, seed=2))
        self.assertGreaterEqual(len(results), 4)
        self.assertTrue(all(abs(error) < 0.01 for error, _ in results))

    def test_nothing_from_noise(self):
        decoder = FskEarlyDecoder(HF_FSK.value)
        noise = (np.random.default_rng(3).standard_normal(20 * cs.FS) * 1000).astype(np.int16)
        frames = []
        for start in range(0, len(noise), 1600):
            frames += decoder.push(noise[start : start + 1600], lambda freedv: (0, 0))
        self.assertEqual(frames, [])


class TestEarlyInDemodulator(tdf.DemodulatorThread):
    MODE = HF_FSK

    def test_frame_passed_on_long_before_the_regular_receiver(self):
        # one frame and then 2 s of noise: the regular receiver needs a frame length
        # (5.44 s) after the end, so only the early decoder can deliver it
        payload = self.make_payloads(1, seed=6)[0]
        modulator = cs._modulator(vhf=False)
        frame = np.frombuffer(modulator.create_burst(HF_FSK, 1, 0, bytearray(payload)), dtype=np.int16)
        audio = np.concatenate([np.zeros(cs.FS // 2, dtype=np.int16), frame, np.zeros(2 * cs.FS, dtype=np.int16)])
        self.feed(cs.to_int16(cs.apply_channel(audio, cs.CHANNELS["awgn"], 10, np.random.default_rng(6))))
        # until the decoding thread has taken all the audio
        deadline = tdf.time.time() + 5
        while self.data["audio_buffer"].nbuffer >= self.data["nin"] and tdf.time.time() < deadline:
            tdf.time.sleep(0.01)
        tdf.time.sleep(0.2)
        self.assertEqual(self.decoded([payload]), [payload])

        # the regular receiver decodes it later as well: dropped as a duplicate
        self.drain()
        self.assertEqual(self.decoded([payload]), [])


if __name__ == "__main__":
    unittest.main()
