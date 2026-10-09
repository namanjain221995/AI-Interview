/**
 * GET /sessions/{id}/review-summary: what the candidate actually did (deterministic facts), evidence-backed
 * strengths and weaknesses, and AI text only when there is enough evidence to judge.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

import { asList, isRecord, numberOrNull, stringOrNull } from "../api/normalize";

export type EvidenceLevel = "none" | "thin" | "partial" | "sufficient";

export interface ReviewPoint {
  competencyKey: string;
  competency: string;
  text: string;
  quote: string | null;
  evidenceId: string | null;
  questionId: string | null;
  answerId: string | null;
  startMs: number | null;
}

export interface ReviewFacts {
  durationSeconds: number;
  plannedSeconds: number;
  endReason: string | null;
  endedEarly: boolean;
  questionsAsked: number;
  questionsAnswered: number;
  questionsSkipped: number;
  candidateWords: number;
  candidateSpeakingSeconds: number;
  minutesAnswered: number;
  silenceShare: number | null;
  speakingTimeEstimated: boolean;
}

export interface ReviewSummary {
  sessionId: string;
  kind: "interview" | "intake";
  completed: boolean;
  evidenceLevel: EvidenceLevel;
  headline: string;
  insufficient: boolean;
  facts: ReviewFacts;
  statements: string[];
  evidenceStatement: string | null;
  assessedCompetencies: number;
  totalCompetencies: number;
  overallScore: number | null;
  recommendation: string | null;
  strengths: ReviewPoint[];
  weaknesses: ReviewPoint[];
  aiAssessment: { text: string; source: string | null; generatedAt: string | null } | null;
  aiWithheldReason: string | null;
  note: string;
}

const LEVELS: readonly EvidenceLevel[] = ["none", "thin", "partial", "sufficient"];

function point(raw: unknown): ReviewPoint | null {
  if (!isRecord(raw)) return null;
  const text = stringOrNull(raw.text);
  const competencyKey = stringOrNull(raw.competency_key);
  if (!text || !competencyKey) return null;
  return {
    competencyKey,
    competency: stringOrNull(raw.competency) ?? competencyKey,
    text,
    quote: stringOrNull(raw.quote),
    evidenceId: stringOrNull(raw.evidence_id),
    questionId: stringOrNull(raw.question_id),
    answerId: stringOrNull(raw.answer_id),
    startMs: numberOrNull(raw.start_ms),
  };
}

export function normalizeReviewSummary(raw: unknown): ReviewSummary | null {
  if (!isRecord(raw) || !isRecord(raw.facts)) return null;
  const f = raw.facts;
  const level = LEVELS.includes(raw.evidence_level as EvidenceLevel) ? (raw.evidence_level as EvidenceLevel) : "none";
  const ai = isRecord(raw.ai_assessment) ? raw.ai_assessment : null;
  const aiText = ai ? stringOrNull(ai.text) : null;
  return {
    sessionId: stringOrNull(raw.session_id) ?? "",
    kind: raw.kind === "intake" ? "intake" : "interview",
    completed: raw.completed === true,
    evidenceLevel: level,
    headline: stringOrNull(raw.headline) ?? "",
    insufficient: raw.insufficient === true || level === "none" || level === "thin",
    facts: {
      durationSeconds: numberOrNull(f.duration_seconds) ?? 0,
      plannedSeconds: numberOrNull(f.planned_seconds) ?? 0,
      endReason: stringOrNull(f.end_reason),
      endedEarly: f.ended_early === true,
      questionsAsked: numberOrNull(f.questions_asked) ?? 0,
      questionsAnswered: numberOrNull(f.questions_answered) ?? 0,
      questionsSkipped: numberOrNull(f.questions_skipped) ?? 0,
      candidateWords: numberOrNull(f.candidate_words) ?? 0,
      candidateSpeakingSeconds: numberOrNull(f.candidate_speaking_seconds) ?? 0,
      minutesAnswered: numberOrNull(f.minutes_answered) ?? 0,
      silenceShare: numberOrNull(f.silence_share),
      speakingTimeEstimated: f.speaking_time_estimated === true,
    },
    statements: asList<unknown>(raw.statements).filter((s): s is string => typeof s === "string"),
    evidenceStatement: stringOrNull(raw.evidence_statement),
    assessedCompetencies: numberOrNull(raw.assessed_competencies) ?? 0,
    totalCompetencies: numberOrNull(raw.total_competencies) ?? 0,
    overallScore: numberOrNull(raw.overall_score),
    recommendation: stringOrNull(raw.recommendation),
    strengths: asList<unknown>(raw.strengths).map(point).filter((p): p is ReviewPoint => p !== null),
    weaknesses: asList<unknown>(raw.weaknesses).map(point).filter((p): p is ReviewPoint => p !== null),
    aiAssessment: ai && aiText ? { text: aiText, source: stringOrNull(ai.source), generatedAt: stringOrNull(ai.generated_at) } : null,
    aiWithheldReason: stringOrNull(raw.ai_withheld_reason),
    note: stringOrNull(raw.note) ?? "",
  };
}

export const reviewSummaryKey = (sessionId: string) => ["sessions", "detail", sessionId, "review-summary"] as const;

export function useReviewSummary(sessionId: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: reviewSummaryKey(sessionId ?? ""),
    queryFn: async ({ signal }) => normalizeReviewSummary(await api.get<unknown>(`/sessions/${sessionId}/review-summary`, { signal })),
    enabled: !!sessionId && enabled,
  });
}
