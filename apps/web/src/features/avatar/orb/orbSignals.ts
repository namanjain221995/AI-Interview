/**
 * OrbDriver: turns the avatar engine's frames into the parameters of the
 * interviewer presence orb. Pure logic (no three.js, no DOM), driven by the
 * frame clock, so the 3D orb, the 2D fallback and the tests share it.
 *
 *   voice     what the candidate hears: the playback analyser's raw loudness
 *             (read after the duck gain, at the audible time) shaped by the
 *             viseme timeline on the same clock (open vowels push bigger
 *             pulses, lip closures dip); attack ~25 ms, release ~140 ms inside
 *             speech and ~65 ms once the audio has gone quiet, so syllables
 *             show and pauses settle within ~200 ms. With a silent analyser
 *             (browser speech fallback) the visemes alone drive it
 *   listen    the candidate's live microphone level while they speak
 *   states    each AvatarState has a look (scale, flow, swirl, hue lean,
 *             brightness, glow, surface motion); looks are eased, never cut
 *   accents   stressed syllables emit ripple rings; nods bounce; questions
 *             lift at the end; closing glows warm then rests
 *   comfort   reduced motion keeps only brightness and glow changes; paused
 *             or disconnected desaturates and breathes slowly
 */
import type { AvatarState } from "@/lib/realtime/protocol";
import { clamp, clamp01, damp } from "../core/math";
import type { AvatarFrame } from "../engine";

export interface OrbRipple {
  /** Seconds since emission. */
  age: number;
  /** 0..1. */
  strength: number;
}

export interface OrbSignals {
  /** Animation clock for the surface noise (s). */
  time: number;
  /** Phase of the inner colour flow (advances with the flow speed). */
  flowPhase: number;
  /** Overall sphere scale (1 = rest). */
  scale: number;
  /** Vertical offset in radii (+ up): question lift, nod bounce. */
  lift: number;
  /** Interviewer voice envelope 0..1. */
  voice: number;
  /** Candidate microphone envelope 0..1. */
  listen: number;
  /** Combined activity 0..1 (voice or listening). */
  energy: number;
  /** Surface displacement amplitude (radii). */
  displace: number;
  /** Fine surface ripple amplitude (listening), radii. */
  ripple: number;
  /** Inner swirl 0..1. */
  swirl: number;
  /** Hue lean: -1 cool .. +1 warm. */
  warmth: number;
  /** Lean toward the thinking accent 0..1. */
  violet: number;
  /** Colour sweep phase 0..1 (transitions). */
  sweep: number;
  brightness: number;
  saturation: number;
  /** Halo intensity. */
  glow: number;
  /** Thinking shimmer 0..1. */
  shimmer: number;
  /** Active ripple rings (newest last). */
  ripples: OrbRipple[];
  /** True when motion is reduced (renderers drop displacement and rings). */
  reduced: boolean;
}

interface Look {
  scale: number;
  flow: number;
  swirl: number;
  warmth: number;
  violet: number;
  brightness: number;
  saturation: number;
  glow: number;
  displace: number;
  shimmer: number;
}

const SPEAK: Look = { scale: 1.0, flow: 0.62, swirl: 0.22, warmth: 0.38, violet: 0, brightness: 1.0, saturation: 1.0, glow: 0.6, displace: 0.012, shimmer: 0 };

/** The orb's look per interviewer state (before voice / listening modulation). */
export const ORB_LOOKS: Record<AvatarState, Look> = {
  IDLE: { scale: 1.0, flow: 0.32, swirl: 0.12, warmth: 0.0, violet: 0, brightness: 0.95, saturation: 0.94, glow: 0.5, displace: 0.012, shimmer: 0 },
  LISTENING: { scale: 0.972, flow: 0.42, swirl: 0.16, warmth: -0.45, violet: 0, brightness: 1.0, saturation: 0.97, glow: 0.56, displace: 0.01, shimmer: 0 },
  ACKNOWLEDGING: { scale: 1.0, flow: 0.46, swirl: 0.16, warmth: 0.48, violet: 0, brightness: 1.07, saturation: 1.0, glow: 0.64, displace: 0.014, shimmer: 0 },
  THINKING: { scale: 0.99, flow: 1.6, swirl: 0.9, warmth: -0.1, violet: 0.8, brightness: 0.98, saturation: 1.0, glow: 0.58, displace: 0.016, shimmer: 0.6 },
  SPEAKING: SPEAK,
  QUESTIONING: { ...SPEAK, warmth: 0.26, brightness: 1.03 },
  ENCOURAGING: { ...SPEAK, warmth: 0.56 },
  TRANSITION: { scale: 1.0, flow: 0.72, swirl: 0.36, warmth: 0.0, violet: 0.25, brightness: 0.97, saturation: 0.95, glow: 0.52, displace: 0.012, shimmer: 0.1 },
  INTERRUPTED: { scale: 0.94, flow: 0.38, swirl: 0.1, warmth: -0.22, violet: 0, brightness: 0.76, saturation: 0.86, glow: 0.38, displace: 0.006, shimmer: 0 },
  CLOSING: { scale: 1.0, flow: 0.3, swirl: 0.1, warmth: 0.72, violet: 0, brightness: 1.08, saturation: 0.96, glow: 0.72, displace: 0.01, shimmer: 0 },
};

const SPEAKING_STATES: ReadonlySet<AvatarState> = new Set(["SPEAKING", "QUESTIONING", "ENCOURAGING", "CLOSING"]);
const NODS = new Set(["nod", "nod_small", "backchannel_nod", "positive_nod"]);
const EMPHASIS = new Set(["small_emphasis_l", "small_emphasis_r", "one_hand_explain_l", "one_hand_explain_r", "two_hand_explain_a", "two_hand_explain_b"]);
const CURIOUS_GESTURES = new Set(["question_gesture_a", "question_gesture_b"]);

/** Voice envelope rates (1/s): attack ~25 ms, release ~140 ms; ~65 ms once the audio is quiet. */
export const VOICE_ATTACK = 40;
export const VOICE_RELEASE = 7;
export const PAUSE_RELEASE = 15;
/** Normalised loudness below this is silence. */
export const VOICE_SILENCE = 0.04;
/** The audio counts as quiet after this long below VOICE_SILENCE (s). */
const QUIET_AFTER = 0.05;
/** Speaking this long with no loudness at all means the voice is not on the analyser (speech fallback). */
const SILENT_ANALYSER_AFTER = 0.8;
/** Candidate envelope rates (1/s). */
export const LISTEN_ATTACK = 18;
export const LISTEN_RELEASE = 4.5;
/** Ripple rings live this long (s). */
export const RIPPLE_LIFE = 1.6;
const MAX_RIPPLES = 4;
/** Breathing: ~+-2 % scale over ~4 s (slower when paused). */
export const BREATH_PERIOD = 4.2;
export const BREATH_DEPTH = 0.02;

export interface OrbDriverOptions {
  reducedMotion?: boolean;
}

/**
 * Loudness + articulation target for the voice envelope. `audio`: the voice is
 * on the playback analyser, so its loudness (including the barge-in duck)
 * leads and the visemes shape it (open vowels push bigger pulses, p/b/m
 * closures dip even when the nasal is loud). Without audio (no analyser, or
 * browser speech that bypasses it) the viseme timeline alone drives it.
 */
export function voiceTarget(frame: Pick<AvatarFrame, "speech" | "mouthOpen" | "lipsPressed">, audio: boolean): number {
  const level = frame.speech.level;
  const open = frame.mouthOpen;
  const closed = frame.lipsPressed;
  if (audio) {
    if (level < VOICE_SILENCE) return 0;
    return clamp01(level * (0.72 + 0.5 * open) - 0.4 * closed);
  }
  return frame.speech.speaking ? clamp01(0.2 + 0.7 * open - 0.45 * closed) : 0;
}

export class OrbDriver {
  private reduced: boolean;
  private readonly s: OrbSignals;
  private look: Look = { ...ORB_LOOKS.IDLE };
  private flow = ORB_LOOKS.IDLE.flow;
  private state: AvatarState = "IDLE";
  private stateSince = 0;
  private slowVoice = 0;
  private prevVoice = 0;
  private lastSound = -Infinity;
  private speakingSince = Infinity;
  private lastRipple = -Infinity;
  private emphasisUntil = -Infinity;
  private seenHead = -1;
  private seenGesture = -1;
  private bounceAt = -Infinity;
  private liftUntil = -Infinity;
  private liftDuration = 1.8;
  private liftAmount = 0;
  private wasSpeaking = false;
  private sweepStart = -Infinity;
  private dim = 0;

  constructor(options: OrbDriverOptions = {}) {
    this.reduced = options.reducedMotion ?? false;
    this.s = {
      time: 0,
      flowPhase: 0,
      scale: 1,
      lift: 0,
      voice: 0,
      listen: 0,
      energy: 0,
      displace: ORB_LOOKS.IDLE.displace,
      ripple: 0,
      swirl: ORB_LOOKS.IDLE.swirl,
      warmth: 0,
      violet: 0,
      sweep: 0,
      brightness: ORB_LOOKS.IDLE.brightness,
      saturation: ORB_LOOKS.IDLE.saturation,
      glow: ORB_LOOKS.IDLE.glow,
      shimmer: 0,
      ripples: [],
      reduced: this.reduced,
    };
  }

  setReducedMotion(value: boolean): void {
    this.reduced = value;
    this.s.reduced = value;
    if (value) this.s.ripples.length = 0;
  }

  /**
   * Advance one frame. `analyser`: the playback analyser is attached (the
   * amplitude is real loudness). `dimmed`: the session is paused or offline.
   */
  update(frame: AvatarFrame, analyser: boolean, dimmed = false): OrbSignals {
    const s = this.s;
    const dt = clamp(frame.dt, 0, 0.1);
    const now = frame.now;
    const b = frame.behavior;
    if (b.state !== this.state) {
      if (b.state === "TRANSITION") this.sweepStart = now;
      this.state = b.state;
      this.stateSince = now;
    }

    // ---- envelopes
    const level = frame.speech.level;
    if (level >= VOICE_SILENCE) this.lastSound = now;
    if (frame.speech.speaking) this.speakingSince = Math.min(this.speakingSince, now);
    else this.speakingSince = Infinity;
    // A voice that never reaches the analyser (speech fallback) is driven by the visemes.
    const silentAnalyser = now - this.speakingSince > SILENT_ANALYSER_AFTER && now - this.lastSound > SILENT_ANALYSER_AFTER;
    const audio = analyser && !silentAnalyser;
    const vt = voiceTarget(frame, audio);
    const quiet = audio ? now - this.lastSound > QUIET_AFTER : vt < 0.02;
    s.voice = damp(s.voice, vt, vt > s.voice ? VOICE_ATTACK : quiet ? PAUSE_RELEASE : VOICE_RELEASE, dt);
    const lt = frame.listen?.speaking ? clamp01((frame.listen.level ?? 0) * 1.6) : 0;
    s.listen = damp(s.listen, lt, lt > s.listen ? LISTEN_ATTACK : LISTEN_RELEASE, dt);
    s.energy = Math.max(s.voice, s.listen * 0.8);

    // ---- accents from the behaviour (nods, emphasis, curiosity, goodbye)
    const head = b.headGesture;
    if (head && head.serial !== this.seenHead) {
      this.seenHead = head.serial;
      if (NODS.has(head.id)) this.bounceAt = now;
      if (head.id === "head_tilt") this.lift(now, 0.022, 1.6);
    }
    const g = b.gesture;
    if (g && g.serial !== this.seenGesture) {
      this.seenGesture = g.serial;
      if (EMPHASIS.has(g.id)) this.emphasisUntil = now + 3;
      if (CURIOUS_GESTURES.has(g.id)) this.lift(now, 0.025, 1.8);
    }
    // Questions lift and brighten as they end.
    const speaking = frame.speech.speaking || s.voice > 0.05;
    if (this.wasSpeaking && !speaking && b.state === "QUESTIONING") this.lift(now, 0.04, 1.8);
    this.wasSpeaking = speaking;

    // ---- the state's look, eased (interruptions react fast)
    const target = this.targetLook(frame, now);
    const rate = b.state === "INTERRUPTED" ? 9 : b.state === "CLOSING" ? 1.6 : 3.4;
    const L = this.look;
    for (const key of Object.keys(L) as Array<keyof Look>) L[key] = damp(L[key], target[key], rate, dt);
    this.dim = damp(this.dim, dimmed ? 1 : 0, 2.2, dt);

    // ---- voice / listening modulation
    const v = s.voice;
    const l = s.listen;
    const r = this.reduced;
    const breathPeriod = BREATH_PERIOD * (1 + 0.7 * this.dim);
    s.time += dt * (1 - 0.6 * this.dim);
    this.flow = damp(this.flow, L.flow * (1 + 0.8 * v + 0.3 * l), 4, dt);
    s.flowPhase += dt * this.flow * (1 - 0.6 * this.dim);
    const breath = Math.sin((2 * Math.PI * s.time) / breathPeriod) * BREATH_DEPTH * (speaking ? 0.5 : 1);

    // Nod: a soft dip and a small rebound. Lift: a slow rise and settle.
    const tb = now - this.bounceAt;
    let bounce = 0;
    if (tb >= 0 && tb < 0.45) bounce = -0.04 * Math.sin((Math.PI * tb) / 0.45);
    else if (tb >= 0.45 && tb < 0.7) bounce = 0.012 * Math.sin((Math.PI * (tb - 0.45)) / 0.25);
    const liftK = now < this.liftUntil ? Math.sin(Math.PI * clamp01(1 - (this.liftUntil - now) / this.liftDuration)) : 0;
    const lift = this.liftAmount * liftK;

    if (r) {
      s.scale = 1;
      s.lift = 0;
      s.displace = 0;
      s.ripple = 0;
    } else {
      s.scale = L.scale * (1 + breath + 0.075 * v + 0.014 * l);
      s.lift = bounce + lift;
      s.displace = L.displace * (1 - 0.5 * this.dim) + 0.022 * v + 0.01 * l;
      s.ripple = 0.012 * l;
    }
    s.swirl = L.swirl;
    s.warmth = L.warmth;
    s.violet = L.violet;
    s.shimmer = L.shimmer;
    const sweepT = (now - this.sweepStart) / 1.4;
    s.sweep = sweepT >= 0 && sweepT <= 1 ? 0.5 - 0.5 * Math.cos(Math.PI * 2 * sweepT) : 0;
    // Reduced motion: breathing becomes a gentle brightness change.
    const calmPulse = r ? 0.04 * Math.sin((2 * Math.PI * s.time) / breathPeriod) : 0;
    s.brightness = (L.brightness + 0.14 * v + 0.06 * l + calmPulse) * (1 - 0.28 * this.dim) + 0.05 * liftK;
    s.saturation = L.saturation * (1 - 0.7 * this.dim);
    s.glow = (L.glow + 0.45 * v + 0.28 * l) * (1 - 0.35 * this.dim);

    // ---- stressed syllables -> ripple rings
    this.slowVoice = damp(this.slowVoice, v, 2.5, dt);
    for (const ring of s.ripples) ring.age += dt;
    while (s.ripples.length && s.ripples[0]!.age > RIPPLE_LIFE) s.ripples.shift();
    if (!r && v > 0.4 && v - this.slowVoice > 0.14 && v > this.prevVoice && now - this.lastRipple > 0.45) {
      const emphasis = now < this.emphasisUntil ? 1.45 : 1;
      if (emphasis > 1) this.emphasisUntil = -Infinity;
      s.ripples.push({ age: 0, strength: clamp01((0.35 + 0.9 * (v - this.slowVoice)) * emphasis) });
      if (s.ripples.length > MAX_RIPPLES) s.ripples.shift();
      this.lastRipple = now;
    }
    this.prevVoice = v;
    s.reduced = r;
    return s;
  }

  private lift(now: number, amount: number, duration: number): void {
    this.liftAmount = amount;
    this.liftDuration = duration;
    this.liftUntil = now + duration;
  }

  private targetLook(frame: AvatarFrame, now: number): Look {
    const b = frame.behavior;
    const base = ORB_LOOKS[b.state] ?? ORB_LOOKS.IDLE;
    const t: Look = { ...base };
    // Expressions: subtle variations only.
    switch (b.expression) {
      case "warm":
      case "pleased":
      case "encouraging":
        t.warmth += 0.18;
        t.displace *= 0.9;
        break;
      case "curious":
        t.brightness += 0.05;
        break;
      case "serious":
      case "thoughtful":
        t.warmth -= 0.18;
        t.flow *= 0.8;
        break;
      default:
        break;
    }
    if (b.gesture?.id === "goodbye_wave") {
      t.warmth = Math.max(t.warmth, 0.7);
      t.glow = Math.max(t.glow, 0.72);
    }
    // Closing: warm glow, then a slow fade to a gentle rest.
    if (b.state === "CLOSING") {
      const k = clamp01((now - this.stateSince - 2) / 6);
      t.brightness -= 0.24 * k;
      t.glow -= 0.3 * k;
      t.flow *= 1 - 0.5 * k;
    }
    t.warmth = clamp(t.warmth, -1, 1);
    return t;
  }

  /** Current signals without advancing (for tests / inspection). */
  get signals(): Readonly<OrbSignals> {
    return this.s;
  }

  /** True when the frame's state is a speaking state. */
  static isSpeakingState(state: AvatarState): boolean {
    return SPEAKING_STATES.has(state);
  }
}
