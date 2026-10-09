/**
 * Hysteresis for the on-device face check.
 *
 * Intervals (reported to the server as integrity observations):
 * - `no_face`: no counted face (static pictures excluded) for `noFaceMs` (5 s);
 * - `multiple_faces`: two or more faces seen moving for `multipleFacesMs` (3 s).
 * An interval starts when its condition has held for the threshold (the
 * reported duration counts from when the condition began), is re-reported
 * every `updateEveryMs` while it lasts (so long ones are visible live), and
 * ends once the condition has been false for `exitMs` (its duration stops
 * where the condition stopped). Before an interval starts, a dropout shorter
 * than `dropoutMs` (the detector missing a face for a frame or two) does not
 * restart the clock; without this a second face missed in one frame of four
 * would almost never reach the 3 s threshold.
 *
 * Guidance (candidate only, never reported): framing and light hints with a
 * shorter hysteresis. Time with the tab hidden is not monitored: open
 * intervals end when monitoring pauses.
 */
import type { GuidanceKind } from "./analysis";

export type FaceIntervalKind = "no_face" | "multiple_faces";
export type FaceGuidance = FaceIntervalKind | GuidanceKind;

export interface FaceThresholds {
  noFaceMs: number;
  multipleFacesMs: number;
  exitMs: number;
  guidanceEnterMs: number;
  guidanceExitMs: number;
  updateEveryMs: number;
  /** A condition off for less than this does not restart its enter clock. */
  dropoutMs: number;
}

export const DEFAULT_FACE_THRESHOLDS: FaceThresholds = {
  noFaceMs: 5_000,
  multipleFacesMs: 3_000,
  exitMs: 1_500,
  guidanceEnterMs: 2_000,
  guidanceExitMs: 1_000,
  updateEveryMs: 15_000,
  dropoutMs: 750,
};

export interface FaceTransition {
  kind: FaceIntervalKind;
  phase: "start" | "update" | "end";
  episode: string;
  /** Wall-clock (epoch ms) start of the condition. */
  startedAt: number;
  durationMs: number;
  /** Most faces seen during the interval (live faces for multiple_faces, 0 for no_face). */
  faces: number;
}

export interface FaceStepInput {
  /** Monotonic time (ms). */
  t: number;
  /** Wall clock (epoch ms) at `t`. */
  wall: number;
  counted: number;
  live: number;
  guidance: readonly GuidanceKind[];
}

export interface FaceStepResult {
  transitions: FaceTransition[];
  guidance: FaceGuidance | null;
}

export interface FaceSummaryCounters {
  monitored_ms: number;
  one_face_ms: number;
  no_face_ms: number;
  multi_face_ms: number;
  frames: number;
}

const GUIDANCE_ORDER: readonly FaceGuidance[] = ["no_face", "multiple_faces", "partly_out", "too_close", "too_far", "low_light"];

interface Interval {
  episode: string;
  startT: number;
  startWall: number;
  faces: number;
  lastReport: number;
}

class IntervalTracker {
  private trueSince: number | null = null;
  private trueSinceWall = 0;
  private falseSince: number | null = null;
  active: Interval | null = null;

  constructor(
    readonly kind: FaceIntervalKind,
    private readonly enterMs: number,
    private readonly thresholds: FaceThresholds,
    private readonly newEpisode: (kind: FaceIntervalKind) => string,
  ) {}

  step(condition: boolean, faces: number, t: number, wall: number): FaceTransition[] {
    const out: FaceTransition[] = [];
    if (condition) {
      this.falseSince = null;
      if (this.trueSince === null) {
        this.trueSince = t;
        this.trueSinceWall = wall;
      }
      if (!this.active && t - this.trueSince >= this.enterMs) {
        this.active = { episode: this.newEpisode(this.kind), startT: this.trueSince, startWall: this.trueSinceWall, faces, lastReport: t };
        out.push(this.transition("start", t - this.trueSince));
      } else if (this.active) {
        this.active.faces = Math.max(this.active.faces, faces);
        if (t - this.active.lastReport >= this.thresholds.updateEveryMs) {
          this.active.lastReport = t;
          out.push(this.transition("update", t - this.active.startT));
        }
      }
      return out;
    }
    if (!this.active) {
      if (this.trueSince === null) return out;
      if (this.falseSince === null) this.falseSince = t;
      if (t - this.falseSince >= this.thresholds.dropoutMs) {
        this.trueSince = null;
        this.falseSince = null;
      }
      return out;
    }
    if (this.falseSince === null) this.falseSince = t;
    if (t - this.falseSince >= this.thresholds.exitMs) out.push(...this.end(this.falseSince));
    return out;
  }

  /** End an open interval at `at` (monotonic ms). */
  end(at: number): FaceTransition[] {
    if (!this.active) return [];
    const transition = this.transition("end", Math.max(0, at - this.active.startT));
    this.active = null;
    this.trueSince = null;
    this.falseSince = null;
    return [transition];
  }

  reset(): void {
    this.active = null;
    this.trueSince = null;
    this.falseSince = null;
  }

  private transition(phase: FaceTransition["phase"], durationMs: number): FaceTransition {
    const active = this.active!;
    return { kind: this.kind, phase, episode: active.episode, startedAt: active.startWall, durationMs: Math.round(durationMs), faces: active.faces };
  }
}

/** Random id for intervals and page runs (matches the server's `[A-Za-z0-9_-]{4,40}`). */
export function randomId(length = 10): string {
  const bytes = new Uint8Array(length);
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
  else for (let i = 0; i < length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (b) => (b % 36).toString(36)).join("");
}

export class FacePresenceStates {
  private readonly thresholds: FaceThresholds;
  private readonly noFace: IntervalTracker;
  private readonly multi: IntervalTracker;
  private readonly guidanceSince = new Map<FaceGuidance, number>();
  private readonly guidanceOffSince = new Map<FaceGuidance, number>();
  private readonly activeGuidance = new Set<FaceGuidance>();
  private lastT: number | null = null;
  private frameMs = 250;
  private readonly counters: FaceSummaryCounters = { monitored_ms: 0, one_face_ms: 0, no_face_ms: 0, multi_face_ms: 0, frames: 0 };

  constructor(thresholds: Partial<FaceThresholds> = {}, newEpisode: (kind: FaceIntervalKind) => string = (kind) => `${kind === "no_face" ? "nf" : "mf"}_${randomId()}`) {
    this.thresholds = { ...DEFAULT_FACE_THRESHOLDS, ...thresholds };
    this.noFace = new IntervalTracker("no_face", this.thresholds.noFaceMs, this.thresholds, newEpisode);
    this.multi = new IntervalTracker("multiple_faces", this.thresholds.multipleFacesMs, this.thresholds, newEpisode);
  }

  /** Expected frame interval (caps the time one frame accounts for). */
  setFrameInterval(ms: number): void {
    this.frameMs = Math.max(50, ms);
  }

  step(input: FaceStepInput): FaceStepResult {
    const { t, wall, counted, live } = input;
    if (this.lastT !== null) {
      const dt = Math.min(Math.max(0, t - this.lastT), this.frameMs * 2.5);
      // A face not yet seen moving (a picture before it is set aside, or someone who just
      // sat down) does not make "more than one face" here either.
      const people = live >= 2 ? live : Math.min(counted, 1);
      this.counters.monitored_ms += dt;
      if (people === 0) this.counters.no_face_ms += dt;
      else if (people === 1) this.counters.one_face_ms += dt;
      else this.counters.multi_face_ms += dt;
    }
    this.counters.frames += 1;
    this.lastT = t;
    const transitions = [
      ...this.noFace.step(counted === 0, 0, t, wall),
      ...this.multi.step(live >= 2, live, t, wall),
    ];
    const raw = new Set<FaceGuidance>(input.guidance);
    if (counted === 0) raw.add("no_face");
    if (live >= 2) raw.add("multiple_faces");
    for (const kind of GUIDANCE_ORDER) this.stepGuidance(kind, raw.has(kind), t);
    return { transitions, guidance: GUIDANCE_ORDER.find((kind) => this.activeGuidance.has(kind)) ?? null };
  }

  private stepGuidance(kind: FaceGuidance, on: boolean, t: number): void {
    if (on) {
      this.guidanceOffSince.delete(kind);
      if (!this.guidanceSince.has(kind)) this.guidanceSince.set(kind, t);
      if (t - this.guidanceSince.get(kind)! >= this.thresholds.guidanceEnterMs) this.activeGuidance.add(kind);
      return;
    }
    if (!this.guidanceSince.has(kind)) return;
    if (!this.guidanceOffSince.has(kind)) this.guidanceOffSince.set(kind, t);
    const off = t - this.guidanceOffSince.get(kind)!;
    if (off >= (this.activeGuidance.has(kind) ? this.thresholds.guidanceExitMs : this.thresholds.dropoutMs)) {
      this.activeGuidance.delete(kind);
      this.guidanceOffSince.delete(kind);
      this.guidanceSince.delete(kind);
    }
  }

  /** Monitoring stops (tab hidden, camera lost, page leaving): close open intervals at `t`. */
  pause(t: number): FaceTransition[] {
    const out = [...this.noFace.end(t), ...this.multi.end(t)];
    this.lastT = null;
    this.guidanceSince.clear();
    this.guidanceOffSince.clear();
    this.activeGuidance.clear();
    return out;
  }

  get openIntervals(): FaceIntervalKind[] {
    return [this.noFace, this.multi].filter((tracker) => tracker.active).map((tracker) => tracker.kind);
  }

  summary(): FaceSummaryCounters {
    return { ...this.counters, monitored_ms: Math.round(this.counters.monitored_ms), one_face_ms: Math.round(this.counters.one_face_ms), no_face_ms: Math.round(this.counters.no_face_ms), multi_face_ms: Math.round(this.counters.multi_face_ms) };
  }
}
