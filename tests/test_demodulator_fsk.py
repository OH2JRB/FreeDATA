"""The demodulator restarts codec2's FSK receiver after every decoded frame and
after transmitting, so FSK bursts that follow each other closely are not lost
(see freedata_server/docs/fading-channels.md, section 7). The HF FSK mode
data_fsk4_50 is modulated and decoded the same way (section 8)."""

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
HF_FSK = codec2.FREEDV_MODE.data_fsk4_50


def fsk_audio(payloads, gap_ms=500, snr_db=20, seed=1, mode=FSK, channel="awgn"):
    """FSK bursts of one frame each, through the channel, as int16 samples."""
    modulator = cs._modulator(vhf=mode == FSK)
    audio = [np.zeros(cs.FS // 2, dtype=np.int16)]
    for payload in payloads:
        audio.append(np.frombuffer(modulator.create_burst(mode, 1, gap_ms, bytearray(payload)), dtype=np.int16))
    rng = np.random.default_rng(seed)
    return cs.to_int16(cs.apply_channel(np.concatenate(audio), cs.CHANNELS[channel], snr_db, rng))


class DemodulatorThread(unittest.TestCase):
    """Runs the decoding thread of MODE, fed through its audio buffer."""

    MODE = FSK

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
        self.data = self.demod.MODE_DICT[self.MODE.value]
        self.thread = threading.Thread(target=self.demod.demodulate_audio, args=[self.MODE.value], daemon=True)
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


class TestFskRestart(DemodulatorThread):
    def test_close_bursts_decode(self):
        # without the restart about half of such bursts are lost (simulator:
        # 6 of 10). With it and a replay of one frame length 7 of these 8 decoded:
        # codec2 decodes a frame up to a bit more than a frame length after its
        # end, so the replay could start just after the next burst had begun
        # (here burst 3), see fading-channels.md section 7. Replaying 1.5 frame
        # lengths, with duplicates dropped, and the early decoder find all 8
        payloads = self.make_payloads(8)
        self.feed(fsk_audio(payloads, gap_ms=500))
        self.drain()
        received = self.decoded(payloads)
        self.assertEqual(len(received), len(set(received)))  # no frame twice
        self.assertTrue(set(received) <= set(payloads))
        self.assertEqual(len(received), 8)

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

    def test_history_keeps_one_and_a_half_frames(self):
        frame_samples = self.data["frame_samples"]
        self.assertGreater(frame_samples, 0)
        self.demod.remember_fsk_audio(FSK.value, np.arange(2 * frame_samples, dtype=np.int16))
        history = self.data["fsk_history"]
        self.assertEqual(len(history), int(1.5 * frame_samples))
        self.assertEqual(history[-1], 2 * frame_samples - 1)

    def test_only_fsk_modes_restart(self):
        self.assertTrue(Demodulator.is_fsk_mode(FSK.value))
        self.assertFalse(Demodulator.is_fsk_mode(codec2.FREEDV_MODE.datac4.value))


class TestHfFsk(DemodulatorThread):
    MODE = HF_FSK

    def test_mode(self):
        self.assertTrue(Demodulator.is_fsk_mode(HF_FSK.value))
        self.assertEqual(self.data["bytes_per_frame"], 32)  # 30 bytes of payload, CRC
        self.assertAlmostEqual(self.data["frame_samples"] / cs.FS, 5.44)
        config = codec2.fsk_configurations[HF_FSK.value]
        # tones 1275 to 1725 Hz, centred like the other modes
        self.assertEqual(config.first_tone + (config.M - 1) * config.tone_spacing / 2, 1500)

    def test_sent_without_preamble(self):
        modulator = cs._modulator(vhf=False)
        burst = modulator.create_burst(HF_FSK, 1, 0, bytearray(self.make_payloads(1)[0]))
        self.assertEqual(len(burst) // 2, self.data["frame_samples"])

    def test_whitened(self):
        # a zero-padded frame (ARQ signalling) must not come out as nearly one tone
        self.assertEqual(codec2._prbs9(4).hex(), "07be2e64")
        payload = bytes(range(14)) + bytes(16)
        self.assertEqual(codec2.whiten(HF_FSK.value, codec2.whiten(HF_FSK.value, payload)), payload)
        self.assertEqual(codec2.whiten(FSK.value, payload), payload)  # data_vhf_1 as before
        burst = np.frombuffer(cs._modulator(vhf=False).create_burst(HF_FSK, 1, 0, bytearray(bytes(30))), np.int16)
        spectrum = np.abs(np.fft.rfft(burst.astype(float))) ** 2
        freqs = np.fft.rfftfreq(len(burst), 1 / cs.FS)
        config = codec2.fsk_configurations[HF_FSK.value]
        shares = [
            spectrum[abs(freqs - (config.first_tone + k * config.tone_spacing)) < 40].sum() / spectrum.sum()
            for k in range(config.M)
        ]
        self.assertLess(max(shares), 0.4)  # unwhitened: 0.64 on one tone

    def test_repeat_passed_on_again(self):
        # an ARQ repeat of the same frame, 15 s later: no duplicate, it is new
        payload = bytes([7]) * 14 + bytes(16)
        self.feed(fsk_audio([payload, payload], gap_ms=15000, snr_db=20, mode=HF_FSK))
        self.drain()
        self.assertEqual(self.decoded([payload]), [payload, payload])

    def test_zero_padded_frames_decode(self):
        # through the demodulator, whitened and back: the payload as sent
        payloads = [bytes([i]) * 4 + bytes(26) for i in range(1, 4)]
        self.assert_bursts_decode(payloads, 2, snr_db=10)

    def assert_bursts_decode(self, payloads, minimum, **channel):
        self.feed(fsk_audio(payloads, mode=HF_FSK, **channel))
        self.drain()
        received = self.decoded(payloads)
        self.assertEqual(len(received), len(set(received)))  # no frame twice
        self.assertTrue(set(received) <= set(payloads))
        self.assertGreaterEqual(len(received), minimum)

    def test_bursts_decode(self):
        # 9 to 10 of 10 in the simulator; as with data_vhf_1, a burst is
        # sometimes lost when codec2 decodes the one before it late
        self.assert_bursts_decode(self.make_payloads(4), 3, snr_db=0)

    def test_auroral_path(self):
        # 10 Hz of Doppler spread, where every OFDM mode fails (fading-channels.md)
        self.assert_bursts_decode(self.make_payloads(4, seed=4), 3, snr_db=10, channel="hl_moderate")

    def test_frames_back_to_back(self):
        # ARQ sends several FSK frames in one transmission (FSK_FRAMES_PER_BURST);
        # the receiver restart after each frame must not lose the next one
        payloads = self.make_payloads(4, seed=5)
        modulator = cs._modulator(vhf=False)
        burst = modulator.create_burst(HF_FSK, 1, 500, [bytearray(p) for p in payloads])
        audio = np.concatenate([np.zeros(cs.FS // 2, dtype=np.int16), np.frombuffer(burst, dtype=np.int16)])
        rng = np.random.default_rng(5)
        self.feed(cs.to_int16(cs.apply_channel(audio, cs.CHANNELS["awgn"], 10, rng)))
        self.drain()
        self.assertEqual(self.decoded(payloads), payloads)


class TestDecodeFailures(DemodulatorThread):
    """On an auroral path datac4 syncs on frames and then fails to decode them:
    the demodulator counts that for the ARQ session (take_decode_failures)."""

    MODE = codec2.FREEDV_MODE.datac4

    def test_failed_frames_counted(self):
        rng = np.random.default_rng(1)
        modulator = cs._modulator(vhf=False)
        size = self.data["bytes_per_frame"] - 2
        frames = [modulator.create_burst(self.MODE, 1, 500, bytearray(rng.bytes(size))) for _ in range(4)]
        audio = np.concatenate([np.zeros(4000, dtype=np.int16)] + [np.frombuffer(f, dtype=np.int16) for f in frames])
        self.feed(cs.to_int16(cs.apply_channel(audio, cs.CHANNELS["hl_moderate"], 15, rng)))
        self.drain()
        self.assertEqual(self.decoded([b""]), [])
        self.assertGreaterEqual(self.demod.take_decode_failures([self.MODE.value]), 1)
        self.assertEqual(self.demod.take_decode_failures([self.MODE.value]), 0)  # counted again from 0


if __name__ == "__main__":
    unittest.main()
