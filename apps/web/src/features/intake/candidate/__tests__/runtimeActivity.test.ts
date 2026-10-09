/**
 * InterviewRuntime and intake activities, inside jsdom with a fake WebSocket and
 * AudioContext (same harness as features/interview/__tests__/runtime.test.ts):
 * activity.finish, the local hide at the countdown zero, hiding on a dropped
 * connection, and the typed-answer rule.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewRuntime } from "@/features/interview/runtime";
import { effectivePolicy } from "@/features/integrity/policy";
import { createInterviewStore } from "@/features/realtime/store";

import { PASSAGE_B } from "./fixtures";

class FakeNode {
  connect<T>(n: T): T {
    return n;
  }
  disconnect() {}
}

class FakeAudioContext {
  currentTime = 0;
  state: AudioContextState = "running";
  destination = new FakeNode();
  resume() {
    return Promise.resolve();
  }
  close() {
    return Promise.resolve();
  }
  createGain() {
    return Object.assign(new FakeNode(), { gain: { value: 1, setTargetAtTime() {}, cancelScheduledValues() {} } });
  }
  createAnalyser() {
    return Object.assign(new FakeNode(), { fftSize: 1024, smoothingTimeConstant: 0, getFloatTimeDomainData: (a: Float32Array) => a.fill(0) });
  }
  createBuffer(_ch: number, length: number) {
    return { length, copyToChannel() {} };
  }
  createBufferSource() {
    return Object.assign(new FakeNode(), { buffer: null, onended: null, start() {}, stop() {} });
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
    // A live server answers heartbeats (the client treats 20 s of silence as a dead socket).
    if (typeof d === "string" && d.includes('"heartbeat"')) {
      const t = (JSON.parse(d) as { t: number }).t;
      queueMicrotask(() => this.readyState === 1 && this.push({ type: "heartbeat.ack", seq: 0, t, server_time: Date.now() }));
    }
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
  drop() {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: "" });
  }
  json(): Array<Record<string, unknown>> {
    return this.sent.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d) as Record<string, unknown>);
  }
}

let seq = 0;
const msg = (m: Record<string, unknown>) => ({ seq: ++seq, ...m });

async function startRuntime() {
  const store = createInterviewStore();
  const runtime = new InterviewRuntime({
    interviewId: "i1",
    sessionId: "s1",
    store,
    policy: effectivePolicy({ integrity: { ...effectivePolicy(null, null, null).integrity, require_fullscreen: false, camera_required: false, camera_frame_analysis: false } }, null, null),
    prefs: { avatarMode: "none", avatarFps: null, ttsMode: "server" },
    avatar: null,
    api: { fetchTicket: async () => ({ ticket: "tk", ws_url: "/ws/sessions/s1" }), postIntegrityEvents: vi.fn(async () => ({ accepted: 0, warnings: [] })), uploadFrame: vi.fn(), uploadRecording: vi.fn() },
  });
  runtime.prepareGesture();
  await runtime.begin();
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.all.at(-1)!;
  socket.open();
  socket.push(
    msg({
      type: "session.state",
      session_id: "s1",
      status: "live",
      turn_state: "listening",
      round_name: "Intake Interview",
      round_type: "intake",
      interviewer_name: "Maya",
      elapsed_s: 600,
      remaining_s: 2100,
      time_limit_s: 2700,
      show_subtitles: true,
      allow_typed_answers: true,
      integrity: {},
      avatar_state: "LISTENING",
      intake: { stage: "reading", stage_number: 4, stage_title: "Reading exercise", stages: [], activity: null, answer_mode: "standard", soft_limit_s: null },
    }),
  );
  return { runtime, store, socket };
}

function startRelay(socket: FakeSocket, remaining = 30_000) {
  socket.push(msg({ type: "activity.start", activity_id: "a1", kind: "read_relay", stage: "reading", title: "Gradient Descent", phases: [{ phase: "read", duration_ms: 30_000, capture: false }, { phase: "relay", duration_ms: 150_000, capture: true }], status: "armed", attempt: 1, resumed: false }));
  socket.push(
    msg({
      type: "activity.phase",
      activity_id: "a1",
      kind: "read_relay",
      phase: "read",
      status: "running",
      duration_ms: 30_000,
      remaining_ms: remaining,
      capture: false,
      can_finish: true,
      content: { title: "Gradient Descent", paragraphs: PASSAGE_B },
      hide_content: false,
      server_time_ms: Date.now(),
    }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  seq = 0;
  FakeSocket.all = [];
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

describe("InterviewRuntime and intake activities", () => {
  it("removes the read-and-relay passage at its countdown zero even without the server's hide", async () => {
    const { runtime, store, socket } = await startRuntime();
    startRelay(socket, 30_000);
    expect(store.getState().activity?.content?.paragraphs).toEqual(PASSAGE_B);
    await vi.advanceTimersByTimeAsync(29_900);
    expect(store.getState().activity?.content).not.toBeNull();
    await vi.advanceTimersByTimeAsync(200);
    expect(store.getState().activity).toMatchObject({ content: null, hiddenBy: "countdown" });
    runtime.dispose();
  });

  it("re-arms the hide with the remaining time of a resumed reading phase", async () => {
    const { runtime, store, socket } = await startRuntime();
    startRelay(socket, 12_000);
    await vi.advanceTimersByTimeAsync(12_050);
    expect(store.getState().activity?.content).toBeNull();
    runtime.dispose();
  });

  it("hides the passage the moment the socket drops", async () => {
    const { runtime, store, socket } = await startRuntime();
    startRelay(socket);
    socket.drop();
    expect(store.getState().connection.status).not.toBe("connected");
    expect(store.getState().activity).toMatchObject({ content: null, hiddenBy: "disconnect" });
    runtime.dispose();
  });

  it("sends activity.finish once per press and waits for the server to end the phase", async () => {
    const { runtime, store, socket } = await startRuntime();
    startRelay(socket);
    expect(runtime.activityFinish("a1", "read")).toBe(true);
    expect(runtime.activityFinish("a1", "read")).toBe(false);
    expect(socket.json().filter((m) => m.type === "activity.finish")).toEqual([{ type: "activity.finish", activity_id: "a1", phase: "read" }]);
    expect(store.getState().activity).toMatchObject({ finishRequested: true });
    expect(store.getState().activity?.content).not.toBeNull();
    socket.push(msg({ type: "activity.phase", activity_id: "a1", kind: "read_relay", phase: "read", status: "ended", duration_ms: 30_000, remaining_ms: 0, capture: false, can_finish: false, content: null, hide_content: true, server_time_ms: 0 }));
    expect(store.getState().activity).toMatchObject({ content: null, status: "between" });
    await vi.advanceTimersByTimeAsync(700);
    expect(runtime.activityFinish("a1", "read")).toBe(true);
    runtime.dispose();
  });

  it("refuses typed answers while reading aloud or reading silently, and allows them in the relay", async () => {
    const { runtime, socket } = await startRuntime();
    socket.push(msg({ type: "activity.start", activity_id: "r1", kind: "reading_aloud", stage: "reading", title: "What Is an API?", phases: [{ phase: "read", duration_ms: 180_000, capture: true }], status: "armed", attempt: 1, resumed: false }));
    socket.push(msg({ type: "activity.phase", activity_id: "r1", kind: "reading_aloud", phase: "read", status: "running", duration_ms: 180_000, remaining_ms: 180_000, capture: true, can_finish: true, content: { title: "What Is an API?", paragraphs: ["An API..."] }, hide_content: false, server_time_ms: 0 }));
    expect(runtime.typingAllowed()).toBe(false);
    expect(runtime.sendTypedAnswer("I would rather type")).toBe(false);
    socket.push(msg({ type: "activity.end", activity_id: "r1", kind: "reading_aloud", reason: "completed" }));
    startRelay(socket);
    expect(runtime.sendTypedAnswer("typed during silent reading")).toBe(false);
    socket.push(msg({ type: "activity.phase", activity_id: "a1", kind: "read_relay", phase: "relay", status: "running", duration_ms: 150_000, remaining_ms: 150_000, capture: true, can_finish: true, content: null, hide_content: true, server_time_ms: 0 }));
    expect(runtime.sendTypedAnswer("The passage explains gradient descent.")).toBe(true);
    expect(socket.json().filter((m) => m.type === "answer.text").map((m) => m.text)).toEqual(["The passage explains gradient descent."]);
    runtime.dispose();
  });
});

describe("activity.finish while offline", () => {
  it("is dropped, never queued for after the reconnect", async () => {
    const { runtime, store, socket } = await startRuntime();
    startRelay(socket);
    socket.drop();
    expect(runtime.activityFinish("a1", "read")).toBe(false);
    expect(store.getState().activity?.finishRequested).toBe(false);
    await vi.advanceTimersByTimeAsync(5_000);
    const next = FakeSocket.all.at(-1)!;
    if (next !== socket) next.open();
    expect(FakeSocket.all.flatMap((s) => s.json()).filter((m) => m.type === "activity.finish")).toEqual([]);
    runtime.dispose();
  });
});
