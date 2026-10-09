/**
 * Session detail data, shared by every tab through context. The container
 * fetches the parts most tabs need (scores, questions, evidence, coding,
 * integrity, reviews) once; heavy or tab-specific parts (transcript,
 * timeline) are fetched by their tabs.
 */

import type { UseQueryResult } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo } from "react";
import { useSearchParams } from "react-router";

import type { ClaimStatus, CodingSessionDetail, EvidenceRecord, Recommendation, ReviewDecision, SessionQuestion } from "@/lib/types";

import {
  isInProgress,
  isLive,
  isWatchable,
  useCompetencyCatalog,
  useSessionCoding,
  useSessionEvidence,
  useSessionIntegrity,
  useSessionQuestions,
  useSessionReviews,
  useSessionScores,
} from "../api/hooks";
import type { SessionDetailView, SessionIntegrityView, SessionScoresView } from "../api/types";
import type { TapeCodingSpan } from "../components/InterviewTape";
import { buildCompetencyIndex, type CompetencyIndex } from "../lib/competencies";
import { collectEvidence } from "../lib/evidence";

export const SESSION_TABS = ["live", "overview", "transcript", "recording", "questions", "competencies", "timeline", "coding", "integrity", "plan", "review"] as const;
export type SessionTab = (typeof SESSION_TABS)[number];

export interface SessionData {
  sessionId: string;
  session: SessionDetailView;
  inProgress: boolean;
  live: boolean;
  /** The live monitor applies (about to start, running or paused). */
  watchable: boolean;
  durationSeconds: number;
  plannedSeconds: number | null;
  scoresQuery: UseQueryResult<SessionScoresView>;
  questionsQuery: UseQueryResult<SessionQuestion[]>;
  evidenceQuery: UseQueryResult<EvidenceRecord[]>;
  codingQuery: UseQueryResult<CodingSessionDetail[]>;
  integrityQuery: UseQueryResult<SessionIntegrityView>;
  reviewsQuery: UseQueryResult<ReviewDecision[]>;
  scores: SessionScoresView | undefined;
  questions: SessionQuestion[];
  evidence: EvidenceRecord[];
  coding: CodingSessionDetail[];
  integrity: SessionIntegrityView | undefined;
  reviews: ReviewDecision[];
  index: CompetencyIndex;
  questionById: Map<string, SessionQuestion>;
  codingSpans: TapeCodingSpan[];
  overallScore: number | null;
  confidence: number | null;
  recommendation: Recommendation | null;
  /** Why the AI suggestion is what it is (scores API, else the session detail). */
  rationale: string | null;
  claims: Array<{ id: string; claim: string; status: ClaimStatus }>;
}

export interface SessionContextValue extends SessionData {
  nav: SessionNav;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export const SessionDataProvider = SessionContext.Provider;

export function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSessionContext must be used inside the session detail view");
  return value;
}

function secondsBetween(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null;
  const a = Date.parse(from);
  const b = Date.parse(to);
  return Number.isFinite(a) && Number.isFinite(b) ? (b - a) / 1000 : null;
}

export function useSessionData(session: SessionDetailView): SessionData {
  const sessionId = session.id;
  const inProgress = isInProgress(session.status);
  const live = isLive(session.status);
  const scoresQuery = useSessionScores(sessionId, { live: inProgress });
  const questionsQuery = useSessionQuestions(sessionId, { live: inProgress });
  const evidenceQuery = useSessionEvidence(sessionId, { live: inProgress });
  const codingQuery = useSessionCoding(sessionId, { live: inProgress });
  const integrityQuery = useSessionIntegrity(sessionId, { live: inProgress });
  const reviewsQuery = useSessionReviews(sessionId);
  const catalog = useCompetencyCatalog();

  const questions = useMemo(() => questionsQuery.data ?? [], [questionsQuery.data]);
  const evidence = useMemo(() => collectEvidence(evidenceQuery.data, questionsQuery.data), [evidenceQuery.data, questionsQuery.data]);
  const coding = useMemo(() => codingQuery.data ?? [], [codingQuery.data]);
  const reviews = useMemo(() => reviewsQuery.data ?? [], [reviewsQuery.data]);
  const scores = scoresQuery.data;

  const index = useMemo(
    () =>
      buildCompetencyIndex({
        roundConfig: session.round_config,
        scores: scores?.competencies,
        digest: session.state_digest?.competencies,
        evidence,
        catalog: catalog.data,
      }),
    [session.round_config, session.state_digest?.competencies, scores?.competencies, evidence, catalog.data],
  );

  const questionById = useMemo(() => new Map(questions.map((q) => [q.id, q])), [questions]);

  const plannedSeconds = session.durations?.scheduled_seconds ?? (session.round_config?.duration_minutes ? session.round_config.duration_minutes * 60 : null);
  const elapsed = session.durations?.elapsed_seconds || session.elapsed_seconds || session.state_digest?.elapsed_s || 0;
  const durationSeconds = Math.max(elapsed, inProgress ? 0 : (secondsBetween(session.started_at, session.ended_at) ?? 0), 60);

  const codingSpans = useMemo<TapeCodingSpan[]>(
    () =>
      coding
        .map((c) => {
          const start = secondsBetween(session.started_at, c.started_at) ?? questionById.get(c.question_id ?? "")?.elapsed_seconds ?? null;
          if (start === null) return null;
          const end = secondsBetween(session.started_at, c.ended_at) ?? start + Math.min(c.time_limit_seconds || 0, Math.max(0, durationSeconds - start));
          return { id: c.id, start: Math.max(0, start), end: Math.max(start + 1, end), label: "Coding" };
        })
        .filter((s): s is TapeCodingSpan => s !== null),
    [coding, session.started_at, questionById, durationSeconds],
  );

  return {
    sessionId,
    session,
    inProgress,
    live,
    watchable: isWatchable(session.status),
    durationSeconds,
    plannedSeconds,
    scoresQuery,
    questionsQuery,
    evidenceQuery,
    codingQuery,
    integrityQuery,
    reviewsQuery,
    scores,
    questions,
    evidence,
    coding,
    integrity: integrityQuery.data,
    reviews,
    index,
    questionById,
    codingSpans,
    overallScore: scores?.overall?.score ?? session.overall_score ?? null,
    confidence: scores?.overall?.confidence ?? session.confidence ?? null,
    recommendation: scores?.recommendation ?? session.recommendation ?? null,
    rationale: scores?.rationale ?? session.recommendation_rationale ?? null,
    claims: session.claims?.length ? session.claims : (session.state_digest?.claims ?? []),
  };
}

// ---------------------------------------------------------------- navigation

export interface SessionTarget {
  tab: SessionTab;
  /** Question id to focus (questions tab). */
  question?: string | null;
  /** Competency key to select (competencies tab). */
  competency?: string | null;
  /** Transcript moment in ms (transcript tab). */
  t?: number | null;
  /** Answer id to focus (transcript tab). */
  answer?: string | null;
}

export interface SessionNav {
  tab: SessionTab;
  question: string | null;
  competency: string | null;
  t: number | null;
  answer: string | null;
  go: (target: SessionTarget, options?: { replace?: boolean }) => void;
}

export function useSessionNav(defaultTab: SessionTab): SessionNav {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: SessionTab = raw && (SESSION_TABS as readonly string[]).includes(raw) ? (raw as SessionTab) : defaultTab;

  const go = useCallback(
    (target: SessionTarget, { replace = false }: { replace?: boolean } = {}) => {
      const next = new URLSearchParams();
      next.set("tab", target.tab);
      if (target.question) next.set("q", target.question);
      if (target.competency) next.set("c", target.competency);
      if (typeof target.t === "number") next.set("t", String(Math.max(0, Math.round(target.t))));
      if (target.answer) next.set("a", target.answer);
      setParams(next, { replace, preventScrollReset: true });
    },
    [setParams],
  );

  return {
    tab,
    question: params.get("q"),
    competency: params.get("c"),
    t: params.get("t") !== null ? Number(params.get("t")) : null,
    answer: params.get("a"),
    go,
  };
}
