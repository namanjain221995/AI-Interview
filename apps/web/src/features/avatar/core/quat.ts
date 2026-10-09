/**
 * Quaternion helpers for the canonical character frame (+Y up, +Z forward,
 * character's left = +X). Separate from ./math so non-3D code paths do not
 * pull three.js into their bundle.
 */
import { Euler, Quaternion } from "three";
import { clamp01, DEG, type EulerDeg } from "./math";

const _euler = new Euler();

/** Quaternion from Euler degrees (default order XYZ: R = Rx * Ry * Rz). */
export function quatFromEulerDeg(e: EulerDeg, order: Euler["order"] = "XYZ", out = new Quaternion()): Quaternion {
  _euler.set(e[0] * DEG, e[1] * DEG, e[2] * DEG, order);
  return out.setFromEuler(_euler);
}

/**
 * Mirror a rotation across the character's sagittal (YZ) plane, mapping a
 * left-side rotation to the matching right-side one: (x, y, z, w) -> (x, -y, -z, w).
 */
export function mirrorQuat(q: Quaternion, out = new Quaternion()): Quaternion {
  return out.set(q.x, -q.y, -q.z, q.w);
}

/** Angle between two rotations in radians. */
export function quatAngle(a: Quaternion, b: Quaternion): number {
  return a.angleTo(b);
}

/**
 * Rotation basis that maps a canonical-frame rotation Q to a bone-local
 * rotation: local = pre * Q * post, where pre = inverse(parentWorldT) and
 * post = parentWorldT * localT, both measured in the reference (T) pose.
 * With the parent in its reference pose, the bone's world rotation becomes
 * Q * worldT, i.e. Q is applied about the bone's pivot in character space.
 */
export interface PoseBasis {
  pre: Quaternion;
  post: Quaternion;
}

export function makeBasis(parentWorldT: Quaternion, localT: Quaternion): PoseBasis {
  return {
    pre: parentWorldT.clone().invert(),
    post: parentWorldT.clone().multiply(localT),
  };
}

export function applyBasis(basis: PoseBasis, q: Quaternion, out = new Quaternion()): Quaternion {
  return out.copy(basis.pre).multiply(q).multiply(basis.post);
}

/** Weighted blend of rotations around identity (scales the rotation angle). */
export function scaleRotation(q: Quaternion, weight: number, out = new Quaternion()): Quaternion {
  return out.identity().slerp(q, clamp01(weight));
}
