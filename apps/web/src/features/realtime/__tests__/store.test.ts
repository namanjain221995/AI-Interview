import { describe, expect, it } from "vitest";

import type { CodingState, ServerMessage } from "@/lib/realtime/protocol";

import {
  clockRunning,
  codingRemainingSeconds,
  createInterviewStore,
  initialInterviewState,
  reduceInterview,
  remainingSeconds,
  type InterviewAction,
  type InterviewViewState,
} from "../store";

function server(msg: Record<string, unknown>, at = 0): InterviewAction {
  return { type: "server", msg: { seq: 0, ...msg } as unknown as ServerMessage, at };
}

function apply(actions: InterviewAction[], state: InterviewViewState = initialInterviewState()): InterviewViewState {
  return actions.reduce(reduceInterview, state);
}

const sessionState = (overrides: Record<string, unknown> = {}) =>
  server({
    type: "session.state",
    session_id: "s1",
    status: "live",
    turn_state: "listening",
    round_name: "Combined AI Round",
    round_type: "combined",
    interviewer_name: "Maya",
    elapsed_s: 60,
    remaining_s: 1740,
    time_limit_s: 1800,
    question_count: 2,
    current_question: "How do you size a Kubernetes cluster?",
    show_subtitles: true,
    allow_typed_answers: true,
    integrity: { require_fullscreen: true },
    avatar_state: "LISTENING",
    ...overrides,
  });

const coding: CodingState = {
  coding_session_id: "c1",
  question: {
    coding_question_id: "q1",
    title: "Most frequent error service",
    prompt_markdown: "Find the service with the most errors.",
    difficulty: "medium",
    languages: ["python", "javascript"],
    starter_code: { python: "def solve(logs):\n    pass\n" },
    visible_tests: [{ id: "t1", args: [[]], expected: null }],
  },
  language: "python",
  time_limit_s: 1200,
  elapsed_s: 0,
  hints_available: 3,
  hints_used: 0,
  hint_texts: [],
};

describe("interview reducer", () => {
  it("syncs session state and turn changes", () => {
    const s = apply([sessionState(), server({ type: "turn.state", state: "candidate_speaking" })]);
    expect(s.session).toMatchObject({ id: "s1", status: "live", turnState: "candidate_speaking", interviewerName: "Maya", remainingS: 1740 });
    expect(s.lastInterviewerText).toBe("How do you size a Kubernetes cluster?");
  });

  it("tracks an utterance from deltas to spoken words to delivery", () => {
    let s = apply([
      sessionState(),
      server({ type: "ai.utterance.start", utterance_id: "u1", kind: "question", behavior: { type: "avatar.behavior", state: "SPEAKING", expression: "warm", gesture: "none", gesture_intensity: "low", tone: "friendly", seq: 0 } }),
      server({ type: "ai.text.delta", utterance_id: "u1", text: "Tell me about " }),
      server({ type: "ai.text.delta", utterance_id: "u1", text: "your last incident." }),
      server({ type: "ai.text.delta", utterance_id: "other", text: "ignored" }),
    ]);
    expect(s.utterance).toMatchObject({ id: "u1", text: "Tell me about your last incident.", mode: "pending", spokenWords: 0 });
    expect(s.behavior?.expression).toBe("warm");
    s = apply([server({ type: "tts.chunk", utterance_id: "u1", chunk_id: 1, index: 0, text: "Tell me about", sample_rate: 24000, duration_ms: 900, visemes: [], word_timings: [] })], s);
    expect(s.utterance?.mode).toBe("audio");
    s = apply([{ type: "playback.chunk_started", utteranceId: "u1", text: "Tell me about" }], s);
    expect(s.utterance?.spokenWords).toBe(3);
    s = apply([server({ type: "ai.utterance.end", utterance_id: "u1", text: "Tell me about your last incident.", interrupted: false })], s);
    expect(s.lastInterviewerText).toBe("Tell me about your last incident.");
    s = apply([{ type: "playback.done", utteranceId: "u1", interrupted: false }], s);
    expect(s.utterance?.delivered).toBe(true);
    expect(s.utterance!.spokenWords).toBeGreaterThan(100);
  });

  it("marks barge-in and keeps acknowledgements out of the question subtitle", () => {
    let s = apply([
      sessionState(),
      server({ type: "ai.utterance.start", utterance_id: "a", kind: "acknowledgment" }),
      server({ type: "ai.utterance.end", utterance_id: "a", text: "Got it." }),
    ]);
    expect(s.lastInterviewerText).toBe("How do you size a Kubernetes cluster?");
    s = apply([server({ type: "ai.utterance.start", utterance_id: "q", kind: "question" }), server({ type: "barge_in", utterance_id: "q" })], s);
    expect(s.utterance?.interrupted).toBe(true);
  });

  it("captions the utterance being heard when the server pipelines the next one", () => {
    // Live trace: the first question starts streaming while the 16 s introduction is still playing.
    let s = apply([
      sessionState({ turn_state: "processing" }),
      server({ type: "ai.utterance.start", utterance_id: "intro", kind: "intro" }),
      server({ type: "ai.text.delta", utterance_id: "intro", text: "Hi, I'm Maya. Over thirty minutes we'll cover your experience." }),
      server({ type: "tts.chunk", utterance_id: "intro", chunk_id: 1, index: 0, text: "Hi, I'm Maya.", sample_rate: 24000, duration_ms: 900, visemes: [], word_timings: [] }),
      server({ type: "ai.utterance.end", utterance_id: "intro", text: "Hi, I'm Maya. Over thirty minutes we'll cover your experience." }),
      { type: "playback.chunk_started", utteranceId: "intro", text: "Hi, I'm Maya." },
      server({ type: "ai.utterance.start", utterance_id: "q1", kind: "question" }),
      server({ type: "ai.text.delta", utterance_id: "q1", text: "To start, walk me through the EKS platform you designed." }),
    ]);
    expect(s.utterance?.id).toBe("intro");
    expect(s.utterance?.spokenWords).toBe(3);
    expect(s.utterances.map((u) => u.id)).toEqual(["intro", "q1"]);
    // The question's audio starts once the introduction has played: the caption moves on.
    s = apply([{ type: "playback.chunk_started", utteranceId: "q1", text: "To start, walk me through" }], s);
    expect(s.utterance?.id).toBe("q1");
    expect(s.utterances[0]).toMatchObject({ id: "intro", delivered: true });
    expect(s.utterance?.spokenWords).toBe(5);
  });

  it("keeps the question's caption when a filler plays while the reply is still being written", () => {
    // Owner report: "One moment." started after the reply was announced (no audio yet), played first, marked the
    // question as heard, and its caption stayed on screen while the question was spoken.
    const chunk = (id: string, text: string) =>
      server({ type: "tts.chunk", utterance_id: id, chunk_id: 1, index: 0, text, sample_rate: 24000, duration_ms: 600, visemes: [], word_timings: [] });
    let s = apply([
      sessionState({ turn_state: "processing" }),
      server({ type: "ai.utterance.start", utterance_id: "ack", kind: "acknowledgment" }),
      chunk("ack", "Okay."),
      server({ type: "ai.utterance.end", utterance_id: "ack", text: "Okay." }),
      { type: "playback.chunk_started", utteranceId: "ack", text: "Okay." },
      { type: "playback.done", utteranceId: "ack", interrupted: false },
      server({ type: "ai.utterance.start", utterance_id: "q", kind: "question" }),
      server({ type: "ai.utterance.start", utterance_id: "filler", kind: "acknowledgment" }),
      chunk("filler", "One moment."),
      server({ type: "ai.utterance.end", utterance_id: "filler", text: "One moment." }),
      { type: "playback.chunk_started", utteranceId: "filler", text: "One moment." },
    ]);
    expect(s.utterances.map((u) => u.id)).toEqual(["ack", "filler", "q"]);
    expect(s.utterance?.id).toBe("filler");
    expect(s.utterances.find((u) => u.id === "q")?.delivered).toBe(false);
    s = apply(
      [
        { type: "playback.done", utteranceId: "filler", interrupted: false },
        server({ type: "ai.text.delta", utterance_id: "q", text: "Which metric told you the rollout was safe?" }),
        chunk("q", "Which metric told you"),
        { type: "playback.chunk_started", utteranceId: "q", text: "Which metric told you" },
      ],
      s,
    );
    expect(s.utterance?.id).toBe("q");
    expect(s.utterance?.spokenWords).toBe(4);
    s = apply(
      [
        server({ type: "ai.utterance.end", utterance_id: "q", text: "Which metric told you the rollout was safe?" }),
        { type: "playback.done", utteranceId: "q", interrupted: false },
        server({ type: "turn.state", state: "listening", reason: "reply_done" }),
      ],
      s,
    );
    expect(s.utterance?.id).toBe("q");
    expect(s.lastInterviewerText).toBe("Which metric told you the rollout was safe?");
  });

  it("puts an acknowledgement after a reply whose audio already started", () => {
    const s = apply([
      sessionState({ turn_state: "ai_speaking" }),
      server({ type: "ai.utterance.start", utterance_id: "q", kind: "question" }),
      server({ type: "tts.chunk", utterance_id: "q", chunk_id: 1, index: 0, text: "How", sample_rate: 24000, duration_ms: 300, visemes: [], word_timings: [] }),
      server({ type: "ai.utterance.start", utterance_id: "ack", kind: "acknowledgment" }),
    ]);
    expect(s.utterances.map((u) => u.id)).toEqual(["q", "ack"]);
  });

  it("shows the whole question once the floor is the candidate's, even if a playback report was lost", () => {
    const s = apply([
      sessionState({ turn_state: "ai_speaking" }),
      server({ type: "ai.utterance.start", utterance_id: "q2", kind: "question" }),
      server({ type: "ai.text.delta", utterance_id: "q2", text: "How did you manage node scaling across the three clusters?" }),
      { type: "playback.chunk_started", utteranceId: "q2", text: "How did you manage node scaling" },
      server({ type: "ai.utterance.end", utterance_id: "q2", text: "How did you manage node scaling across the three clusters?" }),
      server({ type: "turn.state", state: "listening", reason: "start_done" }),
    ]);
    expect(s.utterance).toMatchObject({ id: "q2", delivered: true });
    expect(s.utterance!.spokenWords).toBeGreaterThan(100);
  });

  it("holds the clock while the server finishes preparing, then starts by itself", () => {
    let s = apply([
      sessionState({ status: "live", turn_state: "processing", remaining_s: 1800, elapsed_s: 0 }),
      server({ type: "turn.state", state: "idle", reason: "preparing" }),
      server({ type: "notice", level: "info", code: "preparing", message: "We're finishing preparing your interview." }),
      sessionState({ status: "live", turn_state: "idle", remaining_s: 1800, elapsed_s: 0 }),
      { type: "connection", status: "connected", at: 0 },
    ]);
    expect(s.preparing).toBe(true);
    expect(clockRunning(s)).toBe(false);
    expect(remainingSeconds(s, 60_000)).toBe(1800);
    // A retried start that is still waiting must not flicker the preparing state.
    s = apply([server({ type: "turn.state", state: "processing", reason: "starting" })], s);
    expect(s.preparing).toBe(true);
    s = apply([server({ type: "ai.utterance.start", utterance_id: "intro", kind: "intro" }), server({ type: "turn.state", state: "ai_speaking" })], s);
    expect(s.preparing).toBe(false);
    expect(clockRunning(s)).toBe(true);
  });

  it("starts a fresh candidate transcript for each answer", () => {
    let s = apply([
      sessionState(),
      server({ type: "transcript.partial", speaker: "candidate", segment_id: "g1", text: "I led the" }),
      server({ type: "transcript.final", speaker: "candidate", segment_id: "g1", text: "I led the migration." }),
      server({ type: "transcript.final", speaker: "candidate", segment_id: "g2", text: "It took six weeks." }),
    ]);
    expect(s.candidate.segments.map((x) => x.text)).toEqual(["I led the migration.", "It took six weeks."]);
    expect(s.candidate.segments[0]!.final).toBe(true);
    s = apply([server({ type: "ai.utterance.start", utterance_id: "u2", kind: "followup" })], s);
    expect(s.candidate.segmentEpoch).not.toBe(s.candidate.epoch);
    s = apply([server({ type: "transcript.partial", speaker: "candidate", segment_id: "g3", text: "We measured" })], s);
    expect(s.candidate.segments.map((x) => x.text)).toEqual(["We measured"]);
    s = apply([server({ type: "transcript.final", speaker: "interviewer", segment_id: "i1", text: "ignored" })], s);
    expect(s.candidate.segments).toHaveLength(1);
    s = apply([{ type: "candidate.typed", text: "Typed answer", at: 5 }], s);
    expect(s.candidate.segments.at(-1)?.text).toBe("Typed answer");
  });

  it("runs the coding lifecycle: start, run, result, hints, end", () => {
    let s = apply([sessionState(), server({ type: "coding.start", coding }, 100)]);
    expect(s.coding).toMatchObject({ sessionId: "c1", hintsRemaining: 3, lastRun: null, ended: null });
    s = apply([{ type: "coding.run_requested", kind: "test" }], s);
    expect(s.coding?.pendingRun).toBe("test");
    s = apply(
      [server({ type: "coding.run_result", coding_session_id: "c1", run_id: "r1", kind: "test", status: "failed", stdout: "", stderr: "", compile_output: "", passed: 1, total: 2, tests: [] })],
      s,
    );
    expect(s.coding?.pendingRun).toBeNull();
    expect(s.coding?.lastRun?.status).toBe("failed");
    s = apply([{ type: "coding.hint_requested" }, server({ type: "coding.hint", coding_session_id: "c1", level: 1, text: "Count with a dictionary.", hints_used: 1, hints_remaining: 2 })], s);
    expect(s.coding).toMatchObject({ hintsRemaining: 2, hintPending: false, hints: [{ level: 1, text: "Count with a dictionary." }] });
    // A state sync (e.g. after reconnect) keeps the last run and merges hints.
    s = apply([sessionState({ turn_state: "coding", coding: { ...coding, hints_used: 1, hint_texts: ["Count with a dictionary."], elapsed_s: 300 } })], s);
    expect(s.coding?.lastRun?.run_id).toBe("r1");
    expect(s.coding?.hints).toHaveLength(1);
    s = apply([server({ type: "coding.end", coding_session_id: "c1", reason: "submitted" })], s);
    expect(s.coding?.ended).toBe("submitted");
    s = apply([sessionState({ coding: null })], s);
    expect(s.coding?.ended).toBe("submitted");
  });

  it("keeps the last run when the same exercise is re-announced (language change)", () => {
    let s = apply([sessionState(), server({ type: "coding.start", coding })]);
    s = apply(
      [server({ type: "coding.run_result", coding_session_id: "c1", run_id: "r9", kind: "test", status: "passed", stdout: "", stderr: "", compile_output: "", passed: 2, total: 5, tests: [] })],
      s,
    );
    s = apply([server({ type: "coding.start", coding: { ...coding, language: "javascript", code: "function solve() {}" } })], s);
    expect(s.coding).toMatchObject({ language: "javascript", lastRun: { run_id: "r9" } });
    s = apply([server({ type: "coding.start", coding: { ...coding, coding_session_id: "c2" } })], s);
    expect(s.coding?.lastRun).toBeNull();
  });

  it("raises and clears degradation notices", () => {
    let s = apply([sessionState(), server({ type: "notice", level: "warning", code: "stt_degraded", message: "Transcription is retrying." })]);
    expect(s.sttDegraded).toBe(true);
    expect(s.notices).toHaveLength(1);
    s = apply([server({ type: "notice", level: "info", code: "stt_recovered", message: "Back to normal." })], s);
    expect(s.sttDegraded).toBe(false);
    expect(s.notices).toHaveLength(0);
    s = apply([server({ type: "tts.fallback", utterance_id: "x", text: "Hello", reason: "tts_unavailable" })], s);
    expect(s.ttsDegraded).toBe(true);
  });

  it("keeps the latest gentle integrity warning per type", () => {
    const s = apply([
      server({ type: "integrity.warning", event_type: "tab_hidden", message: "Please keep this tab open.", seq: 3 }),
      server({ type: "integrity.warning", event_type: "tab_hidden", message: "Please keep this tab open.", seq: 4 }),
      server({ type: "integrity.warning", event_type: "fullscreen_exited", message: "Return to full screen.", seq: 5 }),
    ]);
    expect(s.warnings.map((w) => w.eventType)).toEqual(["tab_hidden", "fullscreen_exited"]);
  });

  it("returns to the lobby when Begin fails before a connection exists", () => {
    const store = createInterviewStore();
    store.dispatch({ type: "begun" });
    store.dispatch({ type: "connection", status: "connecting", attempt: 1, reason: null, at: 1 });
    store.dispatch({ type: "begin.failed" });
    expect(store.getState().begun).toBe(false);
    expect(store.getState().connection.status).toBe("idle");
  });

  it("records the end of the session and fatal errors", () => {
    let s = apply([sessionState(), server({ type: "session.ended", reason: "time_limit", report_status: "pending", candidate_report_available: false })]);
    expect(s.ended).toEqual({ reason: "time_limit", reportStatus: "pending", candidateReportAvailable: false });
    expect(s.session?.turnState).toBe("ended");
    s = apply([server({ type: "error", code: "session_active_elsewhere", message: "Open in another tab.", recoverable: false })], s);
    expect(s.fatalError?.code).toBe("session_active_elsewhere");
  });

  it("freezes the clock while disconnected and resumes on sync", () => {
    let s = apply([{ type: "connection", status: "connected", at: 0 }, sessionState({}, ), { type: "connection", status: "connected", at: 0 }]);
    s = apply([server({ type: "turn.state", state: "listening" })], s);
    // session.state at t=0 says 1740 s remain.
    expect(remainingSeconds(s, 10_000)).toBeCloseTo(1730, 3);
    s = apply([{ type: "connection", status: "reconnecting", at: 20_000 }], s);
    expect(clockRunning(s)).toBe(false);
    expect(remainingSeconds(s, 90_000)).toBeCloseTo(1720, 3);
    s = apply([{ type: "connection", status: "connected", at: 95_000 }], s);
    expect(s.connection.reconnects).toBe(1);
    expect(remainingSeconds(s, 100_000)).toBeCloseTo(1715, 3);
  });

  it("computes coding time left", () => {
    const s = apply([sessionState(), server({ type: "coding.start", coding: { ...coding, elapsed_s: 200 } }, 1000)]);
    expect(codingRemainingSeconds(s.coding, 11_000, true)).toBeCloseTo(990, 3);
    expect(codingRemainingSeconds(s.coding, 11_000, false)).toBeCloseTo(1000, 3);
  });

  it("seeds from REST without overriding live state", () => {
    let s = apply([{ type: "seed", session: { id: "s1", status: "ready", interviewerName: "Maya" } }]);
    expect(s.session).toMatchObject({ status: "ready", turnState: "idle" });
    s = apply([sessionState(), { type: "seed", session: { id: "s1", status: "created" } }], s);
    expect(s.session?.status).toBe("live");
  });

  it("treats a started session as live from its first turn (the server announces it later)", () => {
    let s = apply([sessionState({ status: "ready", turn_state: "idle" })]);
    expect(s.session?.status).toBe("ready");
    s = apply([server({ type: "turn.state", state: "idle" })], s);
    expect(s.session?.status).toBe("ready");
    s = apply([server({ type: "turn.state", state: "processing" }, 500)], s);
    expect(s.session).toMatchObject({ status: "live", syncedAtMs: 500 });
    let t = apply([sessionState({ status: "ready", turn_state: "idle" })]);
    t = apply([server({ type: "ai.utterance.start", utterance_id: "u", kind: "intro" })], t);
    expect(t.session?.status).toBe("live");
    // Terminal states are never overridden.
    let u = apply([sessionState({ status: "completed", turn_state: "ended" }), server({ type: "turn.state", state: "processing" })]);
    expect(u.session?.status).toBe("completed");
    u = apply([server({ type: "ai.utterance.start", utterance_id: "x", kind: "closing" })], u);
    expect(u.session?.status).toBe("completed");
  });

  it("works as a store", () => {
    const store = createInterviewStore();
    store.dispatch(sessionState());
    expect(store.getState().session?.roundName).toBe("Combined AI Round");
  });
});
