"""Measure the ARQ speed-level modes for choosing their min_snr.

Sends frames of each ARQ mode through the channel_sim channels at whole-dB true
SNRs (3 kHz) and records, per mode, channel and SNR: the frames decoded, the
payload bit rate, and the mean SNR the receiver reported for the decoded
frames (freedv_get_modem_stats plus snr_correction_db, as
Demodulator.calculate_snr). The ARQ session compares that reported SNR with
min_snr (arq_session.SPEED_LEVEL_DICT, docs/arq-sessions.md section 4.3).

Every frame is its own burst followed by 500 ms of silence, as in ARQ.

    python -I tools/speed_level_sweep.py sweep.json
    python -I tools/speed_level_sweep.py sweep.json --frames 10 --modes data_ofdm_500 datac1

Run one process per mode to use several cores; each takes 5 to 10 minutes
with 30 frames per cell.
"""

import argparse
import ctypes
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import channel_sim as cs  # noqa: E402
from freedata_server import codec2  # noqa: E402

# the ARQ speed-level modes (FSK left out: it is chosen after failures, not by
# SNR) and the true SNRs around their decode thresholds
MODES = {
    "datac4": range(-12, 5),
    "data_ofdm_250_hr": range(-6, 11),
    "data_ofdm_500": range(-6, 11),
    "data_ofdm_500_hr": range(-4, 13),
    "datac1": range(-4, 13),
    "data_ofdm_1700_hr": range(-2, 15),
    "data_ofdm_2438": range(0, 19),
}
CHANNELS = ["awgn", "mid_moderate", "mid_disturbed"]
GAP_MS = 500


def run(mode, channel, snr_db, frames, seed):
    """Frames decoded, payload bytes, frame seconds and the reported SNRs."""
    rng = np.random.default_rng(seed)
    modulator = cs._modulator(False)
    tx = cs._open(mode)
    payload_bytes = int(codec2.api.freedv_get_bits_per_modem_frame(tx) / 8) - 2
    payloads = [rng.integers(0, 256, payload_bytes, dtype=np.uint8).tobytes() for _ in range(frames)]
    audio = [np.zeros(cs.FS // 2, dtype=np.int16)]
    burst_samples = 0
    for payload in payloads:
        burst = cs._burst(modulator, mode, tx, payload, GAP_MS)
        burst_samples = len(burst) - int(cs.FS * GAP_MS / 1000)
        audio.append(burst)
    audio.append(np.zeros(max(cs.FS * 2, 2 * burst_samples), dtype=np.int16))
    rx_audio = cs.to_int16(cs.apply_channel(np.concatenate(audio), cs.CHANNELS[channel], snr_db, rng))

    rx = cs._open(mode)
    codec2.api.freedv_set_frames_per_burst(rx, 1)
    bytes_per_frame = payload_bytes + 2
    bytes_out = ctypes.create_string_buffer(bytes_per_frame)
    expected = set(payloads)
    decoded = set()
    snrs = []
    pos = 0
    nin = codec2.api.freedv_nin(rx)
    while pos + nin <= len(rx_audio):
        chunk = np.ascontiguousarray(rx_audio[pos : pos + nin])
        nbytes = codec2.api.freedv_rawdatarx(rx, bytes_out, chunk.ctypes)
        pos += nin
        nin = codec2.api.freedv_nin(rx)
        if nbytes == bytes_per_frame:
            payload = bytes_out.raw[:payload_bytes]
            if payload in expected and payload not in decoded:
                decoded.add(payload)
                snr = ctypes.c_float()
                sync = ctypes.c_int()
                codec2.api.freedv_get_modem_stats(rx, ctypes.byref(sync), ctypes.byref(snr))
                snrs.append(snr.value + codec2.snr_correction_db(rx))
    return len(decoded), payload_bytes, burst_samples / cs.FS, snrs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", help="JSON file for the results")
    parser.add_argument("--frames", type=int, default=30, help="frames per cell (default 30)")
    parser.add_argument("--modes", nargs="*", choices=list(MODES), default=list(MODES))
    parser.add_argument("--channels", nargs="*", choices=CHANNELS, default=CHANNELS)
    args = parser.parse_args()

    results = []
    start = time.time()
    for name in args.modes:
        mode = codec2.FREEDV_MODE[name]
        for channel in args.channels:
            for snr in MODES[name]:
                ok, payload_bytes, frame_seconds, snrs = run(mode, channel, snr, args.frames, seed=snr * 7 + 1000)
                row = {
                    "mode": name,
                    "channel": channel,
                    "snr": snr,
                    "ok": ok,
                    "frames": args.frames,
                    "payload_bytes": payload_bytes,
                    "frame_seconds": round(frame_seconds, 3),
                    "raw_bps": round(payload_bytes * 8 / frame_seconds, 1),
                    "reported_snr_mean": round(float(np.mean(snrs)), 2) if snrs else None,
                }
                results.append(row)
                print(
                    f"{name:18} {channel:14} {snr:4d} dB  {ok:3d}/{args.frames}"
                    f"  reported {row['reported_snr_mean']}  ({time.time() - start:.0f}s)",
                    flush=True,
                )

    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=1)


if __name__ == "__main__":
    main()
