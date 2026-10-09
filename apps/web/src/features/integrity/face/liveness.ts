/**
 * Micro-motion of a face region, measured on the device to tell a live face
 * from a picture (photo, poster, paused screen).
 *
 * Detector keypoints are far too noisy for this (BlazeFace looks at a 128 px
 * copy of the frame: on a static print with ordinary sensor noise its
 * keypoints wander by 6-19% of the face width). Instead the region where the
 * face was anchored is sampled as a small mean-subtracted grey patch, and the
 * patch is compared with the one from ~1 s earlier block by block: a blink, a
 * word or a breath changes a few blocks a lot, sensor noise changes every
 * block a little. Evidence = the busiest block clearly above the patch's own
 * noise floor while the rest of the frame is calm (no exposure jump or camera
 * shake). Nothing leaves the device: patches are 24x24 numbers in memory.
 */
import type { FaceBox } from "./geometry";

export const PATCH_SIZE = 24;
const BLOCK = 6; // 4 x 4 blocks of 6 x 6 patch pixels

export interface PixelSource {
  data: Uint8ClampedArray | Uint8Array;
  width: number;
  height: number;
}

function luma(data: PixelSource["data"], offset: number): number {
  return 0.2126 * data[offset]! + 0.7152 * data[offset + 1]! + 0.0722 * data[offset + 2]!;
}

/**
 * Area-averaged luma of `box` (normalised) in an RGBA image, as a
 * `size` x `size` patch with its mean removed (uniform exposure changes cancel).
 */
export function lumaPatch(src: PixelSource, box: FaceBox, size = PATCH_SIZE): Float32Array {
  const out = new Float32Array(size * size);
  const x0 = Math.max(0, Math.min(src.width - 1, box.x * src.width));
  const y0 = Math.max(0, Math.min(src.height - 1, box.y * src.height));
  const x1 = Math.max(x0 + 1, Math.min(src.width, (box.x + box.w) * src.width));
  const y1 = Math.max(y0 + 1, Math.min(src.height, (box.y + box.h) * src.height));
  const cellW = (x1 - x0) / size;
  const cellH = (y1 - y0) / size;
  let total = 0;
  for (let py = 0; py < size; py++) {
    const sy0 = Math.floor(y0 + py * cellH);
    const sy1 = Math.max(sy0 + 1, Math.floor(y0 + (py + 1) * cellH));
    for (let px = 0; px < size; px++) {
      const sx0 = Math.floor(x0 + px * cellW);
      const sx1 = Math.max(sx0 + 1, Math.floor(x0 + (px + 1) * cellW));
      let sum = 0;
      let n = 0;
      for (let y = sy0; y < sy1 && y < src.height; y++) {
        for (let x = sx0; x < sx1 && x < src.width; x++) {
          sum += luma(src.data, (y * src.width + x) * 4);
          n++;
        }
      }
      const value = n ? sum / n : 0;
      out[py * size + px] = value;
      total += value;
    }
  }
  const mean = total / out.length;
  for (let i = 0; i < out.length; i++) out[i] = out[i]! - mean;
  return out;
}

/** Mean absolute difference per block; returns the block values (row-major). */
export function blockDiffs(a: Float32Array, b: Float32Array, size = PATCH_SIZE, block = BLOCK): number[] {
  const blocks = Math.floor(size / block);
  const out: number[] = [];
  for (let by = 0; by < blocks; by++) {
    for (let bx = 0; bx < blocks; bx++) {
      let sum = 0;
      for (let y = by * block; y < (by + 1) * block; y++) {
        for (let x = bx * block; x < (bx + 1) * block; x++) {
          const i = y * size + x;
          sum += Math.abs(a[i]! - b[i]!);
        }
      }
      out.push(sum / (block * block));
    }
  }
  return out;
}

export function quantile(values: readonly number[], q: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((x, y) => x - y);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(q * (sorted.length - 1))))]!;
}

export interface MotionOptions {
  /** Frames between compared patches (4 at 4 fps = 1 s). */
  lag?: number;
  /** Busiest block must exceed floor x factor ... */
  factor?: number;
  /** ... and floor + delta (grey levels). */
  delta?: number;
  /** Noise-floor history (frames). */
  history?: number;
  /** Diffs needed before evidence counts (lets the floor settle). */
  warmup?: number;
}

/**
 * Per-region motion meter. `push()` takes the region's patch each frame and
 * says whether this frame is motion evidence.
 */
export class MotionMeter {
  private readonly patches: Float32Array[] = [];
  private readonly peaks: number[] = [];
  private readonly lag: number;
  private readonly factor: number;
  private readonly delta: number;
  private readonly history: number;
  private readonly warmup: number;

  constructor(options: MotionOptions = {}) {
    this.lag = options.lag ?? 4;
    this.factor = options.factor ?? 2.5;
    this.delta = options.delta ?? 4;
    this.history = options.history ?? 24;
    this.warmup = options.warmup ?? 6;
  }

  reset(): void {
    this.patches.length = 0;
    this.peaks.length = 0;
  }

  /** Noise floor: a low percentile of recent busiest-block values (motion is intermittent, noise is always there). */
  get floor(): number {
    return quantile(this.peaks, 0.1);
  }

  push(patch: Float32Array, options: { calm?: boolean } = {}): { peak: number | null; evidence: boolean } {
    this.patches.push(patch);
    if (this.patches.length > this.lag + 1) this.patches.shift();
    if (this.patches.length <= this.lag) return { peak: null, evidence: false };
    const peak = Math.max(...blockDiffs(patch, this.patches[0]!));
    const floor = this.floor;
    const ready = this.peaks.length >= this.warmup;
    const evidence = ready && options.calm !== false && peak > Math.max(floor * this.factor, floor + this.delta);
    // Evidence frames do not raise the floor: a talking face keeps its sensitivity.
    if (!evidence) {
      this.peaks.push(peak);
      if (this.peaks.length > this.history) this.peaks.shift();
    }
    return { peak, evidence };
  }
}

/**
 * Whole-frame calm check: the median block change of a frame thumbnail
 * against its own history. A jump (exposure, lights, camera moved) means
 * face-region changes in this frame are not evidence of life; the median
 * ignores local changes such as the candidate moving.
 */
export class FrameCalm {
  private readonly history: Float32Array[] = [];
  private readonly medians: number[] = [];

  constructor(private readonly lag = 4) {}

  push(patch: Float32Array): boolean {
    this.history.push(patch);
    if (this.history.length > this.lag + 1) this.history.shift();
    if (this.history.length <= this.lag) return true;
    const median = quantile(blockDiffs(patch, this.history[0]!), 0.5);
    const floor = quantile(this.medians, 0.5);
    const calm = this.medians.length < 6 || median <= Math.max(floor * 2, floor + 3);
    if (calm) {
      this.medians.push(median);
      if (this.medians.length > 24) this.medians.shift();
    }
    return calm;
  }
}
