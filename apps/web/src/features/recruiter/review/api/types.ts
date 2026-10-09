/**
 * Recruiter review DTO refinements.
 *
 * `@/lib/types` mirrors the platform contract. The session/report/analytics
 * APIs are allowed to return a little more (signed evidence URLs, timeline
 * windows, analytics breakdowns); those optional extras are typed here and
 * every consumer treats them as optional, so the UI works with the minimal
 * contract shape and lights up richer sections when the backend sends them.
 */

import type {
  AnalyticsOverview,
  CandidateReportMode,
  ClaimStatus,
  CodeLanguage,
  CodingSessionStatus,
  CompetencyDigest,
  ContradictionFinding,
  Difficulty,
  EvidenceRecord,
  IntegrityEvent,
  IntegrityEventType,
  IntegrityPolicy,
  IntegritySeverity,
  JobRef,
  PersonRef,
  Recommendation,
  RecruiterReportNarrative,
  ReportStatus,
  ReviewDecisionValue,
  RoundRef,
  RoundType,
  ScoreRecord,
  ScoreSnapshot,
  SessionDetail,
  SessionEndReason,
  SessionScores,
  SessionStatus,
  TimelineEvent,
  TranscriptSegment,
  TurnState,
} from "@/lib/types";

// ------------------------------------------------------------------ sessions

/** GET /sessions/{id}: SessionDetail plus the fields the sessions API adds. */
export interface SessionDetailView extends SessionDetail {
  live?: boolean;
  durations?: {
    elapsed_seconds: number;
    wall_seconds: number | null;
    paused_seconds: number | null;
    scheduled_seconds: number;
    max_seconds: number;
    remaining_seconds: number | null;
  };
  recommendation_rationale?: string | null;
  human_decision_note?: string;
  scores_final?: boolean;
  scores_computed_at?: string | null;
  rubric?: Array<{ key: string; label: string | null; weight: number | null; score: number; confidence: number }>;
  reports?: Partial<Record<"recruiter" | "candidate", { id: string; version: number; status: ReportStatus; generated_at: string | null; released_to_candidate: boolean }>>;
  candidate_report_mode?: CandidateReportMode | null;
  claims?: Array<{ id: string; claim: string; status: ClaimStatus }>;
  contradictions?: Array<{ id: string | null; description: string | null; severity: string | null; status: string | null }>;
  review_count?: number;
}

/** The "why this score" block the scores API attaches to each competency. */
export interface ScoreWhy {
  positive_evidence: EvidenceRecord[];
  negative_evidence: EvidenceRecord[];
  unanswered_areas: string[];
  supporting_questions: Array<{ id: string; sequence: number; text: string; focus: string | null; difficulty: Difficulty | null }>;
  transcript_refs: Array<Record<string, unknown>>;
  calculation: {
    score?: number;
    bayesian_mean?: number;
    evidence_balance?: number;
    positive_weight?: number;
    negative_weight?: number;
    confidence?: number;
    total_weight?: number;
    strong_evidence?: number;
    score_std?: number;
    consistency_penalty?: number;
    difficulty_levels?: number;
    evidence_count?: number;
  } | null;
  observations: Array<{
    answer_id?: string | null;
    question_id?: string | null;
    coding_session_id?: string | null;
    difficulty?: string;
    score?: number;
    effective_score?: number;
    assistance_factor?: number;
    evaluation_confidence?: number;
    weight?: number;
    elapsed_seconds?: number;
  }>;
}

export type CompetencyScoreRecord = ScoreRecord & { why?: ScoreWhy };

/** GET /sessions/{id}/scores, normalised. */
export interface SessionScoresView extends SessionScores {
  competencies: CompetencyScoreRecord[];
  rationale?: string | null;
  factors?: Record<string, unknown>;
  source?: string | null;
  isFinal?: boolean;
  computedAt?: string | null;
}

/** Extra session list filters (sent as query params; also applied client-side). */
export interface SessionFilters {
  status?: SessionStatus[];
  job_id?: string;
  round_id?: string;
  /** Round types (sent as repeated `round_type`). */
  round_type?: RoundType[];
  recommendation?: Recommendation;
  /** ISO date (YYYY-MM-DD), inclusive. */
  from?: string;
  /** ISO date (YYYY-MM-DD), inclusive. */
  to?: string;
  q?: string;
  /** Completed sessions without a human decision. */
  needs_review?: boolean;
}

/** A transcript segment; the transcript API may attach the answer's audio media id. */
export interface TranscriptEntry extends TranscriptSegment {
  audio_media_id?: string | null;
}

// ------------------------------------------------------------------ timeline

export interface TimelineWindowValueDTO {
  competency_key?: string;
  key?: string;
  score: number;
  confidence?: number | null;
  evidence_count?: number | null;
}

/** A server-bucketed window ("Minute 0-10: AWS 7.2, Kubernetes 6.5"). */
export interface TimelineWindowDTO {
  index?: number;
  start_minute?: number;
  end_minute?: number;
  start_seconds?: number;
  end_seconds?: number;
  label?: string;
  competencies?: Record<string, number | TimelineWindowValueDTO> | TimelineWindowValueDTO[];
  scores?: Record<string, number>;
}

export interface SessionTimelineView {
  snapshots: ScoreSnapshot[];
  events: TimelineEvent[];
  windows: TimelineWindowDTO[];
  windowMinutes: number | null;
  durationSeconds: number | null;
}

// ----------------------------------------------------------------- integrity

/** Integrity event as the review screens consume it (signed evidence URL when the API includes it). */
export interface IntegrityEventView extends IntegrityEvent {
  /** Plain-language name from the API. */
  label?: string;
  evidence_url?: string | null;
  evidence_url_expires_at?: string | null;
  /** False when the evidence frame was deleted (retention) or is unavailable. */
  evidence_available?: boolean | null;
  session?: { id: string; status?: SessionStatus; started_at?: string | null } | null;
  round?: RoundRef | null;
  reviewed_by?: PersonRef | null;
}

export interface SessionIntegrityView {
  events: IntegrityEventView[];
  countsByType: Partial<Record<IntegrityEventType, number>>;
  countsBySeverity: Partial<Record<IntegritySeverity, number>>;
  policy: Partial<IntegrityPolicy> | null;
}

export interface IntegrityQueueFilters {
  status?: IntegrityEventView["review_status"] | IntegrityEventView["review_status"][] | "all";
  type?: IntegrityEventType;
  severity?: IntegritySeverity;
  source?: IntegrityEventView["source"];
  session_id?: string;
  /** Only observations a reviewer should look at (warned, flagged, paused, terminated). */
  actionable?: boolean;
  /** ISO date (YYYY-MM-DD), inclusive. */
  from?: string;
  /** ISO date (YYYY-MM-DD), inclusive. */
  to?: string;
}

// ----------------------------------------------------------------- analytics

export interface ScoreBucketDTO {
  min?: number;
  max?: number;
  bucket?: string;
  label?: string;
  count: number;
}

export interface ScoreGroupDTO {
  key?: string;
  id?: string;
  job_id?: string;
  round_id?: string;
  round_type?: string;
  label?: string;
  title?: string;
  name?: string;
  count?: number;
  sessions?: number;
  completed?: number;
  started?: number;
  average?: number | null;
  average_score?: number | null;
  mean?: number | null;
  median?: number | null;
  min?: number | null;
  max?: number | null;
  p25?: number | null;
  p75?: number | null;
  scores?: number[];
  completion_rate?: number | null;
  average_duration_seconds?: number | null;
  median_duration_seconds?: number | null;
  planned_duration_seconds?: number | null;
}

export interface HintUsageDTO {
  coding_sessions?: number;
  sessions_with_hints?: number;
  average_hints?: number | null;
  total_hints?: number;
  /** "0" | "1" | "2" | "3" -> sessions */
  distribution?: Record<string, number>;
  by_level?: Record<string, number>;
}

/** /analytics/overview with the optional breakdowns the analytics page understands. */
export interface AnalyticsOverviewView extends AnalyticsOverview {
  period?: { days: number; since: string; until: string; job_id?: string | null; round_id?: string | null };
  active_interviews?: number;
  average_confidence?: number | null;
  integrity_events?: number;
  review_decision_distribution?: Record<string, number>;
  /** Share of human decisions that overrode the AI suggestion. */
  ai_override_rate?: number | null;
  sessions_started?: number;
  completed_this_week?: number;
  pending_reviews?: number;
  average_duration_seconds?: number | null;
  median_duration_seconds?: number | null;
  score_distribution?: ScoreBucketDTO[] | Record<string, number>;
  scores_by_job?: ScoreGroupDTO[];
  scores_by_round?: ScoreGroupDTO[];
  completion_by_round?: ScoreGroupDTO[];
  durations_by_round?: ScoreGroupDTO[];
  integrity_events_total?: number;
  integrity_events_per_session?: number | null;
  integrity_by_type?: Record<string, number> | Array<{ type: string; count: number; rate?: number | null }>;
  hint_usage?: HintUsageDTO;
}

// --------------------------------------------------------------------- live

export type LiveConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "closed" | "unsupported";

export interface LiveTranscriptLine {
  id: string;
  speaker: "candidate" | "interviewer" | "system";
  text: string;
  startMs: number | null;
  partial: boolean;
  interrupted: boolean;
}

export interface LiveActivity {
  id: string;
  type: string;
  at: number;
  title: string;
  detail?: string;
  tone: "neutral" | "accent" | "success" | "warning" | "danger" | "signal";
}

export interface LiveState {
  status: SessionStatus | null;
  turnState: TurnState | null;
  elapsedSeconds: number | null;
  remainingSeconds: number | null;
  /** Wall-clock ms when elapsed/remaining were last reported (lets the clock tick between digests). */
  clockAt: number | null;
  questionCount: number | null;
  currentQuestion: { id: string | null; text: string | null } | null;
  currentSegment: string | null;
  competencies: CompetencyDigest[];
  /** Competency key -> previous score estimate, for "moved by" hints. */
  previousScores: Record<string, number>;
  claims: Array<{ id: string; claim: string; status: ClaimStatus }>;
  transcript: LiveTranscriptLine[];
  partials: Partial<Record<"candidate" | "interviewer", LiveTranscriptLine>>;
  integrity: IntegrityEventView[];
  activity: LiveActivity[];
  /** Increments whenever an event means stored data changed (evaluation, integrity, report). */
  dataVersion: number;
  lastEventAt: number | null;
  ended: boolean;
}

export interface LiveEvent {
  type: string;
  payload: Record<string, unknown>;
  /** Wall clock at receipt (ms). */
  receivedAt: number;
}

// ------------------------------------------------------------------- report

export interface ReportEvidenceView {
  id: string;
  competency_key: string;
  signal: "positive" | "negative" | "neutral";
  dimension?: string;
  evidence: string;
  quote?: string | null;
  strength?: number;
  confidence?: number;
  question_id?: string | null;
}

export interface ReportCompetencyView {
  key: string;
  label: string;
  score: number | null;
  confidence: number | null;
  evidenceCount: number | null;
  sufficient: boolean | null;
  weight: number | null;
  summary: string | null;
  strengths: string[];
  weaknesses: string[];
  unansweredAreas: string[];
  evidence: ReportEvidenceView[];
}

export interface ReportQuestionView {
  id: string;
  sequence: number;
  text: string;
  competencyKeys: string[];
  difficulty: string | null;
  parentId: string | null;
  answer: string | null;
  quality: string | null;
  summary: string | null;
  confidence: number | null;
  dimensions: Array<{ key: string; value: number }>;
  evidence: ReportEvidenceView[];
  isFallback: boolean;
}

export interface ReportCodingView {
  id: string;
  title: string;
  language: CodeLanguage | string | null;
  status: CodingSessionStatus | string | null;
  testsPassed: number | null;
  testsTotal: number | null;
  hintsUsed: number;
  hints: Array<{ level: number; content: string; elapsedSeconds: number | null }>;
  scoreEstimate: number | null;
  assistanceFactor: number | null;
  timeComplexity: string | null;
  spaceComplexity: string | null;
  correctnessSummary: string | null;
  hintsImpact: string | null;
  strengths: string[];
  weaknesses: string[];
  finalCode: string | null;
}

export interface ReportClaimView {
  id: string;
  claim: string;
  status: ClaimStatus | string;
  note: string | null;
}

export interface ReportIntegrityView {
  total: number;
  unreviewed: number;
  byType: Array<{ type: string; count: number }>;
  bySeverity: Partial<Record<IntegritySeverity, number>>;
  reviewed: Partial<Record<string, number>>;
}

/** Everything the printable recruiter report renders, normalised from report content + session data. */
export interface RecruiterReportView {
  candidate: PersonRef | null;
  job: JobRef | null;
  round: RoundRef | null;
  startedAt: string | null;
  endedAt: string | null;
  durationSeconds: number | null;
  endReason: SessionEndReason | null;
  overallScore: number | null;
  confidence: number | null;
  recommendation: Recommendation | null;
  recommendationRationale: string | null;
  executiveSummary: string | null;
  narrative: Partial<RecruiterReportNarrative> | null;
  rubric: Array<{ key: string; label: string; weight: number | null; score: number | null; confidence: number | null }>;
  competencies: ReportCompetencyView[];
  questions: ReportQuestionView[];
  excerpts: Array<{ id: string; speaker: string; text: string; startMs: number | null; questionId: string | null }>;
  coding: ReportCodingView[];
  codingSummary: string | null;
  claims: ReportClaimView[];
  claimsSummary: string | null;
  contradictions: ContradictionFinding[];
  contradictionsSummary: string | null;
  integrity: ReportIntegrityView | null;
  followUps: string[];
  risks: string[];
  humanDecision: { decision: ReviewDecisionValue; reviewer: string | null; at: string | null; overridesAi: boolean } | null;
}

// ------------------------------------------------- analytics breakdown APIs

export interface ScoreDistributionGroupDTO {
  key: string;
  label: string;
  count: number;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
  /** Counts per 10-point bucket: 0-9, 10-19, ..., 90-100. */
  buckets: number[];
}

/** GET /analytics/scores */
export interface ScoreDistributionsDTO {
  bucket_labels: string[];
  overall: ScoreDistributionGroupDTO;
  by_job: ScoreDistributionGroupDTO[];
  by_round: ScoreDistributionGroupDTO[];
}

export interface CompletionGroupDTO {
  key: string;
  label: string;
  started: number;
  completed: number;
  completion_rate: number | null;
  average_duration_seconds: number | null;
  median_duration_seconds: number | null;
  p90_duration_seconds: number | null;
}

/** GET /analytics/completion */
export interface CompletionAnalyticsDTO {
  overall: CompletionGroupDTO;
  by_job: CompletionGroupDTO[];
  by_round: CompletionGroupDTO[];
  end_reasons: Record<string, number>;
  status_counts: Record<string, number>;
}

/** GET /analytics/integrity */
export interface IntegrityAnalyticsDTO {
  note?: string;
  sessions: number;
  sessions_with_events: number;
  sessions_with_flags: number;
  flag_rate: number | null;
  events_total: number;
  events_per_session: number | null;
  by_type: Record<string, number>;
  by_severity: Record<string, number>;
  by_source: Record<string, number>;
  review_status: Record<string, number>;
}

/** GET /analytics/hints */
export interface HintUsageAnalyticsDTO {
  coding_sessions: number;
  sessions_with_hints: number;
  hint_rate: number | null;
  average_hints: number | null;
  distribution: Record<string, number>;
  by_level: Record<string, number>;
  by_problem: Array<Record<string, unknown>>;
}

export interface ModelUsageItemView {
  task: string;
  endpoint: string;
  calls: number;
  errors: number;
  error_rate?: number | null;
  fallbacks?: number;
  repaired?: number;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_ms?: number | null;
  avg_ttft_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
}
