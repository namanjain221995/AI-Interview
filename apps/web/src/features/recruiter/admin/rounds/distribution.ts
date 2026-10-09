/**
 * Difficulty mix: three linked percentages that always sum to exactly 100.
 *
 * The backend stores fractions (`{easy: 0.3, medium: 0.4, hard: 0.3}`) and
 * normalises any positive total; the editor works in whole percentages so the
 * three sliders read cleanly and add up to 100 at every step.
 */

import type { DifficultyDistribution } from "@/lib/types";

export const DIFFICULTY_KEYS = ["easy", "medium", "hard"] as const;
export type DifficultyKey = (typeof DIFFICULTY_KEYS)[number];
export type Percentages = Record<DifficultyKey, number>;

export const DEFAULT_PERCENTAGES: Percentages = { easy: 30, medium: 40, hard: 30 };

/**
 * Whole percentages for any set of non-negative weights, using the largest
 * remainder method so the result always sums to 100.
 */
export function toPercentages(distribution: Partial<DifficultyDistribution> | null | undefined): Percentages {
  const raw = DIFFICULTY_KEYS.map((key) => {
    const value = Number(distribution?.[key] ?? 0);
    return Number.isFinite(value) && value > 0 ? value : 0;
  });
  const total = raw.reduce((sum, v) => sum + v, 0);
  if (total <= 0) return { ...DEFAULT_PERCENTAGES };
  const exact = raw.map((v) => (v / total) * 100);
  const floors = exact.map(Math.floor);
  let remainder = 100 - floors.reduce((sum, v) => sum + v, 0);
  const order = exact
    .map((v, index) => ({ index, fraction: v - Math.floor(v) }))
    .sort((a, b) => b.fraction - a.fraction || a.index - b.index);
  for (const { index } of order) {
    if (remainder <= 0) break;
    floors[index] = (floors[index] ?? 0) + 1;
    remainder -= 1;
  }
  return { easy: floors[0] ?? 0, medium: floors[1] ?? 0, hard: floors[2] ?? 0 };
}

/** Fractions for the API (`30` -> `0.3`). */
export function fromPercentages(percentages: Percentages): DifficultyDistribution {
  return {
    easy: round4(percentages.easy / 100),
    medium: round4(percentages.medium / 100),
    hard: round4(percentages.hard / 100),
  };
}

function round4(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

/**
 * Sets one share and redistributes the rest across the other two in proportion
 * to their current values (evenly when both are zero). The result is whole
 * numbers summing to 100. `locked` keeps one of the other shares fixed when it
 * still fits.
 */
export function rebalance(current: Percentages, key: DifficultyKey, value: number, locked?: DifficultyKey | null): Percentages {
  const target = Math.round(Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0)));
  const others = DIFFICULTY_KEYS.filter((k) => k !== key);
  const next: Percentages = { ...current, [key]: target };
  let remaining = 100 - target;

  if (locked && locked !== key && others.includes(locked)) {
    const lockedValue = Math.min(current[locked], remaining);
    next[locked] = lockedValue;
    remaining -= lockedValue;
    const free = others.find((k) => k !== locked)!;
    next[free] = remaining;
    return next;
  }

  const [first, second] = others as [DifficultyKey, DifficultyKey];
  const base = current[first] + current[second];
  if (base <= 0) {
    // Medium gets the odd point so ties lean to the middle.
    const half = Math.floor(remaining / 2);
    const firstShare = first === "medium" ? remaining - half : half;
    next[first] = firstShare;
    next[second] = remaining - firstShare;
  } else {
    const firstShare = Math.round((remaining * current[first]) / base);
    next[first] = firstShare;
    next[second] = remaining - firstShare;
  }
  return next;
}

export function percentageTotal(percentages: Percentages): number {
  return DIFFICULTY_KEYS.reduce((sum, key) => sum + percentages[key], 0);
}

/** Normalises any stored distribution to fractions of whole percentages. */
export function normalizeDistribution(distribution: Partial<DifficultyDistribution> | null | undefined): DifficultyDistribution {
  return fromPercentages(toPercentages(distribution));
}
