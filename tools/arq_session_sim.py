"""Run whole ARQ sessions between two FreeDATA stations through the channel
simulator, in real time, and measure the data rate.

Each station is a full FreeDATA context in test mode: its demodulator threads,
frame dispatcher and ARQ sessions run as in the modem. Only the audio is
simulated: a station's transmissions are modulated as the modem does, keep the
station transmitting for their airtime, and reach the other station through
the Watterson channel of channel_sim (fading continuing from one transmission
to the next) with noise, in 0.1 s blocks at the pace of the audio. A station
does not hear itself. Every transmission has the same mean power, like a
transmitter with ALC; the SNR is measured against it in 3 kHz.

    python tools/arq_session_sim.py --channels awgn hl_disturbed --snr 10 --bytes 500
    python tools/arq_session_sim.py --jobs 4 ...   # sessions in parallel, one process each

Sessions take minutes: they run at the speed of the air.
"""

import argparse
import base64
import concurrent.futures
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FS = 8000
BLOCK = 800  # samples per audio block, 0.1 s
TX_RMS = 3000.0  # mean amplitude of every transmission
TAP_RATE = 400  # fading gains are made at this rate and interpolated


# the stations' configs, and the message database of a worker process
_TMP = tempfile.mkdtemp(prefix="freedata-arqsim-")

import channel_sim as cs  # noqa: E402
from freedata_server.modem_frametypes import FRAME_TYPE  # noqa: E402

ARQ_BURST_FRAME = FRAME_TYPE.ARQ_BURST_FRAME.value


class StreamChannel:
    """One direction of the channel: fading gains over the whole run, indexed by
    the sample time at which a transmission is received."""

    def __init__(self, channel, rng, seconds):
        self.channel = channel
        self.delay = int(round(channel.delay_ms * FS / 1000))
        n = int(seconds * TAP_RATE) + 2
        self.taps = [cs.fading_taps(n, channel.doppler_spread_hz, rng, fs=TAP_RATE) for _ in range(2)]

    def apply(self, x, start):
        """The transmission x, received from sample time start on (without noise)."""
        x = np.asarray(x, dtype=float)
        on = x != 0
        if on.any():
            x = x * TX_RMS / np.sqrt(np.mean(x[on] ** 2))
        if self.channel.doppler_spread_hz <= 0:
            return x
        z = cs.analytic(x)
        delayed = np.concatenate([np.zeros(self.delay, dtype=complex), z[: len(z) - self.delay]])
        times = (start + np.arange(len(z))) * TAP_RATE / FS
        grid = np.arange(len(self.taps[0]))
        gains = [np.interp(times, grid, t.real) + 1j * np.interp(times, grid, t.imag) for t in self.taps]
        return np.real((gains[0] * z + gains[1] * delayed) / np.sqrt(2))


class AudioBus:
    """Delivers the audio to both stations at the pace of time: noise, plus the
    other station's transmissions through the channel."""

    def __init__(self, channel, snr_db, seed, seconds):
        rng = np.random.default_rng(seed)
        self.noise_sigma = np.sqrt(TX_RMS**2 / 10 ** (snr_db / 10) * (FS / 2) / 3000)
        self.rng = rng
        self.paths = {}  # (sender, receiver) -> StreamChannel
        self.incoming = {}  # receiver -> list of (start sample, audio)
        self.receivers = {}  # name -> callable(block)
        self.channel = channel
        self.seed = seed
        self.seconds = seconds
        self.sample_time = 0
        self.lock = threading.Lock()
        self.running = False
        self.max_lag = 0.0  # seconds the audio fell behind the clock (a loaded computer)

    def add_station(self, name, receive):
        self.receivers[name] = receive
        self.incoming[name] = []

    def path(self, sender, receiver):
        key = (sender, receiver)
        if key not in self.paths:
            rng = np.random.default_rng([self.seed, len(self.paths)])
            self.paths[key] = StreamChannel(self.channel, rng, self.seconds)
        return self.paths[key]

    def send(self, sender, audio):
        """A transmission starting with the next audio block."""
        with self.lock:
            start = self.sample_time + BLOCK
            for receiver in self.receivers:
                if receiver != sender:
                    self.incoming[receiver].append((start, self.path(sender, receiver).apply(audio, start)))

    def run(self):
        self.running = True
        started = time.monotonic()
        while self.running:
            with self.lock:
                now = self.sample_time
                self.sample_time += BLOCK
                blocks = {}
                for name in self.receivers:
                    block = self.rng.standard_normal(BLOCK) * self.noise_sigma
                    keep = []
                    for start, audio in self.incoming[name]:
                        a, b = max(start, now), min(start + len(audio), now + BLOCK)
                        if a < b:
                            block[a - now : b - now] += audio[a - start : b - start]
                        if start + len(audio) > now + BLOCK:
                            keep.append((start, audio))
                    self.incoming[name] = keep
                    blocks[name] = np.clip(np.round(block), -32768, 32767).astype(np.int16)
            for name, block in blocks.items():
                self.receivers[name](block)
            # the pace of the audio
            delay = started + self.sample_time / FS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                self.max_lag = max(self.max_lag, -delay)

    def stop(self):
        self.running = False


def _config(tmp, name, callsign):
    import configparser

    path = os.path.join(tmp, f"{name}.ini")
    shutil.copyfile(os.path.join(_REPO, "freedata_server", "config.ini.example"), path)
    parser = configparser.ConfigParser()
    parser.read(path)
    parser["STATION"]["mycall"] = callsign
    parser["STATION"]["myssid"] = "1"
    with open(path, "w") as f:
        parser.write(f)
    return path


@dataclass
class Station:
    name: str
    ctx: object
    sent: list = field(default_factory=list)  # (time, mode name, seconds on the air, frame types)
    # one transmission at a time, as the modem's audio output queue plays them
    tx_lock: object = field(default_factory=threading.Lock)
    overlaps: int = 0  # transmissions that had to wait for another one


def _start_station(name, callsign, bus):
    from freedata_server.context import AppContext

    ctx = AppContext(_config(_TMP, name, callsign))
    ctx.TESTMODE = True
    ctx.startup()
    deadline = time.time() + 30
    while getattr(ctx, "rf_modem", None) is None or getattr(ctx.rf_modem, "demodulator", None) is None:
        if time.time() > deadline:
            raise RuntimeError("modem did not start")
        time.sleep(0.1)
    station = Station(name, ctx)
    modem = ctx.rf_modem
    demodulator = modem.demodulator

    def transmit(mode, repeats, repeat_delay, frames):
        # as RF.transmit, with the audio going to the bus instead of the sound card
        if not station.tx_lock.acquire(blocking=False):
            station.overlaps += 1
            station.tx_lock.acquire()
        try:
            _transmit(mode, repeats, repeat_delay, frames)
        finally:
            station.tx_lock.release()

    def _transmit(mode, repeats, repeat_delay, frames):
        demodulator.reset_data_sync()
        ctx.state_manager.waitForTransmission()
        ctx.state_manager.setTransmitting(True)
        audio = np.frombuffer(modem.modulator.create_burst(mode, repeats, repeat_delay, frames), dtype=np.int16)
        types = [f[0] for f in (frames if isinstance(frames, list) else [frames])]
        station.sent.append((time.monotonic(), mode.name, len(audio) / FS, types))
        bus.send(name, audio)
        # on the air from the next block on, until its end
        time.sleep(BLOCK / FS + len(audio) / FS)
        ctx.state_manager.setTransmitting(False)

    modem.transmit = transmit

    def receive(block):
        # as RF.run_rx_audio_dsp, without resampling and level
        demodulator.signal_detector.push(block, transmitting=ctx.state_manager.isTransmitting())
        for data in demodulator.MODE_DICT.values():
            buffer = data["audio_buffer"]
            if buffer and data["decode"] and buffer.nbuffer + len(block) <= buffer.size:
                buffer.push(block)

    bus.add_station(name, receive)
    return station


def run_session(channel_name, snr_db, size, seed=1, max_seconds=1800):
    """One ARQ transfer of size random bytes from ISS to IRS.

    Returns:
        dict: channel, snr, bytes, success, seconds (command to the ISS's end of
        session), bps (payload bits per second), and the transmissions per mode.
    """
    from freedata_server.command_arq_raw import ARQRawCommand

    channel = cs.CHANNELS[channel_name]
    bus = AudioBus(channel, snr_db, seed, max_seconds + 120)
    iss = _start_station("iss", "AA1AAA", bus)
    irs = _start_station("irs", "BB1BBB", bus)
    threading.Thread(target=bus.run, daemon=True).start()
    time.sleep(3)  # noise for the signal detectors' floor

    # differs per channel and SNR too: the session id comes from it, and tells
    # the sessions of parallel runs apart in the logs
    payload = np.random.default_rng([seed, zlib.crc32(channel_name.encode()), int(snr_db * 10) % 1000]).bytes(size)
    params = {"dxcall": "BB1BBB-1", "data": base64.b64encode(payload), "type": "raw"}
    t0 = time.monotonic()
    ARQRawCommand(iss.ctx, params).run()
    success = None
    while time.monotonic() - t0 < max_seconds:
        try:
            event = iss.ctx.TESTMODE_EVENTS.get(timeout=1)
        except queue.Empty:
            continue
        outbound = event.get("arq-transfer-outbound", {})
        if "success" in outbound:
            success = outbound["success"]
            break
    seconds = time.monotonic() - t0

    received = None
    deadline = time.monotonic() + 10
    while success and received is None and time.monotonic() < deadline:
        try:
            inbound = irs.ctx.TESTMODE_EVENTS.get(timeout=1).get("arq-transfer-inbound", {})
        except queue.Empty:
            continue
        if inbound.get("success"):
            received = base64.b64decode(inbound["data"])
    bus.stop()
    for station in (iss, irs):
        try:
            station.ctx.shutdown()
        except Exception:
            pass

    modes = {}
    for station in (iss, irs):
        for _, mode, _, _ in station.sent:
            modes[f"{station.name}:{mode}"] = modes.get(f"{station.name}:{mode}", 0) + 1
    # the data phase: from the first burst on
    bursts = [t for t, _, _, types in iss.sent if ARQ_BURST_FRAME in types]
    setup = bursts[0] - t0 if bursts else seconds
    ok = bool(success) and received == payload
    return {
        "channel": channel_name,
        "snr": snr_db,
        "bytes": size,
        "success": ok,
        "seconds": round(seconds, 1),
        "bps": round(size * 8 / seconds, 1) if ok else 0.0,
        "setup_seconds": round(setup, 1),
        "data_bps": round(size * 8 / (seconds - setup), 1) if ok and seconds > setup else 0.0,
        "modes": modes,
        "max_lag": round(bus.max_lag, 2),
        "overlaps": iss.overlaps + irs.overlaps,
    }


def _run(args):
    # a worker process: its own message database, which FreeDATA opens again and
    # again from FREEDATA_DATABASE (a database shared between processes breaks)
    os.environ["FREEDATA_DATABASE"] = os.path.join(_TMP, "messages.db")
    return run_session(*args)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--channels", nargs="+", default=["awgn", "hl_moderate", "hl_disturbed"], choices=list(cs.CHANNELS)
    )
    parser.add_argument("--snr", nargs="+", type=float, default=[10])
    parser.add_argument("--bytes", type=int, default=500)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1], help="one session per seed (fading, noise, data)")
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument("--jobs", type=int, default=1, help="sessions in parallel, one process each")
    args = parser.parse_args()

    tasks = [(c, s, args.bytes, seed, args.max_seconds) for c in args.channels for s in args.snr for seed in args.seeds]
    print(
        "| channel | SNR | bytes | result | time | payload bps | setup | data phase bps | transmissions | lag | overlaps |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.jobs, max_tasks_per_child=1) as pool:
        for r in pool.map(_run, tasks):
            modes = ", ".join(f"{k} {v}" for k, v in sorted(r["modes"].items()))
            result = "ok" if r["success"] else "failed"
            print(
                f"| `{r['channel']}` | {r['snr']:g} dB | {r['bytes']} | {result} | {r['seconds']:.0f} s | {r['bps']}"
                f" | {r['setup_seconds']:.0f} s | {r['data_bps']} | {modes} | {r['max_lag']} s | {r['overlaps']} |",
                flush=True,
            )


if __name__ == "__main__":
    main()
