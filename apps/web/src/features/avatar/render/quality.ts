/**
 * Adaptive render quality: lowers the pixel ratio when FPS stays below 45 for
 * 2 s, raises it again after sustained headroom, with a cooldown and a cap on
 * failed upgrades so it never oscillates.
 */
export interface AdaptiveDprOptions {
  max: number;
  min?: number;
  step?: number;
  lowFps?: number;
  highFps?: number;
  lowFor?: number;
  highFor?: number;
  cooldown?: number;
  initial?: number;
}

export class AdaptiveDpr {
  dpr: number;
  private readonly max: number;
  private readonly min: number;
  private readonly step: number;
  private readonly lowFps: number;
  private readonly highFps: number;
  private readonly lowFor: number;
  private readonly highFor: number;
  private readonly cooldown: number;
  private lowSince: number | null = null;
  private highSince: number | null = null;
  private noUpgradeUntil = -Infinity;
  private lastUpgradeAt = -Infinity;
  private failedUpgrades = 0;

  constructor(options: AdaptiveDprOptions) {
    this.max = options.max;
    this.min = Math.min(options.min ?? 0.75, options.max);
    this.step = options.step ?? 0.25;
    this.lowFps = options.lowFps ?? 45;
    this.highFps = options.highFps ?? 57;
    this.lowFor = options.lowFor ?? 2;
    this.highFor = options.highFor ?? 8;
    this.cooldown = options.cooldown ?? 20;
    this.dpr = Math.max(this.min, Math.min(this.max, options.initial ?? this.max));
  }

  /** Feed an FPS sample taken at time `now` (s). Returns the new DPR when it changes. */
  update(fps: number, now: number): number | null {
    if (!Number.isFinite(fps)) return null;
    if (fps < this.lowFps) {
      this.highSince = null;
      this.lowSince ??= now;
      if (now - this.lowSince >= this.lowFor && this.dpr > this.min) {
        this.dpr = Math.max(this.min, round2(this.dpr - this.step));
        this.lowSince = now;
        this.noUpgradeUntil = now + this.cooldown;
        if (now - this.lastUpgradeAt < this.cooldown) this.failedUpgrades += 1;
        return this.dpr;
      }
      return null;
    }
    this.lowSince = null;
    if (fps >= this.highFps) {
      this.highSince ??= now;
      if (
        now - this.highSince >= this.highFor &&
        this.dpr < this.max &&
        now >= this.noUpgradeUntil &&
        this.failedUpgrades < 2
      ) {
        this.dpr = Math.min(this.max, round2(this.dpr + this.step));
        this.highSince = now;
        this.lastUpgradeAt = now;
        return this.dpr;
      }
    } else this.highSince = null;
    return null;
  }
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}
