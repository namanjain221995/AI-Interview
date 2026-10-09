/**
 * Intake activities in the interview room: the client side of the server's
 * phases (INTAKE_INTERVIEW.md §7). Pure functions only. The room store
 * (features/realtime/store.ts) calls the slice reducers; the panel renders
 * what the selectors return.
 *
 * Read-and-relay passage secrecy (§7.9), enforced here:
 * - content comes only from a running phase message that may show it, never from
 *   activity.start or a session.state snapshot;
 * - never in the relay phase, and never again once relay was reached for that activity;
 * - removed on the server's hide, at the local countdown zero (even if the server is
 *   late), on "I'm ready", and the moment the connection drops;
 * - kept in memory only (this module never touches storage).
 */
import type {
  ActivityContentMsg,
  ActivityEndMsg,
  ActivityEndReason,
  ActivityKind,
  ActivityPhase,
  ActivityPhaseMsg,
  ActivityStartMsg,
  IntakeStageStatus,
  IntakeStateMsg,
} from "@/lib/realtime/protocol";

import { INTAKE_STAGE_KEYS, INTAKE_STAGE_TITLES, type IntakeStageKey } from "../types";

// ------------------------------------------------------------------ views

export interface ActivityPhasePlan {
  phase: ActivityPhase;
  durationMs: number;
  capture: boolean;
}

export interface ActivityContentView {
  title: string;
  paragraphs: string[];
  bullets: string[];
  instruction: string | null;
}

/**
 * armed: the instruction is playing; running: a phase is live; between: a phase
 * ended and the next one starts after the next instruction; ended: activity.end
 * arrived (the panel says "Done" briefly, then the room dismisses it).
 */
export type ActivityStatus = "armed" | "running" | "between" | "ended";
export type HideReason = "server" | "countdown" | "finish" | "disconnect";

export interface ActivityView {
  id: string;
  kind: ActivityKind;
  stage: string;
  title: string;
  phases: ActivityPhasePlan[];
  attempt: number;
  resumed: boolean;
  status: ActivityStatus;
  phase: ActivityPhase | null;
  durationMs: number;
  /** Authoritative remaining time at `syncedAtMs` (null while armed). */
  remainingMs: number | null;
  /** performance.now() when `remainingMs` was last synchronised. */
  syncedAtMs: number;
  capture: boolean;
  canFinish: boolean;
  content: ActivityContentView | null;
  /** Why content was last removed (null while shown, or never shown). */
  hiddenBy: HideReason | null;
  /** The relay phase was reached: the passage can never come back for this activity. */
  relayReached: boolean;
  /** Read-and-relay: how long the passage has been on screen in earlier windows (all attempts of this activity). */
  shownMs: number;
  /** performance.now() when the passage's current window opened (null while not shown). */
  shownSinceMs: number | null;
  /** The candidate pressed the finish button; waiting for the server's next phase message. */
  finishRequested: boolean;
  /** The connection dropped since the server last spoke about this activity (session.state reconciles). */
  stale: boolean;
  endReason: ActivityEndReason | null;
  endedAtMs: number | null;
}

export interface ActivitySnapshotView {
  id: string;
  kind: ActivityKind;
  phase: ActivityPhase;
  status: "armed" | "running" | "ended";
  remainingMs: number | null;
}

export interface IntakeStageView {
  key: string;
  number: number;
  title: string;
  status: IntakeStageStatus;
}

export interface IntakeView {
  stage: string;
  stageNumber: number;
  stageTitle: string;
  stages: IntakeStageView[];
  activity: ActivitySnapshotView | null;
  answerMode: "standard" | "long";
  softLimitS: number | null;
}

// ------------------------------------------------------------------ guards

const KINDS = new Set<ActivityKind>(["reading_aloud", "read_relay", "topic_card"]);
const PHASES = new Set<ActivityPhase>(["read", "relay", "prepare", "speak"]);
const STAGE_STATUSES = new Set<IntakeStageStatus>(["pending", "current", "done", "incomplete"]);

export function isActivityKind(value: unknown): value is ActivityKind {
  return typeof value === "string" && KINDS.has(value as ActivityKind);
}

export function isActivityPhase(value: unknown): value is ActivityPhase {
  return typeof value === "string" && PHASES.has(value as ActivityPhase);
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function texts(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && Boolean(v.trim())) : [];
}

export function normalizeContent(raw: ActivityContentMsg | null | undefined): ActivityContentView | null {
  if (!raw || typeof raw !== "object") return null;
  const paragraphs = texts(raw.paragraphs);
  const bullets = texts(raw.bullets);
  if (!paragraphs.length && !bullets.length) return null;
  return { title: text(raw.title) ?? "", paragraphs, bullets, instruction: text(raw.instruction) };
}

// ------------------------------------------------------------------ intake state (session.state.intake)

export function normalizeIntake(raw: IntakeStateMsg | null | undefined): IntakeView | null {
  if (!raw || typeof raw !== "object") return null;
  const stageKey = text(raw.stage) ?? "greeting";
  const fallbackIndex = Math.max(0, INTAKE_STAGE_KEYS.indexOf(stageKey as IntakeStageKey));
  const stageNumber = num(raw.stage_number) ?? fallbackIndex + 1;
  let stages: IntakeStageView[] = (Array.isArray(raw.stages) ? raw.stages : [])
    .map((s, i) => {
      const key = text(s?.key);
      if (!key) return null;
      const number = num(s.number) ?? i + 1;
      const status: IntakeStageStatus = STAGE_STATUSES.has(s.status) ? s.status : number < stageNumber ? "done" : number === stageNumber ? "current" : "pending";
      return { key, number, title: text(s.title) ?? INTAKE_STAGE_TITLES[key as IntakeStageKey] ?? key, status };
    })
    .filter((s): s is IntakeStageView => Boolean(s))
    .sort((a, b) => a.number - b.number);
  if (!stages.length) {
    stages = INTAKE_STAGE_KEYS.map((key, i) => ({
      key,
      number: i + 1,
      title: INTAKE_STAGE_TITLES[key],
      status: i + 1 < stageNumber ? "done" : i + 1 === stageNumber ? "current" : "pending",
    }));
  }
  const current = stages.find((s) => s.key === stageKey) ?? stages.find((s) => s.number === stageNumber);
  const snapshot = raw.activity;
  const activity: ActivitySnapshotView | null =
    snapshot && text(snapshot.activity_id) && isActivityKind(snapshot.kind) && isActivityPhase(snapshot.phase)
      ? {
          id: snapshot.activity_id,
          kind: snapshot.kind,
          phase: snapshot.phase,
          status: snapshot.status === "armed" || snapshot.status === "ended" ? snapshot.status : "running",
          remainingMs: num(snapshot.remaining_ms),
        }
      : null;
  return {
    stage: stageKey,
    stageNumber,
    stageTitle: text(raw.stage_title) ?? current?.title ?? INTAKE_STAGE_TITLES[stageKey as IntakeStageKey] ?? stageKey,
    stages,
    activity,
    answerMode: raw.answer_mode === "long" ? "long" : "standard",
    softLimitS: num(raw.soft_limit_s),
  };
}

// ------------------------------------------------------------------ slice reducers

function placeholder(id: string, kind: ActivityKind, at: number): ActivityView {
  return {
    id,
    kind,
    stage: kind === "topic_card" ? "topic" : "reading",
    title: "",
    phases: [],
    attempt: 1,
    resumed: true,
    status: "armed",
    phase: null,
    durationMs: 0,
    remainingMs: null,
    syncedAtMs: at,
    capture: false,
    canFinish: false,
    content: null,
    hiddenBy: null,
    relayReached: false,
    shownMs: 0,
    shownSinceMs: null,
    finishRequested: false,
    stale: false,
    endReason: null,
    endedAtMs: null,
  };
}

/** Close the passage's open reveal window (its time counts towards the 30 s cap). */
function closeReveal(view: ActivityView, at: number): Pick<ActivityView, "shownMs" | "shownSinceMs"> {
  if (view.shownSinceMs === null) return { shownMs: view.shownMs, shownSinceMs: null };
  return { shownMs: view.shownMs + Math.max(0, at - view.shownSinceMs), shownSinceMs: null };
}

/** The read-and-relay passage's total reveal time (the read phase of the plan, 30 s by default). */
export function revealLimitMs(view: Pick<ActivityView, "phases">): number {
  return view.phases.find((p) => p.phase === "read")?.durationMs || 30_000;
}

export function reduceActivityStart(prev: ActivityView | null, msg: ActivityStartMsg, at: number): ActivityView | null {
  if (!text(msg.activity_id) || !isActivityKind(msg.kind)) return prev;
  const phases: ActivityPhasePlan[] = (Array.isArray(msg.phases) ? msg.phases : [])
    .filter((p) => isActivityPhase(p?.phase))
    .map((p) => ({ phase: p.phase, durationMs: Math.max(0, num(p.duration_ms) ?? 0), capture: Boolean(p.capture) }));
  const same = prev && prev.id === msg.activity_id ? prev : null;
  const attempt = num(msg.attempt) ?? 1;
  // A repeated announcement of a phase already under way changes nothing.
  if (same && !msg.resumed && same.attempt === attempt && same.status !== "armed" && same.status !== "ended") {
    return { ...same, title: text(msg.title) ?? same.title, phases: phases.length ? phases : same.phases };
  }
  return {
    ...placeholder(msg.activity_id, msg.kind, at),
    stage: text(msg.stage) ?? same?.stage ?? (msg.kind === "topic_card" ? "topic" : "reading"),
    title: text(msg.title) ?? same?.title ?? "",
    phases: phases.length ? phases : (same?.phases ?? []),
    attempt,
    resumed: Boolean(msg.resumed),
    durationMs: phases[0]?.durationMs ?? 0,
    // A resumed relay never brings the passage back, and a re-shown read only gets the reveal time left.
    relayReached: same?.relayReached ?? false,
    hiddenBy: same?.hiddenBy ?? null,
    shownMs: same ? closeReveal(same, at).shownMs : 0,
  };
}

export function reduceActivityPhase(prev: ActivityView | null, msg: ActivityPhaseMsg, at: number): ActivityView | null {
  if (!text(msg.activity_id) || !isActivityPhase(msg.phase)) return prev;
  const kind = isActivityKind(msg.kind) ? msg.kind : prev?.id === msg.activity_id ? prev.kind : null;
  if (!kind) return prev;
  const base = prev && prev.id === msg.activity_id ? prev : placeholder(msg.activity_id, kind, at);
  if (base.status === "ended") return base;
  const phase = msg.phase;
  const relayReached = base.relayReached || phase === "relay";
  const durationMs = Math.max(0, num(msg.duration_ms) ?? base.phases.find((p) => p.phase === phase)?.durationMs ?? 0);
  const remainingMs = Math.max(0, num(msg.remaining_ms) ?? 0);

  if (msg.status === "ended") {
    // The read-and-relay passage goes when its reading time ends, whatever the flag says.
    const hide = Boolean(msg.hide_content) || kind === "read_relay";
    return {
      ...base,
      ...(hide ? closeReveal(base, at) : {}),
      status: "between",
      phase,
      durationMs,
      remainingMs: 0,
      syncedAtMs: at,
      capture: false,
      canFinish: false,
      finishRequested: false,
      stale: false,
      relayReached,
      content: hide ? null : base.content,
      hiddenBy: hide && base.content ? "server" : base.hiddenBy,
    };
  }

  // A re-shown read phase never pushes the passage past its total reveal time (the server caps it too).
  const revealUsed = kind === "read_relay" && closeReveal(base, at).shownMs >= revealLimitMs(base);
  const mayShow = !msg.hide_content && phase !== "relay" && !(kind === "read_relay" && (relayReached || revealUsed));
  const incoming = mayShow ? normalizeContent(msg.content) : null;
  // The topic card stays up from preparation to speaking; otherwise content belongs to its phase.
  const kept = mayShow && base.content && (base.phase === phase || kind === "topic_card") ? base.content : null;
  const content = incoming ?? kept;
  const removed = !content && base.content !== null;
  // Reveal bookkeeping (read-and-relay): a window opens with new content and stays open while it is kept.
  const reveal =
    kind !== "read_relay"
      ? { shownMs: base.shownMs, shownSinceMs: null }
      : content
        ? { shownMs: base.shownMs, shownSinceMs: base.shownSinceMs ?? at }
        : closeReveal(base, at);
  return {
    ...base,
    ...reveal,
    kind,
    status: "running",
    phase,
    durationMs,
    remainingMs,
    syncedAtMs: at,
    capture: Boolean(msg.capture),
    canFinish: Boolean(msg.can_finish),
    content,
    hiddenBy: content ? null : removed ? "server" : base.hiddenBy,
    relayReached,
    finishRequested: false,
    stale: false,
    endReason: null,
    endedAtMs: null,
  };
}

export function reduceActivityEnd(prev: ActivityView | null, msg: ActivityEndMsg, at: number): ActivityView | null {
  if (!prev || prev.id !== msg.activity_id) return prev;
  return {
    ...prev,
    ...closeReveal(prev, at),
    status: "ended",
    endReason: msg.reason ?? "completed",
    content: null,
    hiddenBy: prev.content ? "server" : prev.hiddenBy,
    capture: false,
    canFinish: false,
    finishRequested: false,
    remainingMs: null,
    stale: false,
    endedAtMs: at,
  };
}

/**
 * Remove the read-and-relay passage locally (countdown zero, "I'm ready", lost
 * connection). Other activities keep their content: the server restarts or resumes them.
 */
export function hideActivityContent(prev: ActivityView | null, reason: HideReason, at: number): ActivityView | null {
  if (!prev || prev.kind !== "read_relay" || prev.status === "ended") return prev;
  if (reason === "countdown" && !(prev.phase === "read" && prev.status === "running")) return prev;
  if (!prev.content && prev.hiddenBy === reason) return prev;
  return {
    ...prev,
    ...closeReveal(prev, at),
    content: null,
    hiddenBy: prev.content ? reason : (prev.hiddenBy ?? reason),
    canFinish: reason === "disconnect" ? prev.canFinish : false,
  };
}

/** The connection left "connected": the server's clock stops, so does ours; the passage goes. */
export function freezeActivity(prev: ActivityView | null, at: number): ActivityView | null {
  if (!prev || prev.status === "ended") return prev;
  const remainingMs = activityRemainingMs(prev, at, true);
  const hidden = hideActivityContent(prev, "disconnect", at) ?? prev;
  return { ...hidden, remainingMs, syncedAtMs: at, finishRequested: false, stale: true };
}

/** The connection is back: the frozen countdown resumes from where it stopped. */
export function resumeActivityClock(prev: ActivityView | null, at: number): ActivityView | null {
  if (!prev || prev.status === "ended") return prev;
  return { ...prev, syncedAtMs: at };
}

/**
 * Reconcile with the content-free snapshot of a session.state. Live messages are
 * trusted while connected. After a dropped connection the snapshot decides whether
 * the activity still exists (activity messages are never replayed); the server then
 * re-announces a live one (activity.start{resumed} + activity.phase). A snapshot never
 * creates an activity by itself, so a late state can't bring a finished one back.
 */
export function reconcileActivity(prev: ActivityView | null, snapshot: ActivitySnapshotView | null, at: number): ActivityView | null {
  if (!prev || prev.status === "ended" || !prev.stale) return prev;
  if (!snapshot || snapshot.status === "ended" || snapshot.id !== prev.id) return null;
  const samePhase = snapshot.status === "running" && snapshot.phase === prev.phase;
  return {
    ...prev,
    status: snapshot.status === "armed" ? "armed" : "running",
    phase: snapshot.status === "armed" ? prev.phase : snapshot.phase,
    remainingMs: snapshot.remainingMs ?? prev.remainingMs,
    syncedAtMs: at,
    content: samePhase && prev.kind !== "read_relay" ? prev.content : null,
    relayReached: prev.relayReached || snapshot.phase === "relay",
    stale: false,
  };
}

// ------------------------------------------------------------------ selectors

/** Remaining phase time: `remaining_ms − (now − syncedAt)` while the clock runs. */
export function activityRemainingMs(activity: ActivityView | null, nowMs: number, clockRunning: boolean): number | null {
  if (!activity || activity.remainingMs === null) return null;
  if (activity.status !== "running" || !clockRunning) return Math.max(0, activity.remainingMs);
  return Math.max(0, activity.remainingMs - Math.max(0, nowMs - activity.syncedAtMs));
}

/** The passage/card may be on screen right now (the read-and-relay passage never past its zero). */
export function contentVisible(activity: ActivityView | null, remainingMs: number | null): boolean {
  if (!activity?.content || activity.status === "ended") return false;
  if (activity.kind === "read_relay") return activity.phase === "read" && activity.status === "running" && (remainingMs ?? 0) > 0;
  return true;
}

/** Minimum time in a phase before its finish button works (the server sends `can_finish`). */
const FINISH_AFTER_MS: Partial<Record<ActivityKind, Partial<Record<ActivityPhase, number>>>> = {
  reading_aloud: { read: 5_000 },
  read_relay: { read: 10_000 },
};

export function finishAfterMs(kind: ActivityKind, phase: ActivityPhase | null): number {
  return phase ? (FINISH_AFTER_MS[kind]?.[phase] ?? 0) : 0;
}

export function finishEnabled(activity: ActivityView | null, remainingMs: number | null): boolean {
  if (!activity || activity.status !== "running" || !activity.canFinish || activity.finishRequested || activity.stale) return false;
  const elapsed = Math.max(0, activity.durationMs - (remainingMs ?? activity.durationMs));
  return elapsed >= finishAfterMs(activity.kind, activity.phase);
}

/** Seconds until the finish button unlocks (0 when it already has). */
export function finishLocksForS(activity: ActivityView | null, remainingMs: number | null): number {
  if (!activity || activity.status !== "running") return 0;
  const elapsed = Math.max(0, activity.durationMs - (remainingMs ?? activity.durationMs));
  return Math.max(0, Math.ceil((finishAfterMs(activity.kind, activity.phase) - elapsed) / 1000));
}

/** What the dock may offer while an activity is up (the panel owns the finish action). */
export type ActivityDockMode = "none" | "listening" | "silent" | "capture" | "reading" | "ended";

export function activityDockMode(activity: ActivityView | null): ActivityDockMode {
  if (!activity) return "none";
  // The brief "Done": the exercise layout (and its dock) stays until the room dismisses it.
  if (activity.status === "ended") return "ended";
  if (activity.status === "armed" || activity.status === "between") return "listening";
  if (activity.kind === "reading_aloud") return "reading";
  return activity.capture ? "capture" : "silent";
}

// ------------------------------------------------------------------ copy

export const KIND_LABELS: Record<ActivityKind, string> = {
  reading_aloud: "Reading aloud",
  read_relay: "Read and explain",
  topic_card: "Speaking exercise",
};

export interface PhaseCopy {
  /** One line under the title: what to do now. */
  lede: string;
  /** Footer instruction next to the finish button. */
  instruction: string;
  /** Finish button label (null: no button in this state). */
  finishLabel: string | null;
  /** Word after the countdown ("left", "to read"). */
  timerWord: string;
  /** Polite announcement when this state begins. */
  announce: string;
}

export function phaseCopy(activity: Pick<ActivityView, "kind" | "phase" | "status" | "phases">, interviewer: string): PhaseCopy {
  const { kind, phase, status } = activity;
  if (status === "armed") {
    const lede =
      kind === "reading_aloud" ? "A short passage to read aloud." : kind === "read_relay" ? "A short passage to read silently, then explain." : "A topic to talk about for about 2 minutes.";
    const plan =
      kind === "reading_aloud"
        ? "The passage appears when the instructions end. Read it aloud at your own pace."
        : kind === "read_relay"
          ? "You'll have 30 seconds to read the passage. Then it disappears and you explain it in your own words."
          : "You'll get 60 seconds to prepare, then about 2 minutes to speak.";
    return { lede, instruction: plan, finishLabel: null, timerWord: "planned", announce: `${KIND_LABELS[kind]}. Listen to the instructions from ${interviewer}.` };
  }
  if (status === "ended") return { lede: "", instruction: "", finishLabel: null, timerWord: "", announce: "Done." };
  if (status === "between") {
    if (kind === "read_relay" && phase === "read") {
      return { lede: "Reading time is over.", instruction: "", finishLabel: null, timerWord: "", announce: "The passage is now hidden." };
    }
    if (kind === "topic_card" && phase === "prepare") {
      return { lede: "Preparation time is over.", instruction: "Start speaking when the instructions end.", finishLabel: null, timerWord: "", announce: "Preparation time is over." };
    }
    // A capture phase ended (reading aloud, the explanation, the answer): the interview continues in a moment.
    return { lede: "Thank you. One moment.", instruction: "", finishLabel: null, timerWord: "", announce: "Thank you." };
  }
  switch (`${kind}:${phase}`) {
    case "reading_aloud:read":
      return {
        lede: "Read the passage aloud, from the beginning.",
        instruction: "Read at your own pace. Press Finish reading when you reach the end.",
        finishLabel: "Finish reading",
        timerWord: "left",
        announce: "Reading time has started. Read the passage aloud.",
      };
    case "read_relay:read":
      return {
        lede: "Read silently. The passage disappears when the time is up.",
        instruction: "Take in the main idea and the key details.",
        finishLabel: "I'm ready",
        timerWord: "to read",
        announce: "You have 30 seconds to read the passage silently.",
      };
    case "read_relay:relay":
      return {
        lede: "Speak when you're ready. The passage won't come back.",
        instruction: "Take your time. Press I'm done when you finish.",
        finishLabel: "I'm done",
        timerWord: "left",
        announce: "The passage is hidden. Explain it in your own words.",
      };
    case "topic_card:prepare":
      return {
        lede: "Prepare your answer.",
        instruction: "Nothing is recorded while you prepare. Press Start now if you're ready early.",
        finishLabel: "Start now",
        timerWord: "to prepare",
        announce: "Preparation time has started: 60 seconds.",
      };
    case "topic_card:speak":
      return {
        lede: "Speak for about 2 minutes.",
        instruction: "Connect all four points in one answer. Press I'm done when you finish.",
        finishLabel: "I'm done",
        timerWord: "left",
        announce: "Speaking time has started.",
      };
    default:
      return { lede: "", instruction: "", finishLabel: null, timerWord: "left", announce: "" };
  }
}

/** "1 minute 5 seconds" for the countdown's accessible name. */
export function spokenDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const minutes = Math.floor(s / 60);
  const seconds = s % 60;
  const parts: string[] = [];
  if (minutes) parts.push(`${minutes} minute${minutes === 1 ? "" : "s"}`);
  if (seconds || !minutes) parts.push(`${seconds} second${seconds === 1 ? "" : "s"}`);
  return parts.join(" ");
}

/** Planned length of a phase in the plan, for the armed state's quiet timer. */
export function plannedMs(activity: Pick<ActivityView, "phases" | "kind">): number {
  return activity.phases[0]?.durationMs ?? 0;
}

/** The quiet cue under the interviewer during an activity (whose turn, and what kind of time it is). */
export function activityCue(activity: ActivityView | null, turn: string, interviewer: string): { text: string; tone: "speaking" | "listening" | "quiet" } {
  if (!activity) return { text: "", tone: "quiet" };
  if (activity.status === "ended") return turn === "ai_speaking" ? { text: `${interviewer} is speaking`, tone: "speaking" } : { text: "", tone: "quiet" };
  if (activity.status === "armed") return { text: turn === "ai_speaking" ? `${interviewer} is explaining the exercise` : `${interviewer} is about to explain the exercise`, tone: turn === "ai_speaking" ? "speaking" : "quiet" };
  if (activity.status === "between") return turn === "ai_speaking" ? { text: `${interviewer} is speaking`, tone: "speaking" } : { text: "One moment", tone: "quiet" };
  if (activity.capture) return { text: "Listening", tone: "listening" };
  return { text: activity.kind === "topic_card" ? "Preparation time" : "Reading time", tone: "quiet" };
}
