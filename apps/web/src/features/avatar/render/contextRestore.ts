/**
 * WebGL context-loss recovery policy for the stage (no three.js imports).
 *
 * A lost context switches the stage to the 2D portrait. After a cooldown the
 * stage probes for a fresh WebGL context a few times and, on success, mounts
 * the 3D renderer again (the controller keeps the loaded rig, so nothing is
 * downloaded). A second loss soon after a restore means the GPU is unstable:
 * the stage then stays 2D for the rest of the session instead of flapping.
 */
export interface ContextRestoreOptions {
  /** Wait after a loss before the first probe (ms). */
  retryMs?: number;
  /** Probes per loss before giving up. */
  maxProbes?: number;
  /** A loss within this long after a restore keeps 2D for good (ms). */
  flapWindowMs?: number;
}

export class ContextRestorePolicy {
  readonly retryMs: number;
  readonly maxProbes: number;
  private readonly flapWindowMs: number;
  private restoredAt: number | null = null;
  private exhausted = false;
  private probes = 0;

  constructor(options: ContextRestoreOptions = {}) {
    this.retryMs = options.retryMs ?? 3000;
    this.maxProbes = options.maxProbes ?? 3;
    this.flapWindowMs = options.flapWindowMs ?? 60_000;
  }

  /** True once the stage must stay 2D. */
  get givenUp(): boolean {
    return this.exhausted;
  }

  /** A context loss at `now` (ms): returns the delay before probing, or null to stay 2D. */
  onLost(now: number): number | null {
    if (this.exhausted) return null;
    if (this.restoredAt !== null && now - this.restoredAt < this.flapWindowMs) {
      this.exhausted = true;
      return null;
    }
    this.probes = 0;
    return this.retryMs;
  }

  /** A probe failed: returns the delay before the next one, or null to stay 2D. */
  onProbeFailed(): number | null {
    this.probes += 1;
    if (this.probes >= this.maxProbes) {
      this.exhausted = true;
      return null;
    }
    return this.retryMs;
  }

  /** The 3D renderer was mounted again at `now` (ms). */
  onRestored(now: number): void {
    this.restoredAt = now;
    this.probes = 0;
  }
}
