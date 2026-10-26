"""
Detects transmissions on the channel by their power.

On a strongly fading path, an auroral one with 30 Hz of Doppler spread for
instance, the OFDM modes do not even sync on a frame, so codec2 reports
nothing, although the frame arrives loud and clear. ARQ sessions use this
detector to notice that: a signal as long as the expected frame, with
nothing decoded (see docs/arq-sessions.md, section 4.1). It also tells the
ISS that the other station is still transmitting, so it waits for that
answer instead of sending over it.

The received audio is cut into 0.1 s blocks. The noise floor is the lowest
0.5 s average power of the last minute (the own transmissions left out), and
a signal is present where a block is THRESHOLD_DB above it. In the channel
simulator this finds OFDM and FSK frames on `hl_disturbed` to the block from
5 dB SNR up, and nothing on noise. A receiver's AGC that raises the noise in
the pauses would hide the signals: then nothing is detected, and the
sessions fall back on their retries as before.
"""

import threading
import time
from collections import deque

import numpy as np


class SignalDetector:
    FS = 8000
    BLOCK_SECONDS = 0.1
    THRESHOLD_DB = 3.0
    FLOOR_SECONDS = 0.5  # the noise floor is the lowest average over this long
    HISTORY_SECONDS = 60
    GAP_SECONDS = 0.3  # shorter pauses do not end a signal
    MIN_SECONDS = 0.3  # shorter signals are ignored

    def __init__(self):
        self.block_samples = int(self.BLOCK_SECONDS * self.FS)
        self.blocks = deque()  # (end time, power in dB or None while transmitting)
        self.rest = np.zeros(0, dtype=np.float64)
        self.lock = threading.Lock()

    def push(self, samples, transmitting=False, now=None):
        """Adds received audio.

        Args:
            samples: The audio, 8 kHz.
            transmitting: True while this station transmits: these blocks are
                left out (the own signal, or a muted receiver).
            now: The time (time.monotonic()) the audio ends; now by default.
        """
        now = time.monotonic() if now is None else now
        audio = np.concatenate([self.rest, np.asarray(samples, dtype=np.float64)])
        n = len(audio) // self.block_samples
        self.rest = audio[n * self.block_samples :]
        if n == 0:
            return
        power = 10 * np.log10(np.mean(audio[: n * self.block_samples].reshape(n, -1) ** 2, axis=1) + 1e-9)
        with self.lock:
            for i, p in enumerate(power):
                end = now - (n - 1 - i) * self.BLOCK_SECONDS
                self.blocks.append((end, None if transmitting else float(p)))
            while self.blocks and self.blocks[0][0] < now - self.HISTORY_SECONDS:
                self.blocks.popleft()

    def noise_floor(self):
        """The noise floor in dB, or None while there is too little audio."""
        with self.lock:
            powers = [p for _, p in self.blocks]
        window = int(self.FLOOR_SECONDS / self.BLOCK_SECONDS)
        averages = []
        for i in range(len(powers) - window + 1):
            part = powers[i : i + window]
            if None not in part:
                averages.append(np.mean(10 ** (np.array(part) / 10)))
        if len(averages) < window:
            return None
        return 10 * np.log10(min(averages))

    def segments(self, since=None):
        """The signals received, as (start, end) times, that end after since."""
        floor = self.noise_floor()
        if floor is None:
            return []
        with self.lock:
            blocks = list(self.blocks)
        found = []
        start = end = None
        for block_end, power in blocks:
            on = power is not None and power > floor + self.THRESHOLD_DB
            if on:
                if start is None or block_end - self.BLOCK_SECONDS - end > self.GAP_SECONDS:
                    if start is not None:
                        found.append((start, end))
                    start = block_end - self.BLOCK_SECONDS
                end = block_end
        if start is not None:
            found.append((start, end))
        return [(s, e) for s, e in found if e - s >= self.MIN_SECONDS and (since is None or e > since)]

    def receiving(self, now=None):
        """True while a signal is on the channel (one ended within GAP_SECONDS)."""
        now = time.monotonic() if now is None else now
        found = self.segments()
        return bool(found) and now - found[-1][1] <= self.GAP_SECONDS
