"""HF channel simulator for testing the FreeDATA modes offline.

A Watterson model: two equal-power paths with independent Rayleigh fading,
each with a Gaussian Doppler spectrum, the second path delayed. On top of it a
fixed Doppler shift and white noise. The presets follow the ITU-R F.1520 /
F.1487 test channels; the high-latitude ones stand for auroral paths, where the
Doppler spread is ten or more times that of the mid-latitude channels.

The modes are run through the codec2 library like the modem does: frames are
built by freedata_server.modulator and decoded with freedv_rawdatarx, but
offline and faster than real time.
"""

import ctypes
import os
import sys
import types
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freedata_server import codec2  # noqa: E402
from freedata_server.modulator import Modulator  # noqa: E402

FS = codec2.api.FREEDV_FS_8000


@dataclass(frozen=True)
class Channel:
    name: str
    doppler_spread_hz: float  # two-sided spread, 2 sigma of the Gaussian Doppler spectrum
    delay_ms: float  # delay of the second path
    description: str


CHANNELS = {
    channel.name: channel
    for channel in (
        Channel("awgn", 0.0, 0.0, "no fading, noise only"),
        Channel("mid_moderate", 0.5, 1.0, "mid-latitude moderate"),
        Channel("mid_disturbed", 1.0, 2.0, "mid-latitude disturbed (codec2 MPP)"),
        Channel("hl_moderate", 10.0, 3.0, "high-latitude moderate, auroral"),
        Channel("hl_disturbed", 30.0, 7.0, "high-latitude disturbed, strong aurora"),
    )
}


def analytic(x):
    """Complex analytic signal of a real signal (FFT Hilbert transform)."""
    n = len(x)
    spectrum = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1
    if n % 2 == 0:
        h[n // 2] = 1
        h[1 : n // 2] = 2
    else:
        h[1 : (n + 1) // 2] = 2
    return np.fft.ifft(spectrum * h)


def fading_taps(n, spread_hz, rng, fs=FS):
    """Complex Rayleigh fading gains with a Gaussian Doppler spectrum and unit
    mean power. A spread of 0 gives a constant gain of 1."""
    if spread_hz <= 0:
        return np.ones(n, dtype=complex)
    noise = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    freqs = np.fft.fftfreq(n, 1 / fs)
    sigma = spread_hz / 2
    shape = np.exp(-(freqs**2) / (2 * sigma**2))
    taps = np.fft.ifft(np.fft.fft(noise) * np.sqrt(shape))
    return taps / np.sqrt(np.mean(np.abs(taps) ** 2))


def apply_channel(x, channel, snr_db, rng, doppler_shift_hz=0.0, fs=FS):
    """Pass real audio samples through the channel and add noise.

    snr_db is the SNR in a 3 kHz noise bandwidth, measured against the mean
    power of the samples where the transmitter is on, like codec2's ch tool.
    """
    x = np.asarray(x, dtype=float)
    on = x != 0
    signal_power = np.mean(x[on] ** 2) if on.any() else 0.0

    if channel.doppler_spread_hz > 0:
        z = analytic(x)
        delay = int(round(channel.delay_ms * fs / 1000))
        delayed = np.concatenate([np.zeros(delay, dtype=complex), z[: len(z) - delay]])
        tap1 = fading_taps(len(z), channel.doppler_spread_hz, rng, fs)
        tap2 = fading_taps(len(z), channel.doppler_spread_hz, rng, fs)
        # two equal paths, total mean power unchanged
        z = (tap1 * z + tap2 * delayed) / np.sqrt(2)
    else:
        z = analytic(x) if doppler_shift_hz else x

    if doppler_shift_hz:
        z = z * np.exp(2j * np.pi * doppler_shift_hz * np.arange(len(z)) / fs)
    y = np.real(z)

    # white noise over 0..fs/2, of which 3 kHz counts for the SNR
    noise_power = signal_power / 10 ** (snr_db / 10) * (fs / 2) / 3000
    return y + rng.standard_normal(len(y)) * np.sqrt(noise_power)


def to_int16(y):
    """Scale to int16 without clipping, keeping the SNR."""
    peak = np.max(np.abs(y))
    scale = min(1.0, 0.9 * 32767 / peak) if peak else 1.0
    return np.round(y * scale).astype(np.int16)


MODES = [
    codec2.FREEDV_MODE.datac4,
    codec2.FREEDV_MODE.signalling,  # DATAC13
    codec2.FREEDV_MODE.signalling_ack,  # DATAC14
    codec2.FREEDV_MODE.datac0,
    codec2.FREEDV_MODE.datac3,
    codec2.FREEDV_MODE.datac1,
    codec2.FREEDV_MODE.data_ofdm_200,
    codec2.FREEDV_MODE.data_ofdm_250,
    codec2.FREEDV_MODE.data_ofdm_500,
    codec2.FREEDV_MODE.data_ofdm_1700,
    codec2.FREEDV_MODE.data_ofdm_2438,
    codec2.FREEDV_MODE.data_vhf_1,
]

# modes of the ARQ speed levels, see arq_session.SPEED_LEVEL_DICT
ARQ_MODES = {
    codec2.FREEDV_MODE.datac4,
    codec2.FREEDV_MODE.data_ofdm_500,
    codec2.FREEDV_MODE.datac1,
    codec2.FREEDV_MODE.data_ofdm_2438,
}


def _modulator(vhf):
    config = {"MODEM": {"tx_delay": 0}, "EXP": {"enable_vhf": vhf}}
    ctx = types.SimpleNamespace(config_manager=types.SimpleNamespace(config=config))
    modulator = Modulator(ctx)
    modulator.log = types.SimpleNamespace(debug=lambda *a, **k: None)
    return modulator


@dataclass
class Result:
    mode: str
    frames_sent: int
    frames_ok: int
    payload_bytes: int
    frame_seconds: float

    @property
    def success_rate(self):
        return self.frames_ok / self.frames_sent if self.frames_sent else 0.0

    @property
    def raw_bps(self):
        """Payload bit rate of one frame on the air (preamble and postamble included)."""
        return self.payload_bytes * 8 / self.frame_seconds


def run_mode(mode, channel, snr_db, frames=10, seed=1, doppler_shift_hz=0.0, gap_ms=500):
    """Send frames bursts of one frame each through the channel and count the
    frames decoded with the right payload."""
    rng = np.random.default_rng(seed)
    vhf = mode == codec2.FREEDV_MODE.data_vhf_1
    modulator = _modulator(vhf)

    tx = codec2.open_instance(mode.value)
    payload_bytes = int(codec2.api.freedv_get_bits_per_modem_frame(tx) / 8) - 2

    payloads = [rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes() for _ in range(frames)]
    audio = [np.zeros(int(FS * gap_ms / 1000), dtype=np.int16)]
    burst_samples = 0
    for payload in payloads:
        burst = np.frombuffer(modulator.create_burst(mode, 1, gap_ms, bytearray(payload)), dtype=np.int16)
        burst_samples = len(burst) - int(FS * gap_ms / 1000)
        audio.append(burst)
    rx_audio = to_int16(apply_channel(np.concatenate(audio), channel, snr_db, rng, doppler_shift_hz))

    rx = codec2.open_instance(mode.value)
    codec2.api.freedv_set_frames_per_burst(rx, 1)
    bytes_per_frame = payload_bytes + 2
    bytes_out = ctypes.create_string_buffer(bytes_per_frame)
    expected = set(payloads)
    decoded = set()
    pos = 0
    nin = codec2.api.freedv_nin(rx)
    while pos + nin <= len(rx_audio):
        chunk = np.ascontiguousarray(rx_audio[pos : pos + nin])
        nbytes = codec2.api.freedv_rawdatarx(rx, bytes_out, chunk.ctypes)
        pos += nin
        nin = codec2.api.freedv_nin(rx)
        if nbytes == bytes_per_frame:
            payload = bytes_out.raw[:payload_bytes]
            if payload in expected:
                decoded.add(payload)

    return Result(mode.name, frames, len(decoded), payload_bytes, burst_samples / FS)
