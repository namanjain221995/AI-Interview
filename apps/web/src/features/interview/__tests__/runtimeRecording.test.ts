/**
 * InterviewRuntime recording wiring: camera picture + one audio track mixing
 * the interviewer with the microphone, part timing from the interview clock,
 * server confirmation, camera loss -> audio-only part, stop + completion at the
 * end. Fake WebSocket, AudioContext, MediaStream and MediaRecorder.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RecordingUploadMeta } from "@/features/candidate/api";
import { effectivePolicy, type ConsentFlags } from "@/features/integrity/policy";
import { createInterviewStore } from "@/features/realtime/store";

import { InterviewRuntime } from "../runtime";

// ------------------------------------------------------------------ fakes

class FakeTrack {
  readyState: "live" | "ended" = "live";
  private listeners = new Set<() => void>();
  constructor(
    readonly kind: "audio" | "video",
    readonly label: string,
  ) {}
  addEventListener(_type: string, fn: () => void) {
    this.listeners.add(fn);
  }
  removeEventListener(_type: string, fn: () => void) {
    this.listeners.delete(fn);
  }
  stop() {
    this.readyState = "ended";
  }
  end() {
    this.readyState = "ended";
    for (const fn of [...this.listeners]) fn();
  }
}

class FakeStream {
  readonly tracks: FakeTrack[];
  constructor(tracks: FakeTrack[] = []) {
    this.tracks = [...tracks];
  }
  getTracks() {
    return this.tracks;
  }
  getAudioTracks() {
    return this.tracks.filter((t) => t.kind === "audio");
  }
  getVideoTracks() {
    return this.tracks.filter((t) => t.kind === "video");
  }
}

class FakeNode {
  connected: FakeNode[] = [];
  constructor(readonly name = "node") {}
  connect<T extends FakeNode>(n: T): T {
    this.connected.push(n);
    return n;
  }
  disconnect() {
    this.connected = [];
  }
}

class FakeAudioContext {
  static mixTracks: FakeTrack[] = [];
  currentTime = 0;
  state: AudioContextState = "running";
  destination = new FakeNode("speakers");
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    return Promise.resolve();
  }
  createGain() {
    return Object.assign(new FakeNode("gain"), { gain: { value: 1, setTargetAtTime() {}, cancelScheduledValues() {} } });
  }
  createAnalyser() {
    return Object.assign(new FakeNode("analyser"), { fftSize: 1024, smoothingTimeConstant: 0, getFloatTimeDomainData: (a: Float32Array) => a.fill(0) });
  }
  createBuffer(_c: number, length: number) {
    return { length, copyToChannel() {} };
  }
  createBufferSource() {
    return Object.assign(new FakeNode("source"), { buffer: null, onended: null, start() {}, stop() {} });
  }
  createMediaStreamDestination() {
    const track = new FakeTrack("audio", "mix");
    FakeAudioContext.mixTracks.push(track);
    return Object.assign(new FakeNode("recording"), { stream: new FakeStream([track]), channelCount: 2, channelCountMode: "max", channelInterpretation: "speakers" });
  }
  createMediaStreamSource() {
    return new FakeNode("microphone");
  }
}

class FakeMediaRecorder {
  static all: FakeMediaRecorder[] = [];
  static isTypeSupported(type: string) {
    return ["video/webm;codecs=vp8,opus", "audio/webm;codecs=opus", "video/webm;codecs=vp8"].includes(type);
  }
  state: "inactive" | "recording" = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly mimeType: string;
  constructor(
    readonly stream: FakeStream,
    options: MediaRecorderOptions,
  ) {
    this.mimeType = options.mimeType ?? "";
    FakeMediaRecorder.all.push(this);
  }
  start() {
    this.state = "recording";
  }
  emit(bytes = 10) {
    this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)], { type: this.mimeType }) });
  }
  stop() {
    if (this.state === "inactive") return;
    this.state = "inactive";
    this.emit(3);
    this.onstop?.();
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
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send() {}
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
}

let seq = 0;
const stateMsg = (status: string, turn: string, elapsed: number, integrity: Record<string, unknown> = {}) => ({
  seq: ++seq,
  type: "session.state",
  session_id: "s1",
  status,
  turn_state: turn,
  round_name: "Round",
  round_type: "combined",
  interviewer_name: "Maya",
  elapsed_s: elapsed,
  remaining_s: 1800 - elapsed,
  time_limit_s: 1800,
  show_subtitles: true,
  allow_typed_answers: true,
  integrity,
  avatar_state: "IDLE",
});

const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};

let camera: FakeTrack;
let microphone: FakeTrack;

beforeEach(() => {
  vi.useFakeTimers();
  seq = 0;
  FakeSocket.all = [];
  FakeMediaRecorder.all = [];
  FakeAudioContext.mixTracks = [];
  camera = new FakeTrack("video", "camera");
  microphone = new FakeTrack("audio", "microphone");
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("MediaStream", FakeStream);
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  // jsdom has no media playback (the camera frame sampler plays a hidden <video>).
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async (constraints: MediaStreamConstraints) => new FakeStream(constraints.video ? [microphone, camera] : [microphone])),
    },
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function policyFor(consent: ConsentFlags | null, recording: Record<string, boolean> = {}) {
  const base = effectivePolicy(null, null, null);
  return effectivePolicy(
    {
      integrity: { ...base.integrity, require_fullscreen: false, camera_required: false, camera_frame_analysis: false },
      recording: { ...base.recording, record_video: true, ...recording },
    },
    null,
    consent,
  );
}

async function start(consent: ConsentFlags | null = { video_recording: true }) {
  const store = createInterviewStore();
  const uploadRecording = vi.fn(async (..._args: [string, Blob, number, string, ("camera" | "screen")?, RecordingUploadMeta?]) => ({}));
  const completeRecording = vi.fn(async (..._args: [string, { partId: string; kind: string; segments: number }]) => ({}));
  const runtime = new InterviewRuntime({
    interviewId: "i1",
    sessionId: "s1",
    store,
    policy: policyFor(consent),
    prefs: { avatarMode: "3d", avatarFps: 60, ttsMode: "server" },
    api: {
      fetchTicket: async () => ({ ticket: "tk", ws_url: "/ws/sessions/s1" }),
      postIntegrityEvents: vi.fn(async () => ({ accepted: 0, warnings: [] })),
      uploadFrame: vi.fn(),
      uploadRecording,
      completeRecording,
    },
  });
  runtime.prepareGesture();
  await runtime.begin();
  await vi.advanceTimersByTimeAsync(0);
  const socket = FakeSocket.all.at(-1)!;
  socket.open();
  await flush();
  return { runtime, store, socket, uploadRecording, completeRecording };
}

describe("InterviewRuntime recording", () => {
  it("records the camera with one track mixing both voices, with part timing", async () => {
    const { runtime, socket, uploadRecording } = await start();
    const [recorder] = FakeMediaRecorder.all;
    expect(recorder).toBeDefined();
    const tracks = recorder!.stream.getTracks();
    expect(tracks.map((t) => t.label)).toEqual(["camera", "mix"]); // not the raw microphone alone
    expect(recorder!.mimeType).toBe("video/webm;codecs=vp8,opus");

    recorder!.emit();
    await flush();
    const first = uploadRecording.mock.calls[0]!;
    expect(first[0]).toBe("s1");
    expect(first[2]).toBe(0);
    expect(first[4]).toBe("camera");
    expect(first[5]).toMatchObject({ index: 0, audioOnly: false, sessionMs: null });

    socket.push(stateMsg("live", "listening", 30));
    await vi.advanceTimersByTimeAsync(2_000);
    recorder!.emit();
    await flush();
    const second = uploadRecording.mock.calls[1]![5]!;
    expect(second.partId).toBe(first[5]!.partId);
    expect(second.sessionMs).toBeGreaterThanOrEqual(31_900);
    expect(second.sessionMs).toBeLessThanOrEqual(32_100);
    expect(runtime.sessionClockMs()).not.toBeNull();
    runtime.dispose();
  });

  it("stops the clock samples while reconnecting", async () => {
    const { runtime, socket } = await start();
    socket.push(stateMsg("live", "listening", 10));
    expect(runtime.sessionClockMs()).toBe(10_000);
    socket.readyState = 3;
    socket.onclose?.({ code: 1006, reason: "" });
    await flush();
    expect(runtime.sessionClockMs()).toBeNull();
    runtime.dispose();
  });

  it("finishes the part when the interview ends: last timeslice, then completion", async () => {
    const { socket, uploadRecording, completeRecording } = await start();
    const recorder = FakeMediaRecorder.all[0]!;
    recorder.emit();
    socket.push(stateMsg("completed", "ended", 600));
    await vi.advanceTimersByTimeAsync(500);
    expect(recorder.state).toBe("inactive");
    expect(uploadRecording).toHaveBeenCalledTimes(2);
    const partId = uploadRecording.mock.calls[0]![5]!.partId;
    expect(completeRecording).toHaveBeenCalledWith("s1", { partId, kind: "camera", segments: 2 });
  });

  it("keeps recording the conversation as audio only when the camera goes away", async () => {
    const { runtime, uploadRecording, completeRecording } = await start();
    const first = FakeMediaRecorder.all[0]!;
    first.emit();
    await flush();
    camera.end();
    await vi.advanceTimersByTimeAsync(500);
    expect(first.state).toBe("inactive");
    expect(completeRecording).toHaveBeenCalledTimes(1);
    const second = FakeMediaRecorder.all[1]!;
    expect(second.stream.getTracks().map((t) => t.label)).toEqual(["mix"]);
    expect(second.mimeType).toBe("audio/webm;codecs=opus");
    second.emit();
    await flush();
    const meta = uploadRecording.mock.calls.at(-1)![5]!;
    expect(meta.audioOnly).toBe(true);
    expect(meta.partId).not.toBe(uploadRecording.mock.calls[0]![5]!.partId);
    expect(runtime.recordingReason).toBe("camera_ended");
    runtime.dispose();
  });

  it("waits for consent: starts only when the server confirms it, stops when the server refuses", async () => {
    const { runtime, socket } = await start(null);
    expect(FakeMediaRecorder.all).toHaveLength(0); // no local record of consent (other device)
    socket.push(stateMsg("live", "listening", 5, { record_video: true }));
    await vi.advanceTimersByTimeAsync(0);
    await flush();
    const recorder = FakeMediaRecorder.all[0]!;
    // Nothing needed the camera here, so it was never opened: the conversation is recorded as audio.
    expect(recorder.stream.getTracks().map((t) => t.label)).toEqual(["mix"]);
    socket.push(stateMsg("live", "listening", 6, { record_video: false }));
    await vi.advanceTimersByTimeAsync(500);
    expect(recorder.state).toBe("inactive");
    runtime.dispose();
  });

  it("records the camera the interview already uses once the server confirms consent", async () => {
    const store = createInterviewStore();
    const base = effectivePolicy(null, null, null);
    // Camera required by the integrity policy (so it is open), no consent record in this tab.
    const policy = effectivePolicy({ integrity: { ...base.integrity, require_fullscreen: false, camera_frame_analysis: false }, recording: { ...base.recording, record_video: true } }, null, null);
    const runtime = new InterviewRuntime({
      interviewId: "i1",
      sessionId: "s1",
      store,
      policy,
      prefs: { avatarMode: "3d", avatarFps: 60, ttsMode: "server" },
      api: { fetchTicket: async () => ({ ticket: "tk", ws_url: "/ws/sessions/s1" }), postIntegrityEvents: vi.fn(async () => ({ accepted: 0, warnings: [] })), uploadFrame: vi.fn(), uploadRecording: vi.fn(async () => ({})), completeRecording: vi.fn(async () => ({})) },
    });
    runtime.prepareGesture();
    await runtime.begin();
    await vi.advanceTimersByTimeAsync(0);
    const socket = FakeSocket.all.at(-1)!;
    socket.open();
    socket.push(stateMsg("live", "listening", 5, { record_video: true }));
    await vi.advanceTimersByTimeAsync(0);
    await flush();
    expect(FakeMediaRecorder.all[0]!.stream.getTracks().map((t) => t.label)).toEqual(["camera", "mix"]);
    runtime.dispose();
  });

  it("does not record when the candidate declined or the policy is off", async () => {
    const { runtime } = await start({ video_recording: false });
    expect(FakeMediaRecorder.all).toHaveLength(0);
    runtime.dispose();
    const declinedCamera = await start({ video_recording: true, camera: false });
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenLastCalledWith(expect.objectContaining({ video: false }));
    expect(FakeMediaRecorder.all[0]!.stream.getTracks().map((t) => t.label)).toEqual(["mix"]);
    declinedCamera.runtime.dispose();
  });
});
