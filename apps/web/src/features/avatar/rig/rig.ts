/**
 * AvatarRig: the normalised view of any avatar (GLB, VRM or procedural) that
 * the animation system drives. Every rig exposes canonical humanoid bones, a
 * per-bone pose basis measured in a canonical T-pose (arms along ±X, palms
 * down, fingers straight; character faces +Z with its left at +X) and a morph
 * dictionary using ARKit/Oculus names.
 */
import { Box3, Object3D, Quaternion, SkinnedMesh, Vector3 } from "three";
import { makeBasis, type PoseBasis } from "../core/quat";
import { fingerChain, FINGERS, type HumanBoneName, type Side } from "./humanoid";
import type { MorphDictionary } from "./morphs";

export type RigKind = "glb" | "vrm" | "procedural";
export type BoneMap = Partial<Record<HumanBoneName, Object3D>>;
export type BasisMap = Partial<Record<HumanBoneName, PoseBasis>>;
export type FaceWeights = Record<string, number>;

/** Rest-pose measurements (rig root space, metres) used for camera framing. */
export interface RigMetrics {
  headTopY: number;
  eyeY: number;
  neckY: number;
  chestY: number;
  shoulderWidth: number;
  /** Eye midpoint in rig space (look-at origin). */
  eyeCenter: Vector3;
}

export interface AvatarRig {
  readonly kind: RigKind;
  readonly name: string;
  /** Add this to the scene. The character faces +Z, feet near y = 0. */
  readonly root: Object3D;
  readonly bones: BoneMap;
  readonly basis: BasisMap;
  /** World rotation (relative to the root's parent) of each bone in the reference T-pose. */
  readonly referenceWorld: Partial<Record<HumanBoneName, Quaternion>>;
  readonly metrics: RigMetrics;
  readonly morphs: MorphDictionary;
  /**
   * How setGaze moves the eyes: "bones" rotates eye bones (the animator still
   * drives eyeLook* morphs at reduced gain so lids follow), "morphs" means the
   * animator drives eyeLook* morphs only, "native" uses the rig's own look-at.
   */
  readonly gazeMode: "bones" | "morphs" | "native";
  /** Apply final face weights (ARKit + viseme_* names); unknown names are ignored. */
  applyFace(weights: Readonly<FaceWeights>): void;
  /**
   * Eye direction relative to the head's reference frame, radians (yaw + =
   * toward the character's left, pitch + = up); `worldTarget` is the same gaze
   * as a world-space point for rigs with a native look-at. `vergence` (rad)
   * turns each eye inward so both converge on a near target.
   */
  setGaze(yaw: number, pitch: number, worldTarget: Vector3, vergence?: number): void;
  /** Per-frame hook after animation (VRM uses it to propagate normalized bones / expressions). */
  update(dt: number): void;
  dispose(): void;
}

const _v1 = new Vector3();
const _v2 = new Vector3();
const _q1 = new Quaternion();
const _q2 = new Quaternion();

/** Rotate `bone` by a rotation expressed in the root's parent frame, about its pivot. */
export function rotateBoneWorld(bone: Object3D, worldDelta: Quaternion): void {
  const parent = bone.parent;
  if (parent) {
    parent.getWorldQuaternion(_q1);
    _q2.copy(_q1).invert().multiply(worldDelta).multiply(_q1);
    bone.quaternion.premultiply(_q2);
  } else bone.quaternion.premultiply(worldDelta);
  bone.updateMatrixWorld(true);
}

function alignBoneToward(bone: Object3D | undefined, child: Object3D | undefined, target: Vector3): void {
  if (!bone || !child) return;
  bone.getWorldPosition(_v1);
  child.getWorldPosition(_v2);
  const dir = _v2.sub(_v1);
  if (dir.lengthSq() < 1e-10) return;
  dir.normalize();
  const delta = new Quaternion().setFromUnitVectors(dir, target);
  rotateBoneWorld(bone, delta);
}

/**
 * Twist the forearm about the arm axis so the palm faces down (thumb side
 * toward +Z), the VRM/Mixamo T-pose convention. Rigs bound with palms forward
 * (MakeHuman/MPFB A-pose) would otherwise curl fingers sideways.
 */
function normalizePalm(side: Side, bones: BoneMap, axis: Vector3): void {
  const lower = bones[`${side}LowerArm`];
  const index = bones[`${side}IndexProximal`];
  const little = bones[`${side}LittleProximal`] ?? bones[`${side}RingProximal`];
  if (!lower || !index || !little) return;
  const lateral = index.getWorldPosition(new Vector3()).sub(little.getWorldPosition(new Vector3()));
  lateral.addScaledVector(axis, -lateral.dot(axis));
  if (lateral.lengthSq() < 1e-10) return;
  lateral.normalize();
  const forward = new Vector3(0, 0, 1);
  const angle = Math.atan2(axis.dot(new Vector3().crossVectors(lateral, forward)), lateral.dot(forward));
  if (Math.abs(angle) < 1e-4) return;
  rotateBoneWorld(lower, new Quaternion().setFromAxisAngle(axis, angle));
}

/**
 * Pose a bind-pose rig (A-pose, relaxed fingers...) into the canonical T-pose:
 * upper arm, forearm and hand along ±X, palms down (forearm twist), non-thumb
 * finger segments straight. Direction changes use minimal-arc rotations.
 */
export function poseToCanonicalTPose(root: Object3D, bones: BoneMap): void {
  root.updateMatrixWorld(true);
  for (const side of ["left", "right"] as Side[]) {
    const target = new Vector3(side === "left" ? 1 : -1, 0, 0);
    const upper = bones[`${side}UpperArm`];
    const lower = bones[`${side}LowerArm`];
    const hand = bones[`${side}Hand`];
    alignBoneToward(upper, lower, target);
    alignBoneToward(lower, hand, target);
    const middle = bones[`${side}MiddleProximal`] ?? bones[`${side}IndexProximal`];
    alignBoneToward(hand, middle, target);
    normalizePalm(side, bones, target);
    alignBoneToward(hand, middle, target);
    for (const finger of FINGERS) {
      if (finger === "Thumb") continue;
      const chain = fingerChain(side, finger);
      alignBoneToward(bones[chain[0]!], bones[chain[1]!], target);
      alignBoneToward(bones[chain[1]!], bones[chain[2]!], target);
    }
  }
  root.updateMatrixWorld(true);
}

export interface ReferenceData {
  basis: BasisMap;
  referenceWorld: Partial<Record<HumanBoneName, Quaternion>>;
  referenceLocal: Partial<Record<HumanBoneName, Quaternion>>;
}

/** Measure the pose basis of every mapped bone in the current (reference) pose. */
export function captureReference(root: Object3D, bones: BoneMap): ReferenceData {
  root.updateMatrixWorld(true);
  const basis: BasisMap = {};
  const referenceWorld: Partial<Record<HumanBoneName, Quaternion>> = {};
  const referenceLocal: Partial<Record<HumanBoneName, Quaternion>> = {};
  for (const [name, bone] of Object.entries(bones) as [HumanBoneName, Object3D][]) {
    const parentWorld = bone.parent ? bone.parent.getWorldQuaternion(new Quaternion()) : new Quaternion();
    basis[name] = makeBasis(parentWorld, bone.quaternion);
    referenceWorld[name] = bone.getWorldQuaternion(new Quaternion());
    referenceLocal[name] = bone.quaternion.clone();
  }
  return { basis, referenceWorld, referenceLocal };
}

/** Snapshot / restore local transforms of a set of nodes. */
export function snapshotPose(nodes: Iterable<Object3D>): () => void {
  const saved = [...nodes].map((n) => [n, n.quaternion.clone(), n.position.clone()] as const);
  return () => {
    for (const [n, q, p] of saved) {
      n.quaternion.copy(q);
      n.position.copy(p);
    }
  };
}

/** Rest-pose metrics from bone positions and skinned bounds. */
export function measureRig(root: Object3D, bones: BoneMap): RigMetrics {
  root.updateMatrixWorld(true);
  const pos = (b: Object3D | undefined, fallback: Vector3) => (b ? b.getWorldPosition(new Vector3()) : fallback.clone());
  const head = pos(bones.head, new Vector3(0, 1.55, 0));
  const neck = pos(bones.neck, head.clone().add(new Vector3(0, -0.08, 0)));
  const chest = pos(bones.upperChest ?? bones.chest, neck.clone().add(new Vector3(0, -0.15, 0)));
  const lEye = bones.leftEye?.getWorldPosition(new Vector3());
  const rEye = bones.rightEye?.getWorldPosition(new Vector3());
  const eyeCenter =
    lEye && rEye ? lEye.clone().add(rEye).multiplyScalar(0.5) : head.clone().add(new Vector3(0, 0.09, 0.08));

  let headTopY = -Infinity;
  const box = new Box3();
  root.traverse((obj) => {
    const mesh = obj as SkinnedMesh;
    if (!(mesh as { isMesh?: boolean }).isMesh) return;
    try {
      if (mesh.isSkinnedMesh) {
        mesh.computeBoundingBox();
        if (!mesh.boundingBox) return;
        box.copy(mesh.boundingBox).applyMatrix4(mesh.matrixWorld);
      } else box.setFromObject(mesh);
      if (Number.isFinite(box.max.y) && box.max.y < head.y + 0.6) headTopY = Math.max(headTopY, box.max.y);
    } catch {
      /* bounds are best effort */
    }
  });
  if (!Number.isFinite(headTopY) || headTopY < eyeCenter.y) headTopY = eyeCenter.y + 0.13;

  const lArm = bones.leftUpperArm?.getWorldPosition(new Vector3());
  const rArm = bones.rightUpperArm?.getWorldPosition(new Vector3());
  const shoulderWidth = lArm && rArm ? lArm.distanceTo(rArm) + 0.12 : 0.42;

  return { headTopY, eyeY: eyeCenter.y, neckY: neck.y, chestY: chest.y, shoulderWidth, eyeCenter };
}

/** Morph-driven gaze for rigs without eye bones (ARKit eyeLook*). */
export function gazeToEyeLook(yaw: number, pitch: number, out: FaceWeights, gain = 1): FaceWeights {
  const range = 0.52; // ~30 degrees at weight 1
  const h = Math.max(-1, Math.min(1, yaw / range)) * gain;
  const v = Math.max(-1, Math.min(1, pitch / range)) * gain;
  // Looking toward the character's left (+yaw): left eye looks out, right eye looks in.
  out.eyeLookOutLeft = h > 0 ? h : 0;
  out.eyeLookInRight = h > 0 ? h : 0;
  out.eyeLookInLeft = h < 0 ? -h : 0;
  out.eyeLookOutRight = h < 0 ? -h : 0;
  out.eyeLookUpLeft = out.eyeLookUpRight = v > 0 ? v : 0;
  out.eyeLookDownLeft = out.eyeLookDownRight = v < 0 ? -v : 0;
  return out;
}
