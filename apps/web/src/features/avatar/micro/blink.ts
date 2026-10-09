/**
 * Blink scheduler: intervals of 2-6 s (skewed toward the short end, mean
 * ~3.7 s), occasional double blinks, 120-180 ms per blink (fast close, slower
 * open). Deterministic for a given seed; time is passed in explicitly.
 */
import { clamp, clamp01, easeInOutSine, easeOutCubic, mulberry32, randRange, type Rng } from "../core/math";

export interface BlinkOptions {
  minInterval: number;
  maxInterval: number;
  /** Probability that a blink is followed by a second one. */
  doubleChance: number;
  minDuration: number;
  maxDuration: number;
}

export const DEFAULT_BLINK: BlinkOptions = {
  minInterval: 2,
  maxInterval: 6,
  doubleChance: 0.15,
  minDuration: 0.12,
  maxDuration: 0.18,
};

interface ActiveBlink {
  start: number;
  duration: number;
  /** Whether another blink follows this one. */
  double: boolean;
}

export class BlinkScheduler {
  private readonly rng: Rng;
  private readonly options: BlinkOptions;
  private next: number;
  private active: ActiveBlink | null = null;
  private queued: number | null = null;
  private lastBlinkEnd = -Infinity;
  /** Interval multiplier (<1 blinks more often, e.g. while speaking). */
  rate = 1;

  constructor(seed: number, now = 0, options: Partial<BlinkOptions> = {}) {
    this.rng = mulberry32(seed);
    this.options = { ...DEFAULT_BLINK, ...options };
    this.next = now + this.sampleInterval();
  }

  /** One inter-blink interval (s), in [minInterval, maxInterval]. */
  sampleInterval(): number {
    const { minInterval, maxInterval } = this.options;
    // u^1.4 skews toward short intervals like natural blink statistics.
    const u = Math.pow(this.rng(), 1.4);
    return minInterval + (maxInterval - minInterval) * u;
  }

  sampleDuration(): number {
    return randRange(this.rng, this.options.minDuration, this.options.maxDuration);
  }

  /** Force a blink soon (e.g. on a large gaze shift), unless one just happened. */
  trigger(now: number): void {
    if (this.active || now - this.lastBlinkEnd < 0.8) return;
    this.next = Math.min(this.next, now + 0.02);
  }

  /** Lid closure 0 (open) .. 1 (closed) at time `now`. */
  update(now: number): number {
    if (!this.active && this.queued !== null && now >= this.queued) {
      this.start(now, false);
      this.queued = null;
    } else if (!this.active && now >= this.next) {
      this.start(now, this.rng() < this.options.doubleChance);
    }
    if (!this.active) return 0;

    const { start, duration, double } = this.active;
    const t = (now - start) / duration;
    if (t >= 1) {
      this.active = null;
      this.lastBlinkEnd = now;
      if (double) this.queued = now + randRange(this.rng, 0.06, 0.14);
      const { minInterval, maxInterval } = this.options;
      this.next = now + clamp(this.sampleInterval() * this.rate, minInterval, maxInterval);
      return 0;
    }
    // 40% closing, 60% opening (lids open more slowly than they close).
    return t < 0.4 ? easeInOutSine(t / 0.4) : clamp01(1 - easeOutCubic((t - 0.4) / 0.6));
  }

  private start(now: number, double: boolean): void {
    this.active = { start: now, duration: this.sampleDuration(), double };
  }
}
