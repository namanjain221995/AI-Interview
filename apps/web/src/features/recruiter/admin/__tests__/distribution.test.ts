import { describe, expect, it } from "vitest";

import {
  DIFFICULTY_KEYS,
  fromPercentages,
  normalizeDistribution,
  percentageTotal,
  rebalance,
  toPercentages,
  type Percentages,
} from "../rounds/distribution";

describe("toPercentages", () => {
  it("converts fractions to whole percentages", () => {
    expect(toPercentages({ easy: 0.3, medium: 0.4, hard: 0.3 })).toEqual({ easy: 30, medium: 40, hard: 30 });
  });

  it("always sums to exactly 100 using the largest remainder", () => {
    const thirds = toPercentages({ easy: 1, medium: 1, hard: 1 });
    expect(percentageTotal(thirds)).toBe(100);
    expect(thirds).toEqual({ easy: 34, medium: 33, hard: 33 });
    expect(percentageTotal(toPercentages({ easy: 0.333333, medium: 0.333333, hard: 0.333334 }))).toBe(100);
    expect(percentageTotal(toPercentages({ easy: 0.15, medium: 0.45, hard: 0.4 }))).toBe(100);
  });

  it("normalises any positive total (the backend accepts unnormalised weights)", () => {
    expect(toPercentages({ easy: 2, medium: 2, hard: 4 })).toEqual({ easy: 25, medium: 25, hard: 50 });
  });

  it("falls back to the default mix for empty, zero or invalid input", () => {
    expect(toPercentages(null)).toEqual({ easy: 30, medium: 40, hard: 30 });
    expect(toPercentages({ easy: 0, medium: 0, hard: 0 })).toEqual({ easy: 30, medium: 40, hard: 30 });
    expect(toPercentages({ easy: Number.NaN, medium: -1, hard: 0 })).toEqual({ easy: 30, medium: 40, hard: 30 });
  });
});

describe("fromPercentages / normalizeDistribution", () => {
  it("maps percentages back to API fractions", () => {
    expect(fromPercentages({ easy: 25, medium: 50, hard: 25 })).toEqual({ easy: 0.25, medium: 0.5, hard: 0.25 });
  });

  it("round-trips stored distributions to clean fractions", () => {
    expect(normalizeDistribution({ easy: 0.4, medium: 0.45, hard: 0.15 })).toEqual({ easy: 0.4, medium: 0.45, hard: 0.15 });
    const d = normalizeDistribution({ easy: 1, medium: 1, hard: 1 });
    expect(d.easy + d.medium + d.hard).toBeCloseTo(1, 10);
  });
});

describe("rebalance (three linked sliders)", () => {
  const start: Percentages = { easy: 30, medium: 40, hard: 30 };

  it("keeps the total at 100 after every move", () => {
    for (const key of DIFFICULTY_KEYS) {
      for (let value = 0; value <= 100; value += 7) {
        const next = rebalance(start, key, value);
        expect(percentageTotal(next)).toBe(100);
        expect(next[key]).toBe(value);
        for (const k of DIFFICULTY_KEYS) expect(next[k]).toBeGreaterThanOrEqual(0);
      }
    }
  });

  it("splits the remainder in proportion to the other two shares", () => {
    // 50 left, medium:hard = 40:30
    expect(rebalance(start, "easy", 50)).toEqual({ easy: 50, medium: 29, hard: 21 });
    // 20 left, easy:hard = 30:30
    expect(rebalance(start, "medium", 80)).toEqual({ easy: 10, medium: 80, hard: 10 });
  });

  it("splits evenly when the other two are both zero, leaning to medium", () => {
    expect(rebalance({ easy: 100, medium: 0, hard: 0 }, "easy", 49)).toEqual({ easy: 49, medium: 26, hard: 25 });
  });

  it("gives everything to one share at 100 and spreads from 0", () => {
    expect(rebalance(start, "hard", 100)).toEqual({ easy: 0, medium: 0, hard: 100 });
    expect(rebalance({ easy: 0, medium: 0, hard: 100 }, "hard", 0)).toEqual({ easy: 50, medium: 50, hard: 0 });
  });

  it("clamps and rounds out-of-range input", () => {
    expect(rebalance(start, "easy", 140).easy).toBe(100);
    expect(rebalance(start, "easy", -5).easy).toBe(0);
    expect(rebalance(start, "easy", 33.6).easy).toBe(34);
    expect(percentageTotal(rebalance(start, "easy", Number.NaN))).toBe(100);
  });

  it("honours a locked share while it still fits", () => {
    expect(rebalance(start, "easy", 50, "hard")).toEqual({ easy: 50, medium: 20, hard: 30 });
    // locked share no longer fits: it shrinks to what is left
    expect(rebalance(start, "easy", 90, "hard")).toEqual({ easy: 90, medium: 0, hard: 10 });
  });
});
