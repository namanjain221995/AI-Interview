/**
 * AvatarEngine: renderer-independent heart of the interviewer. Combines the
 * behaviour controller, lip-sync, amplitude envelope, expressions and
 * micro-behaviours into one AvatarFrame per rendered frame. Both the 3D
 * animator and the 2D portrait consume the same frames.
 */
import { BehaviorController, type BehaviorSnapshot } from "./behavior/behaviorController";
import { clamp, clamp01, deriveSeed, mulberry32, type Rng } from "./core/math";
import { ExpressionMixer } from "./face/expressions";
import { AMPLITUDE_LEAD, AmplitudeFollower, LEVEL_LEAD } from "./lipsync/amplitude";
import { audibleClock } from "./lipsync/audioClock";
import { CLOSURE_VISEMES, LipSync, SHAPE_VISEMES, type ShapeViseme } from "./lipsync/lipSync";
import { BlinkScheduler } from "./micro/blink";
import { GazeController, type GazeOutput } from "./micro/gaze";
import { VISEME_MORPHS } from "./rig/morphs";
import type { FaceWeights } from "./rig/rig";

/**
 * Per-viseme articulation gain. Shapes at weight 1 read as over-articulated on
 * the interviewer model (aa = 1 shows the whole tongue), so open shapes are
 * scaled to conversational size; lip closures keep their full strength (frame
 * strips against real TTS audio, dev/lipSyncSim + the playground strip mode).
 */
const VISEME_GAIN: Record<ShapeViseme, number> = {
  PP: 1.0,
  FF: 0.75, // lower lip on the teeth reads clearly from ~0.5; 0.9 bared the teeth
  TH: 0.72,
  DD: 0.62,
  kk: 0.62,
  CH: 0.72,
  SS: 0.62,
  nn: 0.58,
  RR: 0.66,
  aa: 0.68,
  E: 0.66,
  I: 0.66,
  O: 0.72,
  U: 0.74,
};

export interface SpeechInfo {
  activity: number;
  /** Loudness envelope 0..1 at the audible time (0 without an analyser). */
  amplitude: number;
  /** Raw loudness 0..1 (no envelope) just ahead of the audible time; see LEVEL_LEAD. */
  level: number;
  speaking: boolean;
  /** Lip-sync clock (audible audio time once an AudioContext is attached), seconds. */
  clock: number;
}

export interface ListenInfo {
  /** The candidate is speaking (local VAD / server turn state). */
  speaking: boolean;
  /** Latest candidate microphone level 0..1 (raw; renderers smooth it). */
  level: number;
}

export interface AvatarFrame {
  /** Wall-clock seconds. */
  now: number;
  dt: number;
  behavior: BehaviorSnapshot;
  /** Candidate microphone activity (drives the presence orb's listening ripples). */
  listen: ListenInfo;
  gaze: GazeOutput;
  /** Final morph weights: ARKit names + viseme_* names. */
  face: FaceWeights;
  blink: number;
  speech: SpeechInfo;
  /** Multiplier for procedural motion (reduced motion lowers it). */
  motionScale: number;
  /** Summary for the 2D portrait. */
  mouthOpen: number;
  mouthWide: number;
  mouthRound: number;
  lipsPressed: number;
  smile: number;
  browRaise: number;
}

export interface EngineOptions {
  seed: number;
  reducedMotion?: boolean;
  wallClock?: () => number;
}

const defaultWallClock = () => (typeof performance !== "undefined" ? performance.now() : Date.now()) / 1000;

export class AvatarEngine {
  readonly behavior: BehaviorController;
  readonly lipSync: LipSync;
  readonly amplitude = new AmplitudeFollower();
  private readonly blink: BlinkScheduler;
  private readonly gaze: GazeController;
  private readonly expressions = new ExpressionMixer();
  private readonly rng: Rng;
  private readonly wallClock: () => number;
  private reducedMotion: boolean;
  private lastNow: number | null = null;
  private context: AudioContext | null = null;
  private readonly face: FaceWeights = {};
  private frame: AvatarFrame | null = null;
  private readonly listen: ListenInfo = { speaking: false, level: 0 };

  constructor(options: EngineOptions) {
    this.wallClock = options.wallClock ?? defaultWallClock;
    const now = this.wallClock();
    this.reducedMotion = options.reducedMotion ?? false;
    this.behavior = new BehaviorController({ seed: options.seed, reducedMotion: this.reducedMotion, now });
    this.lipSync = new LipSync(this.wallClock);
    this.blink = new BlinkScheduler(deriveSeed(options.seed, "blink"), now);
    this.gaze = new GazeController(deriveSeed(options.seed, "gaze"), now);
    this.rng = mulberry32(deriveSeed(options.seed, "engine"));
  }

  now(): number {
    return this.wallClock();
  }

  setReducedMotion(value: boolean): void {
    this.reducedMotion = value;
    this.behavior.setReducedMotion(value);
  }

  attachAudio(context: AudioContext, analyser: AnalyserNode | null): void {
    // Follow what the listener hears, not the scheduling clock (output latency);
    // the loudness envelope is read at the same audible time.
    this.context = context;
    this.lipSync.setClock(audibleClock(context));
    this.amplitude.attach(analyser);
  }

  /** Candidate microphone activity (levels arrive ~15 times a second while they speak). */
  setCandidateLevel(speaking: boolean, level?: number): void {
    this.listen.speaking = speaking;
    this.listen.level = speaking && typeof level === "number" && Number.isFinite(level) ? clamp01(level) : 0;
  }

  /** True once a playback analyser is attached (the amplitude is real loudness). */
  get analyserAttached(): boolean {
    return this.amplitude.attached;
  }

  /** Last computed frame (null before the first update). */
  get lastFrame(): AvatarFrame | null {
    return this.frame;
  }

  update(): AvatarFrame {
    const now = this.wallClock();
    const dt = this.lastNow === null ? 1 / 60 : clamp(now - this.lastNow, 0, 0.1);
    this.lastNow = now;

    const clock = this.lipSync.now();
    const lip = this.lipSync.sampleAt(clock);
    const amplitude = this.amplitude.sample(dt, this.context?.currentTime, clock + AMPLITUDE_LEAD, clock + LEVEL_LEAD);
    const level = this.amplitude.level;
    this.behavior.setSpeechActive(lip.speaking, now);
    const behavior = this.behavior.update(now);

    this.blink.rate = lip.speaking ? 0.85 : behavior.state === "THINKING" ? 0.9 : 1;
    const gaze = this.gaze.update(now, dt, behavior.look, lip.speaking, this.reducedMotion);
    if (gaze.bigShift && this.rng() < 0.45) this.blink.trigger(now);
    const blink = this.blink.update(now);

    const expr = this.expressions.update(behavior.expression, behavior.expressionIntensity, dt);
    const face = this.face;
    for (const key in face) face[key] = 0;
    for (const key in expr) face[key] = expr[key]!;

    // Speech: smile and lip press yield to articulation.
    const activity = lip.activity;
    const speechDamp = 1 - 0.45 * activity;
    face.mouthSmileLeft = (face.mouthSmileLeft ?? 0) * speechDamp;
    face.mouthSmileRight = (face.mouthSmileRight ?? 0) * speechDamp;
    face.mouthPressLeft = (face.mouthPressLeft ?? 0) * (1 - activity);
    face.mouthPressRight = (face.mouthPressRight ?? 0) * (1 - activity);

    // Visemes; open shapes follow loudness when an analyser is attached. Lip
    // closures keep their full gain: a p/b/m or f/v is acoustically quiet, so
    // loudness scaling would weaken exactly the shapes that must be reached.
    const articulation = this.amplitude.attached ? 0.62 + 0.38 * amplitude : 0.9;
    for (const v of SHAPE_VISEMES) {
      const gain = VISEME_GAIN[v] * (CLOSURE_VISEMES.has(v) ? 1 : articulation);
      face[VISEME_MORPHS[v]] = clamp01(lip.weights[v] * gain);
    }
    face.jawOpen = this.amplitude.attached && lip.speaking ? 0.14 * amplitude * (1 - lip.closure) : 0;

    face.eyeBlinkLeft = Math.max(face.eyeBlinkLeft ?? 0, blink);
    face.eyeBlinkRight = Math.max(face.eyeBlinkRight ?? 0, blink);

    const w = lip.weights;
    const smile = ((face.mouthSmileLeft ?? 0) + (face.mouthSmileRight ?? 0)) / 2;
    const frame: AvatarFrame = {
      now,
      dt,
      behavior,
      listen: { speaking: this.listen.speaking, level: this.listen.level },
      gaze,
      face,
      blink,
      speech: { activity, amplitude, level, speaking: lip.speaking, clock },
      motionScale: this.reducedMotion ? 0.35 : 1,
      mouthOpen: clamp01(
        face.jawOpen +
          w.aa +
          0.75 * w.O +
          0.5 * w.E +
          0.35 * w.I +
          0.3 * w.U +
          0.35 * w.DD +
          0.4 * w.kk +
          0.3 * (w.TH + w.CH + w.RR) +
          0.25 * w.nn +
          0.15 * w.SS +
          0.1 * w.FF,
      ),
      mouthWide: clamp(0.6 * w.E + 0.7 * w.I + 0.4 * w.SS + 0.6 * smile - 0.5 * (w.O + w.U), -1, 1),
      mouthRound: clamp01(0.7 * w.O + w.U + 0.5 * w.CH + 0.4 * w.RR),
      lipsPressed: clamp01(w.PP + 0.5 * w.FF),
      smile,
      browRaise: clamp(
        (face.browInnerUp ?? 0) +
          0.5 * ((face.browOuterUpLeft ?? 0) + (face.browOuterUpRight ?? 0)) / 2 -
          ((face.browDownLeft ?? 0) + (face.browDownRight ?? 0)) / 2,
        -1,
        1,
      ),
    };
    this.frame = frame;
    return frame;
  }
}
