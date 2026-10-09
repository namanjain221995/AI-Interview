import { describe, expect, it } from "vitest";

import type { RubricCategory } from "@/lib/types";

import {
  distributeEvenly,
  isRubricBalanced,
  keyForLabel,
  newRubricCategory,
  normalizeRubricWeights,
  rubricBalanceMessage,
  rubricTotal,
} from "../rounds/rubric";

function cat(key: string, weight: number): RubricCategory {
  return { key, label: key, weight, competency_keys: [], dimensions: [], description: null };
}

describe("rubric totals", () => {
  it("accepts weights summing to 100 within the backend tolerance (±0.5)", () => {
    expect(isRubricBalanced([cat("a", 30), cat("b", 30), cat("c", 40)])).toBe(true);
    expect(isRubricBalanced([cat("a", 33.3), cat("b", 33.3), cat("c", 33.3)])).toBe(true); // 99.9
    expect(isRubricBalanced([cat("a", 50), cat("b", 49.4)])).toBe(false); // 99.4
    expect(isRubricBalanced([cat("a", 60), cat("b", 50)])).toBe(false);
  });

  it("allows an empty rubric", () => {
    expect(isRubricBalanced([])).toBe(true);
    expect(rubricBalanceMessage([])).toBeNull();
  });

  it("explains how far off the weights are", () => {
    expect(rubricTotal([cat("a", 40), cat("b", 50)])).toBe(90);
    expect(rubricBalanceMessage([cat("a", 40), cat("b", 50)])).toBe("Weights add up to 90%. Assign 10 more points to reach 100%.");
    expect(rubricBalanceMessage([cat("a", 60), cat("b", 41)])).toBe("Weights add up to 101%. Remove 1 point to reach 100%.");
    expect(rubricBalanceMessage([cat("a", 50), cat("b", 50)])).toBeNull();
  });

  it("ignores non-finite weights in the total", () => {
    expect(rubricTotal([cat("a", Number.NaN), cat("b", 100)])).toBe(100);
  });
});

describe("weight helpers", () => {
  it("distributes 100 evenly in whole numbers", () => {
    const three = distributeEvenly([cat("a", 0), cat("b", 0), cat("c", 0)]);
    expect(three.map((c) => c.weight)).toEqual([34, 33, 33]);
    expect(rubricTotal(three)).toBe(100);
    expect(distributeEvenly([cat("a", 1)]).map((c) => c.weight)).toEqual([100]);
    expect(rubricTotal(distributeEvenly(Array.from({ length: 7 }, (_, i) => cat(`k${i}`, 0))))).toBe(100);
  });

  it("scales weights proportionally to exactly 100", () => {
    const scaled = normalizeRubricWeights([cat("a", 20), cat("b", 20), cat("c", 40)]);
    expect(scaled.map((c) => c.weight)).toEqual([25, 25, 50]);
    expect(rubricTotal(normalizeRubricWeights([cat("a", 1), cat("b", 1), cat("c", 1)]))).toBe(100);
    expect(normalizeRubricWeights([cat("a", 0), cat("b", 0)]).map((c) => c.weight)).toEqual([50, 50]);
  });

  it("creates new categories with unique keys and the unassigned points", () => {
    const rubric = [cat("new_category", 70)];
    const next = newRubricCategory(rubric);
    expect(next.key).toBe("new_category_2");
    expect(next.weight).toBe(30);
    expect(newRubricCategory([cat("a", 100)]).weight).toBe(0);
  });

  it("derives keys from labels without colliding with other categories", () => {
    const rubric = [cat("technical_knowledge", 50), cat("x", 50)];
    expect(keyForLabel("Technical Knowledge", rubric, 1)).toBe("technical_knowledge_2");
    expect(keyForLabel("Technical Knowledge", rubric, 0)).toBe("technical_knowledge");
    expect(keyForLabel("  Système & Design!  ", rubric, 1)).toBe("systeme_design");
  });
});
