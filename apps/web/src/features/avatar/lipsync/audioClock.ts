/**
 * Audible-time clock for an AudioContext: the context time of the sample the
 * listener hears right now. `currentTime` runs ahead of the speakers by the
 * output latency (about 10-40 ms on wired outputs, 100-300 ms on Bluetooth),
 * so lip-sync on `currentTime` would lead the voice by that much.
 *
 * Uses getOutputTimestamp() (context time of the frame at the output at a
 * given performance.now()) extrapolated to now, else outputLatency +
 * baseLatency; estimates outside [currentTime - 0.5 s, currentTime] are
 * ignored. Never throws; the result never runs backwards.
 */
const MAX_LATENCY = 0.5;

interface TimestampedContext {
  readonly currentTime: number;
  readonly baseLatency?: number;
  readonly outputLatency?: number;
  getOutputTimestamp?: () => AudioTimestamp;
}

export function audibleClock(context: TimestampedContext, perfNow: () => number = () => performance.now()): () => number {
  let last = -Infinity;
  return () => {
    const now = context.currentTime;
    let audible = now;
    try {
      const ts = typeof context.getOutputTimestamp === "function" ? context.getOutputTimestamp() : undefined;
      const contextTime = ts?.contextTime;
      const performanceTime = ts?.performanceTime;
      let estimate: number | null = null;
      if (typeof contextTime === "number" && typeof performanceTime === "number" && performanceTime > 0 && contextTime > 0) {
        estimate = contextTime + (perfNow() - performanceTime) / 1000;
      }
      if (estimate !== null && estimate <= now + 1e-3 && estimate >= now - MAX_LATENCY) {
        audible = Math.min(now, estimate);
      } else {
        const latency = (Number(context.outputLatency) || 0) + (Number(context.baseLatency) || 0);
        if (latency > 0 && latency <= MAX_LATENCY) audible = now - latency;
      }
    } catch {
      audible = now;
    }
    // Timestamp updates arrive per audio callback; never step backwards.
    if (audible < last && last - audible < 0.05) audible = last;
    last = audible;
    return audible;
  };
}
