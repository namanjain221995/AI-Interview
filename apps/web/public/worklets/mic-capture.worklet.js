/*
 * Microphone capture AudioWorklet (processor name "mic-capture").
 *
 * Input: the microphone at the AudioContext's device rate (global `sampleRate`,
 * typically 48 kHz or 44.1 kHz). Output (port messages, no audio output):
 *
 *   { type: "frame", pcm: ArrayBuffer, rms: number }
 *
 * where `pcm` holds exactly 320 PCM16 samples = 20 ms of 16 kHz mono audio and
 * `rms` is that frame's linear RMS (0-1). The main thread batches 5 frames into
 * one 100 ms binary 0x01 WebSocket frame and runs the local energy VAD on `rms`.
 *
 * The downsampler mirrors `Downsampler` in src/lib/realtime/pcm.ts exactly
 * (two cascaded Butterworth low-pass sections, then linear interpolation at
 * fractional positions with state carried across 128-sample render quanta).
 * A unit test executes this file against that implementation.
 *
 * Port commands: { type: "mute", muted: boolean } sends silence while muted;
 * { type: "stop" } ends processing.
 */

const OUTPUT_RATE = 16000;
const FRAME_SAMPLES = 320;

function lowpassCoefficients(rate, cutoffHz) {
  const w0 = (2 * Math.PI * cutoffHz) / rate;
  const cos = Math.cos(w0);
  const alpha = Math.sin(w0) / (2 * Math.SQRT1_2);
  const a0 = 1 + alpha;
  return {
    b0: (1 - cos) / 2 / a0,
    b1: (1 - cos) / a0,
    b2: (1 - cos) / 2 / a0,
    a1: (-2 * cos) / a0,
    a2: (1 - alpha) / a0,
  };
}

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.outputRate = opts.outputRate || OUTPUT_RATE;
    this.inputRate = opts.inputRate || sampleRate;
    this.ratio = this.inputRate / this.outputRate;
    this.coeffs = this.ratio === 1 ? null : lowpassCoefficients(this.inputRate, Math.min(7200, this.outputRate * 0.45));
    this.stages = [
      { x1: 0, x2: 0, y1: 0, y2: 0 },
      { x1: 0, x2: 0, y1: 0, y2: 0 },
    ];
    this.position = 0;
    this.previous = 0;
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.muted = false;
    this.stopped = false;
    this.port.onmessage = (event) => {
      const data = event.data || {};
      if (data.type === "stop") this.stopped = true;
      else if (data.type === "mute") this.muted = Boolean(data.muted);
    };
  }

  downsample(input) {
    const n = input.length;
    if (n === 0) return new Float32Array(0);
    if (!this.coeffs) return Float32Array.from(input);
    const c = this.coeffs;
    const filtered = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let x = input[i];
      for (let k = 0; k < this.stages.length; k++) {
        const s = this.stages[k];
        const y = c.b0 * x + c.b1 * s.x1 + c.b2 * s.x2 - c.a1 * s.y1 - c.a2 * s.y2;
        s.x2 = s.x1;
        s.x1 = x;
        s.y2 = s.y1;
        s.y1 = y;
        x = y;
      }
      filtered[i] = x;
    }
    const capacity = Math.ceil((n - this.position) / this.ratio) + 1;
    const out = new Float32Array(Math.max(0, capacity));
    let count = 0;
    let p = this.position;
    while (p <= n - 1) {
      const base = Math.floor(p);
      const frac = p - base;
      const a = base < 0 ? this.previous : filtered[base];
      const next = filtered[base + 1];
      const b = next === undefined ? a : next;
      out[count++] = a + (b - a) * frac;
      p += this.ratio;
    }
    this.position = p - n;
    this.previous = filtered[n - 1];
    return out.subarray(0, count);
  }

  emitFrame() {
    let sum = 0;
    for (let i = 0; i < FRAME_SAMPLES; i++) {
      const v = this.frame[i] / 0x8000;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / FRAME_SAMPLES);
    const pcm = this.frame.buffer;
    this.port.postMessage({ type: "frame", pcm, rms }, [pcm]);
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
  }

  process(inputs) {
    if (this.stopped) return false;
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    let mono = input[0];
    if (input.length > 1) {
      mono = new Float32Array(input[0].length);
      for (let ch = 0; ch < input.length; ch++) {
        const data = input[ch];
        for (let i = 0; i < data.length; i++) mono[i] += data[i] / input.length;
      }
    }
    const down = this.downsample(mono);
    for (let i = 0; i < down.length; i++) {
      let value = 0;
      if (!this.muted) {
        const s = Math.max(-1, Math.min(1, down[i]));
        value = Math.round(s < 0 ? s * 0x8000 : s * 0x7fff);
      }
      this.frame[this.filled++] = value;
      if (this.filled === FRAME_SAMPLES) this.emitFrame();
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCaptureProcessor);
