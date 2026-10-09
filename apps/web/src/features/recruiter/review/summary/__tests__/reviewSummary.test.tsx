/** AI review summary + "no evidence = no score" rendering (owner report: silent intake showed 50/100 everywhere). */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { normalizeScores } from "../../api/normalize";
import { ScoreCell } from "../../sessions/SessionsTable";
import { normalizeReviewSummary } from "../reviewSummary";
import { ReviewSummaryView } from "../ReviewSummaryCard";

afterEach(cleanup);

const facts = (over: Record<string, unknown> = {}) => ({
  duration_seconds: 20,
  planned_seconds: 2700,
  end_reason: "candidate_ended",
  ended_early: true,
  questions_asked: 1,
  questions_answered: 0,
  questions_skipped: 1,
  unprompted_answers: 0,
  candidate_words: 9,
  candidate_speaking_seconds: 3.6,
  minutes_answered: 0.06,
  candidate_talk_share: 0.18,
  silence_share: 0.62,
  speaking_time_estimated: true,
  ...over,
});

const silent = {
  session_id: "s1",
  kind: "intake",
  completed: true,
  evidence_level: "none",
  headline: "Insufficient evidence — interview too short / no answers",
  insufficient: true,
  facts: facts(),
  statements: ["The interview ran 20 s of 45.0 min planned and was ended by the candidate."],
  evidence_statement: "There is no assessable evidence: no answer produced anything to score. No score is given; the AI cannot judge this candidate from this session.",
  assessed_competencies: 0,
  total_competencies: 7,
  overall_score: null,
  recommendation: "insufficient_evidence",
  strengths: [],
  weaknesses: [],
  ai_assessment: null,
  ai_withheld_reason: "Not enough evidence for an AI assessment.",
  note: "Facts are computed from the recording data; AI text is assistive.",
};

describe("review summary", () => {
  it("states the facts and withholds AI text on a silent session", () => {
    const summary = normalizeReviewSummary(silent);
    expect(summary).not.toBeNull();
    render(<ReviewSummaryView summary={summary!} />);
    const card = screen.getByTestId("review-summary");
    expect(card).toHaveAttribute("data-evidence-level", "none");
    expect(screen.getByTestId("review-summary-headline")).toHaveTextContent("Insufficient evidence — interview too short / no answers");
    expect(card).toHaveTextContent("0 of 1");
    expect(card).toHaveTextContent("62%");
    expect(card).toHaveTextContent("≈ 4 s");
    expect(screen.getByTestId("review-summary-evidence")).toHaveTextContent("No score is given");
    expect(screen.getByTestId("review-summary-ai")).toHaveTextContent("Not enough evidence for an AI assessment.");
    expect(card).toHaveTextContent("None can be stated from this little evidence.");
  });

  it("shows evidence-backed strengths/weaknesses and the AI assessment when evidence suffices", () => {
    const summary = normalizeReviewSummary({
      ...silent,
      kind: "interview",
      evidence_level: "sufficient",
      headline: "Enough evidence to review",
      insufficient: false,
      facts: facts({ duration_seconds: 1720, questions_asked: 9, questions_answered: 9, questions_skipped: 0, candidate_speaking_seconds: 405, silence_share: 0.21, speaking_time_estimated: false }),
      evidence_statement: null,
      overall_score: 74.2,
      strengths: [{ competency_key: "kubernetes", competency: "Kubernetes", text: "Gave concrete scale.", quote: "about 120 nodes", evidence_id: "e1", question_id: "q1", answer_id: "a1", start_ms: 8000 }],
      weaknesses: [{ competency_key: "aws", competency: "AWS", text: "No cost controls.", quote: null, evidence_id: "e2", question_id: null, answer_id: null, start_ms: null }],
      ai_assessment: { text: "Strong Kubernetes depth.", source: "recruiter_report", generated_at: null },
      ai_withheld_reason: null,
    });
    render(<ReviewSummaryView summary={summary!} />);
    expect(screen.getByTestId("review-summary")).toHaveAttribute("data-evidence-level", "sufficient");
    expect(screen.getByText("“about 120 nodes”")).toBeInTheDocument();
    expect(screen.getByText("No cost controls.")).toBeInTheDocument();
    expect(screen.getByTestId("review-summary-ai")).toHaveTextContent("Strong Kubernetes depth.");
    expect(screen.queryByTestId("review-summary-evidence")).toBeNull();
  });
});

describe("null scores", () => {
  it("keeps score rows whose score is null (not dropped, not zero)", () => {
    const row = (category: string, key: string, score: number | null) => ({ id: key, session_id: "s1", category, key, label: key, score, confidence: 0, weight: 20, evidence_count: 0, positive_count: 0, negative_count: 0, sufficient_evidence: false, unanswered_areas: [], supporting_question_ids: [], positive_evidence_ids: [], negative_evidence_ids: [], transcript_refs: [], details: {}, is_final: true, created_at: "2026-10-09T00:00:00Z" });
    const scores = normalizeScores({ overall: row("overall", "overall", null), rubric: [row("rubric_category", "communication", null)], competencies: [row("competency", "communication", null)], recommendation: "insufficient_evidence" });
    expect(scores.overall?.score).toBeNull();
    expect(scores.rubric).toHaveLength(1);
    expect(scores.rubric[0]!.score).toBeNull();
    expect(scores.competencies[0]!.score).toBeNull();
  });

  it("labels a missing list score as insufficient evidence instead of a number", () => {
    render(<ScoreCell score={null} insufficient />);
    expect(screen.getByTestId("score-insufficient")).toHaveTextContent("Insufficient evidence");
    expect(screen.getByTestId("score-insufficient")).toHaveAttribute("title", "Insufficient evidence — interview too short / no answers");
  });
});
