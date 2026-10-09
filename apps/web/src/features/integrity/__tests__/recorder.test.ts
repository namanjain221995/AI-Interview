/**
 * SessionRecorder: parts, segment timing, ordered uploads with retries, fatal
 * answers, flush + completion on stop, bounded memory, MIME choice.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { pickMimeType, SessionRecorder, uploadDecision, type RecordingSegmentMeta } from "../recorder";

class FakeTrack {
  readyState: "live" | "ended" = "live";
  constructor(readonly kind: "audio" | "video") {}
}

class FakeStream {
  constructor(private readonly tracks: FakeTrack[]) {}
  getTracks() {
    return this.tracks;
  }
  getVideoTracks() {
    return this.tracks.filter((t) => t.kind === "video");
  }
  getAudioTracks() {
    return this.tracks.filter((t) => t.kind === "audio");
  }
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static supported = new Set(["video/webm;codecs=vp8,opus", "audio/webm;codecs=opus", "video/webm;codecs=vp8"]);
  static isTypeSupported(type: string) {
    return FakeMediaRecorder.supported.has(type);
  }
  state: "inactive" | "recording" = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  timeslice = 0;
  readonly mimeType: string;
  constructor(
    readonly stream: FakeStream,
    readonly options: MediaRecorderOptions,
  ) {
    this.mimeType = options.mimeType ?? "";
    FakeMediaRecorder.instances.push(this);
  }
  start(timeslice: number) {
    this.timeslice = timeslice;
    this.state = "recording";
  }
  emit(bytes: number) {
    this.ondataavailable?.({ data: new Blob([new Uint8Array(bytes)], { type: this.mimeType }) });
  }
  stop() {
    this.state = "inactive";
    this.emit(5);
    this.onstop?.();
  }
}

let clock = 0;
const flush = async () => {
  for (let i = 0; i < 5; i++) await Promise.resolve();
};

beforeEach(() => {
  vi.useFakeTimers();
  clock = 0;
  FakeMediaRecorder.instances = [];
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function cameraStream(video = true) {
  return new FakeStream(video ? [new FakeTrack("video"), new FakeTrack("audio")] : [new FakeTrack("audio")]) as unknown as MediaStream;
}

describe("SessionRecorder", () => {
  it("numbers segments within a part and sends timing with each", async () => {
    const uploads: RecordingSegmentMeta[] = [];
    let session: number | null = null;
    const recorder = new SessionRecorder({
      stream: cameraStream(),
      partId: "part-1",
      now: () => clock,
      sessionClock: () => session,
      upload: async (_blob, meta) => {
        uploads.push(meta);
      },
    });
    expect(recorder.start()).toBe(true);
    const media = FakeMediaRecorder.instances[0]!;
    expect(media.timeslice).toBe(10_000);
    expect(media.options).toMatchObject({ mimeType: "video/webm;codecs=vp8,opus", videoBitsPerSecond: 600_000, audioBitsPerSecond: 48_000 });
    clock = 10_000;
    media.emit(100); // the interview clock is not known yet (preparing)
    clock = 20_050;
    session = 18_000;
    media.emit(100);
    await flush();
    expect(uploads.map((m) => [m.index, m.mediaStartMs, m.mediaEndMs, m.sessionMs])).toEqual([
      [0, 0, 10_000, null],
      [1, 10_000, 20_050, 18_000],
    ]);
    expect(uploads[0]).toMatchObject({ partId: "part-1", kind: "camera", audioOnly: false, mimeType: "video/webm;codecs=vp8,opus" });
    expect(Date.parse(uploads[0]!.partStartedAt)).not.toBeNaN();
  });

  it("records audio only without a camera", () => {
    const recorder = new SessionRecorder({ stream: cameraStream(false), upload: async () => undefined });
    expect(recorder.audioOnly).toBe(true);
    expect(recorder.start()).toBe(true);
    expect(FakeMediaRecorder.instances[0]!.options).toEqual({ mimeType: "audio/webm;codecs=opus", audioBitsPerSecond: 48_000 });
    expect(pickMimeType("screen")).toBe("video/webm;codecs=vp8");
    FakeMediaRecorder.supported = new Set();
    expect(SessionRecorder.isSupported("camera", true)).toBe(false);
    expect(new SessionRecorder({ stream: cameraStream(false), upload: async () => undefined }).start()).toBe(false);
    FakeMediaRecorder.supported = new Set(["video/webm;codecs=vp8,opus", "audio/webm;codecs=opus", "video/webm;codecs=vp8"]);
  });

  it("retries a failed upload in order, then keeps going", async () => {
    const calls: number[] = [];
    let failures = 2;
    const recorder = new SessionRecorder({
      stream: cameraStream(),
      retryDelaysMs: [100, 200],
      upload: async (_b, meta) => {
        calls.push(meta.index);
        if (meta.index === 1 && failures-- > 0) throw new ApiError({ status: 0, code: "network_error", message: "offline" });
      },
    });
    recorder.start();
    const media = FakeMediaRecorder.instances[0]!;
    media.emit(10);
    media.emit(10);
    media.emit(10);
    await flush();
    expect(calls).toEqual([0, 1]);
    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(200);
    await flush();
    expect(calls).toEqual([0, 1, 1, 1, 2]);
    expect(recorder.segmentsUploaded).toBe(3);
  });

  it("drops a segment that keeps failing (a gap) but never the header", async () => {
    const fatal = vi.fn();
    const recorder = new SessionRecorder({
      stream: cameraStream(),
      retryDelaysMs: [10],
      onFatal: fatal,
      upload: async (_b, meta) => {
        if (meta.index === 1) throw new ApiError({ status: 503, code: "service_unavailable", message: "busy" });
      },
    });
    recorder.start();
    const media = FakeMediaRecorder.instances[0]!;
    media.emit(10);
    media.emit(10);
    media.emit(10);
    await vi.advanceTimersByTimeAsync(50);
    expect(recorder.segmentsUploaded).toBe(2);
    expect(recorder.segmentsDropped).toBe(1);
    expect(fatal).not.toHaveBeenCalled();

    const headerless = new SessionRecorder({
      stream: cameraStream(),
      retryDelaysMs: [10],
      onFatal: fatal,
      upload: async () => {
        throw new Error("offline");
      },
    });
    headerless.start();
    FakeMediaRecorder.instances[1]!.emit(10);
    await vi.advanceTimersByTimeAsync(50);
    expect(fatal).toHaveBeenCalledWith({ reason: "header_upload_failed", permanent: false });
    expect(headerless.state).toBe("failed");
    expect(FakeMediaRecorder.instances[1]!.state).toBe("inactive");
  });

  it("stops for good when the server refuses the recording", async () => {
    const fatal = vi.fn();
    const upload = vi.fn(async () => {
      throw new ApiError({ status: 403, code: "recording_not_enabled", message: "no" });
    });
    const recorder = new SessionRecorder({ stream: cameraStream(), onFatal: fatal, upload });
    recorder.start();
    FakeMediaRecorder.instances[0]!.emit(10);
    await flush();
    expect(fatal).toHaveBeenCalledWith({ reason: "recording_not_enabled", permanent: true });
    FakeMediaRecorder.instances[0]!.emit(10);
    await flush();
    expect(upload).toHaveBeenCalledTimes(1);
  });

  it("flushes the last timeslice on stop and reports the part complete", async () => {
    const uploaded: number[] = [];
    const complete = vi.fn(async () => undefined);
    const recorder = new SessionRecorder({
      stream: cameraStream(),
      partId: "p",
      upload: async (_b, meta) => {
        uploaded.push(meta.index);
      },
      complete,
    });
    recorder.start();
    FakeMediaRecorder.instances[0]!.emit(10);
    const stopping = recorder.stop();
    expect(recorder.stop()).toBe(stopping);
    await vi.advanceTimersByTimeAsync(300);
    await stopping;
    expect(uploaded).toEqual([0, 1]);
    expect(complete).toHaveBeenCalledWith({ partId: "p", kind: "camera", segments: 2 });
    expect(recorder.state).toBe("stopped");
  });

  it("treats an already stored segment as uploaded and skips rejected ones", () => {
    expect(uploadDecision(new ApiError({ status: 409, code: "segment_exists", message: "" }))).toEqual({ action: "done" });
    expect(uploadDecision(new ApiError({ status: 409, code: "session_ended", message: "" }))).toMatchObject({ action: "fatal", fatal: { permanent: true } });
    expect(uploadDecision(new ApiError({ status: 409, code: "recording_part_closed", message: "" }))).toMatchObject({ action: "fatal" });
    expect(uploadDecision(new ApiError({ status: 415, code: "unsupported_recording_type", message: "" }))).toEqual({ action: "skip" });
    expect(uploadDecision(new ApiError({ status: 429, code: "rate_limited", message: "", retryAfterSeconds: 7 }))).toEqual({ action: "retry", delayMs: 7000 });
    expect(uploadDecision(new TypeError("Failed to fetch"))).toEqual({ action: "retry" });
  });

  it("bounds memory while offline by dropping newer segments", async () => {
    let release: () => void = () => undefined;
    const recorder = new SessionRecorder({
      stream: cameraStream(),
      maxPendingBytes: 25,
      upload: () => new Promise<void>((resolve) => (release = resolve)),
    });
    recorder.start();
    const media = FakeMediaRecorder.instances[0]!;
    media.emit(10);
    media.emit(10);
    media.emit(10); // over the limit: dropped
    expect(recorder.segmentsDropped).toBe(1);
    release();
    await flush();
  });
});

describe("recording policy", () => {
  it("records the camera only when the candidate did not decline it", async () => {
    const { effectivePolicy } = await import("../policy");
    const snapshot = { recording: { record_answer_audio: true, record_video: true, record_screen: false, show_live_subtitles: true, allow_typed_answers: true } };
    expect(effectivePolicy(snapshot, null, { video_recording: true })).toMatchObject({ recordVideo: true, recordCamera: true });
    expect(effectivePolicy(snapshot, null, { video_recording: true, camera: false })).toMatchObject({ recordVideo: true, recordCamera: false });
    expect(effectivePolicy(snapshot, null, null)).toMatchObject({ recordVideo: false, recordCamera: false });
    expect(effectivePolicy(null, null, { video_recording: true }).recordVideo).toBe(true); // on by default, like the server
  });
});
