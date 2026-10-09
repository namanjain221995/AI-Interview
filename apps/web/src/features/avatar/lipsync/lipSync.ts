/**
 * Lip-sync scheduler. Viseme cues are placed on the audio clock (once an
 * AudioContext is attached: its audible time, i.e. currentTime minus the
 * output latency, see audioClock.ts): a chunk starting at `startTime` places
 * each cue at startTime + t/1000. Sampling blends the current viseme into the next one
 * shortly before its onset (anticipatory coarticulation), decays long holds,
 * closes to "sil" at chunk ends, then smooths every channel with separate
 * attack/release rates. interrupt() drops everything queued and closes the
 * mouth within ~120 ms.
 *
 * Closures are planned rather than merely smoothed. Real TTS timelines give
 * bilabials (PP: p/b/m) as little as 25 ms (Kokoro durations come in 25 ms
 * frames), which smoothing alone never turns into a visible closure. So a
 * short closure cue starts earlier (borrowing from the preceding shape) to get
 * a minimum plateau, closures blend in early and release fast (the burst),
 * and while the lips are sealed the open shapes are suppressed. The timeline
 * is also sampled slightly ahead so the smoothed mouth lands on the sound
 * instead of trailing it (measured on real TTS output, see dev/lipSyncSim).
 */
import type { SpeechCue } from "../types";
import { clamp, clamp01, damp, smoothstep } from "../core/math";
import { VISEME_IDS, type VisemeId } from "../rig/morphs";
import { normalizeViseme, textToVisemes } from "./visemes";

export type ShapeViseme = Exclude<VisemeId, "sil">;
export const SHAPE_VISEMES = VISEME_IDS.filter((v): v is ShapeViseme => v !== "sil");

/** Lip closures (bilabial PP, labiodental FF): must be reached even on very short cues. */
export const CLOSURE_VISEMES: ReadonlySet<VisemeId> = new Set<VisemeId>(["PP", "FF"]);

export type VisemeWeights = Record<ShapeViseme, number>;

export function zeroVisemes(): VisemeWeights {
  const w = {} as VisemeWeights;
  for (const v of SHAPE_VISEMES) w[v] = 0;
  return w;
}

interface Key {
  time: number;
  v: VisemeId;
  w: number;
  utteranceId: string;
  chunkId: number;
}

interface Chunk {
  utteranceId: string;
  chunkId: number;
  start: number;
  end: number;
}

export interface LipSyncFrame {
  /** Smoothed viseme weights (0..1), open shapes already yielding to closures. */
  weights: VisemeWeights;
  /** Overall mouth activity 0..1. */
  activity: number;
  /** Lip closure 0..1 (PP, partly FF): jaw and open shapes yield to it. */
  closure: number;
  /** A chunk is currently playing (with a short tail). */
  speaking: boolean;
  /** Seconds until the next queued chunk starts (Infinity when none). */
  nextStartIn: number;
}

/** Longest a viseme is held without a successor before relaxing (s). */
const HOLD_MAX = 0.22;
const HOLD_RELEASE = 0.12;
const ATTACK = 32;
/** Closures attack fast so even short cues seal the lips (time constant ~12 ms). */
const ATTACK_CLOSURE = 85;
const RELEASE = 20;
/** The release of a stop (the burst) opens quickly. */
const RELEASE_CLOSURE = 32;
const INTERRUPT_RELEASE = 45;
/** Shortest full-weight closure plateau (s); shorter closure cues start earlier. */
const MIN_CLOSURE = 0.05;
/** Never take more than this share of the preceding shape when moving a closure earlier. */
const CLOSURE_BORROW = 0.65;
/** How strongly a sealed closure suppresses the open shapes. */
const CLOSURE_DOMINANCE = 0.85;
/**
 * The timeline is sampled this far ahead (s): smoothing otherwise makes the
 * mouth trail the audio (measured +17..33 ms against loudness on real TTS).
 */
export const VISUAL_LEAD = 0.02;

function upperBound(keys: readonly Key[], t: number): number {
  let lo = 0;
  let hi = keys.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (keys[mid]!.time <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * Give short closure cues a minimum plateau by starting them earlier, never
 * taking more than CLOSURE_BORROW of the preceding cue. Keys stay sorted.
 */
function planClosures(keys: Key[], chunkEnd: number): void {
  for (let i = 0; i < keys.length; i++) {
    const key = keys[i]!;
    if (!CLOSURE_VISEMES.has(key.v)) continue;
    const next = keys[i + 1];
    const duration = (next ? next.time : chunkEnd) - key.time;
    if (duration >= MIN_CLOSURE) continue;
    const prev = keys[i - 1];
    const earliest = prev ? prev.time + (1 - CLOSURE_BORROW) * (key.time - prev.time) : key.time;
    key.time = Math.max(earliest, key.time - (MIN_CLOSURE - duration));
  }
}

export class LipSync {
  private keys: Key[] = [];
  private chunks: Chunk[] = [];
  private readonly smoothed = zeroVisemes();
  private readonly output = zeroVisemes();
  private readonly raw = zeroVisemes();
  private lastTime: number | null = null;
  private closingUntil = -Infinity;
  private wordSerial = 0;
  private readonly frame: LipSyncFrame = { weights: this.output, activity: 0, closure: 0, speaking: false, nextStartIn: Infinity };

  constructor(private clock: () => number) {}

  setClock(clock: () => number): void {
    this.clock = clock;
    this.lastTime = null;
  }

  now(): number {
    return this.clock();
  }

  /** Number of scheduled keys (diagnostics/tests). */
  get queuedKeys(): number {
    return this.keys.length;
  }

  enqueue(cue: SpeechCue): void {
    const start = Number(cue.startTime);
    if (!Number.isFinite(start)) return;
    const durationMs = Math.max(0, Number(cue.durationMs) || 0);
    const end = start + durationMs / 1000;

    // A re-sent chunk replaces itself; a chunk scheduled over an older window wins it.
    this.keys = this.keys.filter(
      (k) => !(k.utteranceId === cue.utteranceId && k.chunkId === cue.chunkId) && (k.time < start - 1e-6 || k.time > end + 1e-6),
    );
    this.chunks = this.chunks.filter((c) => !(c.utteranceId === cue.utteranceId && c.chunkId === cue.chunkId));

    const added: Key[] = [];
    const visemes = Array.isArray(cue.visemes) ? [...cue.visemes] : [];
    visemes.sort((a, b) => a.t - b.t);
    for (const raw of visemes) {
      const v = normalizeViseme(String(raw.v));
      if (!v) continue;
      const t = clamp(Number(raw.t) || 0, 0, durationMs);
      const w = raw.w === undefined || raw.w === null ? 1 : clamp01(Number(raw.w));
      added.push({ time: start + t / 1000, v, w, utteranceId: cue.utteranceId, chunkId: cue.chunkId });
    }
    const last = added[added.length - 1];
    if (!last || last.v !== "sil" || last.time < end - 0.02) {
      added.push({ time: end, v: "sil", w: 0, utteranceId: cue.utteranceId, chunkId: cue.chunkId });
    }
    planClosures(added, end);
    this.keys.push(...added);
    this.keys.sort((a, b) => a.time - b.time);
    this.chunks.push({ utteranceId: cue.utteranceId, chunkId: cue.chunkId, start, end });
    this.chunks.sort((a, b) => a.start - b.start);
    if (start >= this.clock()) this.closingUntil = -Infinity;
  }

  /** Browser speechSynthesis fallback: approximate visemes for one spoken word. */
  speakWord(word: string, durationMs: number): void {
    const steps = textToVisemes(word);
    if (steps.length === 0) return;
    const duration = clamp(durationMs, 80, 2000);
    const total = steps.reduce((n, s) => n + s.d, 0);
    let t = 0;
    const visemes = steps.map((s) => {
      const at = t;
      t += (s.d / total) * duration;
      return { t: Math.round(at), v: s.v, w: s.w };
    });
    this.wordSerial += 1;
    this.enqueue({
      utteranceId: "__speech_synthesis__",
      chunkId: this.wordSerial,
      startTime: this.clock() + 0.005,
      durationMs: duration,
      visemes,
      text: word,
    });
  }

  /** Drop every queued cue and close the mouth quickly. */
  interrupt(): void {
    this.keys = [];
    this.chunks = [];
    this.closingUntil = this.clock() + 0.3;
  }

  sample(): LipSyncFrame {
    return this.sampleAt(this.clock());
  }

  sampleAt(t: number): LipSyncFrame {
    let dt = this.lastTime === null ? 1 / 60 : t - this.lastTime;
    if (dt < 0 || dt > 0.25) dt = dt < 0 ? 0 : 0.25;
    this.lastTime = t;
    this.collect(t);

    const raw = this.raw;
    for (const v of SHAPE_VISEMES) raw[v] = 0;
    const ts = t + VISUAL_LEAD;
    const idx = upperBound(this.keys, ts);
    const c = idx > 0 ? this.keys[idx - 1] : undefined;
    const n = this.keys[idx];
    if (c) {
      let wc = c.v === "sil" ? 0 : c.w;
      if (!n && ts > c.time + HOLD_MAX) wc *= 1 - smoothstep((ts - c.time - HOLD_MAX) / HOLD_RELEASE);
      let s = 0;
      if (n) {
        const gap = n.time - c.time;
        // Into a closure: start early so the lips meet at the onset. Out of a
        // closure: release fast (the burst). Otherwise a symmetric crossfade.
        const blend = CLOSURE_VISEMES.has(n.v)
          ? clamp(gap * 0.7, 0.03, 0.075)
          : CLOSURE_VISEMES.has(c.v)
            ? clamp(gap * 0.4, 0.015, 0.05)
            : clamp(gap * 0.5, 0.02, 0.09);
        s = smoothstep((ts - (n.time - blend)) / blend);
      }
      if (c.v !== "sil") raw[c.v] += wc * (1 - s);
      if (n && n.v !== "sil") raw[n.v] += n.w * s;
    } else if (n && n.v !== "sil") {
      raw[n.v] += n.w * smoothstep((ts - (n.time - 0.06)) / 0.06);
    }

    const closing = t < this.closingUntil;
    for (const v of SHAPE_VISEMES) {
      const cur = this.smoothed[v];
      const target = closing ? 0 : raw[v];
      const closure = CLOSURE_VISEMES.has(v);
      const lambda = closing ? INTERRUPT_RELEASE : target > cur ? (closure ? ATTACK_CLOSURE : ATTACK) : closure ? RELEASE_CLOSURE : RELEASE;
      const next = damp(cur, target, lambda, dt);
      this.smoothed[v] = next < 1e-4 ? 0 : next;
    }

    // While the lips are sealed (PP) or tucked (FF) the open shapes yield.
    const sealed = clamp01(Math.max(this.smoothed.PP, 0.65 * this.smoothed.FF));
    const yieldOpen = 1 - CLOSURE_DOMINANCE * sealed;
    let activity = 0;
    for (const v of SHAPE_VISEMES) {
      const w = CLOSURE_VISEMES.has(v) ? this.smoothed[v] : this.smoothed[v] * yieldOpen;
      this.output[v] = w < 1e-4 ? 0 : w;
      activity = Math.max(activity, this.output[v]);
    }

    let speaking = false;
    let nextStartIn = Infinity;
    for (const ch of this.chunks) {
      if (t >= ch.start - 0.02 && t <= ch.end + 0.15) speaking = true;
      if (ch.start > t) nextStartIn = Math.min(nextStartIn, ch.start - t);
    }
    const f = this.frame;
    f.activity = clamp01(activity);
    f.closure = sealed;
    f.speaking = speaking;
    f.nextStartIn = nextStartIn;
    return f;
  }

  /** Forget cues more than a second in the past (keeps the one in effect). */
  private collect(t: number): void {
    const idx = upperBound(this.keys, t - 1);
    if (idx > 1) this.keys.splice(0, idx - 1);
    if (this.chunks.length && this.chunks[0]!.end < t - 1) this.chunks = this.chunks.filter((c) => c.end >= t - 1);
  }
}
