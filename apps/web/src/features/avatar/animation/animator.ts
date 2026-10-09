/**
 * AvatarAnimator: executes AvatarFrames on a rig.
 *
 * Layers, in order:
 *   1. base body      looping clips, eased weight crossfades (weights sum to 1)
 *   2. gesture overlay additive one-shot arm clips; intensity picks a clip
 *                     pre-scaled in joint space, played at full weight
 *   3. head gestures  additive nods/tilts (weight = intensity)
 *   4. breathing      additive loop
 *   5. procedural     idle sway noise, speech-driven head motion, head/neck
 *                     aim at the camera (held during nods), eye gaze with
 *                     saccades and vergence
 *   6. face           expressions + blink + visemes + jaw (morphs), eyelids
 *                     follow vertical gaze
 */
import {
  AnimationMixer,
  LoopOnce,
  LoopRepeat,
  Quaternion,
  Vector3,
  type AnimationAction,
  type AnimationClip,
} from "three";
import type { AvatarFrame } from "../engine";
import { clamp, damp, DEG, easeInOutSine, hashString, Spring, ValueNoise1D } from "../core/math";
import { quatFromEulerDeg } from "../core/quat";
import type { HumanBoneName } from "../rig/humanoid";
import { gazeToEyeLook, rotateBoneWorld, type AvatarRig, type FaceWeights } from "../rig/rig";
import type { ArmGestureId, BasePoseId, HeadGestureId } from "./catalog";
import { applyPoseToRig, buildClipLibrary, type ClipLibrary } from "./clips";
import { BASE_POSE_SPECS, gestureLevelFor, type GestureLevel } from "./poses";

interface Fade {
  action: AnimationAction;
  from: number;
  to: number;
  start: number;
  duration: number;
}

interface BaseFade {
  target: BasePoseId;
  start: number;
  duration: number;
  targetFrom: number;
  others: Map<BasePoseId, number>;
}

const _v1 = new Vector3();
const _v2 = new Vector3();
const _v3 = new Vector3();
const _q1 = new Quaternion();
const _q2 = new Quaternion();
const _q3 = new Quaternion();
const _qIdentity = new Quaternion();
const _eyes = new Vector3();
const FORWARD = new Vector3(0, 0, 1);

/** Fade-out of running arm gestures on barge-in (s). */
export const BARGE_IN_GESTURE_FADE = 0.4;

export interface AnimatorOptions {
  seed?: number;
}

export class AvatarAnimator {
  readonly mixer: AnimationMixer;
  readonly library: ClipLibrary;
  private readonly rig: AvatarRig;
  private readonly baseActions = new Map<BasePoseId, AnimationAction>();
  private readonly armActions = new Map<`${ArmGestureId}:${GestureLevel}`, AnimationAction[]>();
  private readonly headActions = new Map<HeadGestureId, AnimationAction[]>();
  private readonly breathing: AnimationAction;
  private readonly baseWeights = new Map<BasePoseId, number>();
  private currentBase: BasePoseId | null = null;
  private baseFade: BaseFade | null = null;
  private fades: Fade[] = [];
  private seenBase = -1;
  private seenArm = -1;
  private seenHead = -1;
  private seenCancel = 0;
  private activeHead: AnimationAction | null = null;
  private time = 0;
  private readonly noise: ValueNoise1D;
  private readonly aimYaw = new Spring(0, 5);
  private readonly aimPitch = new Spring(0, 5);
  private aimTargetYaw = 0;
  private aimTargetPitch = 0;
  private speechPitch = 0;
  private ampSlow = 0;
  private readonly faceScratch: FaceWeights = {};
  private readonly headForwardLocal = new Vector3(0, 0, 1);
  /** Eye midpoint in head-local space (fallback when the rig has no eye bones). */
  private readonly eyeOffsetLocal = new Vector3(0, 0.09, 0.08);
  private disposed = false;

  constructor(rig: AvatarRig, options: AnimatorOptions = {}) {
    this.rig = rig;
    this.noise = new ValueNoise1D(options.seed ?? hashString(rig.name));
    // Seed the mixer's "original" state with a natural rest pose, never the A/T-pose.
    applyPoseToRig(rig, BASE_POSE_SPECS.idle_neutral.sample(0));
    rig.root.updateMatrixWorld(true);
    this.library = buildClipLibrary(rig);
    this.mixer = new AnimationMixer(rig.root);

    for (const [id, clip] of Object.entries(this.library.base) as [BasePoseId, AnimationClip][]) {
      const action = this.mixer.clipAction(clip);
      action.setLoop(LoopRepeat, Infinity);
      action.enabled = true;
      this.baseActions.set(id, action);
      this.baseWeights.set(id, 0);
    }
    // Two instances per one-shot so a gesture can restart while its previous run fades.
    for (const [id, levels] of Object.entries(this.library.arm) as [ArmGestureId, Record<GestureLevel, AnimationClip>][]) {
      for (const [level, clip] of Object.entries(levels) as [GestureLevel, AnimationClip][]) {
        this.armActions.set(`${id}:${level}`, [this.oneShot(clip), this.oneShot(clip.clone())]);
      }
    }
    for (const [id, clip] of Object.entries(this.library.head) as [HeadGestureId, AnimationClip][]) {
      this.headActions.set(id, [this.oneShot(clip), this.oneShot(clip.clone())]);
    }
    this.breathing = this.mixer.clipAction(this.library.breathing);
    this.breathing.setLoop(LoopRepeat, Infinity);
    this.breathing.play();

    // Head forward axis in head-local space (reference pose faces +Z).
    const headRef = rig.referenceWorld.head;
    if (headRef) this.headForwardLocal.copy(FORWARD).applyQuaternion(_q1.copy(headRef).invert());
    if (rig.bones.head) this.eyeOffsetLocal.copy(rig.bones.head.worldToLocal(rig.metrics.eyeCenter.clone()));
  }

  private oneShot(clip: AnimationClip): AnimationAction {
    const action = this.mixer.clipAction(clip);
    action.setLoop(LoopOnce, 1);
    action.clampWhenFinished = false;
    return action;
  }

  /** Advance one frame. `cameraWorld` is the viewer position for eye contact. */
  update(frame: AvatarFrame, cameraWorld: Vector3): void {
    if (this.disposed) return;
    const dt = frame.dt;
    this.time += dt;
    const b = frame.behavior;

    // ---- base layer
    if (b.base.serial !== this.seenBase) {
      this.seenBase = b.base.serial;
      this.crossfadeBase(b.base.pose, this.currentBase === null ? 0 : b.base.fade);
    }
    this.updateBaseFade();

    // ---- barge-in: running gestures settle quickly, at about the speed the
    // hands came up (0.2 s dropped a raised two-hand gesture 17 deg in one frame).
    if (b.cancelSerial !== this.seenCancel) {
      this.seenCancel = b.cancelSerial;
      for (const list of this.armActions.values()) for (const a of list) if (a.isRunning()) this.fadeTo(a, 0, BARGE_IN_GESTURE_FADE);
    }

    // ---- gesture overlay
    if (b.gesture && b.gesture.serial !== this.seenArm) {
      this.seenArm = b.gesture.serial;
      for (const list of this.armActions.values()) for (const a of list) if (a.isRunning()) this.fadeTo(a, 0, 0.25);
      // Intensity picks a joint-angle-scaled clip; the overlay itself plays at full weight.
      const pair = this.armActions.get(`${b.gesture.id}:${gestureLevelFor(b.gesture.intensity)}`);
      if (pair) this.startOneShot(pair, 1, b.gesture.speed, 0.12);
    }
    if (b.headGesture && b.headGesture.serial !== this.seenHead) {
      this.seenHead = b.headGesture.serial;
      if (this.activeHead?.isRunning()) this.fadeTo(this.activeHead, 0, 0.2);
      const pair = this.headActions.get(b.headGesture.id);
      if (pair) this.activeHead = this.startOneShot(pair, b.headGesture.intensity * (0.6 + 0.4 * frame.motionScale), 1, 0.08);
    }
    this.updateFades();

    // ---- breathing
    const breathWeight = (frame.speech.speaking ? 0.75 : 1) * (0.55 + 0.45 * frame.motionScale);
    this.breathing.setEffectiveWeight(breathWeight);

    this.mixer.update(dt);
    this.rig.root.updateMatrixWorld(true);

    // ---- face weights for this frame (gaze adds eyelid follow below)
    const face = this.faceScratch;
    for (const key in face) face[key] = 0;
    Object.assign(face, frame.face);

    // ---- procedural layers
    this.applySway(frame);
    this.applyHeadAim(frame, cameraWorld);
    this.applyGaze(frame, cameraWorld);

    this.rig.applyFace(face);
    this.rig.update(dt);
  }

  // ------------------------------------------------------------ base crossfades

  private crossfadeBase(pose: BasePoseId, duration: number): void {
    const target = this.baseActions.get(pose);
    if (!target) return;
    if (this.currentBase === pose && !this.baseFade) return;
    const targetFrom = this.baseWeights.get(pose) ?? 0;
    if (!target.isRunning()) {
      target.reset();
      target.play();
    }
    const others = new Map<BasePoseId, number>();
    for (const [id, w] of this.baseWeights) if (id !== pose && w > 0) others.set(id, w);
    this.currentBase = pose;
    if (duration <= 0) {
      this.setBaseWeight(pose, 1);
      for (const id of others.keys()) this.setBaseWeight(id, 0);
      this.baseFade = null;
      return;
    }
    this.baseFade = { target: pose, start: this.time, duration, targetFrom, others };
  }

  private updateBaseFade(): void {
    const fade = this.baseFade;
    if (!fade) return;
    const s = clamp((this.time - fade.start) / fade.duration, 0, 1);
    const e = easeInOutSine(s);
    this.setBaseWeight(fade.target, fade.targetFrom + (1 - fade.targetFrom) * e);
    for (const [id, from] of fade.others) this.setBaseWeight(id, from * (1 - e));
    if (s >= 1) this.baseFade = null;
  }

  private setBaseWeight(id: BasePoseId, weight: number): void {
    const action = this.baseActions.get(id);
    if (!action) return;
    this.baseWeights.set(id, weight);
    if (weight <= 1e-4) {
      if (action.isRunning()) action.stop();
      this.baseWeights.set(id, 0);
      return;
    }
    if (!action.isRunning()) action.play();
    action.setEffectiveWeight(weight);
  }

  // ------------------------------------------------------------ one-shots

  private startOneShot(pair: AnimationAction[], weight: number, speed: number, fadeIn: number): AnimationAction {
    const action = pair.find((a) => !a.isRunning()) ?? pair[0]!;
    this.fades = this.fades.filter((f) => f.action !== action);
    action.reset();
    action.setLoop(LoopOnce, 1);
    action.timeScale = speed;
    action.setEffectiveWeight(0);
    action.play();
    this.fadeTo(action, weight, fadeIn);
    return action;
  }

  private fadeTo(action: AnimationAction, to: number, duration: number): void {
    this.fades = this.fades.filter((f) => f.action !== action);
    this.fades.push({ action, from: action.getEffectiveWeight(), to, start: this.time, duration: Math.max(1e-3, duration) });
  }

  private updateFades(): void {
    if (!this.fades.length) return;
    const keep: Fade[] = [];
    for (const f of this.fades) {
      const s = clamp((this.time - f.start) / f.duration, 0, 1);
      const w = f.from + (f.to - f.from) * easeInOutSine(s);
      f.action.setEffectiveWeight(w);
      if (s < 1) keep.push(f);
      else if (f.to <= 0) f.action.stop();
    }
    this.fades = keep;
  }

  // ------------------------------------------------------------ procedural

  /** Rotate a bone by a canonical-frame offset relative to its parent's reference frame. */
  private offsetBone(bone: HumanBoneName, euler: readonly [number, number, number]): void {
    const node = this.rig.bones[bone];
    const basis = this.rig.basis[bone];
    if (!node || !basis) return;
    quatFromEulerDeg(euler, "XYZ", _q1);
    // pre * S * pre^-1 applied on the left of the animated local rotation.
    _q2.copy(basis.pre).multiply(_q1).multiply(_q3.copy(basis.pre).invert());
    node.quaternion.premultiply(_q2);
  }

  private applySway(frame: AvatarFrame): void {
    const t = this.time;
    const k = frame.motionScale * (frame.speech.speaking ? 0.6 : 1);
    const n = (o: number, f: number) => this.noise.fractal(t * f + o, 2);
    this.offsetBone("spine", [0.5 * k * n(3.1, 0.11), 0.6 * k * n(7.7, 0.09), 0.4 * k * n(1.3, 0.13)]);
    this.offsetBone("chest", [0.3 * k * n(11.2, 0.12), 0.3 * k * n(5.5, 0.1), 0]);

    // Speech-driven head emphasis: loudness rising above its slow average dips the head slightly.
    const amp = frame.speech.amplitude;
    this.ampSlow = damp(this.ampSlow, amp, 1.5, frame.dt);
    const emphasis = frame.speech.speaking ? clamp(amp - this.ampSlow, -0.3, 0.6) : 0;
    this.speechPitch = damp(this.speechPitch, emphasis * 3.2, 9, frame.dt);
    const talkYaw = frame.speech.speaking ? 0.9 * n(21.7, 0.35) : 0;
    this.offsetBone("neck", [0.35 * k * n(9.9, 0.15), 0.5 * k * n(2.2, 0.12), 0.3 * k * n(4.4, 0.1)]);
    this.offsetBone("head", [this.speechPitch + 0.4 * k * n(13.3, 0.2), talkYaw * frame.motionScale, 0.35 * k * n(8.8, 0.17)]);
    this.rig.root.updateMatrixWorld(true);
  }

  private applyHeadAim(frame: AvatarFrame, cameraWorld: Vector3): void {
    const head = this.rig.bones.head;
    const neck = this.rig.bones.neck;
    if (!head) return;
    // Current (animated) head forward and the direction to the viewer.
    head.getWorldQuaternion(_q1);
    const forward = _v1.copy(this.headForwardLocal).applyQuaternion(_q1).normalize();
    const eyes = this.eyeCenter(_v2);
    const toCam = _v3.copy(cameraWorld).sub(eyes).normalize();
    const yawErr = Math.atan2(toCam.x, toCam.z) - Math.atan2(forward.x, forward.z);
    const pitchErr = Math.asin(clamp(toCam.y, -1, 1)) - Math.asin(clamp(forward.y, -1, 1));

    // Hold the correction while a nod/tilt plays so the gesture is not cancelled.
    const headBusy = this.activeHead?.isRunning() ?? false;
    if (!headBusy) {
      this.aimTargetYaw = clamp(yawErr * 0.7, -0.3, 0.3);
      this.aimTargetPitch = clamp(pitchErr * 0.55, -0.2, 0.2);
    }
    const yaw = this.aimYaw.update(this.aimTargetYaw + frame.gaze.headYaw, frame.dt);
    const pitch = this.aimPitch.update(this.aimTargetPitch + frame.gaze.headPitch, frame.dt);

    // Ry(yaw) * Rx(-pitch) in world space, 35% on the neck, the rest on the head.
    quatFromEulerDeg([-pitch / DEG, yaw / DEG, 0], "YXZ", _q2);
    if (neck) rotateBoneWorld(neck, _q3.copy(_qIdentity).slerp(_q2, 0.35));
    rotateBoneWorld(head, _q3.copy(_qIdentity).slerp(_q2, 0.65));
  }

  private eyeCenter(out: Vector3): Vector3 {
    const l = this.rig.bones.leftEye;
    const r = this.rig.bones.rightEye;
    if (l && r) {
      l.getWorldPosition(out);
      r.getWorldPosition(_v3);
      return out.add(_v3).multiplyScalar(0.5);
    }
    const head = this.rig.bones.head;
    if (!head) return out.copy(this.rig.metrics.eyeCenter);
    return head.localToWorld(out.copy(this.eyeOffsetLocal));
  }

  private applyGaze(frame: AvatarFrame, cameraWorld: Vector3): void {
    const head = this.rig.bones.head;
    const headRef = this.rig.referenceWorld.head;
    if (!head || !headRef) return;
    const eyes = this.eyeCenter(_eyes);
    // Head frame relative to its reference orientation.
    head.getWorldQuaternion(_q1);
    const headFrame = _q1.multiply(_q2.copy(headRef).invert());
    const dirWorld = _v2.copy(cameraWorld).sub(eyes);
    const distance = Math.max(0.3, dirWorld.length());
    const local = dirWorld.normalize().applyQuaternion(_q3.copy(headFrame).invert());
    let yaw = Math.atan2(local.x, local.z) + frame.gaze.eyeYaw;
    let pitch = Math.atan2(local.y, Math.hypot(local.x, local.z)) + frame.gaze.eyePitch;
    yaw = clamp(yaw, -0.5, 0.5);
    pitch = clamp(pitch, -0.35, 0.3);
    const vergence = Math.atan2(0.031, distance);
    const target = _v3
      .set(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch))
      .applyQuaternion(headFrame)
      .multiplyScalar(distance)
      .add(eyes);
    this.rig.setGaze(yaw, pitch, target, vergence);

    // Eyelids follow the eyes vertically (and slightly sideways).
    const lids = this.faceScratch;
    if (this.rig.gazeMode === "bones") gazeToEyeLook(yaw, pitch, lids, 0.45);
    else if (this.rig.gazeMode === "morphs") gazeToEyeLook(yaw, pitch, lids, 1);
  }

  dispose(): void {
    this.disposed = true;
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.rig.root);
  }
}
