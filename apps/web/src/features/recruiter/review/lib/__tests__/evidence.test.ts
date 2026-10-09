import { describe, expect, it } from "vitest";

import {
  collectEvidence,
  evidenceByAnswer,
  evidenceByQuestion,
  evidenceWeight,
  explainCompetency,
  explanationSentence,
  groupEvidenceByCompetency,
  quoteOf,
  sortByWeight,
  topEvidence,
} from "../evidence";
import { answer, evidence, question, score } from "./factories";

describe("evidence weight and ordering", () => {
  it("weights by strength x confidence with clamping", () => {
    expect(evidenceWeight({ strength: 0.5, confidence: 0.8 })).toBeCloseTo(0.4);
    expect(evidenceWeight({ strength: 2, confidence: -1 })).toBe(0);
    expect(evidenceWeight({ strength: Number.NaN, confidence: 1 })).toBe(0);
  });

  it("sorts strongest first and keeps chronological order for ties", () => {
    const a = evidence({ strength: 0.5, confidence: 0.5, elapsed_seconds: 30 });
    const b = evidence({ strength: 0.9, confidence: 0.9 });
    const c = evidence({ strength: 0.5, confidence: 0.5, elapsed_seconds: 10 });
    expect(sortByWeight([a, b, c]).map((e) => e.id)).toEqual([b.id, c.id, a.id]);
  });
});

describe("groupEvidenceByCompetency", () => {
  it("splits by signal, counts non-neutral items and computes a weighted balance", () => {
    const items = [
      evidence({ competency_key: "aws", signal: "positive", strength: 1, confidence: 1 }),
      evidence({ competency_key: "aws", signal: "negative", strength: 0.5, confidence: 1 }),
      evidence({ competency_key: "aws", signal: "neutral" }),
      evidence({ competency_key: "k8s", signal: "negative" }),
    ];
    const groups = groupEvidenceByCompetency(items, ["k8s", "aws", "terraform"]);
    expect(groups.map((g) => g.key)).toEqual(["k8s", "aws", "terraform"]);
    const aws = groups[1]!;
    expect(aws.positive).toHaveLength(1);
    expect(aws.negative).toHaveLength(1);
    expect(aws.neutral).toHaveLength(1);
    expect(aws.counted).toBe(2);
    expect(aws.balance).toBeCloseTo((1 - 0.5) / 1.5, 3);
    expect(groups[2]).toMatchObject({ counted: 0, balance: 0 });
    expect(groups[0]!.balance).toBe(-1);
  });

  it("appends competencies missing from the order in first-seen order", () => {
    const groups = groupEvidenceByCompetency([evidence({ competency_key: "b" }), evidence({ competency_key: "a" })], ["a"]);
    expect(groups.map((g) => g.key)).toEqual(["a", "b"]);
  });
});

describe("evidence lookups", () => {
  it("merges endpoint evidence with nested answer evidence without duplicates", () => {
    const shared = evidence({ id: "shared" });
    const nested = evidence({ id: "nested", question_id: null, answer_id: null });
    const q = question({ id: "q1", answers: [answer({ id: "a1", evidence: [shared, nested] })] });
    const merged = collectEvidence([shared], [q]);
    expect(merged.map((e) => e.id).sort()).toEqual(["nested", "shared"]);
    expect(merged.find((e) => e.id === "nested")).toMatchObject({ question_id: "q1", answer_id: "a1" });
  });

  it("indexes by answer (falling back to question) and by question", () => {
    const items = [evidence({ answer_id: "a1", question_id: "q1" }), evidence({ answer_id: null, question_id: "q2" }), evidence({ answer_id: null, question_id: null })];
    const byAnswer = evidenceByAnswer(items);
    expect([...byAnswer.keys()].sort()).toEqual(["a1", "q:q2"]);
    expect(evidenceByQuestion(items).get("q1")).toHaveLength(1);
  });

  it("picks the strongest items of a signal and reads quotes", () => {
    const strong = evidence({ signal: "negative", strength: 1, confidence: 1, transcript_ref: { quote: "We never tested restores" } });
    const weak = evidence({ signal: "negative", strength: 0.2, confidence: 0.5 });
    expect(topEvidence([weak, strong, evidence()], "negative", 1)).toEqual([strong]);
    expect(quoteOf(strong)).toBe("We never tested restores");
    expect(quoteOf(weak)).toBeNull();
  });
});

describe("explainCompetency", () => {
  const q1 = question({ id: "q1", sequence: 1, competency_keys: ["aws"], answers: [answer({ id: "a1" })] });
  const q2 = question({ id: "q2", sequence: 2, competency_keys: ["aws"], answers: [answer({ id: "a2" })] });
  const q3 = question({ id: "q3", sequence: 3, competency_keys: ["k8s"], answers: [answer({ id: "a3" })] });
  const pos = evidence({ id: "p1", competency_key: "aws", question_id: "q2", signal: "positive", transcript_ref: { answer_id: "a2", start_ms: 90_000, quote: "Cut cost by 40%" } });
  const neg = evidence({ id: "n1", competency_key: "aws", question_id: "q1", signal: "negative", difficulty: "hard" });
  const other = evidence({ id: "o1", competency_key: "k8s", question_id: "q3" });

  it("uses the score record's own lists when present", () => {
    const record = score({
      key: "aws",
      score: 7.2,
      confidence: 0.81,
      evidence_count: 2,
      positive_evidence_ids: ["p1", "missing"],
      negative_evidence_ids: ["n1"],
      supporting_question_ids: ["q2"],
      unanswered_areas: ["cost allocation", "cost allocation"],
      transcript_refs: [{ answer_id: "a2", start_ms: 90_000, quote: "Cut cost by 40%" }],
    });
    const result = explainCompetency({ key: "aws", label: "AWS", score: record, evidence: [pos, neg, other], questions: [q1, q2, q3] });
    expect(result.positives.map((e) => e.id)).toEqual(["p1"]);
    expect(result.negatives.map((e) => e.id)).toEqual(["n1"]);
    expect(result.supportingQuestions.map((q) => q.id)).toEqual(["q2"]);
    expect(result.unansweredAreas).toEqual(["cost allocation"]);
    expect(result.transcriptRefs).toHaveLength(1);
    expect(result).toMatchObject({ score: 7.2, confidence: 0.81, evidenceCount: 2, difficultyMix: { medium: 1, hard: 1 } });
  });

  it("derives everything from raw evidence when the score lists are empty", () => {
    const result = explainCompetency({ key: "aws", label: "AWS", score: score({ key: "aws" }), evidence: [pos, neg, other], questions: [q1, q2, q3] });
    expect(result.positives.map((e) => e.id)).toEqual(["p1"]);
    expect(result.supportingQuestions.map((q) => q.id)).toEqual(["q1", "q2"]);
    expect(result.transcriptRefs[0]).toMatchObject({ answerId: "a2", startMs: 90_000, quote: "Cut cost by 40%", questionId: "q2" });
  });

  it("falls back to the live digest and explains itself in a sentence", () => {
    const result = explainCompetency({
      key: "aws",
      label: "AWS",
      digest: { key: "aws", score_estimate: 6.4, confidence: 0.5, evidence_count: 3, sufficient_evidence: false },
      evidence: [pos, neg],
      questions: [q1, q2],
    });
    expect(result).toMatchObject({ score: 6.4, confidence: 0.5, evidenceCount: 3, sufficient: false });
    expect(explanationSentence(result)).toBe("Based on 1 supporting observation and 1 gap across 2 questions.");
    const empty = explainCompetency({ key: "go", label: "Go", evidence: [], questions: [] });
    expect(explanationSentence(empty)).toBe("No evidence collected for this competency.");
    expect(empty.score).toBeNull();
  });
});
