/**
 * Live monitor state. `GET /sessions/{id}/live` streams Server-Sent Events:
 * session-bus events (evaluation.ready, integrity.event, report.ready,
 * coding.*) plus periodic state digests. Event names and payload nesting are
 * normalised here so the monitor only deals with one shape. Unknown events
 * are ignored; every recognised one updates `lastEventAt`.
 */

import { humanize } from "@/lib/format";
import { INTEGRITY_EVENT_LABELS, labelFor } from "@/lib/labels";
import type { ClaimStatus, CompetencyDigest, IntegrityEventType, IntegritySeverity, SessionStatus, TurnState } from "@/lib/types";

import { isRecord, numberOrNull, stringOrNull } from "../api/normalize";
import type { IntegrityEventView, LiveActivity, LiveEvent, LiveState, LiveTranscriptLine } from "../api/types";

/** Named SSE events the client subscribes to (unnamed events arrive as "message"). */
export const LIVE_EVENT_NAMES = [
  "state",
  "digest",
  "state.digest",
  "session.state",
  "snapshot",
  "status",
  "session.status",
  "session.ended",
  "session.closing",
  "session.ready",
  "scores.finalized",
  "ended",
  "end",
  "turn.state",
  "transcript",
  "transcript.final",
  "transcript.partial",
  "question",
  "question.asked",
  "ai.utterance.start",
  "ai.utterance.end",
  "evaluation.ready",
  "integrity.event",
  "integrity.warning",
  "coding.start",
  "coding.end",
  "coding.run_result",
  "coding.hint",
  "coding.evaluated",
  "report.ready",
  "heartbeat",
  "ping",
] as const;

const ALIASES: Record<string, string> = {
  state: "state",
  digest: "state",
  "state.digest": "state",
  state_digest: "state",
  "session.state": "state",
  "session.digest": "state",
  snapshot: "state",
  status: "status",
  "session.status": "status",
  "session.ended": "ended",
  "session.closing": "ended",
  "session.ready": "ready",
  "scores.finalized": "scores.finalized",
  ended: "ended",
  end: "ended",
  "turn.state": "turn",
  transcript: "transcript.final",
  "transcript.final": "transcript.final",
  "transcript.partial": "transcript.partial",
  question: "question",
  "question.asked": "question",
  "ai.utterance.start": "question",
  "ai.utterance.end": "utterance.end",
  "evaluation.ready": "evaluation.ready",
  integrity: "integrity.event",
  "integrity.event": "integrity.event",
  "integrity.warning": "integrity.warning",
  "coding.start": "coding.start",
  "coding.end": "coding.end",
  "coding.run_result": "coding.run",
  "coding.hint": "coding.hint",
  "coding.evaluated": "coding.evaluated",
  "report.ready": "report.ready",
  heartbeat: "heartbeat",
  ping: "heartbeat",
  keepalive: "heartbeat",
};

export function canonicalType(type: string): string | null {
  return ALIASES[type] ?? null;
}

/** Parses one SSE message. Returns null for malformed or unrecognised events. */
export function parseLiveMessage(eventName: string, data: string, receivedAt = Date.now()): LiveEvent | null {
  let payload: Record<string, unknown> = {};
  const trimmed = (data ?? "").trim();
  if (trimmed) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (!isRecord(parsed)) return null;
      payload = parsed;
    } catch {
      return null;
    }
  }
  const rawType = eventName && eventName !== "message" ? eventName : (stringOrNull(payload.type) ?? stringOrNull(payload.event) ?? (trimmed ? "" : "heartbeat"));
  const type = canonicalType(rawType);
  if (!type) return null;
  // Bus envelopes may nest the event body under "data" or "payload".
  const nested = isRecord(payload.data) ? payload.data : isRecord(payload.payload) ? payload.payload : null;
  return { type, payload: nested ? { ...payload, ...nested } : payload, receivedAt };
}

export const initialLiveState: LiveState = {
  status: null,
  turnState: null,
  elapsedSeconds: null,
  remainingSeconds: null,
  clockAt: null,
  questionCount: null,
  currentQuestion: null,
  currentSegment: null,
  competencies: [],
  previousScores: {},
  claims: [],
  transcript: [],
  partials: {},
  integrity: [],
  activity: [],
  dataVersion: 0,
  lastEventAt: null,
  ended: false,
};

const MAX_TRANSCRIPT = 300;
const MAX_ACTIVITY = 40;
const TERMINAL: readonly SessionStatus[] = ["completing", "completed", "aborted", "failed"];

function pickDigest(payload: Record<string, unknown>): Record<string, unknown> {
  const known = ["turn_state", "competencies", "elapsed_s", "elapsed_seconds", "question_count", "remaining_s", "status", "current_question"];
  for (const candidate of [payload.state_digest, payload.digest, payload.state, payload.session]) {
    if (isRecord(candidate) && known.some((k) => k in candidate)) return { ...payload, ...candidate };
  }
  return payload;
}

function normalizeCompetency(raw: unknown): CompetencyDigest | null {
  if (!isRecord(raw)) return null;
  const key = stringOrNull(raw.key) ?? stringOrNull(raw.competency_key);
  if (!key) return null;
  return {
    key,
    name: stringOrNull(raw.name) ?? stringOrNull(raw.label),
    weight: numberOrNull(raw.weight) ?? undefined,
    required: typeof raw.required === "boolean" ? raw.required : undefined,
    score_estimate: numberOrNull(raw.score_estimate) ?? numberOrNull(raw.score),
    confidence: numberOrNull(raw.confidence) ?? 0,
    evidence_count: numberOrNull(raw.evidence_count) ?? 0,
    sufficient_evidence: raw.sufficient_evidence === true || raw.sufficient === true,
  };
}

function competencyList(raw: unknown): CompetencyDigest[] | null {
  if (Array.isArray(raw)) return raw.map(normalizeCompetency).filter((c): c is CompetencyDigest => !!c);
  if (isRecord(raw)) {
    return Object.entries(raw)
      .map(([key, value]) => normalizeCompetency(isRecord(value) ? { key, ...value } : null))
      .filter((c): c is CompetencyDigest => !!c);
  }
  return null;
}

function activity(event: LiveEvent, title: string, tone: LiveActivity["tone"], detail?: string): LiveActivity {
  return { id: `${event.type}-${event.receivedAt}-${Math.random().toString(36).slice(2, 8)}`, type: event.type, at: event.receivedAt, title, detail, tone };
}

function pushActivity(state: LiveState, entry: LiveActivity): LiveActivity[] {
  return [entry, ...state.activity].slice(0, MAX_ACTIVITY);
}

function toIntegrityEvent(payload: Record<string, unknown>, receivedAt: number): IntegrityEventView | null {
  const body = isRecord(payload.event) ? payload.event : payload;
  const type = (stringOrNull(body.event_type) ?? stringOrNull(body.integrity_type) ?? stringOrNull(body.kind) ?? (body.type !== "integrity.event" ? stringOrNull(body.type) : null)) as IntegrityEventType | null;
  if (!type) return null;
  const id = stringOrNull(body.id) ?? stringOrNull(body.event_id) ?? `live-${type}-${receivedAt}`;
  return {
    id,
    session_id: stringOrNull(body.session_id) ?? "",
    organization_id: stringOrNull(body.organization_id) ?? "",
    type,
    source: (stringOrNull(body.source) ?? "browser") as IntegrityEventView["source"],
    severity: (stringOrNull(body.severity) ?? "info") as IntegritySeverity,
    confidence: numberOrNull(body.confidence),
    occurred_at: stringOrNull(body.occurred_at) ?? new Date(receivedAt).toISOString(),
    elapsed_seconds: numberOrNull(body.elapsed_seconds),
    duration_ms: numberOrNull(body.duration_ms),
    count: numberOrNull(body.count) ?? 1,
    details: isRecord(body.details) ? body.details : {},
    evidence_media_id: stringOrNull(body.evidence_media_id),
    action: (stringOrNull(body.action) ?? "log_only") as IntegrityEventView["action"],
    review_status: (stringOrNull(body.review_status) ?? "unreviewed") as IntegrityEventView["review_status"],
    reviewed_by_id: null,
    reviewed_at: null,
    review_note: null,
    created_at: stringOrNull(body.created_at) ?? new Date(receivedAt).toISOString(),
  };
}

function upsertLine(lines: LiveTranscriptLine[], line: LiveTranscriptLine): LiveTranscriptLine[] {
  const index = lines.findIndex((l) => l.id === line.id);
  const next = index === -1 ? [...lines, line] : lines.map((l, i) => (i === index ? line : l));
  return next.length > MAX_TRANSCRIPT ? next.slice(next.length - MAX_TRANSCRIPT) : next;
}

export function liveReducer(state: LiveState, event: LiveEvent): LiveState {
  const p = event.payload;
  const base: LiveState = { ...state, lastEventAt: event.receivedAt };

  switch (event.type) {
    case "heartbeat":
      return base;

    case "state": {
      const d = pickDigest(p);
      const competencies = competencyList(d.competencies);
      const previousScores = { ...state.previousScores };
      if (competencies) {
        for (const c of competencies) {
          const before = state.competencies.find((x) => x.key === c.key);
          if (before && before.score_estimate !== null && c.score_estimate !== null && Math.abs(before.score_estimate - c.score_estimate) >= 0.05) previousScores[c.key] = before.score_estimate;
        }
      }
      // `session_status` is the stored session status; `status` mirrors the live interview state.
      const status = (stringOrNull(d.session_status) as SessionStatus | null) ?? (stringOrNull(d.status) as SessionStatus | null) ?? state.status;
      const currentText = isRecord(d.current_question) ? stringOrNull(d.current_question.text) : stringOrNull(d.current_question) ?? stringOrNull(d.current_question_text);
      const currentId = isRecord(d.current_question) ? stringOrNull(d.current_question.id) : stringOrNull(d.current_question_id);
      const claims = Array.isArray(d.claims)
        ? d.claims
            .filter(isRecord)
            .map((c) => ({ id: stringOrNull(c.id) ?? "", claim: stringOrNull(c.claim) ?? "", status: (stringOrNull(c.status) ?? "unverified") as ClaimStatus }))
            .filter((c) => c.claim)
        : state.claims;
      const elapsed = numberOrNull(d.elapsed_s) ?? numberOrNull(d.elapsed_seconds);
      const remaining = numberOrNull(d.remaining_s) ?? numberOrNull(d.remaining_seconds);
      // Recent conversation (last turns) arrives with each digest: merge it into the stream.
      let transcript = state.transcript;
      if (Array.isArray(d.conversation)) {
        for (const turn of d.conversation) {
          if (!isRecord(turn)) continue;
          const text = stringOrNull(turn.text);
          if (!text) continue;
          const speaker = (stringOrNull(turn.speaker) ?? "candidate") as LiveTranscriptLine["speaker"];
          const at = numberOrNull(turn.at_s);
          transcript = upsertLine(transcript, {
            id: `conv-${speaker}-${at ?? text.slice(0, 24)}`,
            speaker: speaker === "interviewer" || speaker === "candidate" || speaker === "system" ? speaker : "candidate",
            text,
            startMs: at === null ? null : Math.round(at * 1000),
            partial: false,
            interrupted: turn.interrupted === true,
          });
        }
      }
      const partials = { ...state.partials };
      if (typeof d.partial_transcript === "string") {
        const draft = d.partial_transcript.trim();
        if (draft) partials.candidate = { id: "partial-candidate", speaker: "candidate", text: draft, startMs: null, partial: true, interrupted: false };
        else delete partials.candidate;
      }
      return {
        ...base,
        transcript,
        partials,
        status,
        turnState: (stringOrNull(d.turn_state) as TurnState | null) ?? state.turnState,
        elapsedSeconds: elapsed ?? state.elapsedSeconds,
        remainingSeconds: remaining ?? state.remainingSeconds,
        clockAt: elapsed !== null || remaining !== null ? event.receivedAt : state.clockAt,
        questionCount: numberOrNull(d.question_count) ?? state.questionCount,
        currentQuestion: currentText || currentId ? { id: currentId ?? state.currentQuestion?.id ?? null, text: currentText ?? state.currentQuestion?.text ?? null } : state.currentQuestion,
        currentSegment: stringOrNull(d.current_segment) ?? stringOrNull(d.segment) ?? state.currentSegment,
        competencies: competencies ?? state.competencies,
        previousScores,
        claims,
        ended: state.ended || (status !== null && TERMINAL.includes(status)),
      };
    }

    case "ready":
      return { ...base, activity: pushActivity(state, activity(event, "Interview ready to start", "neutral")) };

    case "scores.finalized":
      return { ...base, activity: pushActivity(state, activity(event, "Final scores ready", "success")), dataVersion: state.dataVersion + 1 };

    case "status":
    case "ended": {
      const reason = stringOrNull(p.reason);
      const status =
        (stringOrNull(p.status) as SessionStatus | null) ?? (stringOrNull(p.session_status) as SessionStatus | null) ?? (event.type === "ended" ? (reason === "not_found" ? state.status : "completing") : state.status);
      const ended = event.type === "ended" || (status !== null && TERMINAL.includes(status));
      return {
        ...base,
        status,
        ended: state.ended || ended,
        turnState: ended ? "ended" : state.turnState,
        activity: ended && !state.ended ? pushActivity(state, activity(event, "Interview ended", "neutral", reason ? humanize(reason) : undefined)) : state.activity,
        dataVersion: ended ? state.dataVersion + 1 : state.dataVersion,
      };
    }

    case "turn":
      return { ...base, turnState: (stringOrNull(p.state) as TurnState | null) ?? (stringOrNull(p.turn_state) as TurnState | null) ?? state.turnState };

    case "transcript.final":
    case "transcript.partial": {
      const speaker = (stringOrNull(p.speaker) ?? "candidate") as LiveTranscriptLine["speaker"];
      const text = stringOrNull(p.text);
      if (!text) return base;
      const line: LiveTranscriptLine = {
        id: stringOrNull(p.segment_id) ?? stringOrNull(p.id) ?? `${speaker}-${event.receivedAt}`,
        speaker,
        text,
        startMs: numberOrNull(p.start_ms),
        partial: event.type === "transcript.partial",
        interrupted: p.interrupted === true,
      };
      if (line.partial) {
        if (speaker === "system") return base;
        return { ...base, partials: { ...state.partials, [speaker]: line } };
      }
      const partials = { ...state.partials };
      if (speaker !== "system") delete partials[speaker];
      return { ...base, transcript: upsertLine(state.transcript, line), partials };
    }

    case "utterance.end": {
      const text = stringOrNull(p.text);
      if (!text) return base;
      const line: LiveTranscriptLine = {
        id: stringOrNull(p.utterance_id) ?? `ai-${event.receivedAt}`,
        speaker: "interviewer",
        text,
        startMs: numberOrNull(p.start_ms),
        partial: false,
        interrupted: p.interrupted === true,
      };
      return { ...base, transcript: upsertLine(state.transcript, line) };
    }

    case "question": {
      const id = stringOrNull(p.question_id);
      const text = stringOrNull(p.text) ?? stringOrNull(p.question_text) ?? stringOrNull(p.question);
      const kind = stringOrNull(p.kind);
      // Acknowledgements and comments are not new questions.
      if (!id && !text) return base;
      if (kind && !["question", "followup", "intro"].includes(kind) && !id) return base;
      const sameQuestion = id !== null && state.currentQuestion?.id === id;
      return {
        ...base,
        currentQuestion: { id: id ?? state.currentQuestion?.id ?? null, text: text ?? (sameQuestion ? state.currentQuestion?.text ?? null : null) },
        dataVersion: sameQuestion ? state.dataVersion : state.dataVersion + 1,
      };
    }

    case "evaluation.ready": {
      const updates = competencyList(p.competencies) ?? [];
      const keys = updates.length ? updates.map((c) => c.key) : Array.isArray(p.competency_keys) ? p.competency_keys.filter((k): k is string => typeof k === "string") : [];
      let competencies = state.competencies;
      const previousScores = { ...state.previousScores };
      if (updates.length) {
        const byKey = new Map(state.competencies.map((c) => [c.key, c]));
        for (const update of updates) {
          const before = byKey.get(update.key);
          if (before && before.score_estimate !== null && update.score_estimate !== null && Math.abs(before.score_estimate - update.score_estimate) >= 0.05) previousScores[update.key] = before.score_estimate;
          byKey.set(update.key, { ...before, ...update, name: update.name ?? before?.name ?? null, weight: before?.weight ?? update.weight, required: before?.required ?? update.required });
        }
        competencies = [...byKey.values()];
      }
      const quality = stringOrNull(p.quality);
      return {
        ...base,
        competencies,
        previousScores,
        activity: pushActivity(
          state,
          activity(event, p.is_fallback === true ? "Answer evaluated (fallback)" : "Answer evaluated", p.is_fallback === true ? "warning" : "accent", [quality ? humanize(quality) : null, keys.length ? `updated ${keys.join(", ")}` : null].filter(Boolean).join(", ") || undefined),
        ),
        dataVersion: state.dataVersion + 1,
      };
    }

    case "integrity.event": {
      const item = toIntegrityEvent(p, event.receivedAt);
      if (!item) return base;
      const exists = state.integrity.some((e) => e.id === item.id);
      const integrity = exists ? state.integrity.map((e) => (e.id === item.id ? { ...e, ...item } : e)) : [item, ...state.integrity];
      const tone: LiveActivity["tone"] = item.severity === "high" || item.severity === "medium" ? "warning" : "neutral";
      return {
        ...base,
        integrity,
        activity: exists ? state.activity : pushActivity(state, activity(event, labelFor(INTEGRITY_EVENT_LABELS, item.type), tone, "Observation recorded for review")),
        dataVersion: state.dataVersion + 1,
      };
    }

    case "integrity.warning":
      return { ...base, activity: pushActivity(state, activity(event, "Candidate shown a reminder", "neutral", stringOrNull(p.message) ?? undefined)) };

    case "coding.start":
      return { ...base, turnState: "coding", activity: pushActivity(state, activity(event, "Coding exercise started", "accent")), dataVersion: state.dataVersion + 1 };

    case "coding.run": {
      const passed = numberOrNull(p.passed);
      const total = numberOrNull(p.total);
      return {
        ...base,
        activity: pushActivity(state, activity(event, "Code run", "neutral", passed !== null && total !== null ? `${passed} of ${total} tests passed` : undefined)),
      };
    }

    case "coding.hint":
      return {
        ...base,
        activity: pushActivity(state, activity(event, "Hint given", "warning", numberOrNull(p.level) !== null ? `Level ${numberOrNull(p.level)}` : undefined)),
        dataVersion: state.dataVersion + 1,
      };

    case "coding.end":
      return { ...base, activity: pushActivity(state, activity(event, "Coding exercise finished", "neutral")), dataVersion: state.dataVersion + 1 };

    case "coding.evaluated": {
      const passed = numberOrNull(p.tests_passed);
      const total = numberOrNull(p.tests_total);
      return {
        ...base,
        activity: pushActivity(state, activity(event, "Code evaluated", "accent", passed !== null && total !== null ? `${passed} of ${total} tests passed` : undefined)),
        dataVersion: state.dataVersion + 1,
      };
    }

    case "report.ready":
      return { ...base, activity: pushActivity(state, activity(event, "Report ready", "success")), dataVersion: state.dataVersion + 1 };

    default:
      return state;
  }
}

/** Elapsed seconds now, ticking forward from the last digest while the interview runs. */
export function projectedElapsed(state: Pick<LiveState, "elapsedSeconds" | "clockAt" | "status" | "turnState">, now: number): number | null {
  if (state.elapsedSeconds === null) return null;
  if (state.clockAt === null || state.status !== "live" || state.turnState === "paused") return state.elapsedSeconds;
  return state.elapsedSeconds + Math.max(0, (now - state.clockAt) / 1000);
}

export function projectedRemaining(state: Pick<LiveState, "remainingSeconds" | "clockAt" | "status" | "turnState">, now: number): number | null {
  if (state.remainingSeconds === null) return null;
  if (state.clockAt === null || state.status !== "live" || state.turnState === "paused") return state.remainingSeconds;
  return Math.max(0, state.remainingSeconds - Math.max(0, (now - state.clockAt) / 1000));
}
