/**
 * FacePresenceMonitor: the on-device face check loop.
 *
 * About 4 times a second (paused while the tab is hidden or the camera track
 * is muted/ended) it draws the camera picture into a small canvas (320 px
 * wide), detects faces, follows them (`FaceTracker`: live vs. static), derives
 * framing/light guidance (`analysis.ts`) and runs the hysteresis
 * (`FacePresenceStates`). Only aggregated observations leave this class:
 *
 * - `face_not_visible` / `multiple_faces_visible` intervals (start, a
 *   re-report every 15 s, end) with their duration - integrity events;
 * - one snapshot *request* when such an interval starts (the caller captures
 *   and uploads it for the server's confirmation);
 * - `static_face_ignored` once per motionless face set aside;
 * - `face_check_summary` every minute (and at the end): monitored time and
 *   how much of it showed exactly one face, no face, several faces.
 *
 * Head direction is never reported. The camera picture never leaves the
 * device from here.
 */
import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

import { analyseFrame, type FrameAnalysis } from "./analysis";
import { createBlazeFaceDetector, FaceCheckUnsupportedError, type FaceDetectorEngine } from "./detector";
import { area, center, roundBox, type FaceBox } from "./geometry";
import { FacePresenceStates, randomId, type FaceGuidance, type FaceThresholds, type FaceTransition } from "./states";
import { FaceTracker, type TrackedFace } from "./tracker";

export type FaceMonitorStatus = "idle" | "loading" | "running" | "paused" | "unsupported" | "error" | "stopped";

export interface FaceSnapshotRequest {
  reason: "no_face" | "multiple_faces";
  episode: string;
  durationMs: number;
  /** ISO time the condition began. */
  startedAt: string;
  faces: number;
  /** Faces judged static on the device (normalised [x, y, w, h]) - a hint for the server. */
  staticBoxes: Array<[number, number, number, number]>;
}

export interface FaceMonitorViewFace {
  id: number;
  box: FaceBox;
  live: boolean;
  static: boolean;
  primary: boolean;
}

export interface FaceMonitorView {
  faces: FaceMonitorViewFace[];
  counted: number;
  live: number;
  guidance: FaceGuidance | null;
  /** Size of the analysed video (for overlays). */
  videoWidth: number;
  videoHeight: number;
}

export interface FaceMonitorPerf {
  frames: number;
  /** Detector time per frame (ms). */
  detectP50: number;
  detectP95: number;
  /** Whole analysis per frame: draw + read pixels + detect + tracking (ms). */
  frameP50: number;
  frameP95: number;
  frameMean: number;
}

export interface FaceMonitorEnv {
  /** Wall clock (epoch ms). */
  now: () => number;
  /** Monotonic clock (ms). */
  perf: () => number;
  doc: Document | null;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
}

export interface FacePresenceMonitorOptions {
  /** The camera stream (a hidden video element is created) ... */
  stream?: MediaStream | null;
  /** ... or an existing, playing video element. */
  video?: HTMLVideoElement | null;
  createDetector?: () => Promise<FaceDetectorEngine>;
  fps?: number;
  analysisWidth?: number;
  thresholds?: Partial<FaceThresholds>;
  /** Emit integrity events and snapshot requests (the interview room); the system check only shows guidance. */
  report?: boolean;
  summaryEveryMs?: number;
  onEvent?: (event: ClientIntegrityEvent) => void;
  onSnapshot?: (request: FaceSnapshotRequest) => void;
  onView?: (view: FaceMonitorView) => void;
  onGuidance?: (guidance: FaceGuidance | null) => void;
  onStatus?: (status: FaceMonitorStatus, detail?: string) => void;
  env?: Partial<FaceMonitorEnv>;
}

function defaultEnv(): FaceMonitorEnv {
  return {
    now: () => Date.now(),
    perf: () => performance.now(),
    doc: typeof document !== "undefined" ? document : null,
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  };
}

function percentile(values: number[], q: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]!;
}

const EVENT_TYPE: Record<FaceTransition["kind"], ClientIntegrityEvent["event_type"]> = {
  no_face: "face_not_visible",
  multiple_faces: "multiple_faces_visible",
};

export class FacePresenceMonitor {
  private readonly opts: FacePresenceMonitorOptions;
  private readonly env: FaceMonitorEnv;
  private readonly tracker = new FaceTracker();
  private readonly states: FacePresenceStates;
  private readonly run = `run_${randomId()}`;
  private readonly intervalMs: number;
  private detector: FaceDetectorEngine | null = null;
  private video: HTMLVideoElement | null = null;
  private ownsVideo = false;
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private timer: unknown = null;
  private statusValue: FaceMonitorStatus = "idle";
  private stopped = false;
  private paused = false;
  private lastSummaryAt = 0;
  private staticReported = 0;
  private lastGuidance: FaceGuidance | null = null;
  private lastAnalysis: FrameAnalysis | null = null;
  private readonly detectTimes: number[] = [];
  private readonly frameTimes: number[] = [];
  private frames = 0;
  private readonly onVisibility = () => this.handleVisibility();

  constructor(options: FacePresenceMonitorOptions) {
    this.opts = options;
    this.env = { ...defaultEnv(), ...options.env };
    this.intervalMs = Math.round(1000 / Math.min(10, Math.max(1, options.fps ?? 4)));
    this.states = new FacePresenceStates(options.thresholds);
    this.states.setFrameInterval(this.intervalMs);
  }

  get status(): FaceMonitorStatus {
    return this.statusValue;
  }

  get runId(): string {
    return this.run;
  }

  private setStatus(status: FaceMonitorStatus, detail?: string): void {
    if (this.statusValue === status) return;
    this.statusValue = status;
    this.opts.onStatus?.(status, detail);
  }

  async start(): Promise<void> {
    if (this.detector || this.stopped) return;
    this.setStatus("loading");
    try {
      const detector = await (this.opts.createDetector ?? createBlazeFaceDetector)();
      if (this.stopped) {
        detector.close();
        return;
      }
      this.detector = detector;
    } catch (error) {
      if (error instanceof FaceCheckUnsupportedError) this.setStatus("unsupported", error.message);
      else this.setStatus("error", error instanceof Error ? error.message : String(error));
      return;
    }
    this.video = this.opts.video ?? null;
    if (!this.video && this.opts.stream && this.env.doc) {
      const video = this.env.doc.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = this.opts.stream;
      this.video = video;
      this.ownsVideo = true;
      try {
        await video.play();
      } catch {
        /* frames still decode for a muted, inline video */
      }
    }
    this.canvas = this.env.doc?.createElement("canvas") ?? null;
    this.ctx = this.canvas?.getContext("2d", { willReadFrequently: true }) ?? null;
    this.env.doc?.addEventListener("visibilitychange", this.onVisibility);
    this.lastSummaryAt = this.env.perf();
    this.setStatus("running");
    this.schedule(0);
  }

  private schedule(ms: number): void {
    if (this.stopped) return;
    this.timer = this.env.setTimer(() => this.tick(), ms);
  }

  private videoUsable(): boolean {
    const video = this.video;
    if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return false;
    const stream = typeof MediaStream !== "undefined" && video.srcObject instanceof MediaStream ? video.srcObject : this.opts.stream;
    const track = stream?.getVideoTracks?.()[0];
    return !track || (track.readyState === "live" && !track.muted);
  }

  private tick(): void {
    this.timer = null;
    if (this.stopped || !this.detector) return;
    const t = this.env.perf();
    if (this.env.doc?.visibilityState === "hidden" || !this.videoUsable()) {
      this.pause(t);
      this.schedule(500);
      return;
    }
    if (this.paused) {
      this.paused = false;
      this.setStatus("running");
    }
    try {
      this.analyse(t);
    } catch (error) {
      this.setStatus("error", error instanceof Error ? error.message : String(error));
      this.stopLoop();
      return;
    }
    const elapsed = this.env.perf() - t;
    this.schedule(Math.max(0, this.intervalMs - elapsed));
  }

  /** One analysis step (public for tests and benchmarks). */
  analyse(t = this.env.perf()): FrameAnalysis | null {
    const video = this.video;
    const detector = this.detector;
    if (!video || !detector) return null;
    const width = this.opts.analysisWidth ?? 320;
    const height = Math.max(1, Math.round((width * video.videoHeight) / Math.max(1, video.videoWidth)));
    let pixels: { data: Uint8ClampedArray; width: number; height: number } | null = null;
    let source: HTMLCanvasElement | HTMLVideoElement = video;
    if (this.canvas && this.ctx) {
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.ctx.drawImage(video, 0, 0, width, height);
      pixels = this.ctx.getImageData(0, 0, width, height);
      source = this.canvas;
    }
    const detectStart = this.env.perf();
    const detections = detector.detect(source, t);
    const detectMs = this.env.perf() - detectStart;
    const tracked = this.tracker.update(detections, t, pixels);
    const analysis = analyseFrame(tracked, pixels);
    this.lastAnalysis = analysis;
    const wall = this.env.now();
    const step = this.states.step({ t, wall, counted: analysis.counted.length, live: analysis.live.length, guidance: analysis.guidance });
    if (this.opts.report) {
      for (const transition of step.transitions) this.reportTransition(transition);
      for (const face of tracked) if (face.newlyStatic) this.reportStatic(face);
      if (t - this.lastSummaryAt >= (this.opts.summaryEveryMs ?? 60_000)) this.reportSummary(t);
    }
    if (step.guidance !== this.lastGuidance) {
      this.lastGuidance = step.guidance;
      this.opts.onGuidance?.(step.guidance);
    }
    this.opts.onView?.({
      faces: tracked.map((f) => ({ id: f.id, box: f.box, live: f.live, static: f.static, primary: f === analysis.primary })),
      counted: analysis.counted.length,
      live: analysis.live.length,
      guidance: step.guidance,
      videoWidth: video.videoWidth,
      videoHeight: video.videoHeight,
    });
    this.frames += 1;
    this.detectTimes.push(detectMs);
    this.frameTimes.push(this.env.perf() - t);
    if (this.detectTimes.length > 600) this.detectTimes.shift();
    if (this.frameTimes.length > 600) this.frameTimes.shift();
    return analysis;
  }

  private pause(t: number): void {
    if (this.paused) return;
    this.paused = true;
    const transitions = this.states.pause(t);
    if (this.opts.report) for (const transition of transitions) this.reportTransition(transition);
    if (this.lastGuidance !== null) {
      this.lastGuidance = null;
      this.opts.onGuidance?.(null);
    }
    this.setStatus("paused");
  }

  private handleVisibility(): void {
    if (this.env.doc?.visibilityState === "hidden") this.pause(this.env.perf());
  }

  private emit(event: ClientIntegrityEvent): void {
    this.opts.onEvent?.(event);
  }

  private reportTransition(transition: FaceTransition): void {
    const fps = Math.round(1000 / this.intervalMs);
    this.emit({
      event_type: EVENT_TYPE[transition.kind],
      occurred_at: new Date(transition.startedAt).toISOString(),
      duration_ms: transition.durationMs,
      details: { episode: transition.episode, phase: transition.phase, faces: transition.faces, detector: this.detector?.name ?? "unknown", fps },
    });
    if (transition.phase === "start") {
      this.opts.onSnapshot?.({
        reason: transition.kind,
        episode: transition.episode,
        durationMs: transition.durationMs,
        startedAt: new Date(transition.startedAt).toISOString(),
        faces: transition.faces,
        staticBoxes: this.staticBoxes(),
      });
    }
  }

  private reportStatic(face: TrackedFace): void {
    this.staticReported += 1;
    const c = center(face.box);
    this.emit({
      event_type: "static_face_ignored",
      occurred_at: new Date(this.env.now()).toISOString(),
      details: {
        still_seconds: Math.round(face.stillMs / 1000),
        face_size: Math.round(area(face.box) * 1000) / 1000,
        position: c.x < 0.33 ? "left" : c.x > 0.67 ? "right" : "middle",
      },
    });
  }

  private reportSummary(t: number): void {
    this.lastSummaryAt = t;
    const counters = this.states.summary();
    if (counters.frames === 0) return;
    const perf = this.perf();
    this.emit({
      event_type: "face_check_summary",
      occurred_at: new Date(this.env.now()).toISOString(),
      details: { run: this.run, ...counters, static_faces: this.staticReported, detector: this.detector?.name ?? "unknown", frame_ms: Math.round(perf.frameP50 * 10) / 10 },
    });
  }

  /** Boxes of faces currently judged static (hint for the server, also attached to periodic frames). */
  staticBoxes(): Array<[number, number, number, number]> {
    return this.tracker.staticBoxes(this.env.perf()).slice(0, 8).map((box) => roundBox(box, 4));
  }

  get latest(): FrameAnalysis | null {
    return this.lastAnalysis;
  }

  perf(): FaceMonitorPerf {
    return {
      frames: this.frames,
      detectP50: percentile(this.detectTimes, 0.5),
      detectP95: percentile(this.detectTimes, 0.95),
      frameP50: percentile(this.frameTimes, 0.5),
      frameP95: percentile(this.frameTimes, 0.95),
      frameMean: this.frameTimes.length ? this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length : 0,
    };
  }

  /** Close open intervals (with their duration so far) and send the summary: page leaving or interview ending. */
  finalize(): void {
    if (!this.detector) return;
    const t = this.env.perf();
    const transitions = this.states.pause(t);
    if (this.opts.report) {
      for (const transition of transitions) this.reportTransition(transition);
      this.reportSummary(t);
    }
    this.paused = true;
  }

  private stopLoop(): void {
    if (this.timer !== null) this.env.clearTimer(this.timer);
    this.timer = null;
  }

  stop(): void {
    if (this.stopped) return;
    this.finalize();
    this.stopped = true;
    this.stopLoop();
    this.env.doc?.removeEventListener("visibilitychange", this.onVisibility);
    this.detector?.close();
    this.detector = null;
    if (this.ownsVideo && this.video) {
      try {
        this.video.pause();
      } catch {
        /* ignore */
      }
      this.video.srcObject = null;
    }
    this.video = null;
    this.setStatus("stopped");
  }
}
