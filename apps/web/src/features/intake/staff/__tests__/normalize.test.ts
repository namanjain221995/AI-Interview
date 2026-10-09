import { describe, expect, it } from "vitest";

import { normalizeAnalytics, normalizeCompetency, normalizeConfig, normalizeDetail, normalizeEvaluation, normalizeIntakeReport, normalizeStats, normalizeSummary, normalizeSummaryJson, normalizeTimeline } from "../api";
import { ANALYTICS, DETAIL, REPORT_CONTENT, STATS, TIMELINE } from "./fixtures";

describe("tolerant normalisers", () => {
  it("survive empty payloads", () => {
    expect(() => normalizeDetail({})).not.toThrow();
    expect(normalizeDetail({})).toMatchObject({ status: "active", bucket: "active", stages: [], activities: [], evaluation: null, decisions: [] });
    expect(normalizeConfig(null).kit.duration).toEqual({ min: 35, max: 50, target: 45 });
    expect(normalizeStats(undefined).buckets.awaiting_review).toBe(0);
    expect(normalizeTimeline("nope").stages).toEqual([]);
    expect(normalizeAnalytics({}).funnel.invitations).toBe(0);
  });

  it("keep the fixtures (contract shapes) unchanged", () => {
    expect(normalizeDetail(DETAIL).evaluation?.weighted_total).toBe("3.63");
    expect(normalizeStats(STATS)).toEqual({ ...STATS, totals: { ...STATS.totals, average_weighted_total: "3.4433" } });
    expect(normalizeTimeline(TIMELINE).stages[0]!.items[0]!.exchanges[0]!.answer!.answer_id).toBe("a-01");
    expect(normalizeAnalytics(ANALYTICS).overrides).toEqual({ count: 3, rate: 0.1429 });
  });

  it("turn numeric decimals into strings and reject unknown enum values", () => {
    const summary = normalizeSummary({ id: "x", bucket: "mystery", status: "draft", evaluation: { id: "e", status: "ready", weighted_total: 3.67, band: "superb" } });
    expect(summary.bucket).toBe("drafts");
    expect(summary.evaluation).toMatchObject({ weighted_total: "3.67", band: null });
    const competency = normalizeCompetency({ key: "technical", weight: 0.15, score: null });
    expect(competency).toMatchObject({ status: "insufficient", score: null, weight: "0.15" });
    expect(normalizeEvaluation({ validator: { issues: [{ code: "evidence_dropped", detail: "x" }] } }).validator_issues).toHaveLength(1);
  });

  it("resolve answer aliases that the server expanded into evidence references", () => {
    const summary = normalizeSummaryJson({
      drill_b: { passage: "C", concepts: [{ key: "noise", text: "Noise", covered: true, answer: { source: "answer", answer_id: "a-1", quote: "including the noise", supports: "positive" } }], coverage: { coverage: 0.5 } },
      bullets: [{ index: 2, status: "partial", excerpt: "time-boxing" }],
      drill_a: { passage_key: "B", passage_title: "Gradient Descent", metrics: { delivery_score: 4, delivery_basis: ["x"], events: [{ kind: "omission", ref_start: 1, ref_end: 2 }] } },
    });
    expect(summary.drill_b).toMatchObject({ passage: { key: "C" }, coverage: 0.5, concepts: [{ key: "noise", status: "covered", quote: "including the noise", evidence: { answer_id: "a-1" } }] });
    expect(summary.bullets[0]).toMatchObject({ index: 2, status: "partial", quote: "time-boxing" });
    expect(summary.drill_a).toMatchObject({ passage: { key: "B", title: "Gradient Descent" }, score: 4, basis: ["x"] });
    expect(summary.drill_a?.events).toHaveLength(1);
  });

  it("read only intake report documents", () => {
    expect(normalizeIntakeReport({ kind: "recruiter_report" })).toBeNull();
    const report = normalizeIntakeReport(REPORT_CONTENT)!;
    expect(report.F_reading.drill_a?.events).toHaveLength(9);
    expect(report.B_overall.band).toBe("accept_with_notes");
    expect(normalizeIntakeReport({ kind: "intake" })!.A_candidate_summary.candidate_name).toBe("Candidate");
  });
});
