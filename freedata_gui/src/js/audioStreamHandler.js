// Plays the received (RX) radio audio the server streams on /audio_rx:
// binary frames of 8 kHz, 16-bit signed, mono PCM, 800 samples (100 ms) each.
//
// Each frame becomes an AudioBuffer scheduled right after the previous one,
// straight from the socket's message handler. The buffers are 8 kHz; the
// browser resamples them to its own output rate. The sound card and the
// browser run on different clocks, so the queue slowly grows or empties: when
// it empties, playback restarts START_DELAY_S ahead, and above MAX_QUEUED_S a
// block is dropped to keep the delay bounded.

export const SAMPLE_RATE = 8000;
// Headroom added when (re)starting after the queue ran empty, absorbs network jitter
export const START_DELAY_S = 0.15;
// Drop blocks while more than this much audio would be scheduled
export const MAX_QUEUED_S = 0.75;
// Highest volume, as a gain factor; the radio's USB audio level is often low
export const MAX_VOLUME = 4;

/**
 * Converts one binary frame to float samples in -1...1.
 * @param {ArrayBuffer} data - little-endian int16 PCM; a trailing odd byte is ignored
 * @returns {Float32Array|null} null when the frame holds no samples
 */
export function pcm16ToFloat32(data) {
  if (!(data instanceof ArrayBuffer)) return null;
  const sampleCount = Math.floor(data.byteLength / 2);
  if (sampleCount === 0) return null;
  const view = new DataView(data, 0, sampleCount * 2);
  const out = new Float32Array(sampleCount);
  for (let i = 0; i < sampleCount; i++) {
    out[i] = view.getInt16(i * 2, true) / 32768;
  }
  return out;
}

/**
 * When a block of the given duration starts playing, or null to drop it.
 * @param {number} now - the audio context's current time
 * @param {number} nextStart - when the previously scheduled block ends
 * @param {number} duration - this block's length in seconds
 */
export function scheduleTime(now, nextStart, duration) {
  if (nextStart < now) return now + START_DELAY_S;
  if (nextStart + duration - now > MAX_QUEUED_S) return null;
  return nextStart;
}

export class RxAudioPlayer {
  constructor() {
    this.ctx = null;
    this.gain = null;
    this.nextStart = 0;
    this.volume = 1;
    this.muted = false;
    this.transmitting = false;
    this.lastBlockAt = 0; // performance.now() of the last frame received
    this.dropped = 0;
  }

  get playing() {
    return this.ctx !== null;
  }

  /** Starts the audio output. Call it from a user gesture (a click), or the
   * browser keeps the output suspended. */
  async start() {
    if (this.ctx) return;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    const ctx = new AudioContextClass();
    this.gain = ctx.createGain();
    this.gain.connect(ctx.destination);
    this.ctx = ctx;
    this.nextStart = 0;
    this.dropped = 0;
    this.applyGain(true);
    if (ctx.state === "suspended") await ctx.resume();
  }

  stop() {
    const ctx = this.ctx;
    this.ctx = null;
    this.gain = null;
    if (ctx) ctx.close().catch(() => {});
  }

  /** Queues one binary frame from /audio_rx for playback. */
  push(data) {
    this.lastBlockAt = performance.now();
    if (!this.ctx) return;
    const samples = pcm16ToFloat32(data);
    if (!samples) return;

    const duration = samples.length / SAMPLE_RATE;
    const at = scheduleTime(this.ctx.currentTime, this.nextStart, duration);
    if (at === null) {
      this.dropped++;
      return;
    }
    const buffer = this.ctx.createBuffer(1, samples.length, SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);
    source.start(at);
    this.nextStart = at + duration;
  }

  setVolume(volume) {
    this.volume = Math.min(Math.max(Number(volume) || 0, 0), MAX_VOLUME);
    this.applyGain();
  }

  setMuted(muted) {
    this.muted = Boolean(muted);
    this.applyGain();
  }

  // The server sends nothing while transmitting; this also silences what is
  // still queued when the PTT goes on.
  setTransmitting(transmitting) {
    this.transmitting = Boolean(transmitting);
    this.applyGain();
  }

  applyGain(immediately = false) {
    if (!this.gain) return;
    const target = this.muted || this.transmitting ? 0 : this.volume;
    const param = this.gain.gain;
    if (immediately) {
      param.value = target;
    } else {
      // a short ramp instead of a step, which would click
      param.setTargetAtTime(target, this.ctx.currentTime, 0.02);
    }
  }
}
