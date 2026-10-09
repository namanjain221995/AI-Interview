import type { EvidenceRecord, ScoreRecord, SessionAnswer, SessionQuestion } from "@/lib/types";

import type { TranscriptEntry } from "../../api/types";

let counter = 0;
const nextId = (prefix: string) => `${prefix}-${++counter}`;

export function evidence(overrides: Partial<EvidenceRecord> = {}): EvidenceRecord {
  return {
    id: nextId("ev"),
    session_id: "s1",
    competency_key: "aws",
    question_id: null,
    answer_id: null,
    evaluation_id: null,
    coding_session_id: null,
    source: "answer",
    signal: "positive",
    dimension: "depth",
    evidence: "Explained the trade-off",
    strength: 0.6,
    confidence: 0.8,
    difficulty: "medium",
    elapsed_seconds: 60,
    transcript_ref: {},
    created_at: "2026-10-06T10:00:00Z",
    ...overrides,
  };
}

export function answer(overrides: Partial<SessionAnswer> = {}): SessionAnswer {
  return {
    id: nextId("ans"),
    question_id: null,
    sequence: 1,
    transcript_text: "An answer",
    typed: false,
    started_ms: 0,
    ended_ms: 10_000,
    duration_ms: 10_000,
    word_count: 30,
    stt_confidence: 0.9,
    interrupted_ai: false,
    status: "evaluated",
    evaluation: null,
    evidence: [],
    ...overrides,
  };
}

export function question(overrides: Partial<SessionQuestion> = {}): SessionQuestion {
  return {
    id: nextId("q"),
    session_id: "s1",
    sequence: 1,
    parent_question_id: null,
    bank_question_id: null,
    coding_question_id: null,
    kind: "technical",
    action: "ask_question",
    source: "generated",
    segment: "technical",
    topic: null,
    competency_keys: ["aws"],
    difficulty: "medium",
    text: "Tell me about it",
    spoken_text: null,
    question_focus: null,
    reason: null,
    expected_evidence: [],
    claim_id: null,
    interrupted: false,
    asked_at: "2026-10-06T10:00:00Z",
    elapsed_seconds: 0,
    answers: [],
    ...overrides,
  };
}

export function score(overrides: Partial<ScoreRecord> = {}): ScoreRecord {
  return {
    id: nextId("score"),
    session_id: "s1",
    category: "competency",
    key: "aws",
    label: "AWS",
    score: 7.2,
    confidence: 0.7,
    weight: 1,
    evidence_count: 0,
    positive_count: 0,
    negative_count: 0,
    sufficient_evidence: true,
    unanswered_areas: [],
    supporting_question_ids: [],
    positive_evidence_ids: [],
    negative_evidence_ids: [],
    transcript_refs: [],
    details: {},
    is_final: true,
    created_at: "2026-10-06T10:30:00Z",
    ...overrides,
  };
}

export function segment(overrides: Partial<TranscriptEntry> = {}): TranscriptEntry {
  return {
    id: nextId("seg"),
    session_id: "s1",
    seq: 1,
    speaker: "candidate",
    kind: "final",
    text: "Hello",
    start_ms: 0,
    end_ms: 1000,
    confidence: 0.9,
    question_id: null,
    answer_id: null,
    interrupted: false,
    created_at: "2026-10-06T10:00:00Z",
    ...overrides,
  };
}
