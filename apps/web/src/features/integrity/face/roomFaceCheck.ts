/**
 * The face check inside the interview room: starts the on-device monitor only
 * when the policy and the candidate's consent cover it, forwards its
 * observations to the integrity batcher, uploads the one snapshot per
 * interval for the server's confirmation, attaches motionless-face hints to
 * the periodic frames and turns lasting framing problems into gentle tips.
 */
import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

import { FACE_GUIDANCE_COPY, GuidanceThrottle } from "./guidance";
import type { FaceMonitorStatus, FacePresenceMonitor, FacePresenceMonitorOptions, FaceSnapshotRequest } from "./monitor";
import type { FaceGuidance } from "./states";

export interface RoomFaceCheckOptions {
  stream: MediaStream;
  /** From the effective policy: true / false, or null when only the server can tell (no local consent record). */
  decision: boolean | null;
  emit: (event: ClientIntegrityEvent) => void;
  captureSnapshot: () => Promise<Blob | null>;
  uploadSnapshot: (image: Blob, capturedAt: string, faceCheck: Record<string, unknown>) => Promise<void>;
  /** A framing tip worth showing now. */
  notify?: (kind: FaceGuidance, message: string) => void;
  reportError?: (message: string) => void;
  /** Did the candidate consent to a disclosure that included the face check? (asked when `decision` is null) */
  checkConsent?: () => Promise<boolean>;
  createMonitor?: (options: FacePresenceMonitorOptions) => FacePresenceMonitor | Promise<FacePresenceMonitor>;
  now?: () => number;
}

/** Snapshots for the same reason are at least this far apart (the server allows 6 a minute). */
const SNAPSHOT_GAP_MS = 10_000;

async function defaultMonitor(options: FacePresenceMonitorOptions): Promise<FacePresenceMonitor> {
  const { FacePresenceMonitor } = await import("./monitor");
  return new FacePresenceMonitor(options);
}

export class RoomFaceCheck {
  private monitor: FacePresenceMonitor | null = null;
  private starting: Promise<void> | null = null;
  private stopped = false;
  private allowed: boolean | null;
  private readonly throttle: GuidanceThrottle;
  private readonly lastSnapshot = new Map<string, number>();
  private statusValue: FaceMonitorStatus = "idle";

  constructor(private readonly opts: RoomFaceCheckOptions) {
    this.allowed = opts.decision;
    this.throttle = new GuidanceThrottle(undefined, undefined, undefined, opts.now);
  }

  get status(): FaceMonitorStatus {
    return this.statusValue;
  }

  get active(): boolean {
    return this.monitor !== null && !this.stopped;
  }

  private now(): number {
    return this.opts.now?.() ?? performance.now();
  }

  start(): Promise<void> {
    if (this.stopped || this.monitor) return Promise.resolve();
    this.starting ??= this.doStart().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private async doStart(): Promise<void> {
    if (this.allowed === null && this.opts.checkConsent) {
      try {
        this.allowed = await this.opts.checkConsent();
      } catch {
        this.allowed = null; // unknown: wait for the server's live flag
      }
    }
    if (this.allowed !== true || this.stopped) return;
    const monitor = await (this.opts.createMonitor ?? defaultMonitor)({
      stream: this.opts.stream,
      report: true,
      onEvent: (event) => this.opts.emit(event),
      onSnapshot: (request) => void this.sendSnapshot(request),
      onGuidance: (guidance) => this.onGuidance(guidance),
      onStatus: (status, detail) => {
        this.statusValue = status;
        if ((status === "error" || status === "unsupported") && detail) this.opts.reportError?.(`face check ${status}: ${detail}`);
      },
    });
    if (this.stopped) {
      monitor.stop();
      return;
    }
    this.monitor = monitor;
    await monitor.start();
  }

  private onGuidance(guidance: FaceGuidance | null): void {
    this.throttle.change(guidance, (kind) => this.opts.notify?.(kind, FACE_GUIDANCE_COPY[kind]));
  }

  private async sendSnapshot(request: FaceSnapshotRequest): Promise<void> {
    const t = this.now();
    const last = this.lastSnapshot.get(request.reason);
    if (last !== undefined && t - last < SNAPSHOT_GAP_MS) return;
    this.lastSnapshot.set(request.reason, t);
    try {
      const image = await this.opts.captureSnapshot();
      if (!image) return;
      await this.opts.uploadSnapshot(image, new Date().toISOString(), {
        reason: request.reason,
        episode: request.episode,
        duration_ms: request.durationMs,
        started_at: request.startedAt,
        faces: request.faces,
        static_boxes: request.staticBoxes,
      });
    } catch {
      /* best effort: the interval itself is already reported */
    }
  }

  /** Extra form fields for a periodic camera frame (motionless faces seen on the device). */
  periodicFrameHint(): Record<string, unknown> | undefined {
    const boxes = this.monitor?.staticBoxes() ?? [];
    return boxes.length ? { static_boxes: boxes } : undefined;
  }

  /** The server's live flags (`session.state.integrity`): it also checks the consent. */
  onServerFlags(flags: Record<string, unknown> | null | undefined): void {
    const value = flags?.face_presence_check;
    if (value === false) {
      this.allowed = false;
      this.stop();
    } else if (value === true && this.allowed !== true && !this.stopped) {
      this.allowed = true;
      void this.start();
    }
  }

  /** Page leaving: close open intervals and send the summary (monitoring resumes if the page stays). */
  finalize(): void {
    this.monitor?.finalize();
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.throttle.clear();
    this.monitor?.stop();
    this.monitor = null;
  }
}

