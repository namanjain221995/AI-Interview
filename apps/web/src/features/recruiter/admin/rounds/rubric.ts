/**
 * Rubric categories: weighted scoring buckets whose weights must sum to 100
 * (the backend accepts ±0.5). Pure helpers used by the editor and its tests.
 */

import type { RubricCategory } from "@/lib/types";

import { slugKey, uniqueKey } from "../lib/utils";

export const RUBRIC_TOLERANCE = 0.5;

export function rubricTotal(rubric: readonly Pick<RubricCategory, "weight">[]): number {
  const total = rubric.reduce((sum, c) => sum + (Number.isFinite(c.weight) ? c.weight : 0), 0);
  return Math.round(total * 100) / 100;
}

/** An empty rubric is allowed (the engine falls back to competency weights). */
export function isRubricBalanced(rubric: readonly Pick<RubricCategory, "weight">[]): boolean {
  return rubric.length === 0 || Math.abs(rubricTotal(rubric) - 100) <= RUBRIC_TOLERANCE;
}

/** Human summary of the balance, e.g. "10 points left to assign". */
export function rubricBalanceMessage(rubric: readonly Pick<RubricCategory, "weight">[]): string | null {
  if (rubric.length === 0 || isRubricBalanced(rubric)) return null;
  const total = rubricTotal(rubric);
  const gap = Math.round((100 - total) * 100) / 100;
  return gap > 0
    ? `Weights add up to ${formatWeight(total)}%. Assign ${formatWeight(gap)} more ${Math.abs(gap) === 1 ? "point" : "points"} to reach 100%.`
    : `Weights add up to ${formatWeight(total)}%. Remove ${formatWeight(-gap)} ${Math.abs(gap) === 1 ? "point" : "points"} to reach 100%.`;
}

export function formatWeight(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

/** Splits 100 evenly in whole numbers (earlier categories take the remainder). */
export function distributeEvenly<T extends Pick<RubricCategory, "weight">>(rubric: readonly T[]): T[] {
  if (rubric.length === 0) return [];
  const base = Math.floor(100 / rubric.length);
  let remainder = 100 - base * rubric.length;
  return rubric.map((category) => {
    const extra = remainder > 0 ? 1 : 0;
    remainder -= extra;
    return { ...category, weight: base + extra };
  });
}

/**
 * Scales weights proportionally so they total exactly 100 (whole numbers,
 * largest remainder). Falls back to an even split when every weight is 0.
 */
export function normalizeRubricWeights<T extends Pick<RubricCategory, "weight">>(rubric: readonly T[]): T[] {
  const total = rubric.reduce((sum, c) => sum + Math.max(0, c.weight || 0), 0);
  if (rubric.length === 0) return [];
  if (total <= 0) return distributeEvenly(rubric);
  const exact = rubric.map((c) => (Math.max(0, c.weight || 0) / total) * 100);
  const floors = exact.map(Math.floor);
  let remainder = 100 - floors.reduce((s, v) => s + v, 0);
  const order = exact.map((v, i) => ({ i, f: v - Math.floor(v) })).sort((a, b) => b.f - a.f || a.i - b.i);
  for (const { i } of order) {
    if (remainder <= 0) break;
    floors[i] = (floors[i] ?? 0) + 1;
    remainder -= 1;
  }
  return rubric.map((c, i) => ({ ...c, weight: floors[i] ?? 0 }));
}

/** A new category with a unique key and the points still unassigned. */
export function newRubricCategory(rubric: readonly RubricCategory[], label = "New category"): RubricCategory {
  const remaining = Math.max(0, Math.round(100 - rubricTotal(rubric)));
  return {
    key: uniqueKey(slugKey(label) || "category", rubric.map((c) => c.key)),
    label,
    weight: remaining,
    competency_keys: [],
    dimensions: [],
    description: null,
  };
}

/** Keys derived from labels, kept unique within the rubric. */
export function keyForLabel(label: string, rubric: readonly RubricCategory[], index: number): string {
  const others = rubric.filter((_, i) => i !== index).map((c) => c.key);
  return uniqueKey(slugKey(label) || "category", others);
}
