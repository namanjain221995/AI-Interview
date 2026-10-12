/**
 * Interview room state: a pure reducer over server messages and local events,
 * wrapped in a vanilla Zustand store owned by the InterviewRuntime. Side
 * effects (audio, avatar, navigation) live in the runtime, never here, so the
 * reducer is deterministic and unit-tested.
 */
// One import path for zustand: the dev server pre-bundles "zustand" (a new deep entry
// would trigger a dependency re-optimisation and a full page reload mid-interview).
import { createStore, useStore, type StoreApi } from "zustand";

import {
  freezeActivity,
  hideActivityContent,
  normalizeIntake,
  reconcileActivity,
  reduceActivityEnd,
  reduceActivityPhase,
  reduceActivityStart,
  resumeActivityClock,
  type ActivityView,
  type HideReason,
  type IntakeView,
} from "@/features/intake/candidate/room/activityModel";
import type {
  ActivityPhase,
  AvatarBehaviorMsg,
  AvatarState,
  CodeLanguage,
  CodingQuestionPublic,
  CodingRunResultMsg,
  CodingState,
  IntegrityEventType,
  RunKind,
  ServerMessage,
  SessionStatus,
  TurnState,
  UtteranceKind,
} from "@/lib/realtime/protocol";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "reconnecting" | "offline" | "closed";

export interface ConnectionView {
  status: ConnectionStatus;
  attempt: number;
  reason: string | null;
  rttMs: number | null;
  reconnects: number;
  everConnected: boolean;
}

export interface SessionView {
  id: string;
  status: SessionStatus;
  turnState: TurnState;
  roundName: string;
  roundType: string;
  interviewerName: string;
  elapsedS: number;
  remainingS: number;
  timeLimitS: number;
  /** performance.now() when remainingS was last synchronised. */
  syncedAtMs: number;
  segment: string | null;
  questionCount: number;
  currentQuestion: string | null;
  showSubtitles: boolean;
  allowTypedAnswers: boolean;
  integrity: Record<string, unknown>;
  avatarState: AvatarState;
}

export type UtteranceMode = "pending" | "audio" | "browser" | "text";

export interface UtteranceView {
  id: string;
  kind: UtteranceKind;
  questionId: string | null;
  text: string;
  /** Words of `text` already heard by the candidate (audio/browser speech). */
  spokenWords: number;
  mode: UtteranceMode;
  ended: boolean;
  interrupted: boolean;
  delivered: boolean;
}

export interface CandidateSegment {
  id: string;
  text: string;
  final: boolean;
}

export interface CandidateTranscriptView {
  /** Interviewer utterances seen; a new answer starts a new epoch. */
  epoch: number;
  segmentEpoch: number;
  segments: CandidateSegment[];
}

export interface CodingHintView {
  level: number;
  text: string;
}

export interface CodingView {
  sessionId: string;
  question: CodingQuestionPublic;
  language: CodeLanguage;
  initialCode: string | null;
  timeLimitS: number;
  elapsedS: number;
  syncedAtMs: number;
  hintsAvailable: number;
  hintsUsed: number;
  hintsRemaining: number;
  hints: CodingHintView[];
  lastRun: CodingRunResultMsg | null;
  pendingRun: RunKind | null;
  hintPending: boolean;
  ended: CodingEndReason | null;
}

export type CodingEndReason = "submitted" | "time_limit" | "interviewer_moved_on" | "session_ended";

export interface NoticeView {
  id: string;
  level: "info" | "warning" | "error";
  code: string;
  message: string;
  at: number;
}

export interface IntegrityWarningView {
  id: string;
  eventType: IntegrityEventType | null;
  message: string;
  at: number;
}

export interface EndedView {
  reason: string;
  reportStatus: string;
  candidateReportAvailable: boolean;
}

export interface InterviewViewState {
  connection: ConnectionView;
  session: SessionView | null;
  /**
   * The interviewer utterance the candidate is hearing (or heard last): drives captions.
   * Derived from `utterances` after every change.
   */
  utterance: UtteranceView | null;
  /**
   * Utterances the server started, oldest first. The server pipelines speech (the
   * introduction and the first question, an acknowledgement and the reply), so the
   * next utterance usually starts while the previous one is still playing.
   */
  utterances: UtteranceView[];
  /** Last interviewer question text, kept for the subtitle between utterances. */
  lastInterviewerText: string;
  candidate: CandidateTranscriptView;
  coding: CodingView | null;
  behavior: AvatarBehaviorMsg | null;
  notices: NoticeView[];
  warnings: IntegrityWarningView[];
  sttDegraded: boolean;
  /**
   * The candidate has spoken in this room (the server heard speech on their floor or
   * returned a transcript). Speech-recognition trouble is only surfaced after this,
   * never during the introduction or before the first answer.
   */
  candidateSpoke: boolean;
  ttsDegraded: boolean;
  ended: EndedView | null;
  fatalError: { code: string; message: string } | null;
  begun: boolean;
  /**
   * The server deferred the start until the interview plan is ready (notice `preparing`);
   * it begins by itself. The clock is stopped meanwhile.
   */
  preparing: boolean;
  /** Intake interviews: stage progress from session.state (null for every other round type). */
  intake: IntakeView | null;
  /** Intake interviews: the reading or speaking exercise on screen (content lives only here, in memory). */
  activity: ActivityView | null;
}

export type LocalAction =
  | { type: "connection"; status: ConnectionStatus; attempt?: number; reason?: string | null; at: number }
  | { type: "rtt"; ms: number }
  | { type: "seed"; session: Partial<SessionView> & { id: string } }
  | { type: "begun" }
  | { type: "begin.failed" }
  | { type: "playback.chunk_started"; utteranceId: string; text: string }
  | { type: "playback.word"; utteranceId: string }
  | { type: "playback.done"; utteranceId: string; interrupted: boolean }
  | { type: "utterance.mode"; utteranceId: string; mode: UtteranceMode }
  | { type: "candidate.typed"; text: string; at: number }
  | { type: "coding.run_requested"; kind: RunKind }
  | { type: "coding.hint_requested" }
  | { type: "coding.language"; language: CodeLanguage }
  | { type: "notice.local"; code: string; level: NoticeView["level"]; message: string; at: number }
  | { type: "notice.dismiss"; code: string }
  | { type: "warning.dismiss"; id: string }
  | { type: "warning.local"; eventType: IntegrityEventType | null; message: string; at: number }
  /** Remove the read-and-relay passage now (countdown zero, "I'm ready", lost connection). */
  | { type: "activity.hide_local"; reason: HideReason; activityId?: string; at: number }
  | { type: "activity.finish_requested"; activityId: string; phase: ActivityPhase; at: number }
  /** The brief "Done" after activity.end is over: close the panel. */
  | { type: "activity.dismiss"; activityId: string };

export type InterviewAction = { type: "server"; msg: ServerMessage; at: number } | LocalAction;

const MAX_NOTICES = 4;

/** Intake activity errors shown as calm information (server wording first, these as fallbacks). */
const ACTIVITY_HINTS: Record<string, string> = {
  finish_not_available: "You can finish in a few seconds.",
  activity_in_progress: "Please wait for the exercise to continue.",
  typed_not_allowed_for_reading: "The reading exercise needs your voice: please read the passage aloud.",
};
const MAX_WARNINGS = 3;
const MAX_UTTERANCES = 6;

export function initialInterviewState(): InterviewViewState {
  return {
    connection: { status: "idle", attempt: 0, reason: null, rttMs: null, reconnects: 0, everConnected: false },
    session: null,
    utterance: null,
    utterances: [],
    lastInterviewerText: "",
    candidate: { epoch: 0, segmentEpoch: 0, segments: [] },
    coding: null,
    behavior: null,
    notices: [],
    warnings: [],
    sttDegraded: false,
    candidateSpoke: false,
    ttsDegraded: false,
    ended: null,
    fatalError: null,
    begun: false,
    preparing: false,
    intake: null,
    activity: null,
  };
}

export function wordCount(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

/** Remaining interview time, honouring a paused clock. */
export function remainingSeconds(state: Pick<InterviewViewState, "session" | "connection"> & { preparing?: boolean }, nowMs: number): number | null {
  const s = state.session;
  if (!s || !(s.timeLimitS > 0 || s.remainingS > 0)) return null;
  const running = clockRunning(state);
  const value = running ? s.remainingS - (nowMs - s.syncedAtMs) / 1000 : s.remainingS;
  return Math.max(0, value);
}

export function clockRunning(state: Pick<InterviewViewState, "session" | "connection"> & { preparing?: boolean }): boolean {
  const s = state.session;
  return Boolean(
    s && s.status === "live" && s.turnState !== "paused" && s.turnState !== "ended" && state.connection.status === "connected" && !state.preparing,
  );
}

export function codingRemainingSeconds(coding: CodingView | null, nowMs: number, running: boolean): number | null {
  if (!coding || !(coding.timeLimitS > 0)) return null;
  const elapsed = coding.elapsedS + (running && !coding.ended ? (nowMs - coding.syncedAtMs) / 1000 : 0);
  return Math.max(0, coding.timeLimitS - elapsed);
}

function codingFromState(prev: CodingView | null, coding: CodingState, at: number): CodingView {
  const same = prev && prev.sessionId === coding.coding_session_id ? prev : null;
  const fromServer = (coding.hint_texts ?? []).map((text, index) => ({ level: index + 1, text }));
  const hints = mergeHints(same?.hints ?? [], fromServer);
  const available = Number.isFinite(coding.hints_available) ? coding.hints_available : 3;
  const used = Math.max(coding.hints_used ?? 0, hints.length);
  return {
    sessionId: coding.coding_session_id,
    question: coding.question,
    language: coding.language,
    initialCode: coding.code ?? same?.initialCode ?? null,
    timeLimitS: coding.time_limit_s,
    elapsedS: coding.elapsed_s ?? 0,
    syncedAtMs: at,
    hintsAvailable: available,
    hintsUsed: used,
    hintsRemaining: Math.max(0, available - used),
    hints,
    lastRun: same?.lastRun ?? null,
    pendingRun: same?.pendingRun ?? null,
    hintPending: false,
    ended: same?.ended ?? null,
  };
}

function mergeHints(a: CodingHintView[], b: CodingHintView[]): CodingHintView[] {
  const byLevel = new Map<number, CodingHintView>();
  for (const h of [...a, ...b]) byLevel.set(h.level, h);
  return [...byLevel.values()].sort((x, y) => x.level - y.level);
}

function pushNotice(list: NoticeView[], notice: NoticeView): NoticeView[] {
  return [...list.filter((n) => n.code !== notice.code), notice].slice(-MAX_NOTICES);
}

function withSession(state: InterviewViewState, patch: Partial<SessionView>): InterviewViewState {
  if (!state.session) return state;
  return { ...state, session: { ...state.session, ...patch } };
}

function candidateWithSegment(candidate: CandidateTranscriptView, segment: CandidateSegment): CandidateTranscriptView {
  const fresh = candidate.segmentEpoch !== candidate.epoch;
  const segments = fresh ? [] : candidate.segments;
  const index = segments.findIndex((s) => s.id === segment.id);
  const next = index === -1 ? [...segments, segment] : segments.map((s, i) => (i === index ? { ...s, text: segment.text, final: segment.final || s.final } : s));
  return { ...candidate, segmentEpoch: candidate.epoch, segments: next.slice(-24) };
}

/** Has the candidate heard (or been shown) this utterance in full? */
function finished(u: UtteranceView): boolean {
  return u.delivered || u.interrupted;
}

/**
 * The utterance captions follow: the oldest one not yet heard in full (speech is
 * played strictly in order), else the most recent one.
 */
export function currentUtterance(list: readonly UtteranceView[]): UtteranceView | null {
  return list.find((u) => !finished(u)) ?? list[list.length - 1] ?? null;
}

/**
 * Insert a started utterance where the candidate will hear it. Normally that is last (speech plays in order),
 * but an acknowledgement ("Okay.", "One moment.") is played at once from a cache: when it starts while the reply
 * was already announced but has no audio yet (the model is still writing it), it is heard first. Placed after
 * that silent reply it took over the caption, marked the reply as heard when it played, and stayed on screen
 * instead of the question.
 */
function placeUtterance(list: readonly UtteranceView[], utterance: UtteranceView): UtteranceView[] {
  const rest = list.filter((u) => u.id !== utterance.id);
  if (utterance.kind !== "acknowledgment") return [...rest, utterance];
  let at = rest.length;
  while (at > 0 && !finished(rest[at - 1]) && rest[at - 1].mode === "pending" && rest[at - 1].spokenWords === 0) at--;
  return [...rest.slice(0, at), utterance, ...rest.slice(at)];
}

function withUtterances(state: InterviewViewState, utterances: UtteranceView[]): InterviewViewState {
  const list = utterances.slice(-MAX_UTTERANCES);
  return { ...state, utterances: list, utterance: currentUtterance(list) };
}

/** Update one utterance by id (no-op for unknown ids). */
function patchUtterance(state: InterviewViewState, id: string, patch: (u: UtteranceView) => UtteranceView): InterviewViewState {
  if (!state.utterances.some((u) => u.id === id)) return state;
  return withUtterances(
    state,
    state.utterances.map((u) => (u.id === id ? patch(u) : u)),
  );
}

/** Mark as heard every ended utterance (the server only moves on once delivery is confirmed or timed out). */
function settleEnded(state: InterviewViewState): InterviewViewState {
  if (!state.utterances.some((u) => u.ended && !finished(u))) return state;
  return withUtterances(
    state,
    state.utterances.map((u) => (u.ended && !finished(u) ? { ...u, delivered: true, spokenWords: Number.MAX_SAFE_INTEGER } : u)),
  );
}

/**
 * The runtime considers a session live as soon as it starts taking turns, but
 * only broadcasts session.state once the opening utterance is done. Mirror the
 * server's own notion of "live" (and start the clock) from the first turn.
 */
function liveOnceStarted(session: SessionView, active: boolean, at: number): Partial<SessionView> {
  if (!active || !["created", "preparing", "ready"].includes(session.status)) return {};
  return { status: "live", syncedAtMs: at };
}

function reduceServer(state: InterviewViewState, msg: ServerMessage, at: number): InterviewViewState {
  switch (msg.type) {
    case "session.state": {
      const session: SessionView = {
        id: msg.session_id,
        status: msg.status,
        turnState: msg.turn_state,
        roundName: msg.round_name ?? state.session?.roundName ?? "",
        roundType: msg.round_type ?? state.session?.roundType ?? "",
        interviewerName: msg.interviewer_name || state.session?.interviewerName || "Interviewer",
        elapsedS: msg.elapsed_s ?? 0,
        remainingS: msg.remaining_s ?? 0,
        timeLimitS: msg.time_limit_s ?? 0,
        syncedAtMs: at,
        segment: msg.segment ?? null,
        questionCount: msg.question_count ?? 0,
        currentQuestion: msg.current_question ?? null,
        showSubtitles: msg.show_subtitles ?? true,
        allowTypedAnswers: msg.allow_typed_answers ?? true,
        integrity: msg.integrity ?? {},
        avatarState: msg.avatar_state ?? "IDLE",
      };
      const coding = msg.coding ? codingFromState(state.coding, msg.coding, at) : state.coding?.ended ? state.coding : null;
      // Intake: stage progress, and the activity reconciled with the content-free snapshot.
      const intake = msg.intake === undefined ? state.intake : normalizeIntake(msg.intake);
      const activity = intake ? reconcileActivity(state.activity, intake.activity, at) : msg.intake === null ? null : state.activity;
      return {
        ...state,
        session,
        coding,
        intake,
        activity,
        lastInterviewerText: state.lastInterviewerText || msg.current_question || "",
      };
    }
    case "turn.state": {
      if (!state.session) return state;
      const patch: Partial<SessionView> = { turnState: msg.state, ...liveOnceStarted(state.session, msg.state !== "idle" && msg.state !== "ended", at) };
      let next = withSession(state, patch);
      // Deferred start: the engine waits for the interview plan and begins by itself.
      if (msg.state === "idle" && msg.reason === "preparing") next = { ...next, preparing: true };
      else if (["ai_speaking", "listening", "candidate_speaking", "coding"].includes(msg.state)) next = { ...next, preparing: false };
      // The floor is the candidate's: whatever the interviewer said has been delivered.
      if (msg.state === "listening" || msg.state === "candidate_speaking") next = settleEnded(next);
      if (msg.state === "candidate_speaking" && !next.candidateSpoke) next = { ...next, candidateSpoke: true };
      return next;
    }
    case "avatar.behavior":
      return { ...state, behavior: msg, ...(state.session ? { session: { ...state.session, avatarState: msg.state } } : {}) };
    case "ai.utterance.start": {
      const utterance: UtteranceView = {
        id: msg.utterance_id,
        kind: msg.kind,
        questionId: msg.question_id ?? null,
        text: "",
        spokenWords: 0,
        mode: state.ttsDegraded ? "browser" : "pending",
        ended: false,
        interrupted: false,
        delivered: false,
      };
      const next = withUtterances(state, placeUtterance(state.utterances, utterance));
      return {
        ...next,
        ...(state.session ? { session: { ...state.session, ...liveOnceStarted(state.session, true, at) } } : {}),
        behavior: msg.behavior ?? state.behavior,
        candidate: { ...state.candidate, epoch: state.candidate.epoch + 1 },
        preparing: false,
      };
    }
    case "ai.text.delta":
      return patchUtterance(state, msg.utterance_id, (u) => ({ ...u, text: u.text + msg.text }));
    case "tts.chunk":
      return patchUtterance(state, msg.utterance_id, (u) => (u.mode === "audio" ? u : { ...u, mode: "audio" }));
    case "tts.fallback": {
      const next = { ...state, ttsDegraded: true };
      return patchUtterance(next, msg.utterance_id, (u) => ({ ...u, mode: u.mode === "text" ? "text" : "browser" }));
    }
    case "ai.utterance.end": {
      const target = state.utterances.find((u) => u.id === msg.utterance_id);
      if (!target) return state;
      const text = msg.text?.trim() ? msg.text : target.text;
      const next = patchUtterance(state, target.id, (u) => ({ ...u, text, ended: true, interrupted: msg.interrupted || u.interrupted }));
      const isQuestionLike = target.kind !== "acknowledgment";
      return { ...next, lastInterviewerText: isQuestionLike && text.trim() ? text : state.lastInterviewerText };
    }
    case "barge_in": {
      // The candidate spoke over the interviewer: this utterance is cut short and anything
      // queued after it was never heard (the server cancels it); anything before it had
      // already been heard, since speech plays in order.
      const index = state.utterances.findIndex((u) => u.id === msg.utterance_id);
      if (index === -1) return state;
      return withUtterances(
        state,
        state.utterances.slice(0, index + 1).map((u, i) =>
          i === index ? (u.delivered ? u : { ...u, interrupted: true }) : finished(u) ? u : { ...u, delivered: true, spokenWords: Number.MAX_SAFE_INTEGER },
        ),
      );
    }
    case "transcript.partial":
    case "transcript.final": {
      if (msg.speaker === "interviewer") return state;
      const candidate = candidateWithSegment(state.candidate, { id: msg.segment_id, text: msg.text, final: msg.type === "transcript.final" });
      return { ...state, candidate, candidateSpoke: state.candidateSpoke || Boolean(msg.text.trim()) };
    }
    case "coding.start": {
      // The same exercise re-announced (e.g. after code.language) keeps the last run and hints.
      const coding = codingFromState(state.coding, msg.coding, at);
      return { ...state, coding };
    }
    case "coding.run_result": {
      if (!state.coding || state.coding.sessionId !== msg.coding_session_id) return state;
      return { ...state, coding: { ...state.coding, lastRun: msg, pendingRun: null } };
    }
    case "coding.hint": {
      if (!state.coding || state.coding.sessionId !== msg.coding_session_id) return state;
      const hints = mergeHints(state.coding.hints, [{ level: msg.level, text: msg.text }]);
      return {
        ...state,
        coding: {
          ...state.coding,
          hints,
          hintsUsed: msg.hints_used,
          hintsRemaining: Math.max(0, msg.hints_remaining),
          hintPending: false,
        },
      };
    }
    case "coding.end": {
      if (!state.coding || state.coding.sessionId !== msg.coding_session_id) return state;
      return { ...state, coding: { ...state.coding, ended: msg.reason, pendingRun: null, hintPending: false } };
    }
    case "integrity.warning": {
      const warning: IntegrityWarningView = { id: `w${msg.seq || at}`, eventType: msg.event_type ?? null, message: msg.message, at };
      return { ...state, warnings: [...state.warnings.filter((w) => w.eventType !== warning.eventType), warning].slice(-MAX_WARNINGS) };
    }
    case "notice": {
      const code = msg.code;
      let sttDegraded = state.sttDegraded;
      let ttsDegraded = state.ttsDegraded;
      if (/^stt_(degraded|unavailable|failed)/.test(code)) sttDegraded = true;
      if (/^stt_(recovered|restored|ok)/.test(code)) sttDegraded = false;
      if (/^tts_(degraded|unavailable|failed)/.test(code)) ttsDegraded = true;
      if (/^tts_(recovered|restored|ok)/.test(code)) ttsDegraded = false;
      const notices = /_(recovered|restored|ok)$/.test(code)
        ? state.notices.filter((n) => !n.code.startsWith(code.split("_")[0]!))
        : pushNotice(state.notices, { id: `n${msg.seq || at}`, level: msg.level ?? "info", code, message: msg.message, at });
      return { ...state, notices, sttDegraded, ttsDegraded, preparing: code === "preparing" ? true : state.preparing };
    }
    case "error": {
      if (msg.recoverable === false) return { ...state, fatalError: { code: msg.code, message: msg.message } };
      // Intake activity refusals are calm hints, not failures; a refused finish frees the button again.
      const hint = ACTIVITY_HINTS[msg.code];
      if (hint) {
        const activity = msg.code === "finish_not_available" && state.activity ? { ...state.activity, finishRequested: false } : state.activity;
        return { ...state, activity, notices: pushNotice(state.notices, { id: `e${msg.seq || at}`, level: "info", code: msg.code, message: msg.message?.trim() || hint, at }) };
      }
      return { ...state, notices: pushNotice(state.notices, { id: `e${msg.seq || at}`, level: "error", code: msg.code, message: msg.message, at }) };
    }
    case "session.ended": {
      const ended: EndedView = {
        reason: msg.reason,
        reportStatus: msg.report_status ?? "pending",
        candidateReportAvailable: Boolean(msg.candidate_report_available),
      };
      const coding = state.coding && !state.coding.ended ? { ...state.coding, ended: "session_ended" as const } : state.coding;
      return { ...withSession(state, { turnState: "ended", status: state.session?.status === "live" ? "completing" : (state.session?.status ?? "completing") }), ended, coding, activity: null };
    }
    case "heartbeat.ack":
      return state;
    case "activity.start":
      return { ...state, activity: reduceActivityStart(state.activity, msg, at) };
    case "activity.phase":
      return { ...state, activity: reduceActivityPhase(state.activity, msg, at) };
    case "activity.end":
      return { ...state, activity: reduceActivityEnd(state.activity, msg, at) };
    default:
      return state;
  }
}

export function reduceInterview(state: InterviewViewState, action: InterviewAction): InterviewViewState {
  switch (action.type) {
    case "server":
      return reduceServer(state, action.msg, action.at);
    case "connection": {
      const prev = state.connection;
      const connected = action.status === "connected";
      let session = state.session;
      // Leaving "connected" freezes the clock at its current value (the server pauses it too).
      if (prev.status === "connected" && !connected && session) {
        const remaining = remainingSeconds(state, action.at) ?? session.remainingS;
        session = { ...session, remainingS: remaining, syncedAtMs: action.at };
      }
      if (connected && session) session = { ...session, syncedAtMs: action.at };
      // The activity clock pauses with the interview clock; the relay passage never outlives the socket.
      let activity = state.activity;
      if (!connected && prev.status === "connected") activity = freezeActivity(activity, action.at);
      else if (!connected) activity = hideActivityContent(activity, "disconnect", action.at);
      else if (prev.status !== "connected") activity = resumeActivityClock(activity, action.at);
      return {
        ...state,
        session,
        activity,
        connection: {
          ...prev,
          status: action.status,
          attempt: action.attempt ?? (connected ? 0 : prev.attempt),
          reason: action.reason ?? (connected ? null : prev.reason),
          everConnected: prev.everConnected || connected,
          reconnects: prev.reconnects + (connected && prev.everConnected && prev.status !== "connected" ? 1 : 0),
        },
      };
    }
    case "rtt":
      return { ...state, connection: { ...state.connection, rttMs: Math.round(action.ms) } };
    case "seed": {
      if (state.session) return state;
      const seed: SessionView = {
        status: "created",
        turnState: "idle",
        roundName: "",
        roundType: "",
        interviewerName: "Interviewer",
        elapsedS: 0,
        remainingS: 0,
        timeLimitS: 0,
        syncedAtMs: 0,
        segment: null,
        questionCount: 0,
        currentQuestion: null,
        showSubtitles: true,
        allowTypedAnswers: true,
        integrity: {},
        avatarState: "IDLE",
        ...action.session,
      };
      return { ...state, session: seed };
    }
    case "begun":
      return { ...state, begun: true };
    case "begin.failed":
      // Device setup or the first connection failed before a socket existed: back to the lobby (S5).
      return { ...state, begun: false, connection: { ...state.connection, status: "idle" } };
    case "playback.chunk_started": {
      const index = state.utterances.findIndex((u) => u.id === action.utteranceId);
      if (index === -1) return state;
      // Audio plays strictly in order: anything older has been heard in full.
      return withUtterances(
        state,
        state.utterances.map((u, i) =>
          i === index
            ? { ...u, mode: "audio", spokenWords: u.spokenWords + wordCount(action.text) }
            : i < index && !finished(u)
              ? { ...u, delivered: true, spokenWords: Number.MAX_SAFE_INTEGER }
              : u,
        ),
      );
    }
    case "playback.word":
      return patchUtterance(state, action.utteranceId, (u) => ({ ...u, spokenWords: u.spokenWords + 1 }));
    case "playback.done":
      return patchUtterance(state, action.utteranceId, (u) => ({
        ...u,
        delivered: true,
        interrupted: u.interrupted || action.interrupted,
        spokenWords: action.interrupted ? u.spokenWords : Number.MAX_SAFE_INTEGER,
      }));
    case "utterance.mode":
      return patchUtterance(state, action.utteranceId, (u) => ({ ...u, mode: action.mode, spokenWords: action.mode === "text" ? Number.MAX_SAFE_INTEGER : u.spokenWords }));
    case "candidate.typed": {
      const candidate = candidateWithSegment(state.candidate, { id: `typed-${action.at}`, text: action.text, final: true });
      return { ...state, candidate };
    }
    case "coding.run_requested":
      return state.coding ? { ...state, coding: { ...state.coding, pendingRun: action.kind } } : state;
    case "coding.hint_requested":
      return state.coding ? { ...state, coding: { ...state.coding, hintPending: true } } : state;
    case "coding.language":
      return state.coding ? { ...state, coding: { ...state.coding, language: action.language } } : state;
    case "notice.local":
      return { ...state, notices: pushNotice(state.notices, { id: `l${action.at}`, level: action.level, code: action.code, message: action.message, at: action.at }) };
    case "notice.dismiss":
      return { ...state, notices: state.notices.filter((n) => n.code !== action.code) };
    case "warning.local": {
      const warning: IntegrityWarningView = { id: `lw${action.at}`, eventType: action.eventType, message: action.message, at: action.at };
      return { ...state, warnings: [...state.warnings.filter((w) => w.eventType !== action.eventType), warning].slice(-MAX_WARNINGS) };
    }
    case "warning.dismiss":
      return { ...state, warnings: state.warnings.filter((w) => w.id !== action.id) };
    case "activity.hide_local":
      if (action.activityId && state.activity?.id !== action.activityId) return state;
      return { ...state, activity: hideActivityContent(state.activity, action.reason, action.at) };
    case "activity.finish_requested": {
      const a = state.activity;
      if (!a || a.id !== action.activityId || a.phase !== action.phase || a.status !== "running") return state;
      // The passage stays until the server ends the phase (immediately on "I'm ready"): a refused finish
      // (finish_not_available) must not cost reading time.
      return { ...state, activity: { ...a, finishRequested: true } };
    }
    case "activity.dismiss":
      return state.activity?.id === action.activityId && state.activity.status === "ended" ? { ...state, activity: null } : state;
    default:
      return state;
  }
}

export interface InterviewStore extends StoreApi<InterviewViewState> {
  dispatch(action: InterviewAction): void;
}

export function createInterviewStore(initial: InterviewViewState = initialInterviewState()): InterviewStore {
  const store = createStore<InterviewViewState>()(() => initial);
  return Object.assign(store, {
    dispatch(action: InterviewAction) {
      store.setState((s) => reduceInterview(s, action), true);
    },
  });
}

export function useInterviewState<T>(store: InterviewStore, selector: (state: InterviewViewState) => T): T {
  return useStore(store, selector);
}
