"""
Early decoding of FSK frames.

codec2's FSK_LDPC receiver decodes a frame only once it has collected the
frame length of audio that follows it: up to 5.4 s after the end of a
data_fsk4_50 frame, which an ARQ session pays twice per burst (the burst and
its ACK). FskEarlyDecoder gets the frame much sooner: about once a second it
opens a fresh receiver on the last 1.25 frame lengths of audio, followed by a
frame length of silence in place of the audio still to come. The LDPC code
treats the silence as erasures, so a frame decodes from its end on, often
even before (the CRC16 confirms it).

A frame decoded before its end must not be passed on yet: the station would
answer while the other one still transmits. So the decoder modulates the
decoded frame again and finds where it lies in the received audio by its tone
energies, symbol by symbol (no phase, so fading does not matter): within a few
ms in the channel simulator. The demodulator passes the frame on once its
audio has reached that end. A poor match (score below MIN_SCORE) is dropped and
left to the regular receiver. See docs/fading-channels.md, section 8.
"""

import ctypes

import numpy as np

from freedata_server import codec2

FS = 8000


class FskEarlyDecoder:
    """Decodes the frames of one FSK mode from the recent audio, early."""

    STEP_SECONDS = 1.0  # how often to try
    WINDOW_FRAMES = 1.25  # audio to decode, in frame lengths
    # mean share of the tone energy on the frame's tones, over its symbols: 0.75 to
    # 0.96 at the right position in the simulator, about 0.35 for a wrong frame
    MIN_SCORE = 0.6

    def __init__(self, mode: int):
        self.mode = mode
        config = codec2.fsk_configurations[mode]
        self.tones = [config.first_tone + k * config.tone_spacing for k in range(config.M)]
        self.symbol_samples = FS // config.Rs
        # an instance of our own for modulating decoded frames again
        self.tx = codec2.open_instance(mode)
        self.frame_samples = codec2.api.freedv_get_n_tx_modem_samples(self.tx)
        self.bytes_per_frame = int(codec2.api.freedv_get_bits_per_modem_frame(self.tx) / 8)
        self.window_samples = int(self.WINDOW_FRAMES * self.frame_samples)
        self.step_samples = int(self.STEP_SECONDS * FS)
        self.time = 0  # samples received so far
        self.reset()

    def reset(self):
        """Forgets the recent audio, e.g. after transmitting."""
        self.window = np.zeros(0, dtype=np.int16)
        self.next_try = self.time + self.step_samples

    def push(self, samples, stats, known=lambda payload: False):
        """Adds received audio and tries to decode, about once a second.

        Args:
            samples: New audio, int16.
            stats: stats(freedv) -> (snr, frequency_offset) of a codec2 instance.
            known: known(payload) -> True for frames already decoded, which are skipped.

        Returns:
            list of dict: Decoded frames with "payload" (with CRC), "end" (sample
            time of the frame's end, comparable with self.time), "snr" and
            "frequency_offset".
        """
        self.window = np.concatenate([self.window, samples])[-self.window_samples :]
        self.time += len(samples)
        if self.time < self.next_try:
            return []
        self.next_try = self.time + self.step_samples
        return self.decode(stats, known)

    def decode(self, stats, known):
        audio = np.concatenate([self.window, np.zeros(self.frame_samples, dtype=np.int16)])
        window_start = self.time - len(self.window)
        rx = codec2.open_instance(self.mode)
        frames = []
        try:
            codec2.api.freedv_set_frames_per_burst(rx, 1)
            bytes_out = ctypes.create_string_buffer(self.bytes_per_frame)
            pos = 0
            nin = codec2.api.freedv_nin(rx)
            while pos + nin <= len(audio):
                chunk = np.ascontiguousarray(audio[pos : pos + nin])
                nbytes = codec2.api.freedv_rawdatarx(rx, bytes_out, chunk.ctypes)
                pos += nin
                nin = codec2.api.freedv_nin(rx)
                if nbytes != self.bytes_per_frame:
                    continue
                payload = bytes(bytes_out)
                if known(payload):
                    continue
                end, score = self.locate_end(payload)
                if score < self.MIN_SCORE:
                    continue
                snr, frequency_offset = stats(rx)
                frames.append({
                    "payload": payload,
                    "end": window_start + end,
                    "snr": snr,
                    "frequency_offset": frequency_offset,
                })
        finally:
            codec2.api.freedv_close(rx)
        return frames

    def tone_energy(self, x):
        """Energy at each tone over the symbol starting at each sample: (tones, len(x))."""
        n = np.arange(len(x))
        x = x.astype(np.float64)
        energy = np.zeros((len(self.tones), len(x)))
        for k, tone in enumerate(self.tones):
            cumulative = np.concatenate([[0], np.cumsum(x * np.exp(-2j * np.pi * tone * n / FS))])
            symbol_sum = cumulative[self.symbol_samples :] - cumulative[: -self.symbol_samples]
            energy[k, : len(symbol_sum)] = np.abs(symbol_sum) ** 2
        return energy

    def locate_end(self, payload):
        """Finds where the frame with this payload ends in the recent audio.

        The frame may still be on the air: then its end lies after the audio.

        Returns:
            (end, score): end as a sample index into the recent audio, and the
            mean share of the tone energy that lies on the frame's tones, over
            the symbols received so far (0 to 1).
        """
        frame = np.frombuffer(codec2.modulate_frame(self.tx, payload[:-2]), dtype=np.int16)
        starts = np.arange(0, len(frame) - self.symbol_samples + 1, self.symbol_samples)
        symbols = np.argmax(self.tone_energy(frame)[:, starts], axis=0)

        # the frame may begin up to its length before the recent audio ends
        received = self.tone_energy(self.window)
        received /= received.sum(axis=0, keepdims=True) + 1e-9
        rx = np.concatenate([received, np.zeros((len(self.tones), len(frame)))], axis=1)
        offsets = len(self.window)
        size = 1 << int(np.ceil(np.log2(rx.shape[1] + len(frame))))
        score = np.zeros(offsets)
        for k in range(len(self.tones)):
            comb = np.zeros(len(frame))
            comb[starts[symbols == k]] = 1.0
            correlation = np.fft.irfft(np.fft.rfft(rx[k], size) * np.conj(np.fft.rfft(comb, size)), size)
            score += correlation[:offsets]
        # symbols of the frame inside the recent audio at each offset
        inside = np.clip(
            (len(self.window) - np.arange(offsets) - self.symbol_samples) // self.symbol_samples + 1, 0, None
        )
        inside = np.minimum(inside, len(starts))
        valid = inside >= len(starts) // 3
        if not valid.any():
            return 0, 0.0
        normalised = np.where(valid, score / np.maximum(inside, 1), 0.0)
        offset = int(np.argmax(normalised))
        return offset + len(frame), float(normalised[offset])
