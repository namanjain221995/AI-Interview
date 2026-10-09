/**
 * Objective lip-sync checks on recorded mouth frames (dev + tests). Works on
 * the FINAL morph weights the engine hands to the face (viseme_* + jawOpen)
 * sampled on the audio clock, against the scheduled viseme timeline:
 *
 *   closures      every bilabial (PP: p/b/m) must seal the lips: viseme_PP
 *                 peaks high while the open shapes are nearly gone
 *   labiodentals  every FF (f/v) must reach a visible lip-teeth contact
 *   pauses        the mouth returns to rest inside every pause >= 120 ms
 *   jitter        no micro-oscillation or frame-to-frame jumps of the opening
 *   sync          lag between mouth opening and audio loudness
 */
import type { VisemeId } from "../rig/morphs";
import type { SpeechCue } from "../types";

export interface MouthSample {
  /** Audio-clock seconds. */
  t: number;
  /** Final morph weights applied to the face (viseme_* names + jawOpen). */
  face: Readonly<Record<string, number>>;
  /** Audio loudness (RMS of the last ~20 ms) at `t`, when known. */
  level?: number;
}

export interface TimedViseme {
  /** Absolute onset (audio-clock seconds). */
  t: number;
  /** Next onset in the same chunk, or the chunk end. */
  end: number;
  v: VisemeId;
  w: number;
}

/** How much each viseme opens the mouth (jaw drop); PP closes it. */
export const OPENING_WEIGHT: Readonly<Record<string, number>> = {
  viseme_aa: 1,
  viseme_O: 0.75,
  viseme_E: 0.5,
  viseme_I: 0.35,
  viseme_U: 0.3,
  viseme_DD: 0.35,
  viseme_kk: 0.4,
  viseme_TH: 0.3,
  viseme_CH: 0.3,
  viseme_RR: 0.3,
  viseme_nn: 0.25,
  viseme_SS: 0.15,
  viseme_FF: 0.1,
  jawOpen: 1,
};

/** Copy of the mouth channels of a face (viseme_* + jawOpen) for recording. */
export function mouthWeights(face: Readonly<Record<string, number>>): Record<string, number> {
  const out: Record<string, number> = { jawOpen: face.jawOpen ?? 0 };
  for (const key in face) if (key.startsWith("viseme_")) out[key] = face[key]!;
  return out;
}

export function opening(face: Readonly<Record<string, number>>): number {
  let sum = 0;
  for (const key in OPENING_WEIGHT) sum += OPENING_WEIGHT[key]! * (face[key] ?? 0);
  return sum;
}

/** Everything that moves the mouth away from rest. */
export function mouthActivity(face: Readonly<Record<string, number>>): number {
  let sum = face.jawOpen ?? 0;
  for (const key in face) if (key.startsWith("viseme_")) sum += face[key] ?? 0;
  return sum;
}

export interface LipSyncThresholds {
  /** viseme_PP peak that seals the lips on the interviewer model. */
  sealPP: number;
  /** Max opening allowed at the PP peak. */
  sealOpening: number;
  /** viseme_FF peak for visible lip-teeth contact. */
  reachFF: number;
  ffOpening: number;
  /** Mouth activity that counts as "at rest" inside a pause. */
  restActivity: number;
  /** Pauses shorter than this are coarticulated, not checked (s). */
  minPause: number;
  /** Swing below which a direction change counts as jitter. */
  microSwing: number;
}

/**
 * Calibrated on the interviewer GLB (fixed-weight mouth renders): viseme_PP
 * seals the lips from ~0.5 and they stay sealed with up to ~0.2 of competing
 * opening (e.g. PP 0.8 + aa 0.2); viseme_FF shows lip-teeth contact from ~0.45.
 */
export const DEFAULT_THRESHOLDS: LipSyncThresholds = {
  sealPP: 0.6,
  sealOpening: 0.2,
  reachFF: 0.45,
  ffOpening: 0.3,
  restActivity: 0.08,
  minPause: 0.12,
  microSwing: 0.03,
};

export interface ClosureEvent {
  t: number;
  durationMs: number;
  viseme: "PP" | "FF";
  peak: number;
  openingAtPeak: number;
  ok: boolean;
}

export interface PauseEvent {
  t: number;
  durationMs: number;
  /** Lowest mouth activity reached inside the pause. */
  residual: number;
  ok: boolean;
}

export interface LipSyncReport {
  frames: number;
  speechSeconds: number;
  closures: { total: number; sealed: number; meanPeak: number; minPeak: number; events: ClosureEvent[] };
  labiodentals: { total: number; reached: number; meanPeak: number; events: ClosureEvent[] };
  pauses: { total: number; closed: number; worstResidual: number; events: PauseEvent[] };
  jitter: {
    /** Direction changes of the mouth opening per second of speech. */
    reversalsPerSecond: number;
    /** Direction changes with a swing below `microSwing` per second. */
    microReversalsPerSecond: number;
    /** 95th percentile of the frame-to-frame opening change (60 fps). */
    p95Step: number;
    /** RMS of the second difference of the opening (smoothness). */
    accelRms: number;
  };
  /** Lag (ms) maximising the correlation of opening and loudness; + = mouth after audio. */
  sync: { lagMs: number | null; correlation: number | null };
}

/** Absolute viseme timeline of scheduled cues (as LipSync places them). */
export function timelineFromCues(cues: readonly SpeechCue[]): TimedViseme[] {
  const out: TimedViseme[] = [];
  for (const cue of cues) {
    const end = cue.startTime + cue.durationMs / 1000;
    const sorted = [...cue.visemes].sort((a, b) => a.t - b.t);
    sorted.forEach((v, i) => {
      const next = sorted[i + 1];
      out.push({
        t: cue.startTime + v.t / 1000,
        end: next ? cue.startTime + next.t / 1000 : end,
        v: v.v as VisemeId,
        w: v.w,
      });
    });
  }
  return out.sort((a, b) => a.t - b.t);
}

function within(samples: readonly MouthSample[], from: number, to: number): MouthSample[] {
  return samples.filter((s) => s.t >= from - 1e-9 && s.t <= to + 1e-9);
}

function closureEvents(samples: readonly MouthSample[], timeline: readonly TimedViseme[], viseme: "PP" | "FF", th: LipSyncThresholds): ClosureEvent[] {
  const key = `viseme_${viseme}`;
  const events: ClosureEvent[] = [];
  for (const cue of timeline) {
    if (cue.v !== viseme) continue;
    const window = within(samples, cue.t - 0.03, Math.max(cue.end, cue.t + 0.05) + 0.03);
    let peak = 0;
    let openingAtPeak = 0;
    for (const s of window) {
      const w = s.face[key] ?? 0;
      if (w > peak) {
        peak = w;
        openingAtPeak = opening(s.face);
      }
    }
    const ok = viseme === "PP" ? peak >= th.sealPP && openingAtPeak <= th.sealOpening : peak >= th.reachFF && openingAtPeak <= th.ffOpening;
    events.push({ t: cue.t, durationMs: Math.round((cue.end - cue.t) * 1000), viseme, peak, openingAtPeak, ok });
  }
  return events;
}

/** Pauses: sil cues and gaps between chunks lasting >= minPause. */
function pauseWindows(cues: readonly SpeechCue[], timeline: readonly TimedViseme[], minPause: number): Array<{ t: number; end: number }> {
  const out: Array<{ t: number; end: number }> = [];
  for (const cue of timeline) if (cue.v === "sil" && cue.end - cue.t >= minPause) out.push({ t: cue.t, end: cue.end });
  const chunks = [...cues].sort((a, b) => a.startTime - b.startTime);
  for (let i = 0; i + 1 < chunks.length; i++) {
    const end = chunks[i]!.startTime + chunks[i]!.durationMs / 1000;
    const next = chunks[i + 1]!.startTime;
    if (next - end >= minPause) out.push({ t: end, end: next });
  }
  return out.sort((a, b) => a.t - b.t);
}

function pearson(a: readonly number[], b: readonly number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 3) return 0;
  let ma = 0;
  let mb = 0;
  for (let i = 0; i < n; i++) {
    ma += a[i]!;
    mb += b[i]!;
  }
  ma /= n;
  mb /= n;
  let num = 0;
  let da = 0;
  let db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i]! - ma;
    const y = b[i]! - mb;
    num += x * y;
    da += x * x;
    db += y * y;
  }
  return da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
}

export function analyzeLipSync(
  samples: readonly MouthSample[],
  cues: readonly SpeechCue[],
  thresholds: Partial<LipSyncThresholds> = {},
): LipSyncReport {
  const th = { ...DEFAULT_THRESHOLDS, ...thresholds };
  const timeline = timelineFromCues(cues);
  const inSpeech = (t: number) => cues.some((c) => t >= c.startTime && t <= c.startTime + c.durationMs / 1000);
  const speech = samples.filter((s) => inSpeech(s.t));
  const frameDt = samples.length > 1 ? (samples[samples.length - 1]!.t - samples[0]!.t) / (samples.length - 1) : 1 / 60;
  const speechSeconds = speech.length * frameDt;

  const closures = closureEvents(samples, timeline, "PP", th);
  const labio = closureEvents(samples, timeline, "FF", th);

  const pauses: PauseEvent[] = pauseWindows(cues, timeline, th.minPause).map(({ t, end }) => {
    const dur = end - t;
    const inner = within(samples, t + Math.min(0.1, 0.45 * dur), end - 0.04);
    const residual = inner.length ? Math.min(...inner.map((s) => mouthActivity(s.face))) : Infinity;
    return { t, durationMs: Math.round(dur * 1000), residual, ok: residual <= th.restActivity };
  });

  // Jitter on the opening signal during speech (contiguous runs only).
  const o = speech.map((s) => opening(s.face));
  let reversals = 0;
  let micro = 0;
  const steps: number[] = [];
  let accel = 0;
  let accelN = 0;
  let lastExtreme: number | null = null;
  let dir = 0;
  for (let i = 1; i < o.length; i++) {
    const contiguous = speech[i]!.t - speech[i - 1]!.t < frameDt * 1.5;
    if (!contiguous) {
      dir = 0;
      lastExtreme = null;
      continue;
    }
    const d = o[i]! - o[i - 1]!;
    steps.push(Math.abs(d));
    if (i + 1 < o.length && speech[i + 1]!.t - speech[i]!.t < frameDt * 1.5) {
      const a = o[i + 1]! - 2 * o[i]! + o[i - 1]!;
      accel += a * a;
      accelN += 1;
    }
    const s = d > 1e-4 ? 1 : d < -1e-4 ? -1 : 0;
    if (s !== 0 && dir !== 0 && s !== dir) {
      reversals += 1;
      const extreme = o[i - 1]!;
      if (lastExtreme !== null && Math.abs(extreme - lastExtreme) < th.microSwing) micro += 1;
      lastExtreme = extreme;
    }
    if (s !== 0) dir = s;
  }
  steps.sort((a, b) => a - b);

  // Sync: lag of the best correlation between opening and loudness.
  let lagMs: number | null = null;
  let correlation: number | null = null;
  const levels = speech.map((s) => s.level);
  if (levels.every((l) => typeof l === "number") && speech.length > 30) {
    const lv = levels as number[];
    const maxLag = Math.round(0.12 / frameDt);
    for (let k = -maxLag; k <= maxLag; k++) {
      const a: number[] = [];
      const b: number[] = [];
      for (let i = 0; i < o.length; i++) {
        const j = i + k;
        if (j < 0 || j >= o.length) continue;
        a.push(o[j]!);
        b.push(lv[i]!);
      }
      const r = pearson(a, b);
      if (correlation === null || r > correlation) {
        correlation = r;
        lagMs = Math.round(k * frameDt * 1000);
      }
    }
  }

  const peaks = (events: ClosureEvent[]) => events.map((e) => e.peak);
  const mean = (v: number[]) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0);
  return {
    frames: samples.length,
    speechSeconds,
    closures: {
      total: closures.length,
      sealed: closures.filter((e) => e.ok).length,
      meanPeak: mean(peaks(closures)),
      minPeak: closures.length ? Math.min(...peaks(closures)) : 0,
      events: closures,
    },
    labiodentals: { total: labio.length, reached: labio.filter((e) => e.ok).length, meanPeak: mean(peaks(labio)), events: labio },
    pauses: {
      total: pauses.length,
      closed: pauses.filter((p) => p.ok).length,
      worstResidual: pauses.length ? Math.max(...pauses.map((p) => p.residual)) : 0,
      events: pauses,
    },
    jitter: {
      reversalsPerSecond: speechSeconds > 0 ? reversals / speechSeconds : 0,
      microReversalsPerSecond: speechSeconds > 0 ? micro / speechSeconds : 0,
      p95Step: steps.length ? steps[Math.min(steps.length - 1, Math.floor(steps.length * 0.95))]! : 0,
      accelRms: accelN ? Math.sqrt(accel / accelN) : 0,
    },
    sync: { lagMs, correlation },
  };
}
