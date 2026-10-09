import { describe, expect, it } from "vitest";
import { LipSync, SHAPE_VISEMES } from "../lipsync/lipSync";
import { estimateWordDurationMs, normalizeViseme, textToVisemes } from "../lipsync/visemes";
import type { SpeechCue } from "../types";

/** Fake audio clock: tests move time explicitly. */
function fakeClock(start = 0) {
  let t = start;
  return {
    now: () => t,
    set: (v: number) => {
      t = v;
    },
  };
}

const cue = (overrides: Partial<SpeechCue> = {}): SpeechCue => ({
  utteranceId: "u1",
  chunkId: 0,
  startTime: 10,
  durationMs: 450,
  visemes: [
    { t: 0, v: "PP", w: 1 },
    { t: 120, v: "aa", w: 1 },
    { t: 300, v: "O", w: 0.8 },
  ],
  text: "pa-o",
  ...overrides,
});

/** Sample from `from` to `to` at 120 Hz, returning the frame at `to`. */
function run(lip: LipSync, clock: ReturnType<typeof fakeClock>, from: number, to: number) {
  let frame = lip.sampleAt(from);
  for (let t = from; t <= to + 1e-9; t += 1 / 120) {
    clock.set(t);
    frame = lip.sampleAt(t);
  }
  return frame;
}

const total = (w: Record<string, number>) => SHAPE_VISEMES.reduce((n, v) => n + w[v]!, 0);

describe("LipSync scheduling against the audio clock", () => {
  it("places visemes at startTime + t and follows them in order", () => {
    const clock = fakeClock(9.8);
    const lip = new LipSync(clock.now);
    lip.enqueue(cue());

    let f = run(lip, clock, 9.8, 9.9);
    expect(total(f.weights)).toBe(0);
    expect(f.speaking).toBe(false);

    f = run(lip, clock, 9.9, 10.06);
    expect(f.speaking).toBe(true);
    expect(f.weights.PP).toBeGreaterThan(0.9);

    f = run(lip, clock, 10.06, 10.2);
    expect(f.weights.aa).toBeGreaterThan(0.9);
    expect(f.weights.aa).toBeGreaterThan(f.weights.PP);

    f = run(lip, clock, 10.2, 10.38);
    expect(f.weights.O).toBeGreaterThan(0.65);
    expect(f.weights.O).toBeLessThanOrEqual(0.8 + 1e-6);
    expect(f.weights.O).toBeGreaterThan(f.weights.aa);
  });

  it("anticipates the next viseme (coarticulation) before its onset", () => {
    const clock = fakeClock(9.8);
    const lip = new LipSync(clock.now);
    lip.enqueue(cue());
    const f = run(lip, clock, 9.8, 10.105);
    // aa starts at 10.12 but is already blending in.
    expect(f.weights.aa).toBeGreaterThan(0.2);
  });

  it("decays to silence after the chunk and stops speaking", () => {
    const clock = fakeClock(9.8);
    const lip = new LipSync(clock.now);
    lip.enqueue(cue());
    const f = run(lip, clock, 9.8, 10.75);
    expect(total(f.weights)).toBeLessThan(0.05);
    expect(f.speaking).toBe(false);
  });

  it("interrupt() drops queued cues and closes the mouth within ~120 ms", () => {
    const clock = fakeClock(9.8);
    const lip = new LipSync(clock.now);
    lip.enqueue(cue());
    lip.enqueue(cue({ chunkId: 1, startTime: 10.45, visemes: [{ t: 0, v: "E", w: 1 }] }));
    let f = run(lip, clock, 9.8, 10.2);
    expect(total(f.weights)).toBeGreaterThan(0.8);
    lip.interrupt();
    expect(lip.queuedKeys).toBe(0);
    f = run(lip, clock, 10.2, 10.32);
    expect(total(f.weights)).toBeLessThan(0.05);
    f = run(lip, clock, 10.32, 10.6);
    expect(total(f.weights)).toBe(0);
    expect(f.speaking).toBe(false);
  });

  it("keeps speaking across contiguous chunks and reports the next start", () => {
    const clock = fakeClock(9.5);
    const lip = new LipSync(clock.now);
    lip.enqueue(cue({ chunkId: 0, startTime: 10, durationMs: 400 }));
    lip.enqueue(cue({ chunkId: 1, startTime: 10.4, durationMs: 400 }));
    let f = lip.sampleAt(9.5);
    expect(f.nextStartIn).toBeCloseTo(0.5, 5);
    for (let t = 10.0; t <= 10.8; t += 0.05) {
      clock.set(t);
      f = lip.sampleAt(t);
      expect(f.speaking).toBe(true);
    }
  });

  it("is robust to resent chunks, unsorted and invalid visemes", () => {
    const clock = fakeClock(0);
    const lip = new LipSync(clock.now);
    const messy = cue({
      startTime: 1,
      visemes: [
        { t: 300, v: "O", w: 2 },
        { t: 0, v: "pp", w: 1 },
        { t: 100, v: "bogus", w: 1 },
        { t: 120, v: "viseme_aa", w: -1 },
      ],
    });
    lip.enqueue(messy);
    const keys = lip.queuedKeys;
    lip.enqueue(messy);
    expect(lip.queuedKeys).toBe(keys); // replaced, not duplicated
    // 3 valid visemes + trailing sil
    expect(keys).toBe(4);
    const f = run(lip, clock, 0.9, 1.45);
    // O weight clamped to 1, aa weight clamped to 0.
    expect(f.weights.O).toBeLessThanOrEqual(1);
    expect(Number.isFinite(total(f.weights))).toBe(true);
  });

  it("speakWord() schedules an approximation at the current clock time", () => {
    const clock = fakeClock(5);
    const lip = new LipSync(clock.now);
    lip.speakWord("hello", 400);
    const f = run(lip, clock, 5, 5.2);
    expect(f.speaking).toBe(true);
    expect(total(f.weights)).toBeGreaterThan(0.3);
  });

  it("follows a new clock after setClock (e.g. AudioContext attached)", () => {
    const wall = fakeClock(100);
    const audio = fakeClock(0.5);
    const lip = new LipSync(wall.now);
    lip.setClock(audio.now);
    lip.enqueue(cue({ startTime: 0.6 }));
    const f = run(lip, audio, 0.5, 0.65);
    expect(f.weights.PP).toBeGreaterThan(0.8);
  });
});

describe("viseme helpers", () => {
  it("normalises viseme ids", () => {
    expect(normalizeViseme("aa")).toBe("aa");
    expect(normalizeViseme("AA")).toBe("aa");
    expect(normalizeViseme("viseme_PP")).toBe("PP");
    expect(normalizeViseme("e")).toBe("E");
    expect(normalizeViseme("sil")).toBe("sil");
    expect(normalizeViseme("xyz")).toBeNull();
    expect(normalizeViseme("")).toBeNull();
  });

  it("approximates visemes from spelling", () => {
    expect(textToVisemes("think").map((s) => s.v)).toEqual(["TH", "I", "nn", "kk"]);
    expect(textToVisemes("map").map((s) => s.v)).toEqual(["PP", "aa", "PP"]);
    // silent h, silent final e, punctuation and accents ignored
    expect(textToVisemes("Hello,").map((s) => s.v)).toEqual(["E", "nn", "O"]);
    expect(textToVisemes("make").map((s) => s.v)).toEqual(["PP", "aa", "kk"]);
    expect(textToVisemes("café").length).toBeGreaterThan(0);
    expect(textToVisemes("123")).toEqual([]);
    for (const s of textToVisemes("communication")) {
      expect(s.w).toBeGreaterThan(0);
      expect(s.w).toBeLessThanOrEqual(1);
    }
  });

  it("estimates word durations within sane bounds", () => {
    expect(estimateWordDurationMs("a")).toBeGreaterThanOrEqual(120);
    expect(estimateWordDurationMs("internationalisation")).toBeLessThanOrEqual(900);
  });
});
