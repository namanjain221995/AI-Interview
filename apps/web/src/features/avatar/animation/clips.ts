/**
 * Builds THREE.AnimationClips from the pose library for a specific rig:
 * timelines are sampled, each canonical Euler pose is mapped through the
 * rig's pose basis to bone-local quaternions, and written as
 * QuaternionKeyframeTracks (bound by bone uuid, so duplicate names are safe).
 * Additive clips store deltas against the rest pose.
 */
import {
  AdditiveAnimationBlendMode,
  AnimationClip,
  NormalAnimationBlendMode,
  Quaternion,
  QuaternionKeyframeTrack,
} from "three";
import type { EulerDeg } from "../core/math";
import { applyBasis, quatFromEulerDeg } from "../core/quat";
import type { HumanBoneName } from "../rig/humanoid";
import type { AvatarRig } from "../rig/rig";
import { ARM_GESTURES, BASE_POSES, HEAD_GESTURES, type ArmGestureId, type BasePoseId, type HeadGestureId } from "./catalog";
import {
  ARM_GESTURE_SPECS,
  BASE_POSE_SPECS,
  BREATHING_SPEC,
  GESTURE_LEVELS,
  HEAD_GESTURE_SPECS,
  REST_POSE,
  eulerOrderFor,
  scaledTimeline,
  type GestureLevel,
  type PoseSpec,
  type PoseTimeline,
} from "./poses";

const ZERO: EulerDeg = [0, 0, 0];
const _q = new Quaternion();

/** Bone-local rotation for a canonical pose angle. */
export function poseToLocal(rig: AvatarRig, bone: HumanBoneName, euler: EulerDeg, out = new Quaternion()): Quaternion {
  const basis = rig.basis[bone];
  if (!basis) return out.identity();
  quatFromEulerDeg(euler, eulerOrderFor(bone), _q);
  return applyBasis(basis, _q, out);
}

/** Apply a full pose immediately (used to seed the mixer's original state). */
export function applyPoseToRig(rig: AvatarRig, pose: PoseSpec): void {
  for (const [bone, euler] of Object.entries(pose) as [HumanBoneName, EulerDeg][]) {
    const node = rig.bones[bone];
    if (node && rig.basis[bone]) poseToLocal(rig, bone, euler, node.quaternion);
  }
}

export interface BuildClipOptions {
  fps: number;
  additive: boolean;
  /** Last sample repeats the first (seamless loop). */
  loop: boolean;
  /** Reference pose for additive deltas and for bones a timeline omits. */
  reference?: PoseSpec;
}

export function buildClip(rig: AvatarRig, name: string, timeline: PoseTimeline, options: BuildClipOptions): AnimationClip {
  const reference = options.reference ?? REST_POSE;
  const bones = timeline.bones.filter((b) => rig.bones[b] && rig.basis[b]);
  const frames = Math.max(2, Math.round(timeline.duration * options.fps) + 1);
  const times = new Float32Array(frames);
  const values = bones.map(() => new Float32Array(frames * 4));
  const refInverse = bones.map((b) => poseToLocal(rig, b, reference[b] ?? ZERO).invert());
  const q = new Quaternion();

  for (let i = 0; i < frames; i++) {
    const t = (i / (frames - 1)) * timeline.duration;
    times[i] = t;
    const pose = timeline.sample(options.loop && i === frames - 1 ? 0 : t);
    bones.forEach((bone, j) => {
      poseToLocal(rig, bone, pose[bone] ?? reference[bone] ?? ZERO, q);
      if (options.additive) q.premultiply(refInverse[j]!);
      const arr = values[j]!;
      // Keep successive samples in the same hemisphere so slerp takes the short path.
      if (i > 0) {
        const o = (i - 1) * 4;
        if (arr[o]! * q.x + arr[o + 1]! * q.y + arr[o + 2]! * q.z + arr[o + 3]! * q.w < 0) q.set(-q.x, -q.y, -q.z, -q.w);
      }
      arr.set([q.x, q.y, q.z, q.w], i * 4);
    });
  }

  const tracks: QuaternionKeyframeTrack[] = [];
  bones.forEach((bone, j) => {
    const node = rig.bones[bone]!;
    const arr = values[j]!;
    let constant = true;
    for (let i = 1; i < frames && constant; i++) {
      for (let k = 0; k < 4; k++) {
        if (Math.abs(arr[i * 4 + k]! - arr[k]!) > 1e-6) {
          constant = false;
          break;
        }
      }
    }
    if (constant) {
      tracks.push(
        new QuaternionKeyframeTrack(`${node.uuid}.quaternion`, [0, timeline.duration], [...arr.subarray(0, 4), ...arr.subarray(0, 4)]),
      );
    } else {
      tracks.push(new QuaternionKeyframeTrack(`${node.uuid}.quaternion`, times, arr));
    }
  });
  return new AnimationClip(name, timeline.duration, tracks, options.additive ? AdditiveAnimationBlendMode : NormalAnimationBlendMode);
}

export interface ClipLibrary {
  base: Record<BasePoseId, AnimationClip>;
  /** Arm gestures per intensity level (joint-angle scaled), played at full weight. */
  arm: Record<ArmGestureId, Record<GestureLevel, AnimationClip>>;
  head: Record<HeadGestureId, AnimationClip>;
  breathing: AnimationClip;
}

export function buildClipLibrary(rig: AvatarRig): ClipLibrary {
  const base = {} as Record<BasePoseId, AnimationClip>;
  for (const id of BASE_POSES) {
    base[id] = buildClip(rig, `base:${id}`, BASE_POSE_SPECS[id], { fps: 12, additive: false, loop: true });
  }
  const arm = {} as Record<ArmGestureId, Record<GestureLevel, AnimationClip>>;
  for (const id of ARM_GESTURES) {
    const levels = {} as Record<GestureLevel, AnimationClip>;
    for (const level of GESTURE_LEVELS) {
      levels[level.name] = buildClip(rig, `arm:${id}:${level.name}`, scaledTimeline(ARM_GESTURE_SPECS[id], level.scale), {
        fps: 30,
        additive: true,
        loop: false,
      });
    }
    arm[id] = levels;
  }
  const head = {} as Record<HeadGestureId, AnimationClip>;
  for (const id of HEAD_GESTURES) {
    head[id] = buildClip(rig, `head:${id}`, HEAD_GESTURE_SPECS[id], { fps: 30, additive: true, loop: false });
  }
  // Breathing offsets are relative to "no rotation", not to the rest pose.
  const breathing = buildClip(rig, "breathing", BREATHING_SPEC, { fps: 20, additive: true, loop: true, reference: {} });
  return { base, arm, head, breathing };
}
