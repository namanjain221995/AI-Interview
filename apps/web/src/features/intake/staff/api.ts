/**
 * Intake Interview staff API (design 10.1 admin, 10.2 review). Every response passes
 * through a tolerant normaliser: missing optional parts get safe defaults, decimals stay
 * strings, unknown enum values fall back, so a partial DTO never crashes a page.
 *
 * When the feature flag is off the API answers 404 `intake_disabled` (design 12);
 * `isIntakeDisabled()` lets pages show one empty state for it.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, isApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Page } from "@/lib/types";

import { listParams, statsParams } from "./lib/buckets";
import { normalizeDecimal } from "./lib/decimals";
import {
  DECISION_BANDS,
  DECISION_OUTCOMES,
  INTAKE_BUCKETS,
  STAGE_KEYS,
  TARGET_ROLES,
  type ActivityKind,
  type BulletCoverage,
  type CompetencyScore,
  type CountedBucket,
  type CoverageStatus,
  type DecisionBand,
  type DecisionOutcome,
  type DrillASummary,
  type DrillBSummary,
  type EvaluationSummary,
  type EvidenceRef,
  type EvidenceSupport,
  type IntakeActivity,
  type IntakeAnalytics,
  type IntakeAnalyticsQuery,
  type IntakeBatchCreate,
  type IntakeBatchResponse,
  type IntakeBucket,
  type IntakeConfig,
  type IntakeCreate,
  type IntakeCreateResponse,
  type IntakeDecision,
  type IntakeDecisionIn,
  type IntakeDefaults,
  type IntakeDetail,
  type IntakeEligibilityView,
  type IntakeEvaluation,
  type IntakeInviteSettings,
  type IntakeListFilters,
  type IntakeListQuery,
  type IntakeOnboarding,
  type IntakeOrgSettings,
  type IntakeOrgSettingsIn,
  type IntakeReportContent,
  type IntakeStage,
  type IntakeStats,
  type IntakeSummary,
  type IntakeTimeline,
  type IntakeUpdate,
  type ReadingEvent,
  type ReadingMetricsView,
  type ResumeReviewDoc,
  type ResumeReviewRecord,
  type ReviewItem,
  type StageItemResult,
  type StageKey,
  type TargetRole,
  type TrainingPlan,
  type WorkAuthorizationPolicy,
} from "./types";

// ------------------------------------------------------------------ keys

export const intakeKeys = {
  all: ["intake"] as const,
  config: ["intake", "config"] as const,
  settings: ["intake", "settings"] as const,
  lists: ["intake", "list"] as const,
  list: (params: object) => ["intake", "list", params] as const,
  stats: (params: object) => ["intake", "stats", params] as const,
  detail: (id: string) => ["intake", "detail", id] as const,
  timeline: (id: string) => ["intake", "detail", id, "timeline"] as const,
  evaluation: (id: string, version: number | null) => ["intake", "detail", id, "evaluation", version ?? "latest"] as const,
  decisions: (id: string) => ["intake", "detail", id, "decisions"] as const,
  analytics: (params: object) => ["intake", "analytics", params] as const,
  bySession: (sessionId: string) => ["intake", "by-session", sessionId] as const,
};

// ------------------------------------------------------------- errors

/** 404 `intake_disabled`: the workspace has intake interviews turned off. */
export function isIntakeDisabled(error: unknown): boolean {
  return isApiError(error) && (error.code === "intake_disabled" || (error.status === 404 && error.details?.reason === "intake_disabled"));
}

/** 404 on a list/config route that does not exist on this server yet. */
export function isIntakeUnavailable(error: unknown): boolean {
  return isApiError(error) && error.status === 404 && error.code !== "intake_disabled";
}

// ---------------------------------------------------------- primitives

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {});
const isObj = (v: unknown): v is Rec => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const arr = <T = unknown>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : typeof v === "number" ? String(v) : fallback);
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : typeof v === "number" ? String(v) : null);
const num = (v: unknown, fallback = 0): number => {
  const n = typeof v === "string" && v.trim() !== "" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
};
const numOrNull = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "string" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
};
const bool = (v: unknown, fallback = false): boolean => (typeof v === "boolean" ? v : fallback);
const boolOrNull = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);
const strings = (v: unknown): string[] => arr(v).filter((x): x is string => typeof x === "string" && x.trim() !== "");
const dec = (v: unknown): string | null => (typeof v === "string" || typeof v === "number" ? normalizeDecimal(v) : null);
function oneOf<T extends string>(v: unknown, allowed: readonly T[]): T | null {
  return typeof v === "string" && (allowed as readonly string[]).includes(v) ? (v as T) : null;
}
const person = (v: unknown) => {
  const r = rec(v);
  return typeof r.id === "string" ? { id: r.id, full_name: str(r.full_name, str(r.email)), ...(typeof r.email === "string" ? { email: r.email } : {}) } : null;
};

function page<T>(raw: unknown, map: (item: unknown) => T): Page<T> {
  const r = rec(raw);
  const items = Array.isArray(raw) ? raw : arr(r.items);
  return { items: items.map(map), total: num(r.total, items.length) };
}

// ---------------------------------------------------------- settings

export const DEFAULT_WORK_AUTHORIZATION: WorkAuthorizationPolicy = { enabled: false, country: null, ask_sponsorship: true, variant: "default" };
export const DEFAULT_INTAKE_DEFAULTS: IntakeDefaults = { duration_minutes: 45, max_reconnects: 10, expires_in_hours: 168, presence: "orb", resume_required: true };

export function normalizeWorkAuthorization(raw: unknown): WorkAuthorizationPolicy {
  const r = rec(raw);
  return { enabled: bool(r.enabled), country: strOrNull(r.country), ask_sponsorship: bool(r.ask_sponsorship, true), variant: "default" };
}

export function normalizeDefaults(raw: unknown): IntakeDefaults {
  const r = rec(raw);
  return {
    duration_minutes: num(r.duration_minutes, DEFAULT_INTAKE_DEFAULTS.duration_minutes),
    max_reconnects: num(r.max_reconnects, DEFAULT_INTAKE_DEFAULTS.max_reconnects),
    expires_in_hours: num(r.expires_in_hours, DEFAULT_INTAKE_DEFAULTS.expires_in_hours),
    presence: r.presence === "none" ? "none" : "orb",
    resume_required: bool(r.resume_required, true),
  };
}

export function normalizeInviteSettings(raw: unknown): IntakeInviteSettings {
  const r = rec(raw);
  return {
    duration_minutes: num(r.duration_minutes, 45),
    presence: r.presence === "none" ? "none" : "orb",
    max_reconnects: num(r.max_reconnects, 10),
    expires_in_hours: num(r.expires_in_hours, 168),
    scheduled_for: strOrNull(r.scheduled_for),
    require_access_code: bool(r.require_access_code),
    resume_required: bool(r.resume_required, true),
    assigned_reviewer_id: strOrNull(r.assigned_reviewer_id),
    notify_on_completion: bool(r.notify_on_completion, true),
    work_authorization: isObj(r.work_authorization) ? normalizeWorkAuthorization(r.work_authorization) : null,
    program_name: str(r.program_name),
    kit_version: str(r.kit_version, "v1"),
    kit_sha256: str(r.kit_sha256),
  };
}

export function normalizeConfig(raw: unknown): IntakeConfig {
  const r = rec(raw);
  const kit = rec(r.kit);
  const duration = rec(kit.duration);
  return {
    enabled: r.enabled !== false,
    kit: {
      version: str(kit.version, "v1"),
      sha256: str(kit.sha256),
      title: str(kit.title, "Intake Interview Kit"),
      duration: { min: num(duration.min, 35), max: num(duration.max, 50), target: num(duration.target, 45) },
      stages: arr(kit.stages).map((s, i) => {
        const x = rec(s);
        const minutes = rec(x.minutes);
        return {
          key: (oneOf<StageKey>(x.key, STAGE_KEYS) ?? STAGE_KEYS[i] ?? "greeting") as StageKey,
          number: num(x.number, i + 1),
          title: str(x.title),
          candidate_title: str(x.candidate_title, str(x.title)),
          minutes: { min: num(minutes.min), max: num(minutes.max, num(minutes.min)) },
        };
      }),
      passages: arr(kit.passages).map((p) => {
        const x = rec(p);
        return { key: str(x.key), title: str(x.title), level: str(x.level), drills: strings(x.drills) };
      }),
      topic_cards: arr(kit.topic_cards).map((c) => {
        const x = rec(c);
        return { number: num(x.number), title: str(x.title), type: str(x.type), type_label: str(x.type_label, str(x.type)) };
      }),
    },
    defaults: normalizeDefaults(r.defaults),
    work_authorization_default: normalizeWorkAuthorization(r.work_authorization_default),
    jobs: arr(r.jobs).map((j) => {
      const x = rec(j);
      return { job_id: str(x.job_id, str(x.id)), title: str(x.title, "Untitled job"), status: str(x.status, "open"), round_id: strOrNull(x.round_id), round_name: strOrNull(x.round_name) };
    }),
  };
}

export function normalizeOrgSettings(raw: unknown): IntakeOrgSettings {
  const r = rec(raw);
  return {
    program_name: str(r.program_name, "AI/ML Training Program"),
    work_authorization: normalizeWorkAuthorization(r.work_authorization),
    approved_info: arr(r.approved_info).map((a) => {
      const x = rec(a);
      return { topic: str(x.topic), answer: str(x.answer) };
    }),
    defaults: normalizeDefaults(r.defaults),
    updated_at: strOrNull(r.updated_at),
    updated_by: person(r.updated_by),
  };
}

// ----------------------------------------------------------- summaries

function evaluationRefOf(v: unknown): IntakeSummary["evaluation"] {
  if (!isObj(v)) return null;
  return {
    id: str(v.id),
    status: oneOf(v.status, ["pending", "running", "ready", "failed"] as const) ?? "pending",
    weighted_total: dec(v.weighted_total),
    band: oneOf<DecisionBand>(v.band, DECISION_BANDS),
    evidence_sufficiency: strOrNull(v.evidence_sufficiency),
  };
}

export function normalizeSummary(raw: unknown): IntakeSummary {
  const r = rec(raw);
  const candidate = rec(r.candidate);
  const job = rec(r.job);
  const round = rec(r.round);
  const interview = isObj(r.interview) ? r.interview : null;
  const session = isObj(r.session) ? r.session : null;
  const decision = isObj(r.decision) ? r.decision : null;
  const status = r.status === "draft" || r.status === "cancelled" ? r.status : "active";
  return {
    id: str(r.id),
    status,
    bucket: oneOf<IntakeBucket>(r.bucket, INTAKE_BUCKETS) ?? (status === "draft" ? "drafts" : status === "cancelled" ? "cancelled" : "active"),
    title: str(r.title, "Intake Interview"),
    batch_id: strOrNull(r.batch_id),
    candidate: { id: str(candidate.id), full_name: str(candidate.full_name, "Unnamed candidate"), email: str(candidate.email) },
    job: { id: str(job.id), title: str(job.title) },
    round: typeof round.id === "string" ? { id: round.id, name: str(round.name, "Intake Interview") } : null,
    target_role: oneOf<TargetRole>(r.target_role, TARGET_ROLES),
    interview: interview
      ? { id: str(interview.id), status: str(interview.status, "invited"), invite_expires_at: strOrNull(interview.invite_expires_at), scheduled_for: strOrNull(interview.scheduled_for), verified_at: strOrNull(interview.verified_at) }
      : null,
    session: session
      ? { id: str(session.id), status: str(session.status, "created"), started_at: strOrNull(session.started_at), ended_at: strOrNull(session.ended_at), elapsed_seconds: num(session.elapsed_seconds) }
      : null,
    onboarding_submitted: bool(r.onboarding_submitted),
    resume_confirmed: bool(r.resume_confirmed),
    evaluation: evaluationRefOf(r.evaluation),
    decision:
      decision && oneOf<DecisionOutcome>(decision.outcome, DECISION_OUTCOMES)
        ? { outcome: decision.outcome as DecisionOutcome, created_at: str(decision.created_at), reviewer: person(decision.reviewer) }
        : null,
    assigned_reviewer: person(r.assigned_reviewer),
    has_training_plan: bool(r.has_training_plan),
    review_items_open: num(r.review_items_open),
    integrity_flags: num(r.integrity_flags),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at, str(r.created_at)),
  };
}

const COUNTED: readonly CountedBucket[] = INTAKE_BUCKETS.filter((b): b is CountedBucket => b !== "all");

export function normalizeStats(raw: unknown): IntakeStats {
  const r = rec(raw);
  const buckets = rec(r.buckets);
  const totals = rec(r.totals);
  return {
    buckets: Object.fromEntries(COUNTED.map((b) => [b, num(buckets[b])])) as Record<CountedBucket, number>,
    totals: {
      invitations: num(totals.invitations),
      started: num(totals.started),
      completed: num(totals.completed),
      incomplete: num(totals.incomplete),
      awaiting_review: num(totals.awaiting_review),
      decided: num(totals.decided),
      average_weighted_total: dec(totals.average_weighted_total),
      average_duration_s: numOrNull(totals.average_duration_s),
    },
  };
}

export function normalizeCreateResponse(raw: unknown): IntakeCreateResponse {
  const r = rec(raw);
  return { intake: normalizeSummary(r.intake ?? raw), invite_url: strOrNull(r.invite_url), access_code: strOrNull(r.access_code), expires_at: strOrNull(r.expires_at) };
}

export function normalizeBatchResponse(raw: unknown): IntakeBatchResponse {
  const r = rec(raw);
  return {
    batch_id: str(r.batch_id),
    created: arr(r.created).map((c) => {
      const x = rec(c);
      return {
        row: num(x.row),
        intake_id: str(x.intake_id),
        candidate_id: str(x.candidate_id),
        email: str(x.email),
        candidate_reused: bool(x.candidate_reused),
        invite_url: strOrNull(x.invite_url),
        access_code: strOrNull(x.access_code),
        expires_at: strOrNull(x.expires_at),
      };
    }),
    failed: arr(r.failed).map((f) => {
      const x = rec(f);
      return { row: num(x.row), email: str(x.email), code: str(x.code, "failed"), message: str(x.message) };
    }),
  };
}

// ------------------------------------------------------------ evidence

const SUPPORTS: readonly EvidenceSupport[] = ["positive", "negative", "context"];

export function normalizeEvidenceRef(raw: unknown): EvidenceRef {
  const r = rec(raw);
  const source = r.source === "activity" || r.source === "onboarding" || r.source === "resume" ? r.source : "answer";
  return {
    source,
    stage: oneOf<StageKey>(r.stage, STAGE_KEYS),
    item_key: strOrNull(r.item_key),
    question_id: strOrNull(r.question_id),
    answer_id: strOrNull(r.answer_id),
    activity_id: strOrNull(r.activity_id),
    start_ms: numOrNull(r.start_ms),
    end_ms: numOrNull(r.end_ms),
    quote: strOrNull(r.quote),
    supports: oneOf<EvidenceSupport>(r.supports, SUPPORTS) ?? "context",
  };
}

const evidenceOrNull = (v: unknown): EvidenceRef | null => (isObj(v) ? normalizeEvidenceRef(v) : null);

/** An evidence reference from flat `answer_id` / `start_ms` / `stage` fields (T2 summary rows). */
function idRef(x: Rec, supports: EvidenceSupport = "context", quote: string | null = null): EvidenceRef | null {
  if (typeof x.answer_id !== "string" || !x.answer_id) return null;
  return {
    source: "answer",
    stage: oneOf<StageKey>(x.stage, STAGE_KEYS),
    item_key: strOrNull(x.item_key),
    question_id: strOrNull(x.question_id),
    answer_id: x.answer_id,
    activity_id: null,
    start_ms: numOrNull(x.start_ms),
    end_ms: numOrNull(x.end_ms),
    quote,
    supports,
  };
}

export function normalizeReviewItem(raw: unknown): ReviewItem {
  const r = rec(raw);
  const resolution = isObj(r.resolution) ? r.resolution : null;
  const severity = r.severity === "important" || r.severity === "info" ? r.severity : "attention";
  return {
    id: str(r.id),
    kind: str(r.kind, "evaluator_note"),
    severity,
    title: str(r.title),
    detail: str(r.detail),
    competency_keys: strings(r.competency_keys),
    evidence: arr(r.evidence).map(normalizeEvidenceRef),
    source: str(r.source, "rule"),
    kit_flag: strOrNull(r.kit_flag),
    status: str(r.status, "open"),
    resolution:
      resolution && (resolution.resolution === "confirmed" || resolution.resolution === "dismissed" || resolution.resolution === "follow_up")
        ? { resolution: resolution.resolution, note: str(resolution.note), reviewer: person(resolution.reviewer), at: strOrNull(resolution.at) }
        : null,
  };
}

export function normalizeTrainingPlan(raw: unknown): TrainingPlan | null {
  if (!isObj(raw)) return null;
  return {
    generated_by: str(raw.generated_by, "deterministic"),
    summary: str(raw.summary),
    modules: arr(raw.modules).map((m) => {
      const x = rec(m);
      return {
        key: str(x.key),
        title: str(x.title, str(x.key)),
        priority: num(x.priority, 3),
        competencies: strings(x.competencies),
        why: str(x.why),
        objectives: strings(x.objectives),
        milestones: arr(x.milestones).map((ms) => {
          const y = rec(ms);
          return { week: num(y.week, 1), milestone: str(y.milestone), measure: str(y.measure) };
        }),
      };
    }),
  };
}

// ------------------------------------------------------------- evaluation

export function normalizeMetrics(raw: unknown): ReadingMetricsView | null {
  if (!isObj(raw)) return null;
  const r = raw;
  return {
    method: strOrNull(r.method),
    reference_words: numOrNull(r.reference_words),
    hypothesis_words: numOrNull(r.hypothesis_words),
    matched: numOrNull(r.matched),
    asr_variants: numOrNull(r.asr_variants),
    substitutions: numOrNull(r.substitutions),
    omissions: numOrNull(r.omissions),
    insertions: numOrNull(r.insertions),
    repetitions: numOrNull(r.repetitions),
    reordered: numOrNull(r.reordered),
    fillers: numOrNull(r.fillers),
    wer: numOrNull(r.wer),
    strict_wer: numOrNull(r.strict_wer),
    completeness: numOrNull(r.completeness),
    token_coverage: numOrNull(r.token_coverage),
    sentences: arr(r.sentences).map((s, i) => {
      const x = rec(s);
      const status = x.status === "partial" || x.status === "skipped" ? x.status : "read";
      return { index: num(x.index, i), text: str(x.text), status, coverage: num(x.coverage, status === "read" ? 1 : 0), omitted: num(x.omitted), substituted: num(x.substituted), reordered: bool(x.reordered) };
    }),
    sentences_read: numOrNull(r.sentences_read),
    sentences_partial: numOrNull(r.sentences_partial),
    sentences_skipped: numOrNull(r.sentences_skipped),
    duration_seconds: numOrNull(r.duration_seconds),
    words_per_minute: numOrNull(r.words_per_minute),
    correct_words_per_minute: numOrNull(r.correct_words_per_minute),
    pause_source: strOrNull(r.pause_source),
    pause_count: numOrNull(r.pause_count),
    long_pause_count: numOrNull(r.long_pause_count),
    longest_pause_seconds: numOrNull(r.longest_pause_seconds),
    total_pause_seconds: numOrNull(r.total_pause_seconds),
    delivery_score: numOrNull(r.delivery_score),
    delivery_basis: strings(r.delivery_basis),
    pronunciation: str(r.pronunciation, "not_assessed"),
    review_flags: strings(r.review_flags),
  };
}

const EVENT_KINDS = ["omission", "substitution", "insertion", "repetition", "asr_variant", "reordered"] as const;

export function normalizeEvents(raw: unknown): ReadingEvent[] {
  return arr(raw)
    .map((e) => {
      const x = rec(e);
      const kind = oneOf(x.kind, EVENT_KINDS);
      if (!kind) return null;
      return {
        kind,
        ref_index: numOrNull(x.ref_index),
        ref_word_index: numOrNull(x.ref_word_index),
        ref_start: numOrNull(x.ref_start),
        ref_end: numOrNull(x.ref_end),
        ref_word: strOrNull(x.ref_word),
        hyp_index: numOrNull(x.hyp_index),
        hyp_word: strOrNull(x.hyp_word),
        sentence: numOrNull(x.sentence),
      } satisfies ReadingEvent;
    })
    .filter((e): e is ReadingEvent => e !== null);
}

/** Passage reference as a key/title pair from a string key or an object. */
function passageRef(v: unknown, keyFallback?: unknown, titleFallback?: unknown): { key: string | null; title: string | null } {
  if (typeof v === "string") return { key: v, title: strOrNull(titleFallback) };
  const r = rec(v);
  return { key: strOrNull(r.key) ?? strOrNull(keyFallback), title: strOrNull(r.title) ?? strOrNull(titleFallback) };
}

const COVERAGE: readonly CoverageStatus[] = ["covered", "partial", "missing"];
function coverageStatus(x: Rec): CoverageStatus {
  return oneOf<CoverageStatus>(x.status, COVERAGE) ?? (x.covered === true ? "covered" : "missing");
}

/** `answer` may be an alias the server resolved into an EvidenceRef, or a quote holder. */
function quoteAndEvidence(x: Rec, supports: EvidenceSupport = "positive"): { quote: string | null; evidence: EvidenceRef | null } {
  const objectRef = evidenceOrNull(x.evidence) ?? evidenceOrNull(x.answer) ?? evidenceOrNull(x.ref);
  const quote = strOrNull(x.quote) ?? strOrNull(x.excerpt) ?? objectRef?.quote ?? null;
  return { quote, evidence: objectRef ?? idRef(x, supports, quote) };
}

function normalizeDrillA(raw: unknown): DrillASummary | null {
  if (!isObj(raw)) return null;
  const metrics = normalizeMetrics(raw.metrics);
  const events = Array.isArray(raw.events) ? normalizeEvents(raw.events) : isObj(raw.metrics) && Array.isArray(raw.metrics.events) ? normalizeEvents(raw.metrics.events) : null;
  return {
    passage: passageRef(raw.passage, raw.passage_key, raw.passage_title),
    metrics,
    events,
    score: numOrNull(raw.score) ?? (raw.status === "insufficient" ? null : (metrics?.delivery_score ?? null)),
    basis: strings(raw.basis).length ? strings(raw.basis) : (metrics?.delivery_basis ?? []),
    status: strOrNull(raw.status),
    reason: strOrNull(raw.reason),
    transcript_source: strOrNull(raw.transcript_source),
  };
}

function normalizeDrillB(raw: unknown): DrillBSummary | null {
  if (!isObj(raw)) return null;
  const coverage = raw.coverage;
  const coverageRec = rec(coverage);
  return {
    passage: passageRef(raw.passage, raw.passage_key, raw.passage_title),
    coverage: typeof coverage === "number" || typeof coverage === "string" ? numOrNull(coverage) : numOrNull(coverageRec.coverage),
    concepts: arr(raw.concepts).map((c) => {
      const x = rec(c);
      const { quote, evidence } = quoteAndEvidence(x);
      return { key: str(x.key), text: str(x.text, str(x.key)), status: coverageStatus(x), quote, evidence };
    }),
    paraphrase: strOrNull(raw.paraphrase),
    misstatements: arr(raw.misstatements).map(normalizeEvidenceRef),
    followups: arr(raw.followups).map((f) => {
      const x = rec(f);
      const evidence = evidenceOrNull(x.answer) ?? evidenceOrNull(x.evidence);
      return { question: str(x.question), answer: typeof x.answer === "string" ? x.answer : (strOrNull(x.answer_excerpt) ?? evidence?.quote ?? null), note: str(x.note), evidence };
    }),
    score: numOrNull(raw.score),
    basis: strings(raw.basis),
    status: strOrNull(raw.status),
    reason: strOrNull(raw.reason),
    shown_ms: numOrNull(raw.shown_ms),
  };
}

function normalizeBullets(raw: unknown): BulletCoverage[] {
  return arr(raw).map((b, i) => {
    const x = rec(b);
    const { quote, evidence } = quoteAndEvidence(x);
    return { index: num(x.index, i + 1), text: strOrNull(x.text), status: coverageStatus(x), quote, evidence };
  });
}

export function normalizeSummaryJson(raw: unknown): EvaluationSummary {
  const r = rec(raw);
  const verification = isObj(r.verification) ? r.verification : null;
  return {
    language: strOrNull(r.language),
    strengths: strings(r.strengths),
    improvements: strings(r.improvements),
    drill_a: normalizeDrillA(r.drill_a),
    drill_b: normalizeDrillB(r.drill_b),
    claims: arr(r.claims).map((c) => {
      const x = rec(c);
      // T2 sends {claim_ref: "C1", claim: <text>}; older shapes put the ref in `claim`.
      const ref = strOrNull(x.claim_ref);
      return {
        claim: ref ?? str(x.claim),
        text: ref ? strOrNull(x.claim) : (strOrNull(x.text) ?? strOrNull(x.claim_text)),
        probe: strOrNull(x.probe),
        source: strOrNull(x.source),
        verdict: str(x.verdict, "uncertain"),
        note: str(x.note),
        unresolved: strings(x.unresolved),
        evidence: arr(x.evidence).map(normalizeEvidenceRef),
      };
    }),
    verification: verification ? { quality: strOrNull(verification.quality), note: strOrNull(verification.note), answer: evidenceOrNull(verification.evidence) ?? evidenceOrNull(verification.answer) } : null,
    rating_supported: strOrNull(r.rating_supported),
    bullets: normalizeBullets(r.bullets),
    flow: strOrNull(r.flow),
    examples_used: boolOrNull(r.examples_used),
    gap_handling: strOrNull(r.gap_handling),
    followup_analysis: strOrNull(r.followup_analysis),
    learning_strategy: strOrNull(r.learning_strategy),
    teach_back: strOrNull(r.teach_back),
    ai_ml_openness: strOrNull(r.ai_ml_openness),
    composure_observations: arr(r.composure_observations).map((o) => {
      const x = rec(o);
      return { behaviour: str(x.behaviour), note: str(x.note), evidence: evidenceOrNull(x.answer) ?? evidenceOrNull(x.evidence) ?? idRef(x, "context") };
    }),
    evaluator_notes: arr(r.evaluator_notes).map((n) => {
      const x = rec(n);
      return { evaluator: str(x.evaluator), note: str(x.note) };
    }).filter((n) => n.note),
  };
}

export function normalizeCompetency(raw: unknown): CompetencyScore {
  const r = rec(raw);
  const anchors = rec(r.anchors);
  const score = dec(r.score);
  const status = r.status === "insufficient" || (r.status !== "scored" && score === null) ? "insufficient" : "scored";
  return {
    key: str(r.key, str(r.competency_key)),
    label: str(r.label),
    kit_label: str(r.kit_label),
    weight: dec(r.weight) ?? "0",
    status,
    score: status === "scored" ? score : null,
    weighted: status === "scored" ? dec(r.weighted) : null,
    evaluator: str(r.evaluator),
    reasoning: str(r.reasoning),
    evidence: arr(r.evidence).map(normalizeEvidenceRef),
    missing_evidence: strings(r.missing_evidence),
    strengths: strings(r.strengths),
    improvements: strings(r.improvements),
    anchors: Object.fromEntries((["1", "3", "5"] as const).filter((k) => typeof anchors[k] === "string").map((k) => [k, anchors[k] as string])),
    sub_scores: rec(r.sub_scores),
  };
}

export function normalizeEvaluation(raw: unknown): IntakeEvaluation {
  const r = rec(raw);
  return {
    id: str(r.id),
    version: num(r.version, 1),
    status: oneOf(r.status, ["pending", "running", "ready", "failed"] as const) ?? "ready",
    reason: str(r.reason, "session_completed"),
    created_at: str(r.created_at),
    finished_at: strOrNull(r.finished_at),
    kit_version: str(r.kit_version, "v1"),
    scoring_version: str(r.scoring_version),
    weighted_total: dec(r.weighted_total),
    partial_total: dec(r.partial_total),
    partial_label: strOrNull(r.partial_label),
    normalized_100: dec(r.normalized_100),
    normalized_label: str(r.normalized_label, "Out of 100 (conversion)"),
    band: oneOf<DecisionBand>(r.band, DECISION_BANDS),
    band_label: strOrNull(r.band_label),
    evidence_sufficiency: strOrNull(r.evidence_sufficiency),
    competencies: arr(r.competencies).map(normalizeCompetency),
    summary: normalizeSummaryJson(r.summary),
    validator_issues: arr(r.validator_issues ?? rec(r.validator).issues).map((v) => {
      const x = rec(v);
      return { code: str(x.code), competency: strOrNull(x.competency), detail: str(x.detail) };
    }),
    review_items: arr(r.review_items).map(normalizeReviewItem),
    training_plan: normalizeTrainingPlan(r.training_plan),
    error: strOrNull(r.error),
  };
}

export function normalizeDecision(raw: unknown): IntakeDecision {
  const r = rec(raw);
  const outcome = oneOf<DecisionOutcome>(r.outcome, DECISION_OUTCOMES) ?? "request_evidence";
  return {
    id: str(r.id),
    outcome,
    outcome_label: str(r.outcome_label),
    rationale: str(r.rationale),
    reviewer: person(r.reviewer),
    ai_band_at_decision: oneOf<DecisionBand>(r.ai_band_at_decision, DECISION_BANDS),
    ai_total_at_decision: dec(r.ai_total_at_decision),
    overrides_ai: bool(r.overrides_ai),
    review_items_resolution: arr(r.review_items_resolution).map((x) => {
      const y = rec(x);
      const resolution = y.resolution === "confirmed" || y.resolution === "dismissed" ? y.resolution : "follow_up";
      return { item_id: str(y.item_id), resolution, note: str(y.note) };
    }),
    competency_notes: Object.fromEntries(Object.entries(rec(r.competency_notes)).filter(([, v]) => typeof v === "string")) as Record<string, string>,
    requested_evidence: strings(r.requested_evidence),
    review_decision_id: strOrNull(r.review_decision_id),
    evaluation_id: strOrNull(r.evaluation_id),
    created_at: str(r.created_at),
  };
}

// --------------------------------------------------------------- detail

function normalizeOnboarding(raw: unknown): IntakeOnboarding | null {
  if (!isObj(raw)) return null;
  const r = raw;
  return {
    version: num(r.version, 1),
    full_name: str(r.full_name),
    target_role: oneOf<TargetRole>(r.target_role, TARGET_ROLES),
    target_role_other: strOrNull(r.target_role_other),
    open_to_ai_ml: oneOf(r.open_to_ai_ml, ["yes", "maybe", "no"] as const),
    technical_background: str(r.technical_background),
    primary_language: oneOf(r.primary_language, ["python", "java", "javascript", "csharp", "sql", "other"] as const),
    primary_language_other: strOrNull(r.primary_language_other),
    self_rating: numOrNull(r.self_rating),
    scripting: oneOf(r.scripting, ["none", "some", "regular"] as const),
    scripting_details: strOrNull(r.scripting_details),
    data_ml_libraries: strOrNull(r.data_ml_libraries),
    years_hands_on: numOrNull(r.years_hands_on),
    experience_level_comfort: oneOf(r.experience_level_comfort, ["yes", "unsure", "no"] as const),
    training_commitment: oneOf(r.training_commitment, ["full", "partial", "cannot"] as const),
    weekly_hours: numOrNull(r.weekly_hours),
    availability: oneOf(r.availability, ["immediately", "within_2_weeks", "within_1_month", "later"] as const),
    available_from: strOrNull(r.available_from),
    accuracy_confirmed: bool(r.accuracy_confirmed),
    submitted_at: strOrNull(r.submitted_at),
  };
}

function normalizeEligibility(raw: unknown): IntakeEligibilityView | null {
  if (!isObj(raw)) return null;
  return {
    enabled: bool(raw.enabled, true),
    country: strOrNull(raw.country),
    ask_sponsorship: bool(raw.ask_sponsorship, true),
    authorized: boolOrNull(raw.authorized),
    needs_sponsorship: boolOrNull(raw.needs_sponsorship),
    answered_at: strOrNull(raw.answered_at),
  };
}

function normalizeResumeDoc(raw: unknown): ResumeReviewDoc | null {
  if (!isObj(raw)) return null;
  const r = raw;
  return {
    version: num(r.version, 1),
    headline: strOrNull(r.headline),
    experience_years: numOrNull(r.experience_years),
    roles: arr(r.roles).map((x, i) => {
      const y = rec(x);
      return { ref: str(y.ref, `r${i + 1}`), title: str(y.title), company: str(y.company), start: strOrNull(y.start), end: strOrNull(y.end), responsibilities: strings(y.responsibilities), technologies: strings(y.technologies), achievements: strings(y.achievements) };
    }),
    projects: arr(r.projects).map((x, i) => {
      const y = rec(x);
      return { ref: str(y.ref, `p${i + 1}`), name: str(y.name), role: strOrNull(y.role), description: str(y.description), technologies: strings(y.technologies), outcomes: strings(y.outcomes) };
    }),
    skills: strings(r.skills),
    languages: strings(r.languages),
    tools: strings(r.tools),
    education: arr(r.education).map((x, i) => {
      const y = rec(x);
      return { ref: str(y.ref, `e${i + 1}`), institution: str(y.institution), degree: strOrNull(y.degree), field: strOrNull(y.field) };
    }),
    certifications: strings(r.certifications),
  };
}

function normalizeResumeReview(raw: unknown): ResumeReviewRecord | null {
  if (!isObj(raw)) return null;
  const source = rec(raw.source);
  return {
    action: raw.action === "skipped" ? "skipped" : "confirmed",
    source: { resume_id: strOrNull(source.resume_id), profile_id: strOrNull(source.profile_id), profile_status: strOrNull(source.profile_status) },
    original: normalizeResumeDoc(raw.original),
    document: normalizeResumeDoc(raw.document),
    corrections: arr(raw.corrections).map((c) => {
      const y = rec(c);
      const op = y.op === "added" || y.op === "removed" ? y.op : "changed";
      return { path: str(y.path), op, before: y.before ?? null, after: y.after ?? null };
    }),
    confirmed_at: strOrNull(raw.confirmed_at),
  };
}

function normalizeItem(raw: unknown): StageItemResult {
  const r = rec(raw);
  return {
    item_key: str(r.item_key),
    title: str(r.title, str(r.item_key)),
    mandatory: bool(r.mandatory),
    compressible: bool(r.compressible),
    status: (oneOf(r.status, ["pending", "asked", "answered", "prefilled", "skipped", "declined", "not_reached", "interrupted", "not_applicable"] as const) ?? "pending"),
    question_ids: strings(r.question_ids),
    answer_ids: strings(r.answer_ids),
    followups_used: num(r.followups_used),
    followup_limit: num(r.followup_limit),
    clarifications: num(r.clarifications),
    skip_reason: strOrNull(r.skip_reason),
    started_s: numOrNull(r.started_s),
    ended_s: numOrNull(r.ended_s),
  };
}

function normalizeStage(raw: unknown, index: number): IntakeStage {
  const r = rec(raw);
  return {
    key: (oneOf<StageKey>(r.key ?? r.stage_key, STAGE_KEYS) ?? STAGE_KEYS[index] ?? "greeting") as StageKey,
    number: num(r.number ?? r.stage_number, index + 1),
    title: str(r.title),
    status: oneOf(r.status, ["pending", "in_progress", "completed", "incomplete"] as const) ?? "pending",
    started_s: numOrNull(r.started_s ?? r.started_elapsed_s),
    ended_s: numOrNull(r.ended_s ?? r.ended_elapsed_s),
    budget_s: numOrNull(r.budget_s),
    items: arr(r.items).map(normalizeItem),
    extra: rec(r.extra),
  };
}

const ACTIVITY_KINDS: readonly ActivityKind[] = ["reading_aloud", "read_relay", "topic_card"];

function normalizeActivity(raw: unknown): IntakeActivity | null {
  const r = rec(raw);
  const kind = oneOf<ActivityKind>(r.kind, ACTIVITY_KINDS);
  if (!kind) return null;
  const material = rec(r.material);
  const transcript = rec(r.transcript);
  const reveal = isObj(r.reveal) ? r.reveal : null;
  const capture = isObj(r.capture) ? r.capture : null;
  return {
    id: str(r.id),
    kind,
    stage_key: (oneOf<StageKey>(r.stage_key, STAGE_KEYS) ?? (kind === "topic_card" ? "topic" : "reading")) as StageKey,
    status: oneOf(r.status, ["pending", "armed", "running", "completed", "interrupted", "skipped", "failed"] as const) ?? "pending",
    attempt: num(r.attempt, 1),
    material: {
      key: str(material.key, str(r.material_key)),
      title: str(material.title),
      level: strOrNull(material.level),
      type: strOrNull(material.type),
      type_label: strOrNull(material.type_label),
      paragraphs: strings(material.paragraphs),
      bullets: strings(material.bullets),
      instruction: strOrNull(material.instruction),
    },
    phases: arr(r.phases).map((p) => {
      const x = rec(p);
      return { phase: str(x.phase), status: str(x.status), planned_ms: numOrNull(x.planned_ms), started_elapsed_s: numOrNull(x.started_elapsed_s), ended_elapsed_s: numOrNull(x.ended_elapsed_s), end_reason: strOrNull(x.end_reason) };
    }),
    reveal: reveal
      ? {
          shown_ms: numOrNull(reveal.shown_ms),
          windows: arr(reveal.windows).map((w) => {
            const x = rec(w);
            return { from_elapsed_s: num(x.from_elapsed_s), to_elapsed_s: num(x.to_elapsed_s) };
          }),
          interrupted: bool(reveal.interrupted),
        }
      : null,
    capture: capture
      ? {
          started_ms: numOrNull(capture.started_ms),
          ended_ms: numOrNull(capture.ended_ms),
          voiced_ms: numOrNull(capture.voiced_ms),
          end_reason: strOrNull(capture.end_reason),
          typed: bool(capture.typed),
          pauses_count: numOrNull(capture.pauses_count),
          segments_count: numOrNull(capture.segments_count),
        }
      : null,
    transcript: {
      final: strOrNull(transcript.final) ?? strOrNull(r.transcript_final),
      live: strOrNull(transcript.live) ?? strOrNull(r.transcript_live),
      status: (oneOf(transcript.status ?? r.transcript_status, ["pending", "final", "pending_worker", "live_only", "unavailable"] as const) ?? "pending"),
      source: strOrNull(transcript.source) ?? strOrNull(r.transcript_source),
    },
    question_id: strOrNull(r.question_id),
    answer_id: strOrNull(r.answer_id),
    audio_media_id: strOrNull(r.audio_media_id),
  };
}

export function normalizeDetail(raw: unknown): IntakeDetail {
  const r = rec(raw);
  const evaluation = isObj(r.evaluation) ? normalizeEvaluation(r.evaluation) : null;
  const report = isObj(r.report) ? r.report : null;
  const resume = isObj(r.resume) ? r.resume : null;
  const integrity = rec(r.integrity);
  const recordings = rec(r.recordings);
  const reviewItems = arr(r.review_items).map(normalizeReviewItem);
  return {
    ...normalizeSummary(raw),
    settings: normalizeInviteSettings(r.settings),
    // The review DTO carries no notes; keep "unknown" (undefined) apart from "none" (null).
    ...(typeof r.notes === "string" || r.notes === null ? { notes: r.notes as string | null } : {}),
    onboarding: normalizeOnboarding(r.onboarding),
    eligibility: normalizeEligibility(r.eligibility),
    resume_review: normalizeResumeReview(r.resume_review),
    resume: resume ? { id: str(resume.id), original_filename: str(resume.original_filename, "Résumé"), status: str(resume.status) } : null,
    stages: arr(r.stages).map(normalizeStage).sort((a, b) => a.number - b.number),
    activities: arr(r.activities).map(normalizeActivity).filter((a): a is IntakeActivity => a !== null),
    evaluation,
    review_items: reviewItems.length ? reviewItems : (evaluation?.review_items ?? []),
    decisions: arr(r.decisions).map(normalizeDecision).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    report: report ? { version: num(report.version, 1), status: str(report.status, "ready"), artifact_media_id: strOrNull(report.artifact_media_id), generated_at: strOrNull(report.generated_at) } : null,
    integrity: { flags: num(integrity.flags, num(r.integrity_flags)), unreviewed: num(integrity.unreviewed) },
    // Newest evaluation of any status (a re-evaluation may be running while `evaluation` is the last ready one).
    evaluation_ref: evaluationRefOf(r.evaluation_ref) ?? evaluationRefOf(r.evaluation),
    recordings: { parts: num(recordings.parts), ready: recordings.ready === true || num(recordings.ready) > 0 },
  };
}

export function normalizeTimeline(raw: unknown): IntakeTimeline {
  const r = rec(raw);
  return {
    intake_id: str(r.intake_id),
    session_id: strOrNull(r.session_id),
    stages: arr(r.stages)
      .map((s, i) => {
        const x = rec(s);
        return {
          key: (oneOf<StageKey>(x.key, STAGE_KEYS) ?? STAGE_KEYS[i] ?? "greeting") as StageKey,
          number: num(x.number, i + 1),
          title: str(x.title),
          status: oneOf(x.status, ["pending", "in_progress", "completed", "incomplete"] as const) ?? "pending",
          items: arr(x.items).map((it) => {
            const y = rec(it);
            return {
              item_key: str(y.item_key),
              title: str(y.title, str(y.item_key)),
              mandatory: bool(y.mandatory),
              status: normalizeItem(y).status,
              skip_reason: strOrNull(y.skip_reason),
              exchanges: arr(y.exchanges).map((e) => {
                const z = rec(e);
                const answer = isObj(z.answer) ? z.answer : null;
                return {
                  question_id: str(z.question_id),
                  text: str(z.text),
                  asked_ms: numOrNull(z.asked_ms),
                  template: bool(z.template),
                  item_kind: strOrNull(z.item_kind),
                  answer: answer
                    ? { answer_id: str(answer.answer_id, str(answer.id)), text: str(answer.text), started_ms: numOrNull(answer.started_ms), ended_ms: numOrNull(answer.ended_ms), duration_ms: numOrNull(answer.duration_ms), typed: bool(answer.typed), audio_media_id: strOrNull(answer.audio_media_id) }
                    : null,
                };
              }),
            };
          }),
        };
      })
      .sort((a, b) => a.number - b.number),
  };
}

export function normalizeAnalytics(raw: unknown): IntakeAnalytics {
  const r = rec(raw);
  const period = rec(r.period);
  const funnel = rec(r.funnel);
  const overrides = rec(r.overrides);
  return {
    period: { since: strOrNull(period.since), until: strOrNull(period.until), days: numOrNull(period.days), job_id: strOrNull(period.job_id), target_role: strOrNull(period.target_role), reviewer_id: strOrNull(period.reviewer_id) },
    funnel: {
      invitations: num(funnel.invitations),
      onboarded: num(funnel.onboarded),
      started: num(funnel.started),
      completed: num(funnel.completed),
      incomplete: num(funnel.incomplete),
      evaluated: num(funnel.evaluated),
      decided: num(funnel.decided),
    },
    average_weighted_total: dec(r.average_weighted_total),
    average_duration_s: numOrNull(r.average_duration_s),
    stage_completion: arr(r.stage_completion).map((s) => {
      const x = rec(s);
      return { stage: str(x.stage), title: str(x.title, str(x.stage)), completed: num(x.completed), incomplete: num(x.incomplete), rate: numOrNull(x.rate) };
    }),
    bands: arr(r.bands).map((b) => {
      const x = rec(b);
      return { band: str(x.band), label: str(x.label, str(x.band)), count: num(x.count) };
    }),
    decisions: arr(r.decisions).map((d) => {
      const x = rec(d);
      return { outcome: str(x.outcome), label: str(x.label, str(x.outcome)), count: num(x.count) };
    }),
    overrides: { count: num(overrides.count), rate: numOrNull(overrides.rate) },
    competency_averages: arr(r.competency_averages).map((c) => {
      const x = rec(c);
      return { key: str(x.key), label: str(x.label, str(x.key)), average: dec(x.average), scored: num(x.scored), insufficient: num(x.insufficient) };
    }),
    technical_weaknesses: arr(r.technical_weaknesses).map((t) => {
      const x = rec(t);
      return { key: str(x.key), label: str(x.label, str(x.key)), count: num(x.count) };
    }),
    training_needs: arr(r.training_needs).map((t) => {
      const x = rec(t);
      return { module: str(x.module), label: str(x.label, str(x.module)), count: num(x.count) };
    }),
    reviewers: arr(r.reviewers).map((v) => {
      const x = rec(v);
      return { id: str(x.id), full_name: str(x.full_name), decisions: num(x.decisions) };
    }),
    by_target_role: arr(r.by_target_role).map((t) => {
      const x = rec(t);
      return { target_role: str(x.target_role), label: strOrNull(x.label), count: num(x.count), average_weighted_total: dec(x.average_weighted_total) };
    }),
  };
}

/** The intake report document (`reports.content`, kind "intake"); null for any other report. */
export function normalizeIntakeReport(raw: unknown): IntakeReportContent | null {
  const r = rec(raw);
  if (r.kind !== "intake") return null;
  const a = rec(r.A_candidate_summary);
  const b = rec(r.B_overall);
  const e = rec(r.E_technical);
  const f = rec(r.F_reading);
  const g = rec(r.G_structured);
  const h = rec(r.H_trainability);
  const i = rec(r.I_integrity);
  const k = rec(r.K_human_review);
  const fa = isObj(f.drill_a) ? f.drill_a : null;
  const fb = isObj(f.drill_b) ? f.drill_b : null;
  const tr = rec(f.technical_reading);
  const followup = isObj(g.followup) ? g.followup : null;
  return {
    kind: "intake",
    schema_version: num(r.schema_version, 1),
    kit_version: strOrNull(r.kit_version),
    scoring_version: strOrNull(r.scoring_version),
    reading_method: strOrNull(r.reading_method),
    generated_at: strOrNull(r.generated_at),
    session_id: strOrNull(r.session_id),
    intake_id: strOrNull(r.intake_id),
    evaluation_id: strOrNull(r.evaluation_id),
    evaluation_version: numOrNull(r.evaluation_version),
    A_candidate_summary: {
      candidate_name: str(a.candidate_name, "Candidate"),
      target_role: strOrNull(a.target_role),
      interview_date: strOrNull(a.interview_date),
      duration_s: numOrNull(a.duration_s),
      resume_summary: strOrNull(a.resume_summary),
      technical_background: strOrNull(a.technical_background),
      years_hands_on: numOrNull(a.years_hands_on),
      resume_experience_years: numOrNull(a.resume_experience_years),
      completed_stages: arr(a.completed_stages).map((s) => {
        const x = rec(s);
        return { key: str(x.key), title: str(x.title, str(x.key)), status: str(x.status, "completed") };
      }),
    },
    B_overall: {
      weighted_total: dec(b.weighted_total),
      normalized_100: dec(b.normalized_100),
      normalized_label: str(b.normalized_label, "Out of 100 (conversion)"),
      band: oneOf<DecisionBand>(b.band, DECISION_BANDS),
      band_label: strOrNull(b.band_label),
      band_guidance: strOrNull(b.band_guidance),
      recommendation_note: str(b.recommendation_note, "This is an AI recommendation. A human reviewer makes the decision."),
      human_review_status: b.human_review_status === "decided" ? "decided" : "pending",
      latest_decision: isObj(b.latest_decision) ? normalizeDecision(b.latest_decision) : null,
      evidence_sufficiency: strOrNull(b.evidence_sufficiency),
      partial_total: dec(b.partial_total),
      partial_label: strOrNull(b.partial_label),
      strengths: strings(b.strengths),
      improvements: strings(b.improvements),
    },
    C_competencies: arr(r.C_competencies).map((c) => {
      const x = rec(c);
      const score = dec(x.score);
      return {
        key: str(x.key),
        label: str(x.label),
        weight: dec(x.weight) ?? "0",
        score,
        weighted: dec(x.weighted),
        status: str(x.status, score === null ? "insufficient" : "scored"),
        observations: strings(x.observations),
        explanation: str(x.explanation),
        improvements: strings(x.improvements),
        evidence: arr(x.evidence).map(normalizeEvidenceRef),
        sub_scores: rec(x.sub_scores),
      };
    }),
    D_resume_verification: arr(r.D_resume_verification).map((d) => {
      const x = rec(d);
      return {
        claim_ref: str(x.claim_ref),
        claim: str(x.claim),
        probe: strOrNull(x.probe),
        source: strOrNull(x.source),
        questions: arr(x.questions).map((q) => {
          const y = rec(q);
          return { rung: strOrNull(y.rung), question_id: strOrNull(y.question_id), text: str(y.text) };
        }),
        responses: arr(x.responses).map((q) => {
          const y = rec(q);
          return { answer_id: strOrNull(y.answer_id), excerpt: str(y.excerpt) };
        }),
        verdict: strOrNull(x.verdict),
        supporting_evidence: arr(x.supporting_evidence).map(normalizeEvidenceRef),
        unresolved: strings(x.unresolved),
      };
    }),
    E_technical: {
      language: strOrNull(e.language),
      self_rating: numOrNull(e.self_rating),
      scripting: strOrNull(e.scripting),
      data_ml_libraries: strOrNull(e.data_ml_libraries),
      verification: arr(e.verification).map((v) => {
        const y = rec(v);
        return { question: str(y.question), answer_excerpt: strOrNull(y.answer_excerpt), quality: strOrNull(y.quality), matched_key_points: arr(y.matched_key_points) };
      }),
      rating_supported: strOrNull(e.rating_supported),
      technical_understanding: strOrNull(e.technical_understanding),
      accuracy_notes: strOrNull(e.accuracy_notes),
    },
    F_reading: {
      drill_a: fa
        ? {
            passage_key: strOrNull(fa.passage_key),
            passage_title: strOrNull(fa.passage_title),
            duration_s: numOrNull(fa.duration_s),
            transcript: strOrNull(fa.transcript),
            transcript_source: strOrNull(fa.transcript_source),
            metrics: normalizeMetrics(fa.metrics),
            events: normalizeEvents(fa.events),
            score: numOrNull(fa.score),
            basis: strings(fa.basis),
            pronunciation: str(fa.pronunciation, "not_assessed"),
          }
        : null,
      drill_b: fb
        ? {
            passage_key: strOrNull(fb.passage_key),
            passage_title: strOrNull(fb.passage_title),
            shown_seconds: numOrNull(fb.shown_seconds),
            explanation: strOrNull(fb.explanation),
            concepts: arr(fb.concepts).map((c) => {
              const x = rec(c);
              return { key: str(x.key), text: str(x.text, str(x.key)), status: coverageStatus(x), quote: strOrNull(x.quote) };
            }),
            coverage: numOrNull(fb.coverage),
            paraphrase: strOrNull(fb.paraphrase),
            misstatements: arr(fb.misstatements).map(normalizeEvidenceRef),
            followups: arr(fb.followups).map((x) => {
              const y = rec(x);
              return { question: str(y.question), answer_excerpt: strOrNull(y.answer_excerpt), note: str(y.note) };
            }),
            score: numOrNull(fb.score),
            basis: strings(fb.basis),
          }
        : null,
      technical_reading: { score: dec(tr.score), formula: str(tr.formula, "0.4 × Drill A + 0.6 × Drill B") },
    },
    G_structured: {
      card_number: numOrNull(g.card_number),
      card_title: strOrNull(g.card_title),
      bullets: arr(g.bullets).map((x, idx) => {
        const y = rec(x);
        return { index: num(y.index, idx + 1), text: str(y.text), status: coverageStatus(y), excerpt: strOrNull(y.excerpt) };
      }),
      flow: strOrNull(g.flow),
      examples_used: boolOrNull(g.examples_used),
      gap_handling: strOrNull(g.gap_handling),
      followup: followup ? { question: strOrNull(followup.question), answer_excerpt: strOrNull(followup.answer_excerpt), analysis: strOrNull(followup.analysis) } : null,
      score: dec(g.score),
    },
    H_trainability: {
      learning_strategy: strOrNull(h.learning_strategy),
      teaching_exercise: strOrNull(h.teaching_exercise),
      problem_solving: strOrNull(h.problem_solving),
      training_readiness: strOrNull(h.training_readiness),
      ai_ml_openness: strOrNull(h.ai_ml_openness),
      development_areas: strings(h.development_areas),
    },
    I_integrity: {
      observations: arr(i.observations).map((o) => {
        const x = rec(o);
        return { type: str(x.type), title: str(x.title, str(x.type)), count: num(x.count, 1), first_at: strOrNull(x.first_at), review_status: strOrNull(x.review_status) };
      }),
      unreviewed: num(i.unreviewed),
      note: str(i.note, "Signals are observations, not determinations."),
    },
    J_training_plan: normalizeTrainingPlan(r.J_training_plan),
    K_human_review: {
      review_items: arr(k.review_items).map(normalizeReviewItem),
      decisions: arr(k.decisions).map(normalizeDecision),
      status: k.status === "decided" ? "decided" : "pending",
    },
    eligibility_note: str(r.eligibility_note, "Work-authorisation answers are reviewed by people only; they are not part of this report or any score."),
  };
}

// ------------------------------------------------------------------ hooks

export function useIntakeConfig(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: intakeKeys.config,
    queryFn: async ({ signal }) => normalizeConfig(await api.get<unknown>("/intake/config", { signal })),
    staleTime: 5 * 60_000,
    retry: false,
    enabled: options.enabled ?? true,
    meta: { errorToast: false },
  });
}

export function useIntakeSettings(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: intakeKeys.settings,
    queryFn: async ({ signal }) => normalizeOrgSettings(await api.get<unknown>("/intake/settings", { signal })),
    staleTime: 60_000,
    retry: false,
    enabled: options.enabled ?? true,
    meta: { errorToast: false },
  });
}

export function useSaveIntakeSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: IntakeOrgSettingsIn) => api.put<unknown>("/intake/settings", body).then(normalizeOrgSettings),
    meta: { silentError: true },
    onSuccess: (settings) => {
      client.setQueryData(intakeKeys.settings, settings);
      void client.invalidateQueries({ queryKey: intakeKeys.config });
    },
  });
}

export function useIntakeList(query: IntakeListQuery) {
  const params = listParams(query);
  return useQuery({
    queryKey: intakeKeys.list(params),
    queryFn: async ({ signal }) => page(await api.get<unknown>("/intake/interviews", { params, signal }), normalizeSummary),
    placeholderData: keepPreviousData,
    retry: false,
    refetchInterval: query.bucket === "in_progress" ? 15_000 : 60_000,
  });
}

export function useIntakeStats(filters: IntakeListFilters) {
  const params = statsParams(filters);
  return useQuery({
    queryKey: intakeKeys.stats(params),
    queryFn: async ({ signal }) => normalizeStats(await api.get<unknown>("/intake/stats", { params, signal })),
    placeholderData: keepPreviousData,
    retry: false,
    refetchInterval: 60_000,
    meta: { errorToast: false },
  });
}

function refreshLists(client: QueryClient) {
  void client.invalidateQueries({ queryKey: intakeKeys.lists });
  void client.invalidateQueries({ queryKey: ["intake", "stats"] });
  void client.invalidateQueries({ queryKey: ["intake", "analytics"] });
}

export function useCreateIntake() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: IntakeCreate) => api.post<unknown>("/intake/interviews", body).then(normalizeCreateResponse),
    meta: { silentError: true },
    onSuccess: (result) => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: queryKeys.candidates.all });
      client.removeQueries({ queryKey: intakeKeys.detail(result.intake.id) });
    },
  });
}

export function useBatchCreateIntake() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: IntakeBatchCreate) => api.post<unknown>("/intake/interviews/batch", body, { timeoutMs: 120_000 }).then(normalizeBatchResponse),
    meta: { silentError: true },
    onSuccess: () => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: queryKeys.candidates.all });
    },
  });
}

export function useUpdateIntake(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: IntakeUpdate) => api.patch<unknown>(`/intake/interviews/${id}`, body).then(normalizeSummary),
    meta: { silentError: true },
    onSuccess: () => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id), exact: true });
    },
  });
}

export function useSendIntake(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { expires_in_hours?: number }) => api.post<unknown>(`/intake/interviews/${id}/send`, body).then(normalizeCreateResponse),
    meta: { silentError: true },
    onSuccess: () => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id), exact: true });
    },
  });
}

export function useCancelIntake(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { reason?: string }) => api.post<unknown>(`/intake/interviews/${id}/cancel`, body).then(normalizeSummary),
    meta: { successToast: "Intake cancelled" },
    onSuccess: () => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id), exact: true });
    },
  });
}

export function useRegenerateIntakeLink(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { expires_in_hours?: number }) => api.post<unknown>(`/intake/interviews/${id}/regenerate-link`, body).then(normalizeCreateResponse),
    meta: { silentError: true },
    onSuccess: () => {
      refreshLists(client);
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id), exact: true });
    },
  });
}

const LIVE_SESSION = new Set(["created", "preparing", "ready", "live", "paused", "completing"]);

export function useIntakeDetail(id: string | undefined) {
  return useQuery({
    queryKey: intakeKeys.detail(id ?? ""),
    queryFn: async ({ signal }) => normalizeDetail(await api.get<unknown>(`/intake/interviews/${id}`, { signal })),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      if (data.session && LIVE_SESSION.has(data.session.status)) return 10_000;
      const latest = data.evaluation_ref ?? data.evaluation;
      if (latest && (latest.status === "pending" || latest.status === "running")) return 8_000;
      if (data.session && ["completing", "completed"].includes(data.session.status) && !data.evaluation) return 15_000;
      return false;
    },
  });
}

export function useIntakeTimeline(id: string | undefined, { enabled = true, live = false }: { enabled?: boolean; live?: boolean } = {}) {
  return useQuery({
    queryKey: intakeKeys.timeline(id ?? ""),
    queryFn: async ({ signal }) => normalizeTimeline(await api.get<unknown>(`/intake/interviews/${id}/timeline`, { signal })),
    enabled: Boolean(id) && enabled,
    retry: false,
    refetchInterval: live ? 10_000 : false,
  });
}

export function useIntakeEvaluation(id: string | undefined, version: number | null, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: intakeKeys.evaluation(id ?? "", version),
    queryFn: async ({ signal }) => normalizeEvaluation(await api.get<unknown>(`/intake/interviews/${id}/evaluation`, { params: { version: version ?? undefined }, signal })),
    enabled: Boolean(id) && enabled,
    retry: false,
    meta: { errorToast: false },
  });
}

export function useReevaluateIntake(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ evaluation_version?: number; job_id?: string }>(`/intake/interviews/${id}/evaluate`),
    meta: { silentError: true },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id) });
      refreshLists(client);
    },
  });
}

export function useRecordIntakeDecision(id: string, sessionId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: IntakeDecisionIn) => api.post<unknown>(`/intake/interviews/${id}/decisions`, body).then(normalizeDecision),
    meta: { silentError: true, successToast: "Decision recorded" },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: intakeKeys.detail(id) });
      refreshLists(client);
      if (sessionId) {
        void client.invalidateQueries({ queryKey: queryKeys.sessions.part(sessionId, "reviews") });
        void client.invalidateQueries({ queryKey: queryKeys.sessions.detail(sessionId), exact: true });
      }
      void client.invalidateQueries({ queryKey: ["sessions", "list"] });
    },
  });
}

export function analyticsParams(query: IntakeAnalyticsQuery) {
  return { days: query.days, job_id: query.job_id, target_role: query.target_role, reviewer_id: query.reviewer_id };
}

export function useIntakeAnalytics(query: IntakeAnalyticsQuery) {
  const params = analyticsParams(query);
  return useQuery({
    queryKey: intakeKeys.analytics(params),
    queryFn: async ({ signal }) => normalizeAnalytics(await api.get<unknown>("/intake/analytics", { params, signal })),
    placeholderData: keepPreviousData,
    retry: false,
  });
}

/**
 * The intake row of a session: one exact lookup through the list's `session_id` filter
 * (it matches any session of the intake's interview, within the caller's organisation).
 */
export async function findIntakeForSession(sessionId: string, signal?: AbortSignal): Promise<IntakeSummary | null> {
  const result = page(await api.get<unknown>("/intake/interviews", { params: { session_id: sessionId, limit: 1, offset: 0 }, signal }), normalizeSummary);
  return result.items[0] ?? null;
}
