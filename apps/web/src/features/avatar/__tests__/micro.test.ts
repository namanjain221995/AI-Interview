import { describe, expect, it } from "vitest";
import { BlinkScheduler } from "../micro/blink";
import { GazeController } from "../micro/gaze";
import { DEG } from "../core/math";

interface BlinkEvent {
  start: number;
  end: number;
  peak: number;
}

function simulateBlinks(seed: number, seconds: number, step = 1 / 240): BlinkEvent[] {
  const b = new BlinkScheduler(seed, 0);
  const events: BlinkEvent[] = [];
  let current: BlinkEvent | null = null;
  for (let t = 0; t <= seconds; t += step) {
    const v = b.update(t);
    if (v > 0 && !current) current = { start: t, end: t, peak: v };
    if (current) {
      current.peak = Math.max(current.peak, v);
      if (v === 0) {
        current.end = t;
        events.push(current);
        current = null;
      }
    }
  }
  return events;
}

describe("BlinkScheduler", () => {
  const events = simulateBlinks(1234, 3000);
  const step = 1 / 240;

  it("blinks for 120-180 ms and closes the lids fully", () => {
    expect(events.length).toBeGreaterThan(500);
    for (const e of events) {
      const duration = e.end - e.start;
      expect(duration).toBeGreaterThanOrEqual(0.12 - 2 * step);
      expect(duration).toBeLessThanOrEqual(0.18 + 2 * step);
      expect(e.peak).toBeGreaterThan(0.9);
      expect(e.peak).toBeLessThanOrEqual(1);
    }
  });

  it("uses 2-6 s intervals with occasional double blinks", () => {
    const gaps = events.slice(1).map((e, i) => e.start - events[i]!.end);
    const doubles = gaps.filter((g) => g < 0.25);
    const regular = gaps.filter((g) => g >= 0.25);
    for (const g of doubles) expect(g).toBeLessThanOrEqual(0.14 + 2 * step);
    for (const g of regular) {
      expect(g).toBeGreaterThanOrEqual(2 - 2 * step);
      expect(g).toBeLessThanOrEqual(6 + 2 * step);
    }
    const mean = regular.reduce((n, g) => n + g, 0) / regular.length;
    expect(mean).toBeGreaterThan(3.2);
    expect(mean).toBeLessThan(4.2);
    // Skewed toward short intervals: median below the midpoint of the range.
    const sorted = [...regular].sort((a, b) => a - b);
    expect(sorted[Math.floor(sorted.length / 2)]!).toBeLessThan(4);
    const doubleShare = doubles.length / events.length;
    expect(doubleShare).toBeGreaterThan(0.08);
    expect(doubleShare).toBeLessThan(0.22);
  });

  it("is deterministic for a seed", () => {
    const a = simulateBlinks(99, 120).map((e) => e.start.toFixed(4));
    const b = simulateBlinks(99, 120).map((e) => e.start.toFixed(4));
    const c = simulateBlinks(100, 120).map((e) => e.start.toFixed(4));
    expect(a).toEqual(b);
    expect(a).not.toEqual(c);
  });

  it("trigger() forces a prompt blink unless one just happened", () => {
    const b = new BlinkScheduler(5, 0);
    b.trigger(0.5);
    let blinked = false;
    for (let t = 0.5; t < 0.6; t += 1 / 240) if (b.update(t) > 0) blinked = true;
    expect(blinked).toBe(true);
  });
});

describe("GazeController", () => {
  it("keeps micro-saccades around the viewer and averts gaze while thinking", () => {
    const g = new GazeController(7, 0);
    let maxCamera = 0;
    for (let t = 0; t < 5; t += 1 / 60) {
      const o = g.update(t, 1 / 60, "camera", false, true);
      maxCamera = Math.max(maxCamera, Math.abs(o.eyeYaw), Math.abs(o.eyePitch));
    }
    expect(maxCamera).toBeLessThan(2 * DEG);
    let thinking = { eyeYaw: 0, eyePitch: 0, headYaw: 0, headPitch: 0, bigShift: false };
    for (let t = 5; t < 7; t += 1 / 60) thinking = { ...g.update(t, 1 / 60, "thinking", false) };
    expect(Math.abs(thinking.eyeYaw)).toBeGreaterThan(8 * DEG);
    expect(thinking.eyePitch).toBeGreaterThan(3 * DEG);
    expect(Math.abs(thinking.headYaw)).toBeGreaterThan(1 * DEG);
    expect(Math.abs(thinking.headYaw)).toBeLessThan(Math.abs(thinking.eyeYaw));
  });
});
