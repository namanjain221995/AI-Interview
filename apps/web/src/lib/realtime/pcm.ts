/**
 * PCM helpers shared by the microphone pipeline and its tests.
 *
 * The AudioWorklet in public/worklets/mic-capture.worklet.js implements the
 * SAME algorithm as `Downsampler` below (worklets cannot import app modules);
 * src/features/realtime/__tests__/worklet.test.ts runs the shipped worklet
 * source against this implementation so the two can never drift apart.
 */

import { MIC_SAMPLE_RATE } from "./protocol";

/** 20 ms of 16 kHz audio. */
export const FRAME_SAMPLES = (MIC_SAMPLE_RATE * 20) / 1000; // 320
/** Frames per binary message (5 x 20 ms = 100 ms). */
export const FRAMES_PER_BATCH = 5;
export const BATCH_SAMPLES = FRAME_SAMPLES * FRAMES_PER_BATCH; // 1600

interface BiquadState {
  x1: number;
  x2: number;
  y1: number;
  y2: number;
}

interface BiquadCoefficients {
  b0: number;
  b1: number;
  b2: number;
  a1: number;
  a2: number;
}

/** RBJ cookbook low-pass (Q = 1/sqrt(2), Butterworth section). */
export function lowpassCoefficients(sampleRate: number, cutoffHz: number): BiquadCoefficients {
  const w0 = (2 * Math.PI * cutoffHz) / sampleRate;
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

/**
 * Streaming downsampler: two cascaded Butterworth low-pass sections
 * (anti-aliasing, 4th order) followed by linear interpolation at fractional
 * positions. State carries across blocks, so feeding 128-sample render
 * quanta yields exactly the same output as one long buffer.
 */
export class Downsampler {
  readonly inputRate: number;
  readonly outputRate: number;
  readonly ratio: number;
  private readonly coeffs: BiquadCoefficients | null;
  private readonly stages: BiquadState[] = [
    { x1: 0, x2: 0, y1: 0, y2: 0 },
    { x1: 0, x2: 0, y1: 0, y2: 0 },
  ];
  /** Position of the next output sample, relative to the current block (index -1 = previous block's last sample). */
  private position = 0;
  private previous = 0;

  constructor(inputRate: number, outputRate: number = MIC_SAMPLE_RATE) {
    if (!(inputRate > 0) || !(outputRate > 0)) throw new Error("sample rates must be positive");
    if (inputRate < outputRate) throw new Error("Downsampler cannot upsample");
    this.inputRate = inputRate;
    this.outputRate = outputRate;
    this.ratio = inputRate / outputRate;
    this.coeffs = this.ratio === 1 ? null : lowpassCoefficients(inputRate, Math.min(7200, outputRate * 0.45));
  }

  /** Downsample one block; returns the (possibly empty) 16 kHz output. */
  process(input: Float32Array): Float32Array {
    const n = input.length;
    if (n === 0) return new Float32Array(0);
    if (!this.coeffs) return Float32Array.from(input);

    const filtered = new Float32Array(n);
    const c = this.coeffs;
    for (let i = 0; i < n; i++) {
      let x = input[i]!;
      for (const s of this.stages) {
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
      const a = base < 0 ? this.previous : filtered[base]!;
      const b = filtered[base + 1] ?? a;
      out[count++] = a + (b - a) * frac;
      p += this.ratio;
    }
    this.position = p - n;
    this.previous = filtered[n - 1]!;
    return out.subarray(0, count);
  }

  reset(): void {
    for (const s of this.stages) {
      s.x1 = s.x2 = s.y1 = s.y2 = 0;
    }
    this.position = 0;
    this.previous = 0;
  }
}

/** Float [-1, 1] -> PCM16 with clipping. */
export function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]!));
    out[i] = Math.round(s < 0 ? s * 0x8000 : s * 0x7fff);
  }
  return out;
}

/** PCM16 -> float [-1, 1). */
export function int16ToFloat(input: Int16Array): Float32Array {
  const out = new Float32Array(input.length);
  for (let i = 0; i < input.length; i++) out[i] = input[i]! / 0x8000;
  return out;
}

/** Root-mean-square of PCM16 samples, normalised to 0-1. */
export function rmsInt16(samples: Int16Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i]! / 0x8000;
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

/** Linear 0-1 amplitude -> dBFS (floored at -100). */
export function toDbfs(rms: number): number {
  return rms > 0 ? Math.max(-100, 20 * Math.log10(rms)) : -100;
}

/**
 * Collects 20 ms PCM16 frames and emits one 100 ms batch (exactly
 * BATCH_SAMPLES samples) every FRAMES_PER_BATCH frames. Frames of any length
 * are accepted; the batch boundary is by sample count.
 */
export class FrameBatcher {
  private buffer: Int16Array;
  private filled = 0;
  readonly batchSamples: number;

  constructor(batchSamples: number = BATCH_SAMPLES) {
    this.batchSamples = batchSamples;
    this.buffer = new Int16Array(batchSamples);
  }

  /** Returns zero or more complete batches. */
  push(frame: Int16Array): Int16Array[] {
    const batches: Int16Array[] = [];
    let offset = 0;
    while (offset < frame.length) {
      const take = Math.min(this.batchSamples - this.filled, frame.length - offset);
      this.buffer.set(frame.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.batchSamples) {
        batches.push(this.buffer);
        this.buffer = new Int16Array(this.batchSamples);
        this.filled = 0;
      }
    }
    return batches;
  }

  /** Samples waiting for the next batch. */
  get pending(): number {
    return this.filled;
  }

  reset(): void {
    this.filled = 0;
  }
}
