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
    codec2.FREEDV_MODE.data_fsk4_50,  # the fsk4_50w candidate below
]

# modes of the ARQ speed levels, see arq_session.SPEED_LEVEL_DICT
ARQ_MODES = {
    codec2.FREEDV_MODE.datac4,
    codec2.FREEDV_MODE.data_ofdm_500,
    codec2.FREEDV_MODE.datac1,
    codec2.FREEDV_MODE.data_ofdm_2438,
}


@dataclass(frozen=True)
class FskCandidate:
    """An FSK mode that FreeDATA does not have, opened directly from codec2
    (freedv_open_advanced, FSK_LDPC) to try it in the simulator."""

    name: str
    M: int  # number of tones
    Rs: int  # symbol rate, baud
    tone_spacing: int  # Hz
    codename: str  # LDPC code
    description: str

    @property
    def bandwidth_hz(self):
        """Approximate occupied bandwidth: tone span plus one symbol rate on each side."""
        return (self.M - 1) * self.tone_spacing + 2 * self.Rs

    def open(self):
        config = codec2.create_default_fsk_config()
        config.interleave_frames = 1
        config.M = self.M
        config.Rs = self.Rs
        config.tone_spacing = self.tone_spacing
        config.codename = self.codename.encode("utf-8")
        config.first_tone = codec2.get_centered_first_tone(config)
        # the config must outlive the instance, codec2 keeps pointers into it
        self._configs.append(config)
        return ctypes.cast(codec2.api.freedv_open_advanced(9, ctypes.byref(config)), ctypes.c_void_p)

    _configs = []


# HF FSK candidates for auroral paths: longer symbols than data_vhf_1 (200 baud,
# 5 ms symbols), so that path delays of a few ms smear less of each symbol, and
# tone spacings well above the Doppler spread. All fit in about 500 Hz.
FSK_CANDIDATES = [
    FskCandidate("fsk4_100", 4, 100, 100, "H_256_512_4", "4FSK 100 baud, 100 Hz spacing"),
    FskCandidate("fsk4_50", 4, 50, 100, "H_256_512_4", "4FSK 50 baud, 100 Hz spacing"),
    FskCandidate("fsk4_50w", 4, 50, 150, "H_256_512_4", "4FSK 50 baud, 150 Hz spacing"),
    FskCandidate("fsk2_100", 2, 100, 200, "H_256_512_4", "2FSK 100 baud, 200 Hz spacing"),
    FskCandidate("fsk4_25", 4, 25, 100, "H_256_512_4", "4FSK 25 baud, 100 Hz spacing"),
    FskCandidate("fsk4_100_long", 4, 100, 100, "H_1024_2048_4f", "4FSK 100 baud, longer LDPC code"),
]


def _open(mode):
    return mode.open() if isinstance(mode, FskCandidate) else codec2.open_instance(mode.value)


def _modulator(vhf):
    config = {"MODEM": {"tx_delay": 0}, "EXP": {"enable_vhf": vhf}}
    ctx = types.SimpleNamespace(config_manager=types.SimpleNamespace(config=config))
    modulator = Modulator(ctx)
    modulator.log = types.SimpleNamespace(debug=lambda *a, **k: None)
    return modulator


def _burst(modulator, mode, freedv, payload, gap_ms, fsk_preamble=False):
    """One frame plus gap_ms of silence, as int16 samples."""
    if isinstance(mode, FskCandidate) or (fsk_preamble and mode == codec2.FREEDV_MODE.data_fsk4_50):
        # FSK_LDPC is sent without preamble and postamble, like data_vhf_1 and
        # data_fsk4_50, unless fsk_preamble asks for codec2's FSK preamble
        burst = modulator.transmit_add_preamble(bytes(), freedv) if fsk_preamble else bytes()
        burst = modulator.transmit_create_frame(burst, freedv, bytearray(payload))
        burst = modulator.transmit_add_silence(burst, gap_ms)
    else:
        burst = modulator.create_burst(mode, 1, gap_ms, bytearray(payload))
    return np.frombuffer(burst, dtype=np.int16)


@dataclass
class Result:
    mode: str
    frames_sent: int
    frames_ok: int
    payload_bytes: int
    frame_seconds: float
    duplicates: int = 0  # frames decoded more than once

    @property
    def success_rate(self):
        return self.frames_ok / self.frames_sent if self.frames_sent else 0.0

    @property
    def raw_bps(self):
        """Payload bit rate of one frame on the air (preamble and postamble included)."""
        return self.payload_bytes * 8 / self.frame_seconds


def is_fsk(mode):
    return isinstance(mode, FskCandidate) or mode.value in codec2.fsk_configurations


def run_mode(
    mode,
    channel,
    snr_db,
    frames=10,
    seed=1,
    doppler_shift_hz=0.0,
    gap_ms=500,
    continuous=False,
    fsk_restart=False,
    fsk_preamble=False,
):
    """Send frames through the channel and count the frames decoded with the
    right payload.

    By default every frame is its own burst followed by gap_ms of silence, as
    in ARQ. With continuous=True the frames are sent back to back, which shows
    how the mode copes with the channel without the receiver having to find
    each burst again; the FSK receiver needs this (see fading-channels.md).
    mode is a FREEDV_MODE or an FskCandidate.

    fsk_restart tries a receiver fix for FSK modes: after every decoded frame the
    receiver is opened again and the last frame length of audio is fed to it
    again. codec2's FSK_LDPC receiver otherwise stays locked to the timing of the
    frame it just decoded and misses a burst that starts less than about a frame
    later; freedv_set_sync() and freedv_set_frames_per_burst() do not reset it.
    fsk_preamble sends codec2's FSK preamble (all tones in turn) before every FSK
    frame, which helps the receiver estimate the tone frequencies.
    """
    rng = np.random.default_rng(seed)
    vhf = mode == codec2.FREEDV_MODE.data_vhf_1
    # enable_vhf leaves out the preamble of data_vhf_1
    modulator = _modulator(vhf and not fsk_preamble)
    if continuous:
        gap_ms = 0

    tx = _open(mode)
    payload_bytes = int(codec2.api.freedv_get_bits_per_modem_frame(tx) / 8) - 2

    payloads = [rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes() for _ in range(frames)]
    audio = [np.zeros(FS // 2, dtype=np.int16)]
    burst_samples = 0
    for payload in payloads:
        burst = _burst(modulator, mode, tx, payload, gap_ms, fsk_preamble)
        burst_samples = len(burst) - int(FS * gap_ms / 1000)
        audio.append(burst)
    # the receivers decode with up to a frame of delay; keep the audio running
    audio.append(np.zeros(max(FS * 2, 2 * burst_samples), dtype=np.int16))
    rx_audio = to_int16(apply_channel(np.concatenate(audio), channel, snr_db, rng, doppler_shift_hz))

    restart = fsk_restart and is_fsk(mode)
    frame_samples = codec2.api.freedv_get_n_tx_modem_samples(tx)
    if fsk_preamble:
        frame_samples += codec2.api.freedv_get_n_tx_preamble_modem_samples(tx)
    rx = _open(mode)
    codec2.api.freedv_set_frames_per_burst(rx, 0 if continuous else 1)
    bytes_per_frame = payload_bytes + 2
    bytes_out = ctypes.create_string_buffer(bytes_per_frame)
    expected = set(payloads)
    decoded = set()
    duplicates = 0
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
                duplicates += payload in decoded
                decoded.add(payload)
            if restart:
                rx = _open(mode)
                pos = max(0, pos - frame_samples)
                nin = codec2.api.freedv_nin(rx)

    return Result(mode.name, frames, len(decoded), payload_bytes, burst_samples / FS, duplicates)
