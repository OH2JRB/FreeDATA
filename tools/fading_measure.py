"""Measure how the FreeDATA modes cope with fading channels, including
auroral (high-latitude) ones.

Prints a Markdown table per channel: the share of frames decoded per mode
and SNR. Example:

    python tools/fading_measure.py --frames 20 --channels awgn hl_moderate
    python tools/fading_measure.py --fsk --stream --modes datac4 data_vhf_1 fsk4_100
    python tools/fading_measure.py --restart --modes datac4 data_fsk4_50 --channels hl_disturbed
"""

import argparse
import time

import channel_sim as cs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frames", type=int, default=20, help="frames per mode, channel and SNR")
    parser.add_argument("--snr", type=float, nargs="+", default=[-5, 0, 5, 10, 15, 20, 30], help="SNR in 3 kHz, dB")
    parser.add_argument("--channels", nargs="+", default=list(cs.CHANNELS), choices=list(cs.CHANNELS))
    all_names = [m.name for m in cs.MODES + cs.FSK_CANDIDATES]
    parser.add_argument("--modes", nargs="+", choices=all_names, help="default: all FreeDATA modes")
    parser.add_argument("--fsk", action="store_true", help="also measure the HF FSK candidates (FSK_CANDIDATES)")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="add a row per FSK mode with the frames sent back to back instead of one burst each",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="restart the FSK receiver after every decoded frame (fsk_restart in channel_sim.run_mode)",
    )
    parser.add_argument("--shift", type=float, default=0.0, help="Doppler shift in Hz, added to every channel")
    parser.add_argument(
        "--custom",
        nargs=2,
        type=float,
        metavar=("SPREAD_HZ", "DELAY_MS"),
        help="measure only a custom channel with this Doppler spread and path delay",
    )
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if args.custom:
        spread, delay = args.custom
        cs.CHANNELS["custom"] = cs.Channel("custom", spread, delay, "custom channel")
        args.channels = ["custom"]

    candidates = cs.MODES + cs.FSK_CANDIDATES
    if args.modes:
        modes = [m for m in candidates if m.name in args.modes]
    else:
        modes = cs.MODES + (cs.FSK_CANDIDATES if args.fsk else [])
    rows = []
    for mode in modes:
        rows.append((mode, False))
        if args.stream and cs.is_fsk(mode):
            rows.append((mode, True))
    started = time.time()
    print(
        f"{args.frames} frames per cell, Doppler shift {args.shift:g} Hz, seed {args.seed}"
        + (", FSK receiver restarted after each frame" if args.restart else "")
    )
    for channel_name in args.channels:
        channel = cs.CHANNELS[channel_name]
        print(
            f"\n### {channel.name}: {channel.description}"
            f" (Doppler spread {channel.doppler_spread_hz:g} Hz, delay {channel.delay_ms:g} ms)\n"
        )
        header = "| mode | payload bps | " + " | ".join(f"{snr:g} dB" for snr in args.snr) + " |"
        print(header)
        print("|" + "---|" * (2 + len(args.snr)))
        for mode, stream in rows:
            cells = []
            bps = 0
            for i, snr in enumerate(args.snr):
                seed = args.seed * 1000 + i
                result = cs.run_mode(
                    mode,
                    channel,
                    snr,
                    frames=args.frames,
                    seed=seed,
                    doppler_shift_hz=args.shift,
                    continuous=stream,
                    fsk_restart=args.restart and not stream,
                )
                bps = result.raw_bps
                cells.append(f"{100 * result.success_rate:.0f}%")
            name = f"**{mode.name}**" if mode in cs.ARQ_MODES else mode.name
            if isinstance(mode, cs.FskCandidate):
                name = f"*{mode.name}* ({mode.bandwidth_hz} Hz)"
            if stream:
                name += ", stream"
            elif args.restart and cs.is_fsk(mode):
                name += ", restart"
            print(f"| {name} | {bps:.0f} | " + " | ".join(cells) + " |", flush=True)
    print(f"\n(took {time.time() - started:.0f} s; ARQ speed level modes in bold, FSK candidates in italics)")


if __name__ == "__main__":
    main()
