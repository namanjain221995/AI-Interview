export interface BackoffOptions {
  /** First retry delay (ms). */
  baseMs?: number;
  /** Upper bound for any delay (ms). */
  maxMs?: number;
  factor?: number;
  /** Fractional jitter, e.g. 0.25 = +/-25 %. */
  jitter?: number;
}

export const DEFAULT_BACKOFF: Required<BackoffOptions> = { baseMs: 400, maxMs: 10_000, factor: 2, jitter: 0.25 };

/**
 * Exponential backoff with symmetric jitter. `attempt` is 0 for the first
 * retry. `random` is injectable for deterministic tests.
 */
export function backoffDelay(attempt: number, options: BackoffOptions = {}, random: () => number = Math.random): number {
  const { baseMs, maxMs, factor, jitter } = { ...DEFAULT_BACKOFF, ...options };
  const raw = Math.min(maxMs, baseMs * Math.pow(factor, Math.max(0, attempt)));
  const spread = raw * jitter;
  const value = raw - spread + random() * 2 * spread;
  return Math.max(0, Math.min(maxMs, Math.round(value)));
}
