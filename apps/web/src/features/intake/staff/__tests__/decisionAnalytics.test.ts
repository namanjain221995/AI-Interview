import { describe, expect, it } from "vitest";

import { bandRows, competencyRows, decisionRows, funnelSteps, overrideText, stageRows, trainingRows, weaknessRows } from "../lib/analytics";
import { decisionPayload, decisionServerError, differsFromBand, emptyDecisionDraft, hasDecisionErrors, unresolvedItems, validateDecision } from "../lib/decision";
import { groupReviewItems } from "../components/review/ReviewItems";
import { ANALYTICS, DETAIL } from "./fixtures";

describe("decision rules", () => {
  it("groups open review items of one kind and severity, keeping resolved items apart", () => {
    const base = DETAIL.review_items[0];
    const stage = (n: number) => ({ ...base, id: `ri_stage${n}`, kind: "stage_incomplete", severity: "attention" as const, title: "Stage not completed", detail: `Stage ${n} did not finish.`, resolution: null });
    const resolved = { ...stage(9), resolution: { resolution: "dismissed" as const, note: "", reviewer: null, at: null } };
    const groups = groupReviewItems([stage(1), base, stage(2), resolved, stage(3)]);
    const stages = groups.find((g) => g.items[0].kind === "stage_incomplete" && !g.items[0].resolution);
    expect(stages?.items.map((i) => i.id)).toEqual(["ri_stage1", "ri_stage2", "ri_stage3"]);
    expect(groups.filter((g) => g.items.some((i) => i.id === "ri_stage9"))).toHaveLength(1);
    expect(groups).toHaveLength(3);
  });


  it("uses the server's aligned outcomes per AI band for the override hint", () => {
    expect(differsFromBand("accept", "strong_accept")).toBe(false);
    expect(differsFromBand("second_interview", "accept_with_notes")).toBe(true);
    expect(differsFromBand("accept_with_notes", "borderline")).toBe(false);
    expect(differsFromBand("accept", "borderline")).toBe(true);
    expect(differsFromBand("reject", "reject")).toBe(false);
    expect(differsFromBand("accept_with_notes", "reject")).toBe(true);
    expect(differsFromBand("request_evidence", "insufficient_evidence")).toBe(false);
    expect(differsFromBand("accept", "insufficient_evidence")).toBe(true);
    expect(differsFromBand("reject", null)).toBe(false);
  });

  it("validates the outcome, the rationale length and requested evidence", () => {
    const empty = validateDecision(emptyDecisionDraft());
    expect(empty).toMatchObject({ outcome: expect.any(String), rationale: expect.stringContaining("10 characters") });
    expect(hasDecisionErrors(empty)).toBe(true);
    const ask = validateDecision({ ...emptyDecisionDraft(), outcome: "request_evidence", rationale: "Need the repository link first.", requestedEvidence: ["  "] });
    expect(ask.requested_evidence).toBeDefined();
    const ok = validateDecision({ ...emptyDecisionDraft(), outcome: "accept", rationale: "Strong reading and honest answers." });
    expect(hasDecisionErrors(ok)).toBe(false);
    expect(validateDecision({ ...emptyDecisionDraft(), outcome: "accept", rationale: "x".repeat(5001) }).rationale).toContain("5,000");
  });

  it("sends only resolved, known review items and trimmed notes", () => {
    const draft = {
      ...emptyDecisionDraft(),
      outcome: "accept_with_notes" as const,
      rationale: "  Solid base; flag structured speaking.  ",
      resolutions: {
        ri_7c1e0a9b3d: { resolution: "confirmed" as const, note: "  matches the résumé  " },
        ri_2b8d4f6a10: { resolution: null, note: "" },
        unknown: { resolution: "dismissed" as const, note: "" },
      },
      competencyNotes: { structured_answer: " pair with speaking module ", technical: "   " },
      requestedEvidence: ["ignored unless requesting"],
    };
    expect(decisionPayload(draft, DETAIL.review_items)).toEqual({
      outcome: "accept_with_notes",
      rationale: "Solid base; flag structured speaking.",
      review_items: [{ item_id: "ri_7c1e0a9b3d", resolution: "confirmed", note: "matches the résumé" }],
      competency_notes: { structured_answer: "pair with speaking module" },
      requested_evidence: [],
    });
    expect(unresolvedItems(draft, DETAIL.review_items).map((i) => i.id)).toEqual(["ri_2b8d4f6a10", "ri_5e3a9c7b21", "ri_9f0b1c2d3e"]);
  });

  it("maps server refusals onto the form", () => {
    expect(decisionServerError("rationale_required", "")).toHaveProperty("rationale");
    expect(decisionServerError("requested_evidence_required", "")).toHaveProperty("requested_evidence");
    expect(decisionServerError("evaluation_running", "").form).toContain("evaluation is running");
    expect(decisionServerError("session_not_finished", "").form).toContain("hasn't finished");
    expect(decisionServerError(undefined, "Boom").form).toBe("Boom");
  });
});

describe("analytics transforms", () => {
  it("builds the funnel with step-to-step and from-start shares", () => {
    const steps = funnelSteps(ANALYTICS.funnel);
    expect(steps.map((s) => s.label)).toEqual(["Invited", "Onboarded", "Started", "Completed", "Evaluated", "Decided"]);
    expect(steps[0]!.fromPrevious).toBeNull();
    expect(steps[2]!.fromPrevious).toBeCloseTo(33 / 37);
    expect(steps[5]!.fromStart).toBeCloseTo(21 / 42);
    expect(funnelSteps({ ...ANALYTICS.funnel, invitations: 0, onboarded: 0 })[1]!.fromPrevious).toBeNull();
  });

  it("keeps bands and outcomes in kit order, zero-filled", () => {
    expect(bandRows([{ band: "reject", label: "Reject", count: 2 }]).map((r) => [r.key, r.value])).toEqual([
      ["strong_accept", 0],
      ["accept_with_notes", 0],
      ["borderline", 0],
      ["reject", 2],
      ["insufficient_evidence", 0],
    ]);
    expect(decisionRows(ANALYTICS.decisions).map((r) => r.key)).toEqual(["accept", "accept_with_notes", "second_interview", "reject", "request_evidence"]);
  });

  it("turns averages, stage rates, weaknesses and training needs into bar rows", () => {
    const comps = competencyRows(ANALYTICS.competency_averages);
    expect(comps[0]).toMatchObject({ key: "communication", value: 3.8, average: "3.8" });
    expect(comps[1]!.hint).toContain("2 without enough evidence");
    expect(stageRows(ANALYTICS.stage_completion)[0]).toMatchObject({ value: 1, hint: "33 of 33 completed" });
    expect(weaknessRows(ANALYTICS.technical_weaknesses)[0]!.value).toBe(9);
    expect(trainingRows(ANALYTICS.training_needs)[0]!.label).toBe("AI/ML fundamentals");
    expect(overrideText(ANALYTICS.overrides, 21)).toBe("3 of 21 decisions differed from the AI recommendation (14%).");
    expect(overrideText({ count: 0, rate: null }, 0)).toBe("No decisions in this period.");
  });
});
