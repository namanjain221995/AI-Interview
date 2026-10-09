import { describe, expect, it } from "vitest";

import { parseMatchNotes } from "../candidates/matchNotes";

describe("parseMatchNotes", () => {
  it("splits the formula, the requirement counts and the narrative", () => {
    const notes = parseMatchNotes(
      "Score 92.2 = 0.50xcoverage 89.9 + 0.35xmodel 92 + 0.15xexperience 100. Requirements 15: strong 11, partial 3, weak 1, none 0. Candidate is a strong fit for the role.",
    );
    expect(notes.score).toBe(92.2);
    expect(notes.terms).toEqual([
      { key: "coverage", label: "Requirements covered by the résumé", weight: 0.5, value: 89.9 },
      { key: "model", label: "AI assessment of fit", weight: 0.35, value: 92 },
      { key: "experience", label: "Relevant experience", weight: 0.15, value: 100 },
    ]);
    expect(notes.requirements).toEqual({ total: 15, strong: 11, partial: 3, weak: 1, none: 0 });
    expect(notes.narrative).toBe("Candidate is a strong fit for the role.");
  });

  it("handles re-weighted formulas without a narrative", () => {
    const notes = parseMatchNotes("Score 60.7 = 0.77xcoverage 51.9 + 0.23xexperience 90. Requirements 11: strong 3, partial 2, weak 3, none 3.");
    expect(notes.terms.map((t) => [t.key, t.weight])).toEqual([
      ["coverage", 0.77],
      ["experience", 0.23],
    ]);
    expect(notes.narrative).toBe("");
  });

  it("leaves free text alone", () => {
    expect(parseMatchNotes("Strong Kubernetes background.")).toEqual({ narrative: "Strong Kubernetes background.", score: null, terms: [], requirements: null });
    expect(parseMatchNotes(null).narrative).toBe("");
  });
});

describe("notesFromBreakdown", () => {
  it("builds terms and requirement bands from the structured breakdown", async () => {
    const { notesFromBreakdown } = await import("../candidates/matchNotes");
    const notes = notesFromBreakdown(
      {
        score: 92.2,
        coverage: 89.9,
        model_fit: 92,
        experience_fit: 100,
        weights: { coverage: 0.5, model: 0.35, experience: 0.15 },
        bands: { strong: 11, partial: 3, weak: 1, none: 0 },
      },
      "Strong match for platform work.",
    );
    expect(notes.score).toBe(92.2);
    expect(notes.narrative).toBe("Strong match for platform work.");
    expect(notes.terms.map((t) => [t.key, t.weight, t.value])).toEqual([
      ["coverage", 0.5, 89.9],
      ["model", 0.35, 92],
      ["experience", 0.15, 100],
    ]);
    expect(notes.requirements).toEqual({ total: 15, strong: 11, partial: 3, weak: 1, none: 0 });
  });

  it("drops terms that are missing or carry no weight", async () => {
    const { notesFromBreakdown } = await import("../candidates/matchNotes");
    const notes = notesFromBreakdown(
      { score: 80, coverage: 80, model_fit: null, experience_fit: 90, weights: { coverage: 0.77, experience: 0.23 }, bands: {} },
      "",
    );
    expect(notes.terms.map((t) => t.key)).toEqual(["coverage", "experience"]);
    expect(notes.requirements).toBeNull();
  });
});
