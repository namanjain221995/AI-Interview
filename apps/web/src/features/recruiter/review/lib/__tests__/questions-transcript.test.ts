import { describe, expect, it } from "vitest";

import { buildQuestionThreads, evaluationDimensions, flattenThreads, followUpCount, primaryAnswer, questionSpans } from "../questions";
import { buildTranscriptTurns, highlightParts, interleaveQuestions, searchTurns, turnAt } from "../transcript";
import { answer, question, segment } from "./factories";

describe("question threads", () => {
  it("nests follow-ups under their parent in sequence order", () => {
    const q1 = question({ id: "q1", sequence: 1 });
    const q2 = question({ id: "q2", sequence: 2, parent_question_id: "q1" });
    const q3 = question({ id: "q3", sequence: 3, parent_question_id: "q2" });
    const q4 = question({ id: "q4", sequence: 4 });
    const q5 = question({ id: "q5", sequence: 5, parent_question_id: "q1" });
    const threads = buildQuestionThreads([q4, q3, q5, q1, q2]);
    expect(threads.map((t) => t.question.id)).toEqual(["q1", "q4"]);
    expect(threads[0]!.children.map((t) => t.question.id)).toEqual(["q2", "q5"]);
    expect(threads[0]!.children[0]!.children[0]!).toMatchObject({ depth: 2 });
    expect(followUpCount(threads[0]!)).toBe(3);
    expect(flattenThreads(threads).map((f) => `${f.question.id}:${f.depth}:${f.parentId ?? "-"}`)).toEqual(["q1:0:-", "q2:1:q1", "q3:2:q2", "q5:1:q1", "q4:0:-"]);
  });

  it("promotes orphans and breaks cycles instead of dropping questions", () => {
    const orphan = question({ id: "o", sequence: 2, parent_question_id: "gone" });
    const a = question({ id: "a", sequence: 3, parent_question_id: "b" });
    const b = question({ id: "b", sequence: 4, parent_question_id: "a" });
    const flat = flattenThreads(buildQuestionThreads([orphan, a, b]));
    expect(flat.map((f) => f.question.id).sort()).toEqual(["a", "b", "o"]);
  });

  it("picks the evaluated answer and lists applicable dimensions", () => {
    const evaluated = answer({ sequence: 2, word_count: 5, evaluation: { correctness: 7, depth: null, evaluation: { scores: { specificity: 6 } } } as never });
    const longer = answer({ sequence: 1, word_count: 90 });
    expect(primaryAnswer(question({ answers: [longer, evaluated] }))).toBe(evaluated);
    expect(primaryAnswer(question({ answers: [answer({ word_count: 3 }), longer] }))).toBe(longer);
    expect(primaryAnswer(question({ answers: [] }))).toBeNull();
    expect(evaluationDimensions(evaluated.evaluation).map((d) => [d.key, d.value])).toEqual([
      ["correctness", 7],
      ["specificity", 6],
    ]);
  });

  it("computes time spans that end at the next question or the interview end", () => {
    const spans = questionSpans(
      [question({ id: "b", sequence: 2, elapsed_seconds: 300 }), question({ id: "a", sequence: 1, elapsed_seconds: 20 })],
      900,
    );
    expect(spans.map((s) => [s.question.id, s.start, s.end])).toEqual([
      ["a", 20, 300],
      ["b", 300, 900],
    ]);
  });
});

describe("transcript turns", () => {
  const segments = [
    segment({ id: "s1", seq: 1, speaker: "interviewer", question_id: "q1", text: "How do you scale?", start_ms: 0 }),
    segment({ id: "s2", seq: 2, speaker: "candidate", answer_id: "a1", text: "We used", start_ms: 4000, confidence: 0.9 }),
    segment({ id: "s3", seq: 3, speaker: "candidate", answer_id: "a1", text: "autoscaling groups.", start_ms: 6000, end_ms: 9000, confidence: 0.7 }),
    segment({ id: "p", seq: 4, speaker: "candidate", answer_id: "a1", kind: "partial", text: "partial" }),
    segment({ id: "s4", seq: 5, speaker: "interviewer", question_id: "q2", text: "And the database?", start_ms: 12000, interrupted: true }),
    segment({ id: "s5", seq: 6, speaker: "interviewer", question_id: "q2", text: "Sorry, go on.", start_ms: 15000 }),
  ];

  it("merges consecutive segments of one answer and drops partials", () => {
    const turns = buildTranscriptTurns(segments);
    expect(turns.map((t) => t.id)).toEqual(["s1", "s2", "s4", "s5"]);
    expect(turns[1]).toMatchObject({ text: "We used autoscaling groups.", endMs: 9000, minConfidence: 0.7 });
    expect(turns[2]!.interrupted).toBe(true);
    expect(buildTranscriptTurns(segments, { includePartials: true }).find((t) => t.text.includes("partial"))).toBeDefined();
  });

  it("interleaves question markers by id, then by time, and appends unasked ones", () => {
    const turns = buildTranscriptTurns(segments);
    const blocks = interleaveQuestions(turns, [
      question({ id: "q1", sequence: 1, elapsed_seconds: 0 }),
      question({ id: "q2", sequence: 2, elapsed_seconds: 12 }),
      question({ id: "q3", sequence: 3, elapsed_seconds: 5 }),
      question({ id: "q4", sequence: 4, elapsed_seconds: 999 }),
    ]);
    // q3 has no transcript reference: it is placed by time (asked at 5 s, after the 4 s answer started).
    expect(blocks.map((b) => (b.kind === "question" ? `Q:${b.question.id}` : b.id))).toEqual(["Q:q1", "s1", "s2", "Q:q3", "Q:q2", "s4", "s5", "Q:q4"]);
  });

  it("searches case- and accent-insensitively and highlights matches", () => {
    const turns = buildTranscriptTurns([segment({ id: "x", text: "Résumé review, then the RESUME again" })]);
    expect(searchTurns(turns, "resume")).toEqual({ turnIds: ["x"], count: 2 });
    expect(searchTurns(turns, "  ")).toEqual({ turnIds: [], count: 0 });
    expect(highlightParts("Scale the scaler", "scale")).toEqual([
      { text: "Scale", match: true },
      { text: " the ", match: false },
      { text: "scale", match: true },
      { text: "r", match: false },
    ]);
  });

  it("finds the turn at a moment", () => {
    const turns = buildTranscriptTurns(segments);
    expect(turnAt(turns, 7000)!.id).toBe("s2");
    expect(turnAt(turns, 100_000)!.id).toBe("s5");
  });
});
