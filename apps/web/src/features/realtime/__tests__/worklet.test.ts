/**
 * Executes the SHIPPED AudioWorklet source (public/worklets/mic-capture.worklet.js)
 * in a minimal AudioWorkletGlobalScope stand-in and checks it against the
 * TypeScript reference implementation.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { Downsampler, floatToInt16 } from "@/lib/realtime/pcm";

interface FramePost {
  type: string;
  pcm: ArrayBuffer;
  rms: number;
}

interface Processor {
  port: { postMessage: (msg: FramePost, transfer?: unknown[]) => void; onmessage: ((e: { data: unknown }) => void) | null };
  process(inputs: Float32Array[][]): boolean;
}

const SOURCE = readFileSync(resolve(__dirname, "../../../../public/worklets/mic-capture.worklet.js"), "utf8");

function loadProcessor(sampleRate: number): { processor: Processor; posts: FramePost[] } {
  const posts: FramePost[] = [];
  let Registered: (new (options?: unknown) => Processor) | null = null;
  class FakeAudioWorkletProcessor {
    port = {
      onmessage: null as ((e: { data: unknown }) => void) | null,
      postMessage: (msg: FramePost) => {
        posts.push(msg);
      },
    };
  }
  const scope = new Function("AudioWorkletProcessor", "registerProcessor", "sampleRate", `${SOURCE}`);
  scope(FakeAudioWorkletProcessor, (name: string, ctor: new () => Processor) => {
    expect(name).toBe("mic-capture");
    Registered = ctor;
  }, sampleRate);
  if (!Registered) throw new Error("processor not registered");
  const Ctor = Registered as new (options?: unknown) => Processor;
  return { processor: new Ctor({ processorOptions: { outputRate: 16000 } }), posts };
}

function speechLike(rate: number, samples: number): Float32Array {
  const out = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    const t = i / rate;
    out[i] = 0.4 * Math.sin(2 * Math.PI * 180 * t) + 0.2 * Math.sin(2 * Math.PI * 1300 * t) + 0.1 * Math.sin(2 * Math.PI * 3100 * t);
  }
  return out;
}

function run(processor: Processor, input: Float32Array, channels = 1) {
  for (let i = 0; i < input.length; i += 128) {
    const block = input.subarray(i, i + 128);
    const chans = Array.from({ length: channels }, () => block);
    expect(processor.process([chans])).toBe(true);
  }
}

describe("mic-capture worklet", () => {
  it.each([48000, 44100])("matches the TypeScript downsampler exactly at %i Hz", (rate) => {
    const { processor, posts } = loadProcessor(rate);
    const input = speechLike(rate, rate); // 1 s
    run(processor, input);

    const reference = new Downsampler(rate);
    const parts: Float32Array[] = [];
    for (let i = 0; i < input.length; i += 128) parts.push(reference.process(input.subarray(i, i + 128)));
    const expected = floatToInt16(Float32Array.from(parts.flatMap((p) => Array.from(p))));

    const frames = posts.filter((p) => p.type === "frame");
    expect(frames.length).toBe(Math.floor(expected.length / 320));
    const got = new Int16Array(frames.length * 320);
    frames.forEach((f, i) => {
      expect(f.pcm.byteLength).toBe(640);
      got.set(new Int16Array(f.pcm), i * 320);
    });
    expect(Array.from(got)).toEqual(Array.from(expected.subarray(0, got.length)));
  });

  it("emits 50 frames per second of 20 ms each, with a correct RMS", () => {
    const { processor, posts } = loadProcessor(48000);
    const input = new Float32Array(48000).fill(0.25);
    run(processor, input);
    expect(posts.length).toBe(50);
    const last = posts[posts.length - 1]!;
    expect(last.rms).toBeGreaterThan(0.24);
    expect(last.rms).toBeLessThan(0.26);
  });

  it("averages stereo input to mono", () => {
    const mono = loadProcessor(48000);
    const stereo = loadProcessor(48000);
    const input = speechLike(48000, 4800);
    run(mono.processor, input, 1);
    run(stereo.processor, input, 2);
    expect(stereo.posts.map((p) => Array.from(new Int16Array(p.pcm)))).toEqual(mono.posts.map((p) => Array.from(new Int16Array(p.pcm))));
  });

  it("sends silence while muted and stops on command", () => {
    const { processor, posts } = loadProcessor(48000);
    processor.port.onmessage?.({ data: { type: "mute", muted: true } });
    run(processor, speechLike(48000, 4800));
    expect(posts.length).toBe(5);
    for (const p of posts) expect(new Int16Array(p.pcm).every((v) => v === 0)).toBe(true);
    processor.port.onmessage?.({ data: { type: "stop" } });
    expect(processor.process([[new Float32Array(128)]])).toBe(false);
  });

  it("stays alive without input (device not yet connected)", () => {
    const { processor, posts } = loadProcessor(48000);
    expect(processor.process([[]])).toBe(true);
    expect(processor.process([])).toBe(true);
    expect(posts).toHaveLength(0);
  });
});
