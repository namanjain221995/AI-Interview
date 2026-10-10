/**
 * InterviewRuntime wiring, end to end inside jsdom: fake WebSocket + fake
 * AudioContext. Verifies the protocol choreography the server relies on:
 * hello -> control.start, TTS chunks -> playback.started/finished, barge-in ->
 * playback.interrupted, TTS fallback -> delivery report, typed answers,
 * integrity batching and the end of the session.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { effectivePolicy } from "@/features/integrity/policy";
import { createInterviewStore } from "@/features/realtime/store";
import { ApiError } from "@/lib/api";
import { FRAME_TTS_AUDIO, FRAME_HEADER_BYTES } from "@/lib/realtime/protocol";

import { frameUploadDecision, InterviewRuntime, type RuntimeApi } from "../runtime";
import { BackoffUploadsError, StopUploadsError } from "@/features/integrity/frames";

// ------------------------------------------------------------------ fakes

class FakeParam {
  value = 1;
  setTargetAtTime(v: number) {
    this.value = v;
  }
  cancelScheduledValues() {}
}

class FakeNode {
  connect<T>(n: T): T {
    return n;
  }
  disconnect() {}
}

class FakeSource extends FakeNode {
  static started: FakeSource[] = [];
  buffer: { length: number } | null = null;
  onended: (() => void) | null = null;
  startAt = -1;
  stopped = false;
  start(t: number) {
    this.startAt = t;
    FakeSource.started.push(this);
  }
  stop() {
    this.stopped = true;
  }
}

class FakeAudioContext {
  static current: FakeAudioContext | null = null;
  currentTime = 0;
  state: AudioContextState = "running";
  destination = new FakeNode();
  constructor() {
    FakeAudioContext.current = this;
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    this.state = "closed";
    return Promise.resolve();
  }
  createGain() {
    return Object.assign(new FakeNode(), { gain: new FakeParam() });
  }
  createAnalyser() {
    return Object.assign(new FakeNode(), { fftSize: 1024, smoothingTimeConstant: 0, getFloatTimeDomainData: (a: Float32Array) => a.fill(0) });
  }
  createBuffer(_ch: number, length: number) {
    return { length, copyToChannel() {} };
  }
  createBufferSource() {
    return new FakeSource();
  }
}

class FakeSocket {
  static all: FakeSocket[] = [];
  binaryType = "blob";
  readyState = 0;
  bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  sent: unknown[] = [];
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(d: unknown) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(msg: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  pushBinary(buf: ArrayBuffer) {
    this.onmessage?.({ data: buf });
  }
  json(): Array<Record<string, unknown>> {
    return this.sent.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d) as Record<string, unknown>);
  }
  types(): string[] {
    return this.json().map((m) => String(m.type));
  }
}

function ttsFrame(chunkId: number, samples: number, final = false): ArrayBuffer {
  const buf = new ArrayBuffer(FRAME_HEADER_BYTES + samples * 2);
  const v = new DataView(buf);
  v.setUint8(0, FRAME_TTS_AUDIO);
  v.setUint8(1, final ? 1 : 0);
  v.setUint32(4, chunkId, true);
  return buf;
}

let seq = 0;
const msg = (m: Record<string, unknown>) => ({ seq: ++seq, ...m });

const stateMsg = (status = "ready", turn = "idle") =>
  msg({
    type: "session.state",
    session_id: "s1",
    status,
    turn_state: turn,
    round_name: "Combined AI Round",
    round_type: "combined",
    interviewer_name: "Maya",
    elapsed_s: 0,
    remaining_s: 1800,
    time_limit_s: 1800,
    show_subtitles: true,
    allow_typed_answers: true,
    integrity: {},
    avatar_state: "IDLE",
  });

// ------------------------------------------------------------------ setup

async function startRuntime(
  overrides: {
    onEnded?: () => void;
    policy?: ReturnType<typeof effectivePolicy>;
    screenShare?: ConstructorParameters<typeof InterviewRuntime>[0]["screenShare"];
    beforeBegin?: (runtime: InterviewRuntime) => void;
    api?: Partial<RuntimeApi>;
  } = {},
) {
  const store = createInterviewStore();
  const postIntegrityEvents = vi.fn(async () => ({ accepted: 1, warnings: [] as Array<{ eventType: string | null; message: string }> }));
  const avatar = {
    setState: vi.fn(),
    applyBehavior: vi.fn(),
    attachAudio: vi.fn(),
    enqueueSpeech: vi.fn(),
    speakWordBoundary: vi.fn(),
    interrupt: vi.fn(),
    setCandidateSpeaking: vi.fn(),
    getStatus: () => ({ mode: "3d" as const, loaded: true, fps: 60 }),
    subscribe: () => () => undefined,
    dispose: vi.fn(),
  };
  const runtime = new InterviewRuntime({
    interviewId: "i1",
    sessionId: "s1",
    store,
    policy:
      overrides.policy ??
      effectivePolicy({ integrity: { ...effectivePolicy(null, null, null).integrity, require_fullscreen: false, camera_required: false, camera_frame_analysis: false } }, null, null),
    screenShare: overrides.screenShare,
    prefs: { avatarMode: "3d", avatarFps: 60, ttsMode: "server" },
    avatar,
    api: { fetchTicket: async () => ({ ticket: "tk", ws_url: "/ws/sessions/s1" }), postIntegrityEvents, uploadFrame: vi.fn(), uploadRecording: vi.fn(), ...overrides.api },
    onEnded: overrides.onEnded,
  });
  overrides.beforeBegin?.(runtime);
  runtime.prepareGesture();
  await runtime.begin();
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.all.at(-1)!;
  socket.open();
  return { runtime, store, socket, avatar, postIntegrityEvents, ctx: FakeAudioContext.current! };
}

beforeEach(() => {
  vi.useFakeTimers();
  seq = 0;
  FakeSocket.all = [];
  FakeSource.started = [];
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("WebSocket", FakeSocket);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => Promise.reject(new DOMException("denied", "NotAllowedError"))) },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("InterviewRuntime", () => {
  it("says hello, then starts the interview once on a ready session", async () => {
    const { socket, store, runtime } = await startRuntime();
    expect(socket.url).toContain("/ws/sessions/s1?ticket=tk");
    expect(socket.json()[0]).toMatchObject({ type: "hello", last_seq: 0, client: { avatar_mode: "3d", tts_mode: "server", sample_rate: 16000 } });
    socket.push(stateMsg("ready"));
    socket.push(stateMsg("ready"));
    expect(socket.types().filter((t) => t === "control.start")).toHaveLength(1);
    expect(store.getState().connection.status).toBe("connected");
    // Without a microphone the candidate is told they can type.
    expect(store.getState().notices.some((n) => n.code === "mic_unavailable")).toBe(true);
    runtime.dispose();
  });

  it("does not restart a live session after a reconnect", async () => {
    const { socket, runtime } = await startRuntime();
    socket.push(stateMsg("live", "listening"));
    expect(socket.types()).not.toContain("control.start");
    runtime.dispose();
  });

  it("plays TTS chunks, drives the avatar and reports started/finished", async () => {
    const { socket, store, ctx, avatar, runtime } = await startRuntime();
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "ai.utterance.start", utterance_id: "u1", kind: "question", behavior: { type: "avatar.behavior", state: "SPEAKING", expression: "warm", gesture: "none", gesture_intensity: "low", tone: "friendly" } }));
    socket.push(msg({ type: "ai.text.delta", utterance_id: "u1", text: "Tell me about your last incident." }));
    socket.push(msg({ type: "tts.chunk", utterance_id: "u1", chunk_id: 7, index: 0, text: "Tell me about your last incident.", sample_rate: 24000, duration_ms: 1000, visemes: [{ t: 0, v: "aa", w: 1 }], word_timings: [] }));
    socket.pushBinary(ttsFrame(7, 24000, true));
    expect(avatar.applyBehavior).toHaveBeenCalledWith(expect.objectContaining({ state: "SPEAKING" }));
    expect(avatar.enqueueSpeech).toHaveBeenCalledWith(expect.objectContaining({ utteranceId: "u1", chunkId: 7, durationMs: 1000 }));
    expect(FakeSource.started).toHaveLength(1);
    ctx.currentTime = 0.2;
    await vi.advanceTimersByTimeAsync(50);
    expect(socket.json().find((m) => m.type === "playback.started")).toMatchObject({ utterance_id: "u1", played_ms: 0, chunk_id: 7 });
    expect(store.getState().utterance?.spokenWords).toBe(6);
    socket.push(msg({ type: "ai.utterance.end", utterance_id: "u1", text: "Tell me about your last incident.", interrupted: false }));
    ctx.currentTime = 1.3;
    await vi.advanceTimersByTimeAsync(50);
    expect(socket.json().find((m) => m.type === "playback.finished")).toMatchObject({ utterance_id: "u1", played_ms: 1000 });
    expect(store.getState().utterance?.delivered).toBe(true);
    runtime.dispose();
  });

  it("stops audio immediately on barge-in and reports the played portion", async () => {
    const { socket, ctx, avatar, runtime } = await startRuntime();
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "ai.utterance.start", utterance_id: "u2", kind: "question" }));
    socket.push(msg({ type: "tts.chunk", utterance_id: "u2", chunk_id: 1, index: 0, text: "First part.", sample_rate: 24000, duration_ms: 1000, visemes: [], word_timings: [] }));
    socket.pushBinary(ttsFrame(1, 24000));
    socket.push(msg({ type: "tts.chunk", utterance_id: "u2", chunk_id: 2, index: 1, text: "Second part.", sample_rate: 24000, duration_ms: 1000, visemes: [], word_timings: [] }));
    socket.pushBinary(ttsFrame(2, 24000));
    const startedAt = FakeSource.started[0]!.startAt;
    ctx.currentTime = startedAt + 0.45;
    await vi.advanceTimersByTimeAsync(50);
    socket.push(msg({ type: "barge_in", utterance_id: "u2" }));
    expect(FakeSource.started.every((s) => s.stopped)).toBe(true);
    expect(avatar.interrupt).toHaveBeenCalled();
    const report = socket.json().find((m) => m.type === "playback.interrupted");
    expect(report).toMatchObject({ utterance_id: "u2", chunk_id: 1 });
    expect(Number(report!.played_ms)).toBeGreaterThanOrEqual(440);
    expect(Number(report!.played_ms)).toBeLessThanOrEqual(460);
    // Late audio for the interrupted utterance is never played.
    socket.push(msg({ type: "tts.chunk", utterance_id: "u2", chunk_id: 3, index: 2, text: "Late.", sample_rate: 24000, duration_ms: 500, visemes: [], word_timings: [] }));
    socket.pushBinary(ttsFrame(3, 12000));
    expect(FakeSource.started).toHaveLength(2);
    runtime.dispose();
  });

  it("falls back to text (no speechSynthesis here) and still reports delivery", async () => {
    const { socket, store, runtime } = await startRuntime();
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "ai.utterance.start", utterance_id: "u3", kind: "question" }));
    socket.push(msg({ type: "tts.fallback", utterance_id: "u3", text: "What was the monthly spend before and after?", reason: "tts_unavailable" }));
    expect(store.getState().utterance?.mode).toBe("text");
    expect(socket.json().find((m) => m.type === "playback.started")).toMatchObject({ utterance_id: "u3" });
    socket.push(msg({ type: "ai.utterance.end", utterance_id: "u3", text: "What was the monthly spend before and after?", interrupted: false }));
    await vi.advanceTimersByTimeAsync(3000);
    expect(socket.json().find((m) => m.type === "playback.finished")).toMatchObject({ utterance_id: "u3" });
    runtime.dispose();
  });

  it("gives every fallback chunk its reading time before reporting delivery", async () => {
    const { socket, runtime } = await startRuntime();
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "ai.utterance.start", utterance_id: "u5", kind: "question" }));
    // One tts.fallback per chunk (each ~1.5 s of reading), then the utterance ends.
    socket.push(msg({ type: "tts.fallback", utterance_id: "u5", text: "Thanks, that helps.", reason: "tts_unavailable" }));
    socket.push(msg({ type: "tts.fallback", utterance_id: "u5", text: "How did you measure it?", reason: "tts_unavailable" }));
    socket.push(msg({ type: "ai.utterance.end", utterance_id: "u5", text: "Thanks, that helps. How did you measure it?", interrupted: false }));
    expect(socket.json().filter((m) => m.type === "playback.started")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2000);
    // The first chunk alone (1.5 s) would have finished by now; the second is still being read.
    expect(socket.types()).not.toContain("playback.finished");
    await vi.advanceTimersByTimeAsync(1300);
    const finished = socket.json().find((m) => m.type === "playback.finished");
    expect(finished).toMatchObject({ utterance_id: "u5" });
    expect(Number(finished!.played_ms)).toBeGreaterThanOrEqual(3200); // 1500 + 1765 ms of reading
    runtime.dispose();
  });

  it("reports screen-share observations made in the lobby once monitoring starts", async () => {
    const { socket, runtime } = await startRuntime({
      beforeBegin: (rt) => {
        rt.recordScreenShareEvent("screen_share_started", { display_surface: "window", restarted: false });
        rt.recordScreenShareEvent("screen_share_not_full_screen", { display_surface: "window", restarted: false });
      },
    });
    socket.push(stateMsg("live", "listening"));
    await vi.advanceTimersByTimeAsync(2000);
    const events = socket
      .json()
      .filter((m) => m.type === "integrity.events")
      .flatMap((m) => m.events as Array<{ event_type: string; details: Record<string, unknown> }>);
    expect(events.map((e) => e.event_type)).toEqual(expect.arrayContaining(["screen_share_started", "screen_share_not_full_screen"]));
    expect(events.find((e) => e.event_type === "screen_share_not_full_screen")?.details).toMatchObject({ display_surface: "window" });
    runtime.dispose();
  });

  it("says clearly when this browser can't record the shared screen", async () => {
    const base = effectivePolicy(null, null, null);
    const policy = effectivePolicy(
      {
        integrity: { ...base.integrity, require_fullscreen: false, camera_required: false, camera_frame_analysis: false, screen_share_required: true },
        recording: { ...base.recording, record_screen: true },
      },
      null,
      { screen_share: true },
    );
    expect(policy.recordScreen).toBe(true);
    const screenShare = { mediaStream: {} as MediaStream, displaySurface: "monitor", stop: vi.fn() } as unknown as NonNullable<Parameters<typeof startRuntime>[0]>["screenShare"];
    const { socket, store, runtime } = await startRuntime({ policy, screenShare });
    // jsdom has no MediaRecorder: the candidate and the hiring team are told, the interview goes on.
    expect(store.getState().notices.find((n) => n.code === "screen_recording_unsupported")?.message).toMatch(/can't record your shared screen/);
    expect(socket.json().find((m) => m.type === "client.error")).toMatchObject({ component: "screen_share", fatal: false });
    runtime.dispose();
  });

  it("reports delivery for an utterance whose audio never arrives", async () => {
    const { socket, runtime } = await startRuntime();
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "ai.utterance.start", utterance_id: "u4", kind: "question" }));
    socket.push(msg({ type: "ai.text.delta", utterance_id: "u4", text: "Could you walk me through it?" }));
    socket.push(msg({ type: "ai.utterance.end", utterance_id: "u4", text: "Could you walk me through it?" }));
    await vi.advanceTimersByTimeAsync(2400);
    expect(socket.types()).not.toContain("playback.started");
    // After the grace period the words are shown as text, with a reading pause before "delivered".
    await vi.advanceTimersByTimeAsync(200);
    expect(socket.json().find((m) => m.type === "playback.started")).toMatchObject({ utterance_id: "u4" });
    await vi.advanceTimersByTimeAsync(2200);
    expect(socket.json().find((m) => m.type === "playback.finished")).toMatchObject({ utterance_id: "u4" });
    runtime.dispose();
  });

  it("sends controls, typed answers and coding actions", async () => {
    const { socket, store, runtime } = await startRuntime();
    socket.push(stateMsg("live", "listening"));
    runtime.endTurn();
    runtime.repeat();
    expect(runtime.sendTypedAnswer("   ")).toBe(false);
    expect(runtime.sendTypedAnswer("I would add a read replica.")).toBe(true);
    runtime.codeRun("c1", "test", "print(1)", "python");
    runtime.hintRequest("c1");
    runtime.codeLanguage("c1", "go");
    runtime.codeUpdate("c1", { code: "x", language: "go", version: 3, cursorLine: 2 });
    runtime.codeSubmit("c1", "x", "go");
    expect(socket.types()).toEqual(expect.arrayContaining(["control.end_turn", "control.repeat", "answer.text", "code.run", "code.hint_request", "code.language", "code.update", "code.submit"]));
    expect(socket.json().find((m) => m.type === "answer.text")).toMatchObject({ text: "I would add a read replica." });
    expect(socket.json().find((m) => m.type === "code.update")).toMatchObject({ coding_session_id: "c1", version: 3, cursor_line: 2 });
    expect(store.getState().candidate.segments.at(-1)?.text).toBe("I would add a read replica.");
    runtime.dispose();
  });

  it("batches integrity observations over the socket", async () => {
    const { socket, runtime } = await startRuntime();
    socket.push(stateMsg("live", "listening"));
    await vi.advanceTimersByTimeAsync(9000); // past the start-up grace for permission prompts
    window.dispatchEvent(new Event("blur"));
    await vi.advanceTimersByTimeAsync(1200);
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(1600);
    const batch = socket.json().find((m) => m.type === "integrity.events");
    expect(batch).toBeTruthy();
    expect((batch!.events as Array<{ event_type: string; duration_ms: number }>)[0]).toMatchObject({ event_type: "focus_lost", duration_ms: 1200 });
    runtime.dispose();
  });

  it("leaves the room after the session ends and the closing finishes", async () => {
    const onEnded = vi.fn();
    const { socket, store } = await startRuntime({ onEnded });
    socket.push(stateMsg("live", "ai_speaking"));
    socket.push(msg({ type: "session.ended", reason: "time_limit", report_status: "pending", candidate_report_available: false }));
    expect(store.getState().ended?.reason).toBe("time_limit");
    await vi.advanceTimersByTimeAsync(1000);
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("retries a failed start a few times before leaving it to the candidate", async () => {
    const { socket } = await startRuntime();
    socket.push(stateMsg("ready"));
    const starts = () => socket.types().filter((t) => t === "control.start").length;
    expect(starts()).toBe(1);
    socket.push(msg({ type: "error", code: "start_failed", message: "The interview could not start.", recoverable: true }));
    await vi.advanceTimersByTimeAsync(1600);
    expect(starts()).toBe(2);
    for (let i = 0; i < 3; i++) {
      socket.push(msg({ type: "error", code: "start_failed", message: "The interview could not start.", recoverable: true }));
      await vi.advanceTimersByTimeAsync(10_000);
    }
    expect(starts()).toBe(4); // the first attempt plus three retries, then the notice stands
  });

  it("ends over REST when the socket is down, and leaves", async () => {
    const onEnded = vi.fn();
    const endSession = vi.fn(async () => ({ status: "ending" }));
    const { socket, runtime } = await startRuntime({ onEnded, api: { endSession } });
    socket.push(stateMsg("live", "listening"));
    socket.onclose?.({ code: 1006, reason: "" }); // the network dropped; the client is reconnecting
    runtime.endInterview();
    expect(endSession).toHaveBeenCalledWith("s1", "candidate_ended");
    expect(socket.json().some((m) => m.type === "control.end_interview")).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(onEnded).toHaveBeenCalled();
  });

  it("asks to end, and leaves even if the server never confirms", async () => {
    const onEnded = vi.fn();
    const { socket, runtime } = await startRuntime({ onEnded });
    socket.push(stateMsg("live", "listening"));
    runtime.endInterview();
    expect(socket.json().find((m) => m.type === "control.end_interview")).toMatchObject({ reason: "candidate_ended" });
    await vi.advanceTimersByTimeAsync(13_000);
    expect(onEnded).toHaveBeenCalled();
  });
});

describe("InterviewRuntime before the interview starts", () => {
  it("leaves over REST without ending anything when End is pressed before the start", async () => {
    const onEnded = vi.fn();
    const endSession = vi.fn(async () => ({ status: "not_started" }));
    const { socket, runtime } = await startRuntime({ onEnded, api: { endSession } });
    socket.push(stateMsg("ready")); // Begin sent control.start; the server is still preparing the interview
    runtime.endInterview();
    expect(endSession).toHaveBeenCalledWith("s1", "candidate_ended"); // the server answers not_started
    expect(socket.json().some((m) => m.type === "control.end_interview")).toBe(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(onEnded).toHaveBeenCalled(); // no 12 s wait for a refusal
  });

  it("releases the devices and never connects when the room is left during the permission prompt", async () => {
    const track = { stop: vi.fn(), kind: "audio" };
    const stream = { getTracks: () => [track], getAudioTracks: () => [track], getVideoTracks: () => [] };
    let grant!: (value: unknown) => void;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(() => new Promise((resolve) => (grant = resolve))) },
    });
    const runtime = new InterviewRuntime({
      interviewId: "i1",
      sessionId: "s1",
      store: createInterviewStore(),
      policy: effectivePolicy({ integrity: { ...effectivePolicy(null, null, null).integrity, require_fullscreen: false, camera_required: false, camera_frame_analysis: false } }, null, null),
      prefs: { avatarMode: "3d", avatarFps: 60, ttsMode: "server" },
      api: { fetchTicket: async () => ({ ticket: "tk", ws_url: "/ws/sessions/s1" }), postIntegrityEvents: vi.fn(), uploadFrame: vi.fn(), uploadRecording: vi.fn() },
    });
    runtime.prepareGesture();
    const begun = runtime.begin();
    await vi.advanceTimersByTimeAsync(0);
    runtime.dispose(); // End pressed (or the page left) while the browser still shows the prompt
    grant(stream); // the candidate clicks Allow afterwards
    await begun;
    await vi.advanceTimersByTimeAsync(0);
    expect(track.stop).toHaveBeenCalled(); // microphone released at once
    expect(FakeSocket.all).toHaveLength(0); // and the room never connects
  });
});

describe("frameUploadDecision", () => {
  const err = (status: number, code = "x", retry: number | null = null) => new ApiError({ status, code, message: code, retryAfterSeconds: retry });
  it("stops on refusal or a finished session, backs off on rate limits and rejected frames", () => {
    expect(frameUploadDecision(err(403, "frames_not_enabled"))).toBeInstanceOf(StopUploadsError);
    expect(frameUploadDecision(err(403, "consent_required"))).toBeInstanceOf(StopUploadsError);
    expect(frameUploadDecision(err(409, "session_ended"))).toBeInstanceOf(StopUploadsError);
    const limited = frameUploadDecision(err(429, "rate_limited", 12));
    expect(limited).toBeInstanceOf(BackoffUploadsError);
    expect((limited as BackoffUploadsError).delayMs).toBe(12_000);
    expect((frameUploadDecision(err(413)) as BackoffUploadsError).delayMs).toBe(30_000);
    expect(frameUploadDecision(err(500))).not.toBeInstanceOf(StopUploadsError);
    expect(frameUploadDecision(new Error("network"))).not.toBeInstanceOf(BackoffUploadsError);
  });
});
