/**
 * Motion audit (dev). Imported by verification scripts on the Vite dev server
 * (`import("/src/features/avatar/dev/motionAudit.ts")` in the playground with
 * ?clock=manual); not referenced by the app, so it never ships.
 *
 * It steps the real engine + animator offline on the manual clock and
 * measures, frame by frame:
 *   - hand clipping: wrist and fingertip points raycast against the deformed
 *     outfit (a point behind the blazer/shirt surface is inside the body)
 *   - snapping: the largest per-frame rotation of the major bones
 *   - eye contact: angle between each eye's gaze and the direction to the camera
 */
import {
  BufferGeometry,
  DoubleSide,
  Float32BufferAttribute,
  Mesh,
  MeshBasicMaterial,
  Quaternion,
  Raycaster,
  Vector3,
  type Material,
  type Object3D,
  type SkinnedMesh,
} from "three";
import type { AvatarAnimator } from "../animation/animator";
import { getAvatarInternals } from "../controller";
import type { HumanBoneName } from "../rig/humanoid";
import type { AvatarRig } from "../rig/rig";
import type { AvatarBehavior, AvatarController } from "../types";
import type { ManualClock } from "./realTts";

export interface AuditTarget {
  controller: AvatarController;
  rig: AvatarRig;
  animator: AvatarAnimator;
  camera: Object3D;
  clock: ManualClock;
}

const SIDES = ["left", "right"] as const;
const FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"] as const;
const WATCHED: HumanBoneName[] = [
  "spine",
  "chest",
  "neck",
  "head",
  "leftShoulder",
  "rightShoulder",
  "leftUpperArm",
  "rightUpperArm",
  "leftLowerArm",
  "rightLowerArm",
  "leftHand",
  "rightHand",
];

const _a = new Vector3();
const _b = new Vector3();
const _q = new Quaternion();
const FORWARD = new Vector3(0, 0, 1);

/** Hand probe points: wrist, finger joints and extrapolated fingertips. */
export function handPoints(rig: AvatarRig): Array<{ name: string; point: Vector3 }> {
  const out: Array<{ name: string; point: Vector3 }> = [];
  for (const side of SIDES) {
    const hand = rig.bones[`${side}Hand`];
    if (hand) out.push({ name: `${side}Hand`, point: hand.getWorldPosition(new Vector3()) });
    for (const finger of FINGERS) {
      const distal = rig.bones[`${side}${finger}Distal` as HumanBoneName];
      const inter = rig.bones[(finger === "Thumb" ? `${side}ThumbProximal` : `${side}${finger}Intermediate`) as HumanBoneName];
      if (!distal) continue;
      const d = distal.getWorldPosition(new Vector3());
      out.push({ name: `${side}${finger}Distal`, point: d.clone() });
      if (inter) {
        const i = inter.getWorldPosition(new Vector3());
        out.push({ name: `${side}${finger}Tip`, point: d.clone().add(d.clone().sub(i).multiplyScalar(0.85)) });
      }
    }
  }
  return out;
}

/** The outfit meshes (blazer/shirt/trousers) of a rig. */
export function outfitMeshes(rig: AvatarRig): Mesh[] {
  const out: Mesh[] = [];
  rig.root.traverse((obj) => {
    const mesh = obj as Mesh;
    if (mesh.isMesh && /(suit|shirt|cloth|outfit)/i.test(`${mesh.name} ${(mesh.material as Material).name}`)) out.push(mesh);
  });
  return out;
}

/**
 * Static copies of the outfit in its current (skinned + morphed) pose, so
 * raycasts do not re-skin every vertex per triangle.
 */
export class PoseBaker {
  private readonly baked: Mesh[] = [];
  private readonly v = new Vector3();

  constructor(private readonly sources: Mesh[]) {}

  bake(): Mesh[] {
    this.sources.forEach((src, m) => {
      const geo = src.geometry;
      const count = geo.attributes.position!.count;
      let dst = this.baked[m];
      if (!dst) {
        const g = new BufferGeometry();
        if (geo.index) g.setIndex(geo.index);
        g.setAttribute("position", new Float32BufferAttribute(new Float32Array(count * 3), 3));
        dst = new Mesh(g, new MeshBasicMaterial({ side: DoubleSide }));
        this.baked[m] = dst;
      }
      const pos = dst.geometry.attributes.position!;
      for (let i = 0; i < count; i++) {
        (src as SkinnedMesh).getVertexPosition(i, this.v);
        this.v.applyMatrix4(src.matrixWorld);
        pos.setXYZ(i, this.v.x, this.v.y, this.v.z);
      }
      pos.needsUpdate = true;
      dst.geometry.computeBoundingSphere();
    });
    return this.baked;
  }

  dispose(): void {
    for (const mesh of this.baked) {
      mesh.geometry.dispose();
      (mesh.material as Material).dispose();
    }
  }
}

/**
 * How far a point sits inside the outfit (m, 0 when outside): rays toward the
 * viewer (+Z) and sideways; an odd number of surface crossings means inside,
 * the depth is the distance to the nearest crossing.
 */
export function penetrationDepth(point: Vector3, meshes: Mesh[], raycaster = new Raycaster()): number {
  let depth = Infinity;
  let insideVotes = 0;
  const dirs = [new Vector3(0, 0, 1), new Vector3(1, 0, 0), new Vector3(-1, 0, 0)];
  for (const dir of dirs) {
    raycaster.set(point, dir);
    raycaster.far = 0.6;
    const hits = raycaster.intersectObjects(meshes, false);
    if (hits.length % 2 === 1) {
      insideVotes += 1;
      depth = Math.min(depth, hits[0]!.distance);
    }
  }
  return insideVotes >= 2 ? depth : 0;
}

export interface FrameSample {
  t: number;
  state: string;
  maxJumpDeg: number;
  jumpBone: string;
  eyeErrorDeg: number | null;
  penetration?: { depth: number; point: string };
}

export interface RunOptions {
  seconds: number;
  fps?: number;
  /** Raycast hand points every N frames (0 = never). */
  clipEvery?: number;
}

export class MotionAudit {
  private readonly prev = new Map<HumanBoneName, Quaternion>();
  private readonly baker: PoseBaker;
  private readonly raycaster = new Raycaster();
  private readonly cameraPos = new Vector3();

  constructor(private readonly target: AuditTarget) {
    this.baker = new PoseBaker(outfitMeshes(target.rig));
  }

  dispose(): void {
    this.baker.dispose();
  }

  apply(behavior: AvatarBehavior): void {
    this.target.controller.applyBehavior(behavior);
  }

  /** Step the engine + animator `seconds` forward on the manual clock. */
  run({ seconds, fps = 60, clipEvery = 0 }: RunOptions): FrameSample[] {
    const { controller, rig, animator, camera, clock } = this.target;
    const engine = getAvatarInternals(controller)!.engine;
    const frames = Math.round(seconds * fps);
    const out: FrameSample[] = [];
    for (let i = 0; i < frames; i++) {
      clock.set(clock.now() + 1 / fps);
      const frame = engine.update();
      camera.getWorldPosition(this.cameraPos);
      animator.update(frame, this.cameraPos);
      rig.root.updateMatrixWorld(true);
      let maxJump = 0;
      let jumpBone = "";
      for (const name of WATCHED) {
        const bone = rig.bones[name];
        if (!bone) continue;
        bone.getWorldQuaternion(_q);
        const last = this.prev.get(name);
        if (last) {
          const deg = (last.angleTo(_q) * 180) / Math.PI;
          if (deg > maxJump) {
            maxJump = deg;
            jumpBone = name;
          }
          last.copy(_q);
        } else this.prev.set(name, _q.clone());
      }
      const sample: FrameSample = { t: clock.now(), state: frame.behavior.state, maxJumpDeg: maxJump, jumpBone, eyeErrorDeg: this.eyeError() };
      if (clipEvery > 0 && i % clipEvery === 0) {
        let worst = { depth: 0, point: "" };
        const outfit = this.baker.bake();
        for (const { name, point } of handPoints(rig)) {
          const depth = penetrationDepth(point, outfit, this.raycaster);
          if (depth > worst.depth) worst = { depth, point: name };
        }
        sample.penetration = worst;
      }
      out.push(sample);
    }
    return out;
  }

  /** Largest angle (deg) between an eye's gaze and the direction to the camera. */
  eyeError(): number | null {
    const { rig, camera } = this.target;
    camera.getWorldPosition(this.cameraPos);
    let worst: number | null = null;
    for (const side of ["leftEye", "rightEye"] as const) {
      const eye = rig.bones[side];
      const ref = rig.referenceWorld[side];
      if (!eye || !ref) continue;
      eye.getWorldQuaternion(_q);
      const gaze = _a.copy(FORWARD).applyQuaternion(_q.multiply(ref.clone().invert())).normalize();
      const toCam = _b.copy(this.cameraPos).sub(eye.getWorldPosition(new Vector3())).normalize();
      const deg = (gaze.angleTo(toCam) * 180) / Math.PI;
      worst = worst === null ? deg : Math.max(worst, deg);
    }
    return worst;
  }
}

/** Summary helpers for scripts. */
export function summarize(samples: FrameSample[]): {
  maxJumpDeg: number;
  p99JumpDeg: number;
  jumpBone: string;
  maxDepthCm: number;
  depthPoint: string;
  eyeMedianDeg: number | null;
  eyeP90Deg: number | null;
} {
  const jumps = samples.map((s) => s.maxJumpDeg).sort((a, b) => a - b);
  const worst = samples.reduce((w, s) => (s.maxJumpDeg > w.maxJumpDeg ? s : w), samples[0]!);
  const pen = samples.filter((s) => s.penetration).reduce((w, s) => (s.penetration!.depth > (w?.penetration?.depth ?? 0) ? s : w), undefined as FrameSample | undefined);
  const eyes = samples.map((s) => s.eyeErrorDeg).filter((v): v is number => v !== null).sort((a, b) => a - b);
  return {
    maxJumpDeg: worst?.maxJumpDeg ?? 0,
    p99JumpDeg: jumps.length ? jumps[Math.floor(jumps.length * 0.99)]! : 0,
    jumpBone: worst?.jumpBone ?? "",
    maxDepthCm: pen?.penetration ? pen.penetration.depth * 100 : 0,
    depthPoint: pen?.penetration?.point ?? "",
    eyeMedianDeg: eyes.length ? eyes[Math.floor(eyes.length / 2)]! : null,
    eyeP90Deg: eyes.length ? eyes[Math.floor(eyes.length * 0.9)]! : null,
  };
}
