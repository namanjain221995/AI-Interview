import { describe, expect, it } from "vitest";

import { backoffDelay } from "../backoff";
import { asServerMessage, parseServerMessage } from "../parse";
import { BATCH_SAMPLES, Downsampler, FRAME_SAMPLES, FrameBatcher, floatToInt16, int16ToFloat, rmsInt16, toDbfs } from "../pcm";
import { FRAME_MIC_AUDIO, packMicFrame, unpackFrame } from "../protocol";

function sine(freq: number, rate: number, samples: number, amp = 0.5): Float32Array {
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i++) out[i] = amp * Math.sin((2 * Math.PI * freq * i) / rate);
  return out;
}

function feedInBlocks(ds: Downsampler, input: Float32Array, block = 128): Float32Array {
  const parts: Float32Array[] = [];
  for (let i = 0; i < input.length; i += block) parts.push(ds.process(input.subarray(i, i + block)));
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Float32Array(total);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function rms(x: Float32Array, from = 0): number {
  let s = 0;
  for (let i = from; i < x.length; i++) s += x[i]! * x[i]!;
  return Math.sqrt(s / (x.length - from));
}

describe("Downsampler", () => {
  it("produces exactly 16 kHz output from 48 kHz in render quanta", () => {
    const ds = new Downsampler(48000);
    const out = feedInBlocks(ds, sine(440, 48000, 48000));
    expect(out.length).toBe(16000);
  });

  it("handles the fractional 44.1 kHz ratio without drift", () => {
    const ds = new Downsampler(44100);
    const out = feedInBlocks(ds, sine(440, 44100, 44100 * 3));
    expect(Math.abs(out.length - 48000)).toBeLessThanOrEqual(1);
  });

  it("is block-size independent (state carries across blocks)", () => {
    const input = sine(700, 48000, 9600);
    const whole = new Downsampler(48000).process(input);
    const blocked = feedInBlocks(new Downsampler(48000), input, 128);
    const odd = feedInBlocks(new Downsampler(48000), input, 77);
    expect(blocked.length).toBe(whole.length);
    expect(odd.length).toBe(whole.length);
    for (let i = 0; i < whole.length; i++) {
      expect(blocked[i]).toBeCloseTo(whole[i]!, 6);
      expect(odd[i]).toBeCloseTo(whole[i]!, 6);
    }
  });

  it("keeps speech-band tones and attenuates content above the new Nyquist", () => {
    const pass = feedInBlocks(new Downsampler(48000), sine(1000, 48000, 48000, 0.5));
    const stop = feedInBlocks(new Downsampler(48000), sine(12000, 48000, 48000, 0.5));
    const inRms = 0.5 / Math.SQRT2;
    expect(rms(pass, 200) / inRms).toBeGreaterThan(0.9);
    expect(rms(stop, 200) / inRms).toBeLessThan(0.15);
  });

  it("passes audio through unchanged at 16 kHz", () => {
    const input = sine(300, 16000, 512);
    const out = new Downsampler(16000).process(input);
    expect(Array.from(out)).toEqual(Array.from(input));
  });

  it("rejects upsampling", () => {
    expect(() => new Downsampler(8000)).toThrow();
  });
});

describe("PCM16 conversion", () => {
  it("clips and maps the full range", () => {
    const out = floatToInt16(new Float32Array([0, 1, -1, 2, -2, 0.5]));
    expect(Array.from(out)).toEqual([0, 32767, -32768, 32767, -32768, 16384]);
  });

  it("round-trips within one LSB", () => {
    const input = sine(220, 16000, 320, 0.8);
    const back = int16ToFloat(floatToInt16(input));
    for (let i = 0; i < input.length; i++) expect(Math.abs(back[i]! - input[i]!)).toBeLessThan(1 / 16384);
  });

  it("measures RMS and dBFS", () => {
    expect(rmsInt16(new Int16Array(320))).toBe(0);
    const full = new Int16Array(320).fill(16384);
    expect(rmsInt16(full)).toBeCloseTo(0.5, 3);
    expect(toDbfs(0.5)).toBeCloseTo(-6.02, 1);
    expect(toDbfs(0)).toBe(-100);
  });
});

describe("FrameBatcher", () => {
  it("emits one 100 ms batch per five 20 ms frames, in order", () => {
    const batcher = new FrameBatcher();
    const batches: Int16Array[] = [];
    for (let f = 0; f < 12; f++) {
      const frame = new Int16Array(FRAME_SAMPLES).fill(f);
      batches.push(...batcher.push(frame));
    }
    expect(batches).toHaveLength(2);
    expect(batches[0]).toHaveLength(BATCH_SAMPLES);
    expect(batches[0]![0]).toBe(0);
    expect(batches[0]![BATCH_SAMPLES - 1]).toBe(4);
    expect(batches[1]![0]).toBe(5);
    expect(batcher.pending).toBe(2 * FRAME_SAMPLES);
  });

  it("splits frames that straddle a batch boundary", () => {
    const batcher = new FrameBatcher(10);
    const out = batcher.push(Int16Array.from({ length: 25 }, (_, i) => i));
    expect(out.map((b) => Array.from(b))).toEqual([
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
      [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
    ]);
    expect(batcher.pending).toBe(5);
  });
});

describe("binary frames", () => {
  it("packs mic audio with type, seq and payload (little endian)", () => {
    const pcm = Int16Array.from([1, -2, 32767]);
    const buf = packMicFrame(0x01020304, pcm);
    const view = new DataView(buf);
    expect(view.getUint8(0)).toBe(FRAME_MIC_AUDIO);
    expect(view.getUint8(1)).toBe(0);
    expect(view.getUint32(4, true)).toBe(0x01020304);
    const parsed = unpackFrame(buf);
    expect(parsed.id).toBe(0x01020304);
    expect(Array.from(parsed.payload)).toEqual([1, -2, 32767]);
  });
});

describe("backoff", () => {
  it("grows exponentially, stays within jitter bounds and caps", () => {
    const mid = () => 0.5;
    expect(backoffDelay(0, { baseMs: 400 }, mid)).toBe(400);
    expect(backoffDelay(1, { baseMs: 400 }, mid)).toBe(800);
    expect(backoffDelay(3, { baseMs: 400 }, mid)).toBe(3200);
    expect(backoffDelay(20, { baseMs: 400, maxMs: 10_000 }, mid)).toBe(10_000);
    for (let i = 0; i < 50; i++) {
      const d = backoffDelay(2, { baseMs: 400, jitter: 0.25 });
      expect(d).toBeGreaterThanOrEqual(1200);
      expect(d).toBeLessThanOrEqual(2000);
    }
  });
});

describe("parseServerMessage", () => {
  it("accepts known messages and defaults a missing seq to 0", () => {
    const msg = parseServerMessage(JSON.stringify({ type: "turn.state", state: "listening" }));
    expect(msg).toMatchObject({ type: "turn.state", state: "listening", seq: 0 });
  });

  it("ignores unknown types, malformed JSON and messages missing required fields", () => {
    expect(parseServerMessage("{")).toBeNull();
    expect(parseServerMessage(JSON.stringify({ type: "future.thing", seq: 3 }))).toBeNull();
    expect(asServerMessage({ type: "tts.chunk", seq: 1, utterance_id: "u" })).toBeNull();
    expect(asServerMessage([1, 2])).toBeNull();
  });
});
