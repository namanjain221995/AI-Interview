/**
 * Follows detected faces from frame to frame (IoU matching on a smoothed box)
 * and decides, per face, whether it has shown signs of life:
 *
 * - a large move of the smoothed box (more than `largeMove` of the face width,
 *   well above detector jitter), or
 * - micro-motion inside the region where the face was anchored (blinks,
 *   speaking, breathing; see `liveness.ts`) in `evidenceFrames` frames within
 *   `evidenceWindowMs`, while the rest of the frame is calm.
 *
 * A face present for `staticMs` (30 s) without any sign of life is a picture
 * (photo, poster, paused screen): it is not counted, and reported once so the
 * reviewer can see what was set aside. A face not yet seen moving still counts
 * as "someone is there" but not as an additional person. Liveness also decays:
 * a face with no sign of life for `staticMs` is no longer an additional person
 * (e.g. a poster that took over the track of someone who walked past it), but
 * a face that once moved is never set aside, so a candidate sitting very still
 * is never turned into "no face".
 */
import { center, expandBox, iou, type FaceBox, type Point } from "./geometry";
import { FrameCalm, lumaPatch, MotionMeter, type MotionOptions, type PixelSource } from "./liveness";

export interface RawDetection {
  box: FaceBox;
  score: number;
  keypoints: Point[];
}

export interface TrackedFace {
  id: number;
  box: FaceBox;
  smoothBox: FaceBox;
  score: number;
  keypoints: Point[];
  /** Seen moving within the last `staticMs` (a live face). */
  live: boolean;
  /** Motionless for `staticMs` without ever moving: a photo, poster or screen. */
  static: boolean;
  /** Became static on this frame (report once). */
  newlyStatic: boolean;
  /** Continuous presence of this track (ms). */
  presentMs: number;
  /** Time since the last sign of life (or since first seen). */
  stillMs: number;
  /** Micro-motion of this frame (busiest block change and its noise floor; diagnostics). */
  motion: { peak: number | null; floor: number };
}

export interface TrackerOptions {
  staticMs?: number;
  matchIou?: number;
  /** A track not seen for this long is dropped (live / not yet live faces). */
  liveMissMs?: number;
  stillMissMs?: number;
  largeMove?: number;
  smoothing?: number;
  evidenceFrames?: number;
  evidenceWindowMs?: number;
  motion?: MotionOptions;
}

interface Track {
  id: number;
  box: FaceBox;
  smoothBox: FaceBox;
  anchor: FaceBox;
  score: number;
  keypoints: Point[];
  firstSeen: number;
  lastSeen: number;
  lastLife: number | null;
  live: boolean;
  reportedStatic: boolean;
  evidence: number[];
  meter: MotionMeter;
}

function lerpBox(a: FaceBox, b: FaceBox, k: number): FaceBox {
  return { x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k, w: a.w + (b.w - a.w) * k, h: a.h + (b.h - a.h) * k };
}

export class FaceTracker {
  private tracks: Track[] = [];
  private nextId = 1;
  private readonly calm = new FrameCalm();
  private readonly opts: Required<Omit<TrackerOptions, "motion">> & { motion: MotionOptions };

  constructor(options: TrackerOptions = {}) {
    this.opts = {
      staticMs: options.staticMs ?? 30_000,
      matchIou: options.matchIou ?? 0.3,
      liveMissMs: options.liveMissMs ?? 2_000,
      stillMissMs: options.stillMissMs ?? 6_000,
      largeMove: options.largeMove ?? 0.2,
      smoothing: options.smoothing ?? 0.35,
      evidenceFrames: options.evidenceFrames ?? 2,
      evidenceWindowMs: options.evidenceWindowMs ?? 3_000,
      motion: options.motion ?? {},
    };
  }

  get size(): number {
    return this.tracks.length;
  }

  reset(): void {
    this.tracks = [];
  }

  /** Feed one analysed frame (`t` in ms, monotonic). `pixels` = the analysed RGBA frame, if available. */
  update(detections: readonly RawDetection[], t: number, pixels: PixelSource | null = null): TrackedFace[] {
    const calm = pixels ? this.calm.push(lumaPatch(pixels, { x: 0, y: 0, w: 1, h: 1 })) : true;
    const pairs: Array<[number, number, number]> = [];
    detections.forEach((det, di) => {
      this.tracks.forEach((track, ti) => {
        const overlap = iou(det.box, track.smoothBox);
        if (overlap >= this.opts.matchIou) pairs.push([overlap, di, ti]);
      });
    });
    pairs.sort((a, b) => b[0] - a[0]);
    const detToTrack = new Map<number, Track>();
    const used = new Set<number>();
    for (const [, di, ti] of pairs) {
      if (detToTrack.has(di) || used.has(ti)) continue;
      detToTrack.set(di, this.tracks[ti]!);
      used.add(ti);
    }

    const out: TrackedFace[] = [];
    detections.forEach((det, di) => {
      let track = detToTrack.get(di);
      if (!track) {
        track = {
          id: this.nextId++,
          box: det.box,
          smoothBox: det.box,
          anchor: det.box,
          score: det.score,
          keypoints: det.keypoints,
          firstSeen: t,
          lastSeen: t,
          lastLife: null,
          live: false,
          reportedStatic: false,
          evidence: [],
          meter: new MotionMeter(this.opts.motion),
        };
        this.tracks.push(track);
      } else {
        track.box = det.box;
        track.score = det.score;
        track.keypoints = det.keypoints;
        track.lastSeen = t;
        track.smoothBox = lerpBox(track.smoothBox, det.box, this.opts.smoothing);
        const a = center(track.anchor);
        const s = center(track.smoothBox);
        const limit = this.opts.largeMove * track.anchor.w;
        if (Math.abs(a.x - s.x) > limit || Math.abs(a.y - s.y) > limit || Math.abs(track.anchor.w - track.smoothBox.w) > limit) {
          this.markLife(track, t);
          track.anchor = track.smoothBox;
          track.meter.reset();
        }
      }
      let peak: number | null = null;
      if (pixels) {
        const result = track.meter.push(lumaPatch(pixels, expandBox(track.anchor, 0.05)), { calm });
        const evidence = result.evidence;
        peak = result.peak;
        if (evidence) {
          track.evidence.push(t);
          track.evidence = track.evidence.filter((at) => t - at <= this.opts.evidenceWindowMs);
          if (track.evidence.length >= this.opts.evidenceFrames) this.markLife(track, t);
        }
      }
      if (track.live && track.lastLife !== null && t - track.lastLife >= this.opts.staticMs) track.live = false;
      const isStatic = track.lastLife === null && t - track.firstSeen >= this.opts.staticMs;
      const newlyStatic = isStatic && !track.reportedStatic;
      if (newlyStatic) track.reportedStatic = true;
      out.push({
        id: track.id,
        box: track.box,
        smoothBox: track.smoothBox,
        score: track.score,
        keypoints: track.keypoints,
        live: track.live,
        static: isStatic,
        newlyStatic,
        presentMs: t - track.firstSeen,
        stillMs: t - (track.lastLife ?? track.firstSeen),
        motion: { peak, floor: track.meter.floor },
      });
    });

    this.tracks = this.tracks.filter((track) => t - track.lastSeen <= (track.live ? this.opts.liveMissMs : this.opts.stillMissMs));
    return out;
  }

  private markLife(track: Track, t: number): void {
    track.live = true;
    track.lastLife = t;
    track.reportedStatic = false;
  }

  /** Boxes of the faces currently judged static (hints for the server's own tracker). */
  staticBoxes(t: number): FaceBox[] {
    return this.tracks.filter((track) => track.lastLife === null && t - track.firstSeen >= this.opts.staticMs && t - track.lastSeen <= 1_000).map((track) => track.box);
  }
}
