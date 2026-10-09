import { describe, expect, it } from "vitest";
import { Bone, Group, Quaternion, Vector3 } from "three";
import { damp, mulberry32, pickAvoidingRepeat, Spring, ValueNoise1D } from "../core/math";
import { applyBasis, makeBasis, mirrorQuat, quatFromEulerDeg } from "../core/quat";
import { captureReference, poseToCanonicalTPose, type BoneMap } from "../rig/rig";
import {
  ARM_GESTURE_SPECS,
  BREATHING_SPEC,
  REST_POSE,
  keyframed,
  mirrorPose,
  scaledTimeline,
  symmetric,
} from "../animation/poses";

describe("math utilities", () => {
  it("mulberry32 is deterministic and in [0, 1)", () => {
    const a = mulberry32(1);
    const b = mulberry32(1);
    for (let i = 0; i < 1000; i++) {
      const x = a();
      expect(x).toBe(b());
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThan(1);
    }
  });

  it("pickAvoidingRepeat never repeats the previous choice", () => {
    const rng = mulberry32(3);
    let prev: string | undefined;
    for (let i = 0; i < 200; i++) {
      const next = pickAvoidingRepeat(rng, ["a", "b", "c"], prev);
      expect(next).not.toBe(prev);
      prev = next;
    }
    expect(pickAvoidingRepeat(rng, ["only"], "only")).toBe("only");
  });

  it("damp is frame-rate independent", () => {
    let a = 0;
    for (let i = 0; i < 60; i++) a = damp(a, 1, 5, 1 / 60);
    let b = 0;
    for (let i = 0; i < 30; i++) b = damp(b, 1, 5, 1 / 30);
    expect(a).toBeCloseTo(b, 10);
    expect(a).toBeCloseTo(1 - Math.exp(-5), 6);
  });

  it("critically damped spring converges without overshoot", () => {
    const s = new Spring(0, 8);
    let max = 0;
    for (let i = 0; i < 240; i++) max = Math.max(max, s.update(1, 1 / 60));
    expect(max).toBeLessThanOrEqual(1 + 1e-6);
    expect(s.value).toBeCloseTo(1, 3);
  });

  it("value noise is bounded and continuous", () => {
    const n = new ValueNoise1D(4);
    let prev = n.fractal(0);
    for (let x = 0.001; x < 20; x += 0.001) {
      const v = n.fractal(x);
      expect(Math.abs(v)).toBeLessThanOrEqual(1);
      expect(Math.abs(v - prev)).toBeLessThan(0.05);
      prev = v;
    }
  });
});

describe("quaternion helpers", () => {
  it("mirrors rotations across the sagittal plane", () => {
    const q = quatFromEulerDeg([10, 25, -40]);
    const m = mirrorQuat(q);
    expect(mirrorQuat(m).angleTo(q)).toBeLessThan(1e-6);
    // Mirrored rotation of a mirrored vector equals the mirrored result.
    const v = new Vector3(0.3, -0.2, 0.9);
    const r = v.clone().applyQuaternion(q);
    const vm = new Vector3(-v.x, v.y, v.z).applyQuaternion(m);
    expect(vm.x).toBeCloseTo(-r.x, 9);
    expect(vm.y).toBeCloseTo(r.y, 9);
    expect(vm.z).toBeCloseTo(r.z, 9);
  });

  it("pose basis applies canonical rotations about the bone pivot in character space", () => {
    // Parent and child with arbitrary rest orientations.
    const root = new Group();
    const parent = new Bone();
    parent.quaternion.copy(quatFromEulerDeg([20, -35, 50]));
    const child = new Bone();
    child.position.set(0, 0.3, 0);
    child.quaternion.copy(quatFromEulerDeg([-15, 60, 5]));
    root.add(parent);
    parent.add(child);
    root.updateMatrixWorld(true);
    const parentWorld = parent.getWorldQuaternion(new Quaternion());
    const childWorldRef = child.getWorldQuaternion(new Quaternion());
    const basis = makeBasis(parentWorld, child.quaternion);
    const q = quatFromEulerDeg([30, -10, 45]);
    child.quaternion.copy(applyBasis(basis, q));
    root.updateMatrixWorld(true);
    const expected = q.clone().multiply(childWorldRef);
    expect(child.getWorldQuaternion(new Quaternion()).angleTo(expected)).toBeLessThan(1e-6);
    // Identity pose reproduces the reference local rotation.
    expect(applyBasis(basis, new Quaternion()).angleTo(quatFromEulerDeg([-15, 60, 5]))).toBeLessThan(1e-6);
  });

  it("normalises an A-posed, palm-forward arm to the canonical T-pose", () => {
    const root = new Group();
    const chest = new Bone();
    chest.position.set(0, 1.4, 0);
    root.add(chest);
    const bones: BoneMap = { upperChest: chest };
    // Left arm hanging 45° down-out with the palm facing forward (thumb up).
    const mk = (name: keyof BoneMap, parent: Bone, pos: Vector3) => {
      const b = new Bone();
      b.position.copy(pos);
      parent.add(b);
      bones[name] = b;
      return b;
    };
    const down = new Vector3(1, -1, 0).normalize();
    const upper = mk("leftUpperArm", chest, new Vector3(0.18, 0, 0));
    const lower = mk("leftLowerArm", upper, down.clone().multiplyScalar(0.26));
    const hand = mk("leftHand", lower, down.clone().multiplyScalar(0.25));
    // Fingers: index on the thumb side (+Y when palm faces forward), little on -Y.
    mk("leftMiddleProximal", hand, down.clone().multiplyScalar(0.09));
    mk("leftIndexProximal", hand, down.clone().multiplyScalar(0.085).add(new Vector3(0.016, 0.016, 0)));
    mk("leftLittleProximal", hand, down.clone().multiplyScalar(0.075).add(new Vector3(-0.022, -0.022, 0)));
    poseToCanonicalTPose(root, bones);
    const wp = (b: keyof BoneMap) => bones[b]!.getWorldPosition(new Vector3());
    const armDir = wp("leftLowerArm").sub(wp("leftUpperArm")).normalize();
    const foreDir = wp("leftHand").sub(wp("leftLowerArm")).normalize();
    expect(armDir.x).toBeCloseTo(1, 4);
    expect(foreDir.x).toBeCloseTo(1, 4);
    const lateral = wp("leftIndexProximal").sub(wp("leftLittleProximal"));
    lateral.x = 0;
    lateral.normalize();
    expect(lateral.z).toBeGreaterThan(0.99); // thumb side forward = palm down
    const ref = captureReference(root, bones);
    expect(ref.basis.leftUpperArm).toBeDefined();
    expect(ref.referenceWorld.leftHand).toBeDefined();
  });
});

describe("pose library", () => {
  it("is left/right symmetric by construction", () => {
    const left = REST_POSE.leftUpperArm!;
    const right = REST_POSE.rightUpperArm!;
    expect(right).toEqual([left[0], -left[1], -left[2]]);
    const m = mirrorPose({ leftHand: [1, 2, 3], head: [4, 5, 6] });
    expect(m.rightHand).toEqual([1, -2, -3]);
    expect(m.head).toEqual([4, -5, -6]);
    expect(symmetric({ leftShoulder: [0, 0, 2] }).rightShoulder).toEqual([0, -0, -2]);
  });

  it("keyframed timelines hit their keys and ease monotonically between them", () => {
    const tl = keyframed(2, [
      { t: 0, pose: { head: [0, 0, 0] } },
      { t: 1, pose: { head: [10, 0, 0] } },
      { t: 2, pose: { head: [0, 0, 0] } },
    ]);
    expect(tl.sample(0).head).toEqual([0, 0, 0]);
    expect(tl.sample(1).head![0]).toBeCloseTo(10);
    let prev = -1;
    for (let t = 0; t <= 1; t += 0.05) {
      const v = tl.sample(t).head![0];
      expect(v).toBeGreaterThanOrEqual(prev - 1e-9);
      prev = v;
    }
  });

  it("gestures start and end at the rest pose and scale in joint space", () => {
    for (const [id, tl] of Object.entries(ARM_GESTURE_SPECS)) {
      for (const t of [0, tl.duration]) {
        const pose = tl.sample(t);
        for (const bone of tl.bones) {
          const rest = REST_POSE[bone] ?? [0, 0, 0];
          const v = pose[bone]!;
          for (let k = 0; k < 3; k++) expect(Math.abs(v[k]! - rest[k]!), `${id} ${bone} @${t}`).toBeLessThan(1e-6);
        }
      }
    }
    const tl = ARM_GESTURE_SPECS.one_hand_explain_r;
    const half = scaledTimeline(tl, 0.5);
    const full = tl.sample(1).rightLowerArm!;
    const scaled = half.sample(1).rightLowerArm!;
    const rest = REST_POSE.rightLowerArm!;
    for (let k = 0; k < 3; k++) expect(scaled[k]).toBeCloseTo(rest[k]! + (full[k]! - rest[k]!) * 0.5, 6);
  });

  it("breathing loops seamlessly", () => {
    const a = BREATHING_SPEC.sample(0);
    const b = BREATHING_SPEC.sample(BREATHING_SPEC.duration);
    for (const bone of BREATHING_SPEC.bones) {
      for (let k = 0; k < 3; k++) expect(a[bone]![k]).toBeCloseTo(b[bone]![k]!, 6);
    }
  });
});
