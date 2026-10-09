import { describe, expect, it } from "vitest";

import { normalizeQuestions, normalizeScores, normalizeSessionIntegrity, normalizeTimeline, normalizeTranscript, asPage } from "../../api/normalize";
import { buildCompetencyIndex } from "../competencies";
import { initialLiveState, liveReducer, parseLiveMessage, projectedElapsed, projectedRemaining } from "../liveState";
import { buildReportView, reportFileName } from "../report";
import { answer, evidence, question, score } from "./factories";

function feed(events: Array<[string, unknown]>, start = initialLiveState) {
  return events.reduce((state, [name, data], index) => {
    const event = parseLiveMessage(name, typeof data === "string" ? data : JSON.stringify(data), 1_000 + index * 1_000);
    return event ? liveReducer(state, event) : state;
  }, start);
}

describe("live event parsing", () => {
  it("reads named and unnamed events, nested envelopes and ignores junk", () => {
    expect(parseLiveMessage("message", JSON.stringify({ type: "evaluation.ready", answer_id: "a1" }))).toMatchObject({ type: "evaluation.ready" });
    expect(parseLiveMessage("state.digest", "{}")).toMatchObject({ type: "state" });
    expect(parseLiveMessage("message", JSON.stringify({ type: "integrity.event", data: { event_type: "tab_hidden" } }))!.payload).toMatchObject({ event_type: "tab_hidden" });
    expect(parseLiveMessage("message", "not json")).toBeNull();
    expect(parseLiveMessage("message", JSON.stringify({ type: "something.else" }))).toBeNull();
    expect(parseLiveMessage("message", "")).toMatchObject({ type: "heartbeat" });
  });
});

describe("liveReducer", () => {
  it("applies state digests and remembers previous scores", () => {
    const state = feed([
      ["state", { state_digest: { turn_state: "listening", elapsed_s: 300, remaining_s: 1500, question_count: 4, status: "live", current_question: "How do you roll back?", competencies: [{ key: "aws", name: "AWS", score_estimate: 6.1, confidence: 0.4, evidence_count: 2, sufficient_evidence: false }] } }],
      ["state", { turn_state: "candidate_speaking", competencies: [{ key: "aws", score_estimate: 6.8, confidence: 0.5, evidence_count: 3, sufficient_evidence: false }] }],
    ]);
    expect(state).toMatchObject({ status: "live", turnState: "candidate_speaking", elapsedSeconds: 300, questionCount: 4, currentQuestion: { text: "How do you roll back?" } });
    expect(state.competencies[0]).toMatchObject({ key: "aws", score_estimate: 6.8 });
    expect(state.previousScores.aws).toBe(6.1);
    expect(state.ended).toBe(false);
  });

  it("streams transcript partials and finals per speaker", () => {
    let state = feed([
      ["transcript.partial", { speaker: "candidate", segment_id: "x", text: "We use" }],
      ["transcript.partial", { speaker: "candidate", segment_id: "x", text: "We use Terraform" }],
    ]);
    expect(state.partials.candidate?.text).toBe("We use Terraform");
    state = feed([["transcript.final", { speaker: "candidate", segment_id: "x", text: "We use Terraform modules." }]], state);
    expect(state.partials.candidate).toBeUndefined();
    expect(state.transcript).toEqual([expect.objectContaining({ id: "x", text: "We use Terraform modules.", partial: false })]);
    state = feed([["ai.utterance.end", { utterance_id: "u1", text: "Thanks. Next question.", interrupted: true }]], state);
    expect(state.transcript[1]).toMatchObject({ speaker: "interviewer", interrupted: true });
  });

  it("records integrity observations once, evaluations and the end of the interview", () => {
    const state = feed([
      ["integrity.event", { event: { id: "e1", type: "tab_hidden", severity: "low", confidence: null } }],
      ["integrity.event", { event: { id: "e1", type: "tab_hidden", severity: "low", count: 2 } }],
      ["evaluation.ready", { answer_id: "a1", competency_keys: ["aws"] }],
      ["session.ended", { reason: "time_limit" }],
    ]);
    expect(state.integrity).toHaveLength(1);
    expect(state.integrity[0]).toMatchObject({ id: "e1", count: 2, review_status: "unreviewed" });
    expect(state.activity.map((a) => a.title)).toEqual(["Interview ended", "Answer evaluated", "Switched away from the tab"]);
    expect(state.ended).toBe(true);
    expect(state.dataVersion).toBe(4);
  });

  it("tracks the current question from utterance starts", () => {
    const state = feed([
      ["state", { current_question: { id: "q1", text: "First?" } }],
      ["ai.utterance.start", { utterance_id: "u", kind: "question", question_id: "q2" }],
    ]);
    expect(state.currentQuestion).toEqual({ id: "q2", text: null });
    const same = feed([["ai.utterance.start", { kind: "question", question_id: "q2", text: "Second?" }]], state);
    expect(same.currentQuestion).toEqual({ id: "q2", text: "Second?" });
  });

  it("projects the clock forward between digests only while live", () => {
    const state = { ...initialLiveState, elapsedSeconds: 100, remainingSeconds: 50, clockAt: 1_000, status: "live" as const, turnState: "listening" as const };
    expect(projectedElapsed(state, 11_000)).toBe(110);
    expect(projectedRemaining(state, 61_000)).toBe(0);
    expect(projectedElapsed({ ...state, status: "paused" }, 11_000)).toBe(100);
  });
});

describe("normalisers", () => {
  it("accepts envelopes, bare arrays and named lists", () => {
    expect(asPage([1, 2])).toEqual({ items: [1, 2], total: 2 });
    expect(asPage({ items: [1], total: 9 })).toEqual({ items: [1], total: 9 });
    expect(normalizeTranscript({ segments: [{ id: "a", text: "hi" }, { id: "b" }] })).toHaveLength(1);
    expect(normalizeQuestions({ questions: [question({ sequence: 2 }), question({ sequence: 1, answers: undefined as never })] }).map((q) => q.sequence)).toEqual([1, 2]);
  });

  it("splits flat score lists by category", () => {
    const scores = normalizeScores([score({ category: "overall", key: "overall", score: 72 }), score({ key: "aws" }), score({ category: "rubric_category", key: "tech", score: 70 })]);
    expect(scores.overall?.score).toBe(72);
    expect(scores.competencies).toHaveLength(1);
    expect(scores.rubric).toHaveLength(1);
    expect(normalizeScores({ overall: null, competencies: [score()], rubric: [] }).competencies).toHaveLength(1);
  });

  it("derives integrity counts and reads timeline aliases", () => {
    const integrity = normalizeSessionIntegrity({ events: [{ id: "1", type: "tab_hidden", severity: "low", count: 3, occurred_at: "b" }, { id: "2", type: "copy_event", severity: "medium", count: 1, occurred_at: "a" }] });
    expect(integrity.countsByType).toEqual({ tab_hidden: 3, copy_event: 1 });
    expect(integrity.countsBySeverity).toEqual({ low: 1, medium: 1 });
    const timeline = normalizeTimeline({ points: [{ elapsed_seconds: "60", competency_key: "aws", score: 6 }], window_minutes: 5 });
    expect(timeline.snapshots[0]!.elapsed_seconds).toBe(60);
    expect(timeline.windowMinutes).toBe(5);
  });
});

describe("buildReportView", () => {
  const q1 = question({ id: "q1", sequence: 1, text: "Describe your EKS setup", answers: [answer({ id: "a1", transcript_text: "We ran three clusters", evaluation: { overall_quality: "strong", correctness: 8, confidence: 0.8, is_fallback: false, evaluation: { summary: "Clear", contradictions: [{ statement: "3 clusters", conflicts_with: "resume says 1", severity: "medium" }], claim_verifications: [{ claim_id: "c1", status: "verified", note: "Matched" }] } } as never })] });
  const ev = evidence({ id: "e1", competency_key: "k8s", question_id: "q1", transcript_ref: { quote: "three clusters across regions" } });
  const index = buildCompetencyIndex({ roundConfig: { competencies: [{ key: "k8s", name: "Kubernetes", weight: 1, required: true, min_evidence: 3, target_difficulty: null, focus_areas: [] }] } });

  it("prefers the frozen report content", () => {
    const view = buildReportView({
      index,
      report: {
        id: "r",
        session_id: "s",
        type: "recruiter",
        status: "ready",
        version: 2,
        recommendation: "proceed",
        overall_score: 74,
        confidence: 0.7,
        generated_at: null,
        released_to_candidate: false,
        released_at: null,
        released_by_id: null,
        error: null,
        created_at: "",
        updated_at: "",
        content: {
          candidate: { name: "Priya Shah" },
          narrative: { executive_summary: "Solid.", areas_for_human_followup: ["Ask about on-call"], competency_findings: [{ competency_key: "k8s", summary: "Strong", strengths: ["HPA"], weaknesses: [], unanswered_areas: ["etcd backups"], evidence_ids: ["e1"] }] },
          competencies: [{ key: "k8s", score: 7.5, confidence: 0.6 }],
          claims_verified: ["Led EKS migration"],
          claims_unverified: [{ claim: "Cut costs 40%", note: "Not discussed" }],
          integrity: { total: 2, unreviewed: 1, by_type: { tab_hidden: 2 } },
        } as never,
      },
      evidence: [ev],
      questions: [q1],
    });
    expect(view).toMatchObject({ overallScore: 74, recommendation: "proceed", executiveSummary: "Solid.", followUps: ["Ask about on-call"] });
    expect(view.candidate?.full_name).toBe("Priya Shah");
    expect(view.competencies[0]).toMatchObject({ key: "k8s", label: "Kubernetes", score: 7.5, summary: "Strong", unansweredAreas: ["etcd backups"] });
    expect(view.competencies[0]!.evidence.map((e) => e.id)).toEqual(["e1"]);
    expect(view.claims.map((c) => c.status)).toEqual(["verified", "unverified"]);
    expect(view.integrity).toMatchObject({ total: 2, unreviewed: 1, byType: [{ type: "tab_hidden", count: 2 }] });
    // Sections missing from content come from the session APIs.
    expect(view.questions[0]).toMatchObject({ text: "Describe your EKS setup", quality: "strong", answer: "We ran three clusters" });
    expect(view.contradictions).toHaveLength(1);
    expect(view.excerpts[0]).toMatchObject({ text: "three clusters across regions", questionId: "q1" });
  });

  it("builds a complete view from session data alone", () => {
    const view = buildReportView({
      index,
      session: { state_digest: { claims: [{ id: "c1", claim: "Ran EKS", status: "in_progress" }] } } as never,
      scores: { overall: score({ category: "overall", key: "overall", score: 66, confidence: 0.5 }), competencies: [score({ key: "k8s", score: 6.6 })], rubric: [score({ category: "rubric_category", key: "tech", label: "Technical", score: 66, weight: 60 })] },
      questions: [q1],
      evidence: [ev],
      integrity: { events: [{ review_status: "noted" } as never, { review_status: "unreviewed" } as never], countsByType: { tab_hidden: 2 }, countsBySeverity: { low: 2 }, policy: null },
    });
    expect(view).toMatchObject({ overallScore: 66, confidence: 0.5 });
    expect(view.rubric).toEqual([{ key: "tech", label: "Technical", weight: 60, score: 66, confidence: 0.7 }]);
    expect(view.claims).toEqual([{ id: "c1", claim: "Ran EKS", status: "verified", note: "Matched" }]);
    expect(view.integrity).toMatchObject({ total: 2, unreviewed: 1 });
    expect(view.competencies[0]!.evidence).toHaveLength(1);
  });

  it("names export files safely", () => {
    expect(reportFileName({ candidate: { id: "1", full_name: "Zoë O'Brien" }, startedAt: "2026-10-06T10:00:00Z" })).toBe("interview-report-zoe-o-brien-2026-10-06.json");
    expect(reportFileName({ candidate: null, startedAt: "2026-01-02" }, "pdf")).toBe("interview-report-candidate-2026-01-02.pdf");
  });
});

describe("buildReportView with the stored report document (schema v1)", () => {
  it("reads nested scores, per-question answers, coding results, contradictions and integrity summaries", async () => {
    const demo = await import("../../__fixtures__/demoData");
    const index = buildCompetencyIndex({ roundConfig: demo.demoRoundConfig });
    const view = buildReportView({ report: demo.demoReportResponse(), index });
    expect(view).toMatchObject({ overallScore: 69, recommendation: "proceed", durationSeconds: 1838, endReason: "sufficient_evidence" });
    expect(view.candidate?.full_name).toBe("Jane Doe");
    expect(view.recommendationRationale).toMatch(/Overall 69\/100/);
    expect(view.rubric).toHaveLength(6);
    expect(view.competencies).toHaveLength(11);
    expect(view.competencies.find((c) => c.key === "aws")).toMatchObject({ score: 6.4, sufficient: true, unansweredAreas: ["Egress and VPC endpoint design", "Access reviews"] });
    expect(view.competencies.find((c) => c.key === "aws")!.evidence.length).toBeGreaterThan(0);
    const q1 = view.questions.find((q) => q.sequence === 1)!;
    expect(q1).toMatchObject({ quality: "strong", answer: expect.stringContaining("three EKS clusters") });
    expect(q1.dimensions.map((d) => d.key)).toContain("correctness");
    expect(q1.evidence).toHaveLength(3);
    expect(view.coding[0]).toMatchObject({ title: "Most frequent error service", testsPassed: 9, testsTotal: 10, hintsUsed: 1, assistanceFactor: 0.93 });
    expect(view.contradictions).toEqual([{ statement: "Joined Bluepeak in January 2023", conflicts_with: "Resume lists Harborline employment until March 2023", severity: "low" }]);
    expect(view.integrity).toMatchObject({ total: 4, unreviewed: 1 });
    expect(view.integrity!.byType.find((t) => t.type === "tab_hidden")).toEqual({ type: "tab_hidden", count: 1 });
    expect(view.claims.find((c) => c.id === "c-cost")).toMatchObject({ status: "partially_verified" });
    expect(view.excerpts.length).toBeGreaterThan(3);
    expect(view.excerpts.every((e) => e.text.length > 0)).toBe(true);
    expect(view.followUps).toHaveLength(4);
  });
});
