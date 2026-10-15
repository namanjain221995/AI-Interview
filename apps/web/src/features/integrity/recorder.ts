/**
 * Session recording in parts. Used only when the recording policy is on AND the
 * candidate consented (the server checks both on every upload).
 *
 * Every `start()` is a new *part* (page load, reload, camera lost -> audio only,
 * screen shared again): a fresh MediaRecorder run with its own `partId`, whose
 * timeslices are *segments* numbered from 0. Each segment is uploaded with
 * timing metadata (media time within the part, the interview clock, the
 * browser clock) so staff can line the recording up with the transcript.
 *
 * Uploads run one at a time in index order. A failed upload is retried with
 * backoff (the server answers a repeated segment as a duplicate, so retries
 * are safe); a segment that still fails is dropped (a gap) and recording goes
 * on, except for segment 0, which carries the container header. Answers that
 * mean "never" (recording not allowed, session over, part closed) stop the
 * recorder and report `onFatal`. `stop()` flushes the last timeslice, waits
 * for the uploads, then tells the server the part is complete. The interview
 * never depends on any of this.
 */
import { isApiError } from "@/lib/api";

export type RecordingKind = "camera" | "screen";

export interface RecordingSegmentMeta {
  partId: string;
  index: number;
  kind: RecordingKind;
  /** Browser clock (ISO) when this part started: media time 0. */
  partStartedAt: string;
  /** Media time covered by the segment, ms from the part start. */
  mediaStartMs: number;
  mediaEndMs: number;
  /** Interview clock (ms, the clock of transcript times) at the segment end, when known. */
  sessionMs: number | null;
  audioOnly: boolean;
  mimeType: string;
}

export interface RecordingCompletion {
  partId: string;
  kind: RecordingKind;
  segments: number;
}

export interface RecordingFatal {
  reason: string;
  /** True when retrying can never help (not allowed, session over). */
  permanent: boolean;
}

export type RecorderState = "idle" | "recording" | "stopping" | "stopped" | "failed";

export interface SessionRecorderOptions {
  stream: MediaStream;
  upload: (segment: Blob, meta: RecordingSegmentMeta) => Promise<void>;
  /** Called once after `stop()` when every kept segment was uploaded. */
  complete?: (info: RecordingCompletion) => Promise<void>;
  timesliceMs?: number;
  /** Camera (with the mixed conversation audio, or audio only) or the shared screen. */
  kind?: RecordingKind;
  /** First segment number (default 0). */
  startIndex?: number;
  /** Interview clock in ms, or null while it is not known (paused, reconnecting). */
  sessionClock?: () => number | null;
  /** Called with each segment number as it is queued for upload. */
  onSegment?: (index: number) => void;
  onFatal?: (fatal: RecordingFatal) => void;
  /** Fixed part id (tests); a random UUID otherwise. */
  partId?: string;
  /** Segments waiting for upload beyond this many bytes are dropped (gap). */
  maxPendingBytes?: number;
  /** Backoff before each retry of a failed upload. */
  retryDelaysMs?: readonly number[];
  /** Monotonic clock in ms (tests). */
  now?: () => number;
}

const DEFAULT_TIMESLICE_MS = 10_000;
const DEFAULT_MAX_PENDING = 48 * 1024 * 1024;
const DEFAULT_RETRY_DELAYS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000] as const;
const STOP_DRAIN_TIMEOUT_MS = 20_000;
/** Server codes that mean recording this part can never succeed. */
// recording_quota_exceeded / storage_full (S3): the server will not take more of this recording; stop cleanly
// instead of retrying (the interview itself continues).
const PERMANENT_CODES = new Set(["recording_not_enabled", "consent_required", "session_ended", "recording_part_closed", "session_not_found", "recording_quota_exceeded", "storage_full"]);

const MIME_TYPES = {
  // VP8 first: far cheaper to encode than VP9 on the candidate's machine.
  camera: ["video/webm;codecs=vp8,opus", "video/webm;codecs=vp9,opus", "video/webm", "video/mp4;codecs=avc1,mp4a.40.2", "video/mp4"],
  audio: ["audio/webm;codecs=opus", "audio/webm", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"],
  screen: ["video/webm;codecs=vp8", "video/webm;codecs=vp9", "video/webm", "video/mp4"],
} as const;

type Flavor = keyof typeof MIME_TYPES;

function flavorOf(kind: RecordingKind, audioOnly: boolean): Flavor {
  if (kind === "screen") return "screen";
  return audioOnly ? "audio" : "camera";
}

export function pickMimeType(kind: RecordingKind = "camera", audioOnly = false): string | undefined {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") return undefined;
  return MIME_TYPES[flavorOf(kind, audioOnly)].find((type) => MediaRecorder.isTypeSupported(type));
}

export function newPartId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoApi && typeof cryptoApi.getRandomValues === "function") cryptoApi.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export type UploadDecision = { action: "retry"; delayMs?: number } | { action: "skip" } | { action: "done" } | { action: "fatal"; fatal: RecordingFatal };

/** What to do after a failed segment upload. */
export function uploadDecision(error: unknown): UploadDecision {
  if (!isApiError(error)) return { action: "retry" };
  if (error.status === 409 && error.code === "segment_exists") return { action: "done" };
  if (PERMANENT_CODES.has(error.code) || [401, 403, 404, 410].includes(error.status)) {
    return { action: "fatal", fatal: { reason: error.code || `http_${error.status}`, permanent: true } };
  }
  if (error.status === 429) return { action: "retry", delayMs: Math.max(2, error.retryAfterSeconds ?? 5) * 1000 };
  if ([400, 413, 415, 422].includes(error.status)) return { action: "skip" };
  return { action: "retry" };
}

interface Pending {
  blob: Blob;
  meta: RecordingSegmentMeta;
}

export class SessionRecorder {
  private recorder: MediaRecorder | null = null;
  private nextIndex: number;
  private readonly queue: Pending[] = [];
  private pendingBytes = 0;
  private pumping: Promise<void> | null = null;
  private stopPromise: Promise<void> | null = null;
  private stoppedResolve: (() => void) | null = null;
  private startedAtMs = 0;
  private lastEndMs = 0;
  private uploadedCount = 0;
  private droppedCount = 0;
  private fatal: RecordingFatal | null = null;
  private status: RecorderState = "idle";
  readonly partId: string;
  readonly kind: RecordingKind;
  readonly audioOnly: boolean;
  private mimeType = "";
  private partStartedAt = "";

  constructor(private readonly opts: SessionRecorderOptions) {
    this.nextIndex = Math.max(0, Math.floor(opts.startIndex ?? 0));
    this.partId = opts.partId ?? newPartId();
    this.kind = opts.kind ?? "camera";
    this.audioOnly = this.kind === "camera" && !opts.stream.getVideoTracks().some((t) => t.readyState !== "ended");
  }

  static isSupported(kind: RecordingKind = "camera", audioOnly = false): boolean {
    return typeof MediaRecorder !== "undefined" && Boolean(pickMimeType(kind, audioOnly));
  }

  get state(): RecorderState {
    return this.status;
  }

  /** Segments uploaded so far. */
  get segmentsUploaded(): number {
    return this.uploadedCount;
  }

  get segmentsDropped(): number {
    return this.droppedCount;
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : performance.now();
  }

  /** Starts recording; false when the browser cannot record this stream. */
  start(): boolean {
    if (this.recorder || this.status !== "idle") return false;
    const mimeType = pickMimeType(this.kind, this.audioOnly);
    if (!mimeType) return false;
    const bitrates: MediaRecorderOptions =
      this.kind === "screen"
        ? { videoBitsPerSecond: 400_000 }
        : this.audioOnly
          ? { audioBitsPerSecond: 48_000 }
          : { videoBitsPerSecond: 600_000, audioBitsPerSecond: 48_000 };
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(this.opts.stream, { mimeType, ...bitrates });
    } catch {
      return false;
    }
    this.mimeType = recorder.mimeType || mimeType;
    this.partStartedAt = new Date().toISOString();
    this.startedAtMs = this.now();
    this.lastEndMs = 0;
    recorder.ondataavailable = (event) => this.onData(event.data);
    recorder.onstop = () => {
      this.stoppedResolve?.();
      this.stoppedResolve = null;
    };
    recorder.onerror = () => {
      // The browser gave up on this stream (device lost, encoder error): end the part cleanly.
      void this.stop();
    };
    try {
      recorder.start(this.opts.timesliceMs ?? DEFAULT_TIMESLICE_MS);
    } catch {
      return false;
    }
    this.recorder = recorder;
    this.status = "recording";
    return true;
  }

  private onData(blob: Blob | null): void {
    if (!blob || blob.size === 0 || this.fatal) return;
    const endMs = Math.max(this.lastEndMs, Math.round(this.now() - this.startedAtMs));
    const index = this.nextIndex++;
    let sessionMs: number | null = null;
    try {
      const value = this.opts.sessionClock?.() ?? null;
      sessionMs = typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.round(value) : null;
    } catch {
      sessionMs = null;
    }
    const meta: RecordingSegmentMeta = {
      partId: this.partId,
      index,
      kind: this.kind,
      partStartedAt: this.partStartedAt,
      mediaStartMs: this.lastEndMs,
      mediaEndMs: endMs,
      sessionMs,
      audioOnly: this.audioOnly,
      mimeType: blob.type || this.mimeType,
    };
    this.lastEndMs = endMs;
    const limit = this.opts.maxPendingBytes ?? DEFAULT_MAX_PENDING;
    if (index > 0 && this.pendingBytes + blob.size > limit) {
      // Offline for too long: keep memory bounded, keep the header and the order.
      this.droppedCount += 1;
      return;
    }
    this.opts.onSegment?.(index);
    this.queue.push({ blob, meta });
    this.pendingBytes += blob.size;
    this.pump();
  }

  private pump(): void {
    if (this.pumping) return;
    this.pumping = (async () => {
      while (this.queue.length > 0 && !this.fatal) {
        const item = this.queue[0]!;
        await this.uploadOne(item);
        this.queue.shift();
        this.pendingBytes -= item.blob.size;
      }
      this.pumping = null;
    })();
  }

  private async uploadOne(item: Pending): Promise<void> {
    const delays = this.opts.retryDelaysMs ?? DEFAULT_RETRY_DELAYS;
    for (let attempt = 0; ; attempt++) {
      try {
        await this.opts.upload(item.blob, item.meta);
        this.uploadedCount += 1;
        return;
      } catch (error) {
        const decision = uploadDecision(error);
        if (decision.action === "done") {
          this.uploadedCount += 1;
          return;
        }
        if (decision.action === "fatal") return this.fail(decision.fatal);
        if (decision.action === "skip" || attempt >= delays.length) {
          if (item.meta.index === 0) return this.fail({ reason: decision.action === "skip" ? "header_rejected" : "header_upload_failed", permanent: false });
          this.droppedCount += 1;
          return;
        }
        const wait = decision.delayMs ?? delays[attempt]!;
        await new Promise((resolve) => setTimeout(resolve, wait));
        if (this.fatal) return;
      }
    }
  }

  private fail(fatal: RecordingFatal): void {
    if (this.fatal) return;
    this.fatal = fatal;
    this.status = "failed";
    this.queue.splice(1);
    this.stopRecorder();
    this.opts.onFatal?.(fatal);
  }

  private stopRecorder(): Promise<void> {
    const recorder = this.recorder;
    this.recorder = null;
    if (!recorder || recorder.state === "inactive") return Promise.resolve();
    const stopped = new Promise<void>((resolve) => {
      this.stoppedResolve = resolve;
      // Some browsers never fire "stop" for a recorder whose tracks already ended.
      setTimeout(resolve, 3_000);
    });
    try {
      recorder.stop();
    } catch {
      this.stoppedResolve?.();
      this.stoppedResolve = null;
    }
    return stopped;
  }

  /**
   * Ends the part: flushes the last timeslice, waits (bounded) for the uploads,
   * then reports the part complete. Safe to call more than once.
   */
  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    if (this.status === "idle") {
      this.status = "stopped";
      return (this.stopPromise = Promise.resolve());
    }
    if (this.status === "recording") this.status = "stopping";
    this.stopPromise = (async () => {
      await this.stopRecorder();
      const deadline = this.now() + STOP_DRAIN_TIMEOUT_MS;
      while ((this.pumping || this.queue.length > 0) && !this.fatal && this.now() < deadline) {
        await Promise.race([this.pumping ?? Promise.resolve(), new Promise((resolve) => setTimeout(resolve, 250))]);
      }
      if (this.fatal) return;
      this.status = "stopped";
      if (this.uploadedCount > 0 && this.queue.length === 0) {
        try {
          await this.opts.complete?.({ partId: this.partId, kind: this.kind, segments: this.uploadedCount });
        } catch {
          // The server sweep finalizes parts on its own; completion only speeds it up.
        }
      }
    })();
    return this.stopPromise;
  }
}
