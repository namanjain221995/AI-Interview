/**
 * Tolerant response normalisers. List endpoints return the `{items, total}`
 * envelope by contract, but a few session sub-resources are naturally shaped
 * as objects (`{segments}`, `{questions}`) or bare arrays; these helpers accept
 * every reasonable shape so a backend detail never blanks a screen.
 */

import type {
  CodingSessionDetail,
  EvidenceRecord,
  IntegrityEventType,
  IntegritySeverity,
  Page,
  ReviewDecision,
  ScoreRecord,
  ScoreSnapshot,
  SessionQuestion,
  SessionScores,
  TimelineEvent,
} from "@/lib/types";

import type {
  CompetencyScoreRecord,
  IntegrityEventView,
  SessionIntegrityView,
  SessionScoresView,
  SessionTimelineView,
  TimelineWindowDTO,
  TranscriptEntry,
} from "./types";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** First array found at `raw` itself or under one of `keys` (default: items). */
export function asList<T>(raw: unknown, keys: readonly string[] = ["items"]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (!isRecord(raw)) return [];
  for (const key of [...keys, "items", "data", "results"]) {
    const value = raw[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

/** A list response as a Page (bare arrays get `total = length`). */
export function asPage<T>(raw: unknown, keys?: readonly string[]): Page<T> {
  const items = asList<T>(raw, keys);
  const total = isRecord(raw) && typeof raw.total === "number" ? raw.total : items.length;
  return { items, total };
}

export function numberOrNull(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

export function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => (typeof item === "string" ? item : isRecord(item) ? (stringOrNull(item.text) ?? stringOrNull(item.title) ?? "") : "")).filter(Boolean);
}

// ----------------------------------------------------------------- transcript

/** Transcript segments; answer audio ids come per segment or in the response's `answer_audio` map. */
export function normalizeTranscript(raw: unknown): TranscriptEntry[] {
  const audio = isRecord(raw) && isRecord(raw.answer_audio) ? (raw.answer_audio as Record<string, unknown>) : {};
  return asList<TranscriptEntry>(raw, ["segments", "transcript", "entries"])
    .filter((s) => isRecord(s) && typeof s.text === "string")
    .map((s) => {
      const fromMap = s.answer_id ? stringOrNull(audio[s.answer_id]) : null;
      return s.audio_media_id || !fromMap ? s : { ...s, audio_media_id: fromMap };
    });
}

// ------------------------------------------------------------------ questions

export function normalizeQuestions(raw: unknown): SessionQuestion[] {
  return asList<SessionQuestion>(raw, ["questions"])
    .filter((q) => isRecord(q) && typeof q.id === "string")
    .map((q) => ({
      ...q,
      competency_keys: Array.isArray(q.competency_keys) ? q.competency_keys : [],
      expected_evidence: Array.isArray(q.expected_evidence) ? q.expected_evidence : [],
      answers: Array.isArray(q.answers) ? q.answers : [],
    }))
    .sort((a, b) => a.sequence - b.sequence);
}

// ------------------------------------------------------------------- evidence

export function normalizeEvidence(raw: unknown): EvidenceRecord[] {
  return asList<EvidenceRecord>(raw, ["evidence"]).filter((e) => isRecord(e) && typeof e.id === "string" && typeof e.competency_key === "string");
}

// --------------------------------------------------------------------- scores

function isScoreRecord(value: unknown): value is ScoreRecord {
  return isRecord(value) && typeof value.key === "string" && (typeof value.score === "number" || value.score === null);
}

/** Accepts the SessionScores object (with per-competency `why`) or a flat ScoreRecord list. */
export function normalizeScores(raw: unknown): SessionScoresView {
  const meta = isRecord(raw)
    ? {
        rationale: stringOrNull(raw.rationale),
        factors: isRecord(raw.factors) ? raw.factors : {},
        source: stringOrNull(raw.source),
        isFinal: raw.is_final === true,
        computedAt: stringOrNull(raw.computed_at),
      }
    : {};
  if (isRecord(raw) && !Array.isArray(raw.items) && ("competencies" in raw || "overall" in raw || "rubric" in raw)) {
    return {
      overall: isScoreRecord(raw.overall) ? raw.overall : null,
      competencies: asList<CompetencyScoreRecord>(raw.competencies).filter(isScoreRecord),
      rubric: asList<ScoreRecord>(raw.rubric).filter(isScoreRecord),
      recommendation: (raw.recommendation as SessionScores["recommendation"]) ?? null,
      ...meta,
    };
  }
  const records = asList<ScoreRecord>(raw, ["scores"]).filter(isScoreRecord);
  return {
    overall: records.find((r) => r.category === "overall") ?? null,
    competencies: records.filter((r) => r.category === "competency"),
    rubric: records.filter((r) => r.category === "rubric_category"),
    recommendation: isRecord(raw) ? ((raw.recommendation as SessionScores["recommendation"]) ?? null) : null,
    ...meta,
  };
}

// ------------------------------------------------------------------- timeline

export function normalizeTimeline(raw: unknown): SessionTimelineView {
  const record = isRecord(raw) ? raw : {};
  const snapshots = asList<ScoreSnapshot>(record.snapshots ?? record.points ?? record.raw ?? (Array.isArray(raw) ? raw : []))
    .filter((s) => isRecord(s) && typeof s.competency_key === "string" && typeof s.score === "number")
    .map((s) => ({ ...s, elapsed_seconds: numberOrNull(s.elapsed_seconds) ?? 0 }));
  return {
    snapshots,
    events: asList<TimelineEvent>(record.events),
    windows: asList<TimelineWindowDTO>(record.windows ?? record.buckets),
    windowMinutes: numberOrNull(record.window_minutes),
    durationSeconds: numberOrNull(record.duration_seconds),
  };
}

// --------------------------------------------------------------------- coding

export function normalizeCoding(raw: unknown): CodingSessionDetail[] {
  return asList<CodingSessionDetail>(raw, ["coding_sessions", "sessions", "coding"])
    .filter((c) => isRecord(c) && typeof c.id === "string")
    .map((c) => ({
      ...c,
      snapshots: [...(Array.isArray(c.snapshots) ? c.snapshots : [])].sort((a, b) => a.seq - b.seq),
      runs: [...(Array.isArray(c.runs) ? c.runs : [])].sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0)),
      hints: [...(Array.isArray(c.hints) ? c.hints : [])].sort((a, b) => a.level - b.level),
      evaluation: isRecord(c.evaluation) ? c.evaluation : {},
    }));
}

// ------------------------------------------------------------------ integrity

function countRecord<K extends string>(raw: unknown): Partial<Record<K, number>> {
  const out: Partial<Record<K, number>> = {};
  if (isRecord(raw)) {
    for (const [key, value] of Object.entries(raw)) {
      const n = numberOrNull(value);
      if (n !== null) out[key as K] = n;
    }
  } else if (Array.isArray(raw)) {
    for (const item of raw) {
      if (!isRecord(item)) continue;
      const key = stringOrNull(item.type) ?? stringOrNull(item.key) ?? stringOrNull(item.severity);
      const n = numberOrNull(item.count);
      if (key && n !== null) out[key as K] = n;
    }
  }
  return out;
}

export function normalizeIntegrityEvents(raw: unknown): IntegrityEventView[] {
  return asList<IntegrityEventView>(raw, ["events"]).filter((e) => isRecord(e) && typeof e.id === "string" && typeof e.type === "string");
}

export function normalizeSessionIntegrity(raw: unknown): SessionIntegrityView {
  const events = normalizeIntegrityEvents(raw);
  const record = isRecord(raw) ? raw : {};
  const countsByType = countRecord<IntegrityEventType>(record.counts_by_type ?? record.counts);
  const countsBySeverity = countRecord<IntegritySeverity>(record.counts_by_severity);
  if (Object.keys(countsByType).length === 0) {
    for (const event of events) countsByType[event.type] = (countsByType[event.type] ?? 0) + Math.max(1, event.count ?? 1);
  }
  if (Object.keys(countsBySeverity).length === 0) {
    for (const event of events) countsBySeverity[event.severity] = (countsBySeverity[event.severity] ?? 0) + 1;
  }
  return {
    events: [...events].sort((a, b) => (a.elapsed_seconds ?? 0) - (b.elapsed_seconds ?? 0) || (a.occurred_at < b.occurred_at ? -1 : 1)),
    countsByType,
    countsBySeverity,
    policy: isRecord(record.policy) ? (record.policy as SessionIntegrityView["policy"]) : null,
  };
}

// -------------------------------------------------------------------- reviews

export function normalizeReviews(raw: unknown): ReviewDecision[] {
  return asList<ReviewDecision>(raw, ["reviews", "decisions"])
    .filter((r) => isRecord(r) && typeof r.decision === "string")
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}
