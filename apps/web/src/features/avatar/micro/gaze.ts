/**
 * Gaze micro-behaviour: fixation on the viewer with micro-saccades, brief
 * glances away (more while speaking, rare while listening), and an averted
 * up-left/up-right gaze while thinking. Eyes jump (saccade ~50 ms); the head
 * follows thinking glances slowly and only partially.
 */
import { DEG, damp, mulberry32, randRange, Spring, type Rng } from "../core/math";

export type GazeMode = "camera" | "thinking";

export interface GazeOutput {
  /** Eye offsets (radians) relative to looking straight at the viewer. */
  eyeYaw: number;
  eyePitch: number;
  /** Head offsets (radians) relative to facing the viewer. */
  headYaw: number;
  headPitch: number;
  /** True on the frame a large gaze shift starts (couples with blinks). */
  bigShift: boolean;
}

interface Target {
  yaw: number;
  pitch: number;
  until: number;
}

export class GazeController {
  private readonly rng: Rng;
  private fixation: Target;
  private glance: Target | null = null;
  private nextGlance: number;
  private thinkingSide = 1;
  private mode: GazeMode = "camera";
  private eyeYaw = 0;
  private eyePitch = 0;
  private readonly headYaw = new Spring(0, 3.2);
  private readonly headPitch = new Spring(0, 3.2);
  private readonly out: GazeOutput = { eyeYaw: 0, eyePitch: 0, headYaw: 0, headPitch: 0, bigShift: false };

  constructor(seed: number, now = 0) {
    this.rng = mulberry32(seed);
    this.fixation = { yaw: 0, pitch: 0, until: now + 1 };
    this.nextGlance = now + randRange(this.rng, 6, 12);
  }

  update(now: number, dt: number, mode: GazeMode, speaking: boolean, reducedMotion = false): GazeOutput {
    let bigShift = false;
    if (mode !== this.mode) {
      this.mode = mode;
      this.glance = null;
      if (mode === "thinking") {
        this.thinkingSide = this.rng() < 0.5 ? -1 : 1;
        this.fixation = this.thinkingFixation(now);
        bigShift = true;
      } else {
        this.fixation = { yaw: 0, pitch: 0, until: now + randRange(this.rng, 0.6, 1.4) };
        this.nextGlance = now + randRange(this.rng, 5, 10);
        bigShift = true;
      }
    }

    if (now >= this.fixation.until) {
      this.fixation = mode === "thinking" ? this.thinkingFixation(now) : this.cameraFixation(now, speaking);
    }

    if (mode === "camera") {
      if (this.glance && now >= this.glance.until) {
        this.glance = null;
        bigShift = true;
      }
      if (!this.glance && now >= this.nextGlance && !reducedMotion) {
        // Glance away: sideways/down, 3-7 degrees, 0.35-0.8 s.
        const side = this.rng() < 0.5 ? -1 : 1;
        this.glance = {
          yaw: side * randRange(this.rng, 3, 7) * DEG,
          pitch: -randRange(this.rng, 0.5, 4) * DEG,
          until: now + randRange(this.rng, 0.35, 0.8),
        };
        bigShift = true;
        this.nextGlance = now + (speaking ? randRange(this.rng, 5, 11) : randRange(this.rng, 9, 18));
      }
    }

    const target = this.glance ?? this.fixation;
    // Saccade: ~50 ms to settle.
    this.eyeYaw = damp(this.eyeYaw, target.yaw, 55, dt);
    this.eyePitch = damp(this.eyePitch, target.pitch, 55, dt);

    // Head follows thinking glances partially and slowly.
    const headFollow = mode === "thinking" ? 0.35 : 0;
    this.headYaw.update(target.yaw * headFollow, dt);
    this.headPitch.update(target.pitch * headFollow, dt);

    const o = this.out;
    o.eyeYaw = this.eyeYaw;
    o.eyePitch = this.eyePitch;
    o.headYaw = this.headYaw.value;
    o.headPitch = this.headPitch.value;
    o.bigShift = bigShift;
    return o;
  }

  private cameraFixation(now: number, speaking: boolean): Target {
    // Micro-saccades around the viewer's eyes.
    return {
      yaw: randRange(this.rng, -1.4, 1.4) * DEG,
      pitch: randRange(this.rng, -1.0, 0.8) * DEG,
      until: now + (speaking ? randRange(this.rng, 0.8, 2.4) : randRange(this.rng, 0.5, 2.0)),
    };
  }

  private thinkingFixation(now: number): Target {
    return {
      yaw: this.thinkingSide * randRange(this.rng, 11, 16) * DEG,
      pitch: randRange(this.rng, 5, 9) * DEG,
      until: now + randRange(this.rng, 0.7, 1.6),
    };
  }
}
