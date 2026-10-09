/**
 * Behaviour controller: deterministic mapping from semantic behaviour
 * (state / expression / gesture / intensity / tone) to animation choices.
 *
 * Pure logic driven by explicit timestamps (seconds), so it is fully testable
 * with a fake clock. Guarantees: minimum dwell per state (requests arriving
 * earlier are deferred, latest wins), seeded variant selection that never
 * repeats the previous variant, intensity scaling, per-transition crossfade
 * durations, INTERRUPTED -> LISTENING recovery and occasional backchannel nods
 * while the candidate talks. Micro-behaviours (blink, breathing, saccades) are
 * not decided here and never come from the server.
 */
import type {
  AvatarExpression,
  AvatarGesture,
  AvatarState,
  GestureIntensity,
  SpeechTone,
} from "@/lib/realtime/protocol";
import type { AvatarBehavior } from "../types";
import {
  ARM_GESTURE_DURATION,
  HEAD_GESTURE_DURATION,
  type ArmGestureId,
  type BasePoseId,
  type HeadGestureId,
} from "../animation/catalog";
import { clamp, deriveSeed, mulberry32, pickAvoidingRepeat, randRange, type Rng } from "../core/math";

export type LookMode = "camera" | "thinking";

export interface BaseChoice {
  pose: BasePoseId;
  serial: number;
  /** Crossfade duration (s) into this pose. */
  fade: number;
  since: number;
}

export interface GestureChoice<T extends string> {
  id: T;
  serial: number;
  /** 0..1 additive weight. */
  intensity: number;
  /** Playback rate. */
  speed: number;
  startedAt: number;
  duration: number;
}

export interface BehaviorSnapshot {
  state: AvatarState;
  stateSince: number;
  base: BaseChoice;
  gesture: GestureChoice<ArmGestureId> | null;
  headGesture: GestureChoice<HeadGestureId> | null;
  /** Incremented when running gestures must fade out (barge-in). */
  cancelSerial: number;
  expression: AvatarExpression;
  expressionIntensity: number;
  tone: SpeechTone;
  look: LookMode;
  /** Extra forward lean (0..1) while recovering from an interruption. */
  lean: number;
  pending: AvatarState | null;
}

/** Minimum time (s) a state is held before another request may replace it. */
export const MIN_DWELL: Record<AvatarState, number> = {
  IDLE: 0.3,
  LISTENING: 0.45,
  ACKNOWLEDGING: 0.75,
  THINKING: 0.6,
  SPEAKING: 0.5,
  QUESTIONING: 0.5,
  ENCOURAGING: 0.8,
  TRANSITION: 0.5,
  INTERRUPTED: 0.35,
  CLOSING: 1.5,
};

/** INTERRUPTED automatically recovers to LISTENING after this long (s). */
export const INTERRUPT_RECOVERY = 0.9;

const STATE_POSES: Record<AvatarState, readonly BasePoseId[]> = {
  IDLE: ["idle_neutral", "idle_shift"],
  LISTENING: ["listening_attentive", "listening_tilt"],
  ACKNOWLEDGING: ["listening_attentive"],
  THINKING: ["thinking"],
  SPEAKING: ["speaking_neutral", "speaking_engaged"],
  QUESTIONING: ["speaking_engaged"],
  ENCOURAGING: ["listening_attentive"],
  TRANSITION: ["idle_neutral"],
  INTERRUPTED: ["interrupted_lean"],
  CLOSING: ["closing"],
};

const DEFAULT_EXPRESSION: Record<AvatarState, AvatarExpression> = {
  IDLE: "neutral",
  LISTENING: "neutral",
  ACKNOWLEDGING: "warm",
  THINKING: "thoughtful",
  SPEAKING: "neutral",
  QUESTIONING: "curious",
  ENCOURAGING: "encouraging",
  TRANSITION: "neutral",
  INTERRUPTED: "neutral",
  CLOSING: "warm",
};

const DEFAULT_EXPRESSION_SET: Record<AvatarExpression, true> = {
  neutral: true,
  warm: true,
  curious: true,
  thoughtful: true,
  encouraging: true,
  serious: true,
  pleased: true,
};

const TONE_EXPRESSION_GAIN: Record<SpeechTone, number> = {
  professional: 0.85,
  neutral: 0.9,
  friendly: 1.05,
  supportive: 1.0,
};

const TONE_GESTURE_GAIN: Record<SpeechTone, number> = {
  professional: 0.9,
  neutral: 1.0,
  friendly: 1.08,
  supportive: 1.0,
};

export const INTENSITY_WEIGHT: Record<GestureIntensity, number> = {
  low: 0.5,
  medium: 0.75,
  high: 1.0,
};

const ARM_VARIANTS: Partial<Record<AvatarGesture, readonly ArmGestureId[]>> = {
  small_open_palm: ["small_open_palm_r", "small_open_palm_l"],
  one_hand_explain: ["one_hand_explain_r", "one_hand_explain_l"],
  two_hand_explain: ["two_hand_explain_a", "two_hand_explain_b"],
  small_emphasis: ["small_emphasis_r", "small_emphasis_l"],
  question_gesture: ["question_gesture_a", "question_gesture_b"],
  goodbye: ["goodbye_wave"],
};

const HEAD_FOR_GESTURE: Partial<Record<AvatarGesture, HeadGestureId>> = {
  nod: "nod",
  head_tilt: "head_tilt",
};

const SPEAKING_STATES: ReadonlySet<AvatarState> = new Set(["SPEAKING", "QUESTIONING", "ENCOURAGING", "CLOSING"]);

/** Crossfade duration (s) for a transition. */
export function transitionDuration(from: AvatarState, to: AvatarState): number {
  if (to === "INTERRUPTED") return 0.18;
  if (from === "INTERRUPTED" && to === "LISTENING") return 0.6;
  if (to === "THINKING") return 0.7;
  if (to === "CLOSING") return 0.8;
  if (to === "SPEAKING" || to === "QUESTIONING") return 0.45;
  if (from === to) return 1.2; // variant rotation within a state (weight shift)
  return 0.55;
}

export interface BehaviorOptions {
  seed: number;
  reducedMotion?: boolean;
  now?: number;
}

export class BehaviorController {
  private readonly variantRng: Rng;
  private readonly gestureRng: Rng;
  private readonly timingRng: Rng;
  private reducedMotion: boolean;

  private state: AvatarState = "IDLE";
  private stateSince: number;
  private base: BaseChoice;
  private gesture: GestureChoice<ArmGestureId> | null = null;
  private headGesture: GestureChoice<HeadGestureId> | null = null;
  private cancelSerial = 0;
  private serial = 0;
  private expression: AvatarExpression = "neutral";
  private expressionExplicit = false;
  private tone: SpeechTone = "professional";
  private leanReleasedAt = -Infinity;

  private pending: { behavior: AvatarBehavior; at: number } | null = null;
  private pendingGesture: { gesture: AvatarGesture; intensity: GestureIntensity | undefined } | null = null;
  private lastPose = new Map<AvatarState, BasePoseId>();
  private lastArmVariant = new Map<AvatarGesture, ArmGestureId>();
  private lastGestureRequest: { gesture: AvatarGesture; at: number } | null = null;
  private nextVariantRotation: number;

  private speechActive = false;
  private speechEndedAt = -Infinity;
  private nextBeatAt = Infinity;
  private candidateSpeaking = false;
  private candidateQuietSince: number | null = null;
  private lastBackchannel = -Infinity;
  private backchannelArmed = false;
  private lastNow = 0;

  constructor(options: BehaviorOptions) {
    const now = options.now ?? 0;
    this.variantRng = mulberry32(deriveSeed(options.seed, "behavior.variant"));
    this.gestureRng = mulberry32(deriveSeed(options.seed, "behavior.gesture"));
    this.timingRng = mulberry32(deriveSeed(options.seed, "behavior.timing"));
    this.reducedMotion = options.reducedMotion ?? false;
    this.stateSince = now;
    this.base = { pose: "idle_neutral", serial: this.nextSerial(), fade: 0, since: now };
    this.lastPose.set("IDLE", "idle_neutral");
    this.nextVariantRotation = now + this.rotationDelay("IDLE");
  }

  setReducedMotion(value: boolean): void {
    this.reducedMotion = value;
  }

  getState(): AvatarState {
    return this.state;
  }

  setState(state: AvatarState, now: number): void {
    this.request({ state }, now);
  }

  applyBehavior(behavior: AvatarBehavior, now: number): void {
    this.request(behavior, now);
  }

  /** Barge-in: immediate INTERRUPTED (lean in, gestures fade), then LISTENING. */
  interrupt(now: number): void {
    this.pending = null;
    this.pendingGesture = null;
    this.enter({ state: "INTERRUPTED" }, now);
    this.pending = { behavior: { state: "LISTENING" }, at: now };
  }

  /** Lip-sync activity (interviewer audio actually playing). */
  setSpeechActive(active: boolean, now: number): void {
    if (active === this.speechActive) return;
    this.speechActive = active;
    if (active) {
      this.nextBeatAt = now + randRange(this.timingRng, 2.6, 4.2);
      if (!SPEAKING_STATES.has(this.state) && this.state !== "INTERRUPTED") this.request({ state: "SPEAKING" }, now);
    } else {
      this.speechEndedAt = now;
      this.nextBeatAt = Infinity;
    }
  }

  /** Candidate talking: attentive listening, occasional backchannel nods at pauses. */
  setCandidateSpeaking(speaking: boolean, level: number | undefined, now: number): void {
    const voiced = speaking && (level === undefined || level >= 0.06);
    if (speaking && !this.candidateSpeaking) {
      this.backchannelArmed = false;
      if (this.state === "IDLE" || this.state === "TRANSITION") this.request({ state: "LISTENING" }, now);
    }
    this.candidateSpeaking = speaking;
    if (voiced) {
      this.candidateQuietSince = null;
      this.backchannelArmed = true;
    } else if (this.candidateQuietSince === null) {
      this.candidateQuietSince = now;
    }
  }

  update(now: number): BehaviorSnapshot {
    this.lastNow = now;
    // Deferred state requests (minimum dwell), INTERRUPTED recovery included.
    if (this.pending) {
      const dwell = this.state === "INTERRUPTED" ? INTERRUPT_RECOVERY : MIN_DWELL[this.state];
      if (now - this.stateSince >= dwell) {
        const { behavior } = this.pending;
        this.pending = null;
        this.enter(behavior, now);
      }
    }

    // Deferred gesture once the running one is mostly done.
    if (this.pendingGesture && this.gestureProgress(now) >= 0.7) {
      const { gesture, intensity } = this.pendingGesture;
      this.pendingGesture = null;
      this.triggerGesture(gesture, intensity, now);
    }

    // Occasional weight shift / variant rotation while idle or listening.
    if ((this.state === "IDLE" || this.state === "LISTENING") && !this.pending && now >= this.nextVariantRotation) {
      const options = STATE_POSES[this.state];
      if (options.length > 1) {
        const pose = pickAvoidingRepeat(this.variantRng, options, this.base.pose);
        this.setBase(pose, transitionDuration(this.state, this.state), now);
        this.lastPose.set(this.state, pose);
      }
      this.nextVariantRotation = now + this.rotationDelay(this.state);
    }

    // Speaking beats: small, low-intensity gestures while audio plays.
    if (this.speechActive && !this.reducedMotion && (this.state === "SPEAKING" || this.state === "QUESTIONING")) {
      if (now >= this.nextBeatAt) {
        const busy = this.gestureProgress(now) < 1;
        const chance = this.tone === "friendly" ? 0.75 : this.tone === "professional" ? 0.5 : 0.6;
        if (!busy && this.gestureRng() < chance) {
          const beat: AvatarGesture = this.gestureRng() < 0.55 ? "small_emphasis" : "small_open_palm";
          this.triggerGesture(beat, "low", now, 0.7);
        }
        this.nextBeatAt = now + randRange(this.timingRng, 3.5, 7.0);
      }
    }

    // Return to listening once the interviewer has stopped talking (or never
    // started: text-only turns), unless the server already moved on.
    if (!this.speechActive && !this.pending && (this.state === "SPEAKING" || this.state === "QUESTIONING")) {
      const spokeInState = this.speechEndedAt >= this.stateSince;
      const silentFor = spokeInState ? now - this.speechEndedAt : now - this.stateSince;
      if (silentFor >= (spokeInState ? 1.2 : 4.0)) this.request({ state: "LISTENING" }, now);
    }

    // Backchannel nods at candidate pauses.
    if (
      this.state === "LISTENING" &&
      this.backchannelArmed &&
      this.candidateQuietSince !== null &&
      now - this.candidateQuietSince >= 0.35
    ) {
      this.backchannelArmed = false;
      if (now - this.lastBackchannel >= 4.5 && this.headGestureProgress(now) >= 1 && this.gestureRng() < 0.5) {
        this.lastBackchannel = now;
        this.startHead("backchannel_nod", this.reducedMotion ? 0.45 : 0.7, now);
      }
    }

    return this.snapshot();
  }

  snapshot(): BehaviorSnapshot {
    return {
      state: this.state,
      stateSince: this.stateSince,
      base: this.base,
      gesture: this.gesture,
      headGesture: this.headGesture,
      cancelSerial: this.cancelSerial,
      expression: this.expression,
      expressionIntensity: TONE_EXPRESSION_GAIN[this.tone],
      tone: this.tone,
      look: this.state === "THINKING" ? "thinking" : "camera",
      lean: this.leanAt(this.lastNow),
      pending: this.pending?.behavior.state ?? null,
    };
  }

  // ------------------------------------------------------------ internals

  /** Lean-in is full while INTERRUPTED and eases out over 0.8 s afterwards. */
  private leanAt(now: number): number {
    if (this.state === "INTERRUPTED") return 1;
    const t = (now - this.leanReleasedAt) / 0.8;
    return t >= 1 || t < 0 ? 0 : 1 - t * t * (3 - 2 * t);
  }

  private nextSerial(): number {
    this.serial += 1;
    return this.serial;
  }

  private rotationDelay(state: AvatarState): number {
    return state === "IDLE" ? randRange(this.timingRng, 14, 26) : randRange(this.timingRng, 10, 18);
  }

  private request(raw: AvatarBehavior, now: number): void {
    // Defence in depth: unknown enum values from the wire are dropped, not propagated as NaN gains.
    const behavior: AvatarBehavior = {
      state: raw.state,
      ...(raw.expression && Object.hasOwn(DEFAULT_EXPRESSION_SET, raw.expression) ? { expression: raw.expression } : {}),
      ...(raw.gesture ? { gesture: raw.gesture } : {}),
      ...(raw.gesture_intensity && Object.hasOwn(INTENSITY_WEIGHT, raw.gesture_intensity) ? { gesture_intensity: raw.gesture_intensity } : {}),
    };
    if (raw.tone && Object.hasOwn(TONE_EXPRESSION_GAIN, raw.tone)) this.tone = raw.tone;
    if (behavior.state === "INTERRUPTED") {
      this.interrupt(now);
      return;
    }
    if (behavior.state === this.state) {
      // Same state: refresh expression / gesture without re-entering; a newer
      // request for the current state also supersedes a deferred one.
      this.pending = null;
      this.applyDetails(behavior, now, false);
      return;
    }
    const dwell = this.state === "INTERRUPTED" ? INTERRUPT_RECOVERY : MIN_DWELL[this.state];
    if (now - this.stateSince >= dwell) {
      this.pending = null;
      this.enter(behavior, now);
    } else {
      this.pending = { behavior, at: now }; // latest request wins
    }
  }

  private enter(behavior: AvatarBehavior, now: number): void {
    const from = this.state;
    const to = behavior.state;
    if (from === "INTERRUPTED" && to !== "INTERRUPTED") this.leanReleasedAt = now;
    this.state = to;
    this.stateSince = now;
    this.lastNow = now;

    const options = STATE_POSES[to];
    const pose = pickAvoidingRepeat(this.variantRng, options, this.lastPose.get(to));
    this.lastPose.set(to, pose);
    this.setBase(pose, transitionDuration(from, to), now);
    this.nextVariantRotation = now + this.rotationDelay(to);

    if (to === "INTERRUPTED") {
      this.cancelSerial += 1;
      this.gesture = null;
      this.pendingGesture = null;
    }
    this.expressionExplicit = false;
    this.applyDetails(behavior, now, true);
  }

  private applyDetails(behavior: AvatarBehavior, now: number, entering: boolean): void {
    if (behavior.expression) {
      this.expression = behavior.expression;
      this.expressionExplicit = true;
    } else if (entering || !this.expressionExplicit) {
      this.expression = DEFAULT_EXPRESSION[this.state];
    }

    const gesture = behavior.gesture;
    if (gesture && gesture !== "none") {
      this.triggerGesture(gesture, behavior.gesture_intensity, now);
    } else if (entering) {
      // State-implied head motion when the server did not ask for a gesture.
      if (this.state === "ACKNOWLEDGING") this.startHead("nod_small", this.headIntensity(behavior.gesture_intensity), now);
      else if (this.state === "ENCOURAGING" || this.state === "CLOSING")
        this.startHead("positive_nod", this.headIntensity(behavior.gesture_intensity), now);
      else if (this.state === "QUESTIONING") this.startHead("head_tilt", 0.55 * this.headIntensity(behavior.gesture_intensity), now);
    }
  }

  private setBase(pose: BasePoseId, fade: number, now: number): void {
    if (pose === this.base.pose) return; // same clip keeps playing, no crossfade to itself
    this.base = { pose, serial: this.nextSerial(), fade, since: now };
  }

  private intensityFor(intensity: GestureIntensity | undefined): number {
    const base = INTENSITY_WEIGHT[intensity ?? "medium"];
    const scaled = base * TONE_GESTURE_GAIN[this.tone] * (this.reducedMotion ? 0.6 : 1);
    return clamp(scaled, 0.25, 1);
  }

  private headIntensity(intensity: GestureIntensity | undefined): number {
    return clamp(this.intensityFor(intensity) + 0.1, 0.3, 1);
  }

  private gestureProgress(now: number): number {
    if (!this.gesture) return 1;
    return (now - this.gesture.startedAt) / this.gesture.duration;
  }

  private headGestureProgress(now: number): number {
    if (!this.headGesture) return 1;
    return (now - this.headGesture.startedAt) / this.headGesture.duration;
  }

  private triggerGesture(gesture: AvatarGesture, intensity: GestureIntensity | undefined, now: number, scale = 1): void {
    const head = HEAD_FOR_GESTURE[gesture];
    if (head) {
      this.startHead(head, this.headIntensity(intensity) * scale, now);
      return;
    }
    const variants = ARM_VARIANTS[gesture];
    if (!variants) return;
    // Drop exact repeats of the same request (server retries, duplicate meta).
    if (this.lastGestureRequest && this.lastGestureRequest.gesture === gesture && now - this.lastGestureRequest.at < 1.5) return;
    if (this.gestureProgress(now) < 0.6) {
      this.pendingGesture = { gesture, intensity };
      return;
    }
    this.lastGestureRequest = { gesture, at: now };
    const id = pickAvoidingRepeat(this.gestureRng, variants, this.lastArmVariant.get(gesture));
    this.lastArmVariant.set(gesture, id);
    const weight = clamp(this.intensityFor(intensity) * scale, 0.2, 1);
    // Calmer gestures play a touch slower; energetic ones a touch faster.
    const speed = intensity === "high" ? 1.05 : intensity === "low" ? 0.92 : 1;
    this.gesture = {
      id,
      serial: this.nextSerial(),
      intensity: weight,
      speed,
      startedAt: now,
      duration: ARM_GESTURE_DURATION[id] / speed,
    };
  }

  private startHead(id: HeadGestureId, intensity: number, now: number): void {
    if (this.headGesture && this.headGesture.id === id && now - this.headGesture.startedAt < this.headGesture.duration * 0.8) return;
    this.headGesture = {
      id,
      serial: this.nextSerial(),
      intensity: clamp(intensity, 0.2, 1),
      speed: 1,
      startedAt: now,
      duration: HEAD_GESTURE_DURATION[id],
    };
  }
}
