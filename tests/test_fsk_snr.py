"""The FSK modes' SNR in a 3 kHz noise bandwidth, like the OFDM modes'.

codec2's FSK_LDPC receiver gives the SNR in a bandwidth of about the symbol
rate and does not convert it, so data_fsk4_50 read 17.8 dB and data_vhf_1
11.8 dB too high (10*log10(3000/Rs)); users found the SNR too good.
Demodulator.calculate_snr now converts it (codec2.snr_correction_db)."""

import ctypes
import os
import sys
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.demodulator import Demodulator

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import channel_sim as cs  # noqa: E402

MODE = codec2.FREEDV_MODE


class TestCorrection(unittest.TestCase):
    def test_per_mode(self):
        for mode, expected in (
            (MODE.data_fsk4_50, -17.8),
            (MODE.data_vhf_1, -11.8),
            (MODE.datac4, 0.0),
            (MODE.signalling_ack, 0.0),
            (MODE.data_ofdm_500, 0.0),
        ):
            freedv = codec2.open_instance(mode.value)
            self.assertAlmostEqual(codec2.snr_correction_db(freedv), expected, delta=0.05, msg=mode.name)

    def test_no_instance(self):
        self.assertEqual(codec2.snr_correction_db(None), 0.0)


def reported_snrs(mode, snr_db, frames=3, seed=1):
    """calculate_snr of every frame of mode decoded from AWGN at snr_db (3 kHz)."""
    demodulator = Demodulator.__new__(Demodulator)
    demodulator.log = mock.Mock()
    rng = np.random.default_rng(seed)
    fsk = cs.is_fsk(mode)
    modulator = cs._modulator(mode == MODE.data_vhf_1)
    tx = cs._open(mode)
    payload_bytes = int(codec2.api.freedv_get_bits_per_modem_frame(tx) / 8) - 2
    audio = [np.zeros(cs.FS // 2, dtype=np.int16)]
    for _ in range(frames):
        payload = rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes()
        audio.append(cs._burst(modulator, mode, tx, payload, 500))
    audio.append(np.zeros(cs.FS * 6, dtype=np.int16))
    rx_audio = cs.to_int16(cs.apply_channel(np.concatenate(audio), cs.CHANNELS["awgn"], snr_db, rng))

    frame_samples = codec2.api.freedv_get_n_tx_modem_samples(tx)
    rx = cs._open(mode)
    codec2.api.freedv_set_frames_per_burst(rx, 0 if fsk else 1)
    out = ctypes.create_string_buffer(payload_bytes + 2)
    snrs, pos, nin = [], 0, codec2.api.freedv_nin(rx)
    while pos + nin <= len(rx_audio):
        chunk = np.ascontiguousarray(rx_audio[pos : pos + nin])
        n = codec2.api.freedv_rawdatarx(rx, out, chunk.ctypes)
        pos += nin
        nin = codec2.api.freedv_nin(rx)
        if n == payload_bytes + 2:
            snrs.append(demodulator.calculate_snr(rx))
            if fsk:  # like the modem, the FSK receiver starts again after a frame
                rx = cs._open(mode)
                codec2.api.freedv_set_frames_per_burst(rx, 0)
                pos = max(0, pos - frame_samples)
                nin = codec2.api.freedv_nin(rx)
    return snrs


class TestThroughChannel(unittest.TestCase):
    def test_fsk_and_ofdm_read_the_channel_snr(self):
        # measured: data_fsk4_50 9.8, data_vhf_1 9.6, datac4 9.1 dB at 10 dB
        for mode in (MODE.data_fsk4_50, MODE.data_vhf_1, MODE.datac4):
            snrs = reported_snrs(mode, 10)
            self.assertGreaterEqual(len(snrs), 2, mode.name)
            self.assertAlmostEqual(np.mean(snrs), 10, delta=2, msg=f"{mode.name}: {snrs}")


if __name__ == "__main__":
    unittest.main()
