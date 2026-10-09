/**
 * Speech amplitude envelope from the TTS playback AnalyserNode: RMS of the
 * time-domain signal, normalised against an adaptive peak, with fast attack
 * and slower release. Drives jaw opening and viseme intensity.
 *
 * The analyser sees samples at the graph time (AudioContext.currentTime),
 * which the listener hears one output latency later. The follower keeps a
 * short timestamped history so the engine can read the envelope at the
 * audible time, in step with the visemes (see audioClock.ts).
 */
import { clamp01, damp } from "../core/math";

/** Envelope history kept for latency compensation (s); Bluetooth outputs reach ~0.3 s. */
const HISTORY_SECONDS = 0.6;

/**
 * The envelope trails the loudness by its attack time constant (1/40 s); the
 * engine reads it this far ahead of the audible time to cancel that, the same
 * way lip-sync samples its timeline ahead (VISUAL_LEAD). The history reaches
 * one output latency past the audible time, so the look-ahead is available
 * whenever there is output latency to hide behind.
 */
export const AMPLITUDE_LEAD = 0.025;

/**
 * Where the raw loudness is read relative to the audible time: the RMS window
 * (fftSize samples, ~21 ms) is centred ~10 ms before the graph time of the
 * reading, and the presence orb's own ~25 ms attack plus one display frame
 * want a little more; see `level`.
 */
export const LEVEL_LEAD = 0.03;

export class AmplitudeFollower {
  private analyser: AnalyserNode | null = null;
  private buffer: Float32Array<ArrayBuffer> | null = null;
  private env = 0;
  private peak = 0.05;
  private history: Array<{ t: number; v: number; r: number }> = [];
  private raw = 0;
  private rawAt = 0;

  attach(analyser: AnalyserNode | null): void {
    this.analyser = analyser;
    this.buffer = analyser ? new Float32Array(analyser.fftSize) : null;
    this.env = 0;
    this.raw = 0;
    this.rawAt = 0;
    this.history = [];
  }

  /**
   * Raw normalised loudness (0..1, no envelope) at the `levelAt` time of the
   * last sample() call (else the latest reading). Renderers that want their
   * own attack/release (the presence orb) start from this.
   */
  get level(): number {
    return this.rawAt;
  }

  get attached(): boolean {
    return this.analyser !== null;
  }

  /**
   * Advance the envelope (0..1; 0 when no analyser is attached). With `now`
   * (graph time of this reading) and `at` (the time to report, e.g. the
   * audible time) the value the listener hears at `at` is returned.
   */
  sample(dt: number, now?: number, at?: number, levelAt?: number): number {
    const env = this.measure(dt);
    this.rawAt = this.raw;
    if (now === undefined || at === undefined || !Number.isFinite(now) || !Number.isFinite(at)) return env;
    const history = this.history;
    const last = history[history.length - 1];
    if (last && now < last.t) history.length = 0; // clock restarted
    history.push({ t: now, v: env, r: this.raw });
    while (history.length > 2 && history[0]!.t < now - HISTORY_SECONDS) history.shift();
    if (levelAt !== undefined && Number.isFinite(levelAt)) this.rawAt = this.read(levelAt, now, "r", this.raw);
    return this.read(at, now, "v", env);
  }

  /** History value (`v` envelope or `r` raw level) at time `at` (linear interpolation). */
  private read(at: number, now: number, key: "v" | "r", current: number): number {
    const history = this.history;
    if (at >= now) return current;
    for (let i = history.length - 1; i > 0; i--) {
      const a = history[i - 1]!;
      const b = history[i]!;
      if (at >= a.t) return b.t > a.t ? a[key] + ((b[key] - a[key]) * (at - a.t)) / (b.t - a.t) : b[key];
    }
    return history[0]![key];
  }

  private measure(dt: number): number {
    const analyser = this.analyser;
    const buffer = this.buffer;
    if (!analyser || !buffer) {
      this.raw = 0;
      return 0;
    }
    if (buffer.length !== analyser.fftSize) {
      this.buffer = new Float32Array(analyser.fftSize);
      return this.env;
    }
    let level = 0;
    try {
      analyser.getFloatTimeDomainData(buffer);
      let sum = 0;
      for (let i = 0; i < buffer.length; i++) {
        const s = buffer[i]!;
        sum += s * s;
      }
      const rms = Math.sqrt(sum / buffer.length);
      if (!Number.isFinite(rms)) return this.env;
      // Adaptive peak: follows loud speech up immediately, relaxes over ~3 s.
      this.peak = Math.max(rms, 0.02, this.peak * Math.exp(-dt / 3));
      const floor = 0.004;
      level = clamp01((rms - floor) / (this.peak - floor));
    } catch {
      level = 0;
    }
    this.raw = level;
    this.env = damp(this.env, level, level > this.env ? 40 : 9, dt);
    return this.env;
  }
}
