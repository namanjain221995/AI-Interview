/**
 * Per-frame measurements for the on-device face check and the candidate
 * guidance derived from them. Everything here is about framing and light -
 * never about the person (no expression, attention or any attribute). Head
 * direction from keypoints is computed for context only and is never a signal.
 */
import { area, clamp, type FaceBox, type Point } from "./geometry";
import type { PixelSource } from "./liveness";
import type { TrackedFace } from "./tracker";

/** Detections below this score are ignored. */
export const MIN_FACE_SCORE = 0.6;

export type GuidanceKind = "partly_out" | "too_close" | "too_far" | "low_light";

export const GUIDANCE_THRESHOLDS = {
  /** Face narrower than this share of the frame width: "move closer". */
  tooFarWidth: 0.12,
  /** Face wider than this share of the frame width, or taller than `tooCloseHeight`: "move back". */
  tooCloseWidth: 0.5,
  tooCloseHeight: 0.85,
  /** Box within this margin of an edge (or cut by it): "move into the middle". */
  edgeMargin: 0.01,
  /** Mean face luma (0-255) below this: "add light". */
  lowLightFace: 60,
  /** Face much darker than the room (backlit). */
  backlitRatio: 0.55,
  backlitFace: 95,
} as const;

export interface FaceLight {
  /** Mean luma (0-255) of the face region. */
  face: number;
  /** Mean luma of the whole frame. */
  frame: number;
  /** Laplacian variance of the face region (focus; context only). */
  sharpness: number;
}

export interface FrameAnalysis {
  faces: TrackedFace[];
  /** Faces that count as "someone is there" (not static). */
  counted: TrackedFace[];
  /** Faces seen moving (count towards "more than one face"). */
  live: TrackedFace[];
  primary: TrackedFace | null;
  light: FaceLight | null;
  guidance: GuidanceKind[];
}

function lumaAt(data: PixelSource["data"], offset: number): number {
  return 0.2126 * data[offset]! + 0.7152 * data[offset + 1]! + 0.0722 * data[offset + 2]!;
}

/** Mean luma of a normalised region (sampled on a coarse grid, cheap). */
export function regionLuma(src: PixelSource, box: FaceBox, step = 2): number {
  const x0 = Math.floor(clamp(box.x, 0, 1) * src.width);
  const y0 = Math.floor(clamp(box.y, 0, 1) * src.height);
  const x1 = Math.ceil(clamp(box.x + box.w, 0, 1) * src.width);
  const y1 = Math.ceil(clamp(box.y + box.h, 0, 1) * src.height);
  let sum = 0;
  let n = 0;
  for (let y = y0; y < y1; y += step) {
    for (let x = x0; x < x1; x += step) {
      sum += lumaAt(src.data, (y * src.width + x) * 4);
      n++;
    }
  }
  return n ? sum / n : 0;
}

/** Variance of a 4-neighbour Laplacian over the region (higher = sharper). */
export function regionSharpness(src: PixelSource, box: FaceBox): number {
  const x0 = Math.max(1, Math.floor(clamp(box.x, 0, 1) * src.width));
  const y0 = Math.max(1, Math.floor(clamp(box.y, 0, 1) * src.height));
  const x1 = Math.min(src.width - 1, Math.ceil(clamp(box.x + box.w, 0, 1) * src.width));
  const y1 = Math.min(src.height - 1, Math.ceil(clamp(box.y + box.h, 0, 1) * src.height));
  let sum = 0;
  let sumSq = 0;
  let n = 0;
  const at = (x: number, y: number) => lumaAt(src.data, (y * src.width + x) * 4);
  for (let y = y0; y < y1; y += 1) {
    for (let x = x0; x < x1; x += 1) {
      const lap = 4 * at(x, y) - at(x - 1, y) - at(x + 1, y) - at(x, y - 1) - at(x, y + 1);
      sum += lap;
      sumSq += lap * lap;
      n++;
    }
  }
  if (!n) return 0;
  const mean = sum / n;
  return Math.max(0, sumSq / n - mean * mean);
}

/**
 * Rough head yaw in degrees from BlazeFace keypoints (right eye, left eye,
 * nose tip, ...): context for debugging only - never sent, never a signal.
 */
export function roughYaw(keypoints: readonly Point[]): number | null {
  const [rightEye, leftEye, nose] = keypoints;
  if (!rightEye || !leftEye || !nose) return null;
  const eyes = Math.abs(leftEye.x - rightEye.x);
  if (eyes <= 1e-6) return null;
  const offset = (nose.x - (leftEye.x + rightEye.x) / 2) / eyes;
  return Math.round((Math.asin(clamp(offset * 2, -1, 1)) * 180) / Math.PI);
}

/** Framing / light guidance for the primary face, most important first. */
export function classifyGuidance(primary: TrackedFace | null, light: FaceLight | null): GuidanceKind[] {
  const out: GuidanceKind[] = [];
  if (!primary) return out;
  const t = GUIDANCE_THRESHOLDS;
  const b = primary.smoothBox;
  if (b.x <= t.edgeMargin || b.y <= t.edgeMargin || b.x + b.w >= 1 - t.edgeMargin || b.y + b.h >= 1 - t.edgeMargin) {
    // A box filling the frame is "too close", not "partly out".
    if (!(b.w >= t.tooCloseWidth || b.h >= t.tooCloseHeight)) out.push("partly_out");
  }
  if (b.w >= t.tooCloseWidth || b.h >= t.tooCloseHeight) out.push("too_close");
  else if (b.w < t.tooFarWidth) out.push("too_far");
  if (light && (light.face < t.lowLightFace || (light.face < t.backlitFace && light.face < light.frame * t.backlitRatio))) {
    out.push("low_light");
  }
  return out;
}

/** Combine tracked faces + pixels into the frame analysis. */
export function analyseFrame(faces: TrackedFace[], pixels: PixelSource | null): FrameAnalysis {
  const counted = faces.filter((f) => !f.static);
  const live = counted.filter((f) => f.live);
  // The candidate is the largest face seen moving; a picture that has not been set aside yet
  // (often larger, e.g. a poster close to the camera) never drives the guidance while a live face exists.
  const largest = (list: TrackedFace[]) => list.reduce<TrackedFace | null>((best, f) => (best === null || area(f.smoothBox) > area(best.smoothBox) ? f : best), null);
  const primary = largest(live) ?? largest(counted);
  let light: FaceLight | null = null;
  if (pixels && primary) {
    light = {
      face: regionLuma(pixels, primary.box),
      frame: regionLuma(pixels, { x: 0, y: 0, w: 1, h: 1 }, 4),
      sharpness: regionSharpness(pixels, primary.box),
    };
  }
  return { faces, counted, live, primary, light, guidance: classifyGuidance(primary, light) };
}
