/**
 * Periodic frame capture from the camera (or a shared screen) and the local
 * camera-blocked luminance check.
 *
 * Frames are uploaded only when the policy enables analysis AND the candidate
 * consented; the interval comes from the policy. The luminance check runs
 * locally on a tiny 64x48 sample and never leaves the browser except as the
 * resulting `camera_blocked` observation.
 */

export interface LuminanceStats {
  mean: number;
  stddev: number;
}

/** Mean / standard deviation of luma (0-255) over RGBA pixels. */
export function luminanceStats(rgba: Uint8ClampedArray): LuminanceStats {
  const n = Math.floor(rgba.length / 4);
  if (n === 0) return { mean: 0, stddev: 0 };
  let sum = 0;
  let sumSq = 0;
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    const y = 0.2126 * rgba[o]! + 0.7152 * rgba[o + 1]! + 0.0722 * rgba[o + 2]!;
    sum += y;
    sumSq += y * y;
  }
  const mean = sum / n;
  return { mean, stddev: Math.sqrt(Math.max(0, sumSq / n - mean * mean)) };
}

/** A covered lens is dark and flat, or any colour but almost perfectly uniform. */
export function looksBlocked(stats: LuminanceStats): boolean {
  return (stats.mean < 22 && stats.stddev < 9) || stats.stddev < 3.5;
}

/** Tracks consecutive blocked samples so only sustained blocking is reported. */
export class BlockedDetector {
  private run = 0;
  private reported = false;

  constructor(private readonly samplesNeeded = 3) {}

  /** Returns true once when blocking becomes sustained; re-arms after recovery. */
  push(stats: LuminanceStats): boolean {
    if (looksBlocked(stats)) {
      this.run += 1;
      if (this.run >= this.samplesNeeded && !this.reported) {
        this.reported = true;
        return true;
      }
      return false;
    }
    this.run = 0;
    this.reported = false;
    return false;
  }
}

export interface FrameCapturerOptions {
  stream: MediaStream;
  kind: "camera" | "screen";
  /** Upload interval; null disables uploads (luminance check only). */
  uploadIntervalMs: number | null;
  upload?: (image: Blob, capturedAt: string) => Promise<void>;
  /** Luminance check interval (camera only). */
  checkIntervalMs?: number;
  onBlocked?: (stats: LuminanceStats & { seconds: number }) => void;
  maxWidth?: number;
  quality?: number;
}

/** Signals that uploads must stop for the rest of the session (policy/consent refused, session ended). */
export class StopUploadsError extends Error {}

/** Signals a temporary refusal (rate limit, rejected frame): pause uploads for `delayMs`. */
export class BackoffUploadsError extends Error {
  constructor(
    message: string,
    readonly delayMs: number,
  ) {
    super(message);
  }
}

export class FrameCapturer {
  private readonly video: HTMLVideoElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly sample: HTMLCanvasElement;
  private uploadTimer: ReturnType<typeof setInterval> | null = null;
  private checkTimer: ReturnType<typeof setInterval> | null = null;
  private readonly detector = new BlockedDetector();
  private uploading = false;
  private uploadsDisabled = false;
  private backoffUntil = 0;

  constructor(private readonly opts: FrameCapturerOptions) {
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.srcObject = opts.stream;
    this.canvas = document.createElement("canvas");
    this.sample = document.createElement("canvas");
    this.sample.width = 64;
    this.sample.height = 48;
  }

  async start(): Promise<void> {
    try {
      await this.video.play();
    } catch {
      /* frames are best effort */
    }
    if (this.opts.uploadIntervalMs && this.opts.upload) {
      this.uploadTimer = setInterval(() => void this.captureAndUpload(), this.opts.uploadIntervalMs);
      // First frame shortly after the stream settles.
      setTimeout(() => void this.captureAndUpload(), 2500);
    }
    if (this.opts.kind === "camera" && this.opts.onBlocked) {
      const interval = this.opts.checkIntervalMs ?? 2000;
      this.checkTimer = setInterval(() => this.checkLuminance(interval), interval);
    }
  }

  private ready(): boolean {
    return this.video.readyState >= 2 && this.video.videoWidth > 0;
  }

  checkLuminance(intervalMs: number): LuminanceStats | null {
    if (!this.ready()) return null;
    const ctx = this.sample.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(this.video, 0, 0, this.sample.width, this.sample.height);
    const stats = luminanceStats(ctx.getImageData(0, 0, this.sample.width, this.sample.height).data);
    if (this.detector.push(stats)) this.opts.onBlocked?.({ ...stats, seconds: Math.round((3 * intervalMs) / 1000) });
    return stats;
  }

  async captureBlob(): Promise<Blob | null> {
    if (!this.ready()) return null;
    const maxWidth = this.opts.maxWidth ?? 640;
    const scale = Math.min(1, maxWidth / this.video.videoWidth);
    this.canvas.width = Math.round(this.video.videoWidth * scale);
    this.canvas.height = Math.round(this.video.videoHeight * scale);
    const ctx = this.canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height);
    return new Promise((resolve) => this.canvas.toBlob((blob) => resolve(blob), "image/jpeg", this.opts.quality ?? 0.72));
  }

  private async captureAndUpload(): Promise<void> {
    if (this.uploading || this.uploadsDisabled || !this.opts.upload || Date.now() < this.backoffUntil) return;
    this.uploading = true;
    try {
      const blob = await this.captureBlob();
      if (blob) await this.opts.upload(blob, new Date().toISOString());
    } catch (error) {
      if (error instanceof StopUploadsError) {
        this.uploadsDisabled = true;
        if (this.uploadTimer) clearInterval(this.uploadTimer);
        this.uploadTimer = null;
      } else {
        this.backoffUntil = Date.now() + (error instanceof BackoffUploadsError ? error.delayMs : 15_000);
      }
    } finally {
      this.uploading = false;
    }
  }

  stop(): void {
    if (this.uploadTimer) clearInterval(this.uploadTimer);
    if (this.checkTimer) clearInterval(this.checkTimer);
    this.uploadTimer = null;
    this.checkTimer = null;
    try {
      this.video.pause();
    } catch {
      /* ignore */
    }
    this.video.srcObject = null;
  }
}
