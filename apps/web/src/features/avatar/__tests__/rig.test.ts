import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { Quaternion } from "three";
import { buildClipLibrary } from "../animation/clips";
import { AvatarAnimator } from "../animation/animator";
import { BASE_BONES, GESTURE_LEVELS } from "../animation/poses";
import { ARM_GESTURES, BASE_POSES, HEAD_GESTURES } from "../animation/catalog";
import { AvatarEngine } from "../engine";
import { normalizeBoneName } from "../rig/humanoid";
import { canonicalMorphName, VISEME_MORPH_NAMES } from "../rig/morphs";
import { mapFaceToVrm } from "../rig/vrmRig";
import { createProceduralRig } from "../procedural/proceduralAvatar";
import { resolveSameOrigin } from "../rig/loader";
import type { AvatarRig } from "../rig/rig";

// jsdom has no canvas backend: stub getContext so procedural textures fall back to flat colours quietly.
const originalGetContext = HTMLCanvasElement.prototype.getContext;
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof originalGetContext;
});
afterAll(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

describe("bone and morph name normalisation", () => {
  it.each([
    ["mixamorig:LeftArm", "leftUpperArm"],
    ["mixamorigLeftArm", "leftUpperArm"],
    ["mixamorig_RightForeArm", "rightLowerArm"],
    ["Armature|Spine2", "upperChest"],
    ["Spine1", "chest"],
    ["Hips", "hips"],
    ["LeftHandThumb1", "leftThumbMetacarpal"],
    ["RightHandPinky3", "rightLittleDistal"],
    ["LeftHandIndex4", null],
    ["leftUpperArm", "leftUpperArm"],
    ["Jaw", "jaw"],
    ["PonytailRoot", null],
  ])("%s -> %s", (raw, expected) => {
    expect(normalizeBoneName(raw)).toBe(expected);
  });

  it.each([
    ["blendShape1.jawOpen", "jawOpen"],
    ["JawOpen", "jawOpen"],
    ["eyeBlink_L", "eyeBlinkLeft"],
    ["viseme_aa", "viseme_aa"],
    ["mouthSmile_R", "mouthSmileRight"],
    ["customThing", "customThing"],
  ])("morph %s -> %s", (raw, expected) => {
    expect(canonicalMorphName(raw)).toBe(expected);
  });
});

describe("same-origin asset policy", () => {
  it("accepts same-origin paths and rejects other origins", () => {
    const base = "https://interview.example.com/candidate/room";
    expect(resolveSameOrigin("/avatars/interviewer.glb", base).pathname).toBe("/avatars/interviewer.glb");
    expect(() => resolveSameOrigin("https://cdn.example.net/a.glb", base)).toThrow(/same origin/);
    expect(() => resolveSameOrigin("data:model/gltf-binary;base64,AAAA", base)).toThrow();
    expect(() => resolveSameOrigin("javascript:alert(1)", base)).toThrow();
  });
});

describe("VRM expression mapping", () => {
  const available = new Set(["aa", "ih", "ou", "ee", "oh", "blink", "happy"]);
  it("maps visemes and smiles onto VRM presets, clamped", () => {
    const out = mapFaceToVrm(
      { viseme_aa: 0.8, viseme_DD: 0.5, mouthSmileLeft: 0.6, mouthSmileRight: 0.6, browInnerUp: 0.4 },
      new Set(),
      available,
      new Map(),
    );
    expect(out.get("aa")).toBeCloseTo(0.8 + 0.15 * 0.5);
    expect(out.get("ih")).toBeCloseTo(0.4 * 0.5);
    expect(out.get("happy")).toBeCloseTo(0.6);
    expect(out.has("browInnerUp")).toBe(false);
  });

  it("falls back to a single blink and passes perfect-sync names through", () => {
    const out = mapFaceToVrm({ eyeBlinkLeft: 1, eyeBlinkRight: 0.6, jawOpen: 0.3 }, new Set(["jawOpen"]), available, new Map());
    expect(out.get("blink")).toBe(1);
    expect(out.get("jawOpen")).toBeCloseTo(0.3);
    expect(out.has("aa")).toBe(false);
  });
});

describe("procedural avatar rig", () => {
  let rig: AvatarRig;
  beforeAll(() => {
    rig = createProceduralRig();
  });

  it("exposes a humanoid skeleton, metrics and the facial morph vocabulary", () => {
    for (const b of ["hips", "spine", "chest", "upperChest", "neck", "head", "leftEye", "rightEye", "leftUpperArm", "rightHand", "leftIndexDistal"]) {
      expect(rig.bones[b as keyof typeof rig.bones], b).toBeDefined();
    }
    for (const name of [...VISEME_MORPH_NAMES, "eyeBlinkLeft", "eyeBlinkRight", "mouthSmileLeft", "browInnerUp", "jawOpen"]) {
      expect(rig.morphs.has(name), name).toBe(true);
    }
    expect(rig.metrics.eyeY).toBeGreaterThan(1.5);
    expect(rig.metrics.headTopY).toBeGreaterThan(rig.metrics.eyeY);
    expect(rig.metrics.shoulderWidth).toBeGreaterThan(0.3);
  });

  it("fans one morph out to every mesh that carries it", () => {
    const bindings = rig.morphs.bindings("viseme_aa");
    expect(bindings.length).toBeGreaterThanOrEqual(3); // patch, cavity, teeth
    rig.morphs.set("viseme_aa", 0.7);
    for (const { mesh, index } of bindings) expect(mesh.morphTargetInfluences![index]).toBeCloseTo(0.7);
    rig.morphs.set("viseme_aa", 2);
    expect(rig.morphs.get("viseme_aa")).toBe(1);
    rig.morphs.reset();
    expect(rig.morphs.get("viseme_aa")).toBe(0);
    expect(rig.morphs.set("notAMorph", 1)).toBe(false);
  });

  it("builds clips: complete base layers, additive gestures that start and end at identity", () => {
    const lib = buildClipLibrary(rig);
    for (const id of BASE_POSES) {
      const names = new Set(lib.base[id].tracks.map((t) => t.name.split(".")[0]));
      for (const bone of BASE_BONES) {
        const node = rig.bones[bone];
        if (node) expect(names.has(node.uuid), `${id}:${bone}`).toBe(true);
      }
    }
    const identity = new Quaternion();
    for (const id of ARM_GESTURES) {
      for (const level of GESTURE_LEVELS) {
        const clip = lib.arm[id][level.name];
        expect(clip.blendMode).toBe(2501); // AdditiveAnimationBlendMode
        for (const track of clip.tracks) {
          const v = track.values;
          const first = new Quaternion(v[0], v[1], v[2], v[3]);
          const last = new Quaternion(v[v.length - 4], v[v.length - 3], v[v.length - 2], v[v.length - 1]);
          expect(first.angleTo(identity), `${id} start`).toBeLessThan(1e-3);
          expect(last.angleTo(identity), `${id} end`).toBeLessThan(1e-3);
          for (let i = 0; i < v.length; i += 4) {
            expect(Math.hypot(v[i]!, v[i + 1]!, v[i + 2]!, v[i + 3]!)).toBeCloseTo(1, 4);
          }
        }
      }
    }
    for (const id of HEAD_GESTURES) expect(lib.head[id].tracks.length).toBeGreaterThan(0);
  });

  it("animates without errors: rest pose lowers the arms, gestures raise the hands", () => {
    let t = 100;
    const engine = new AvatarEngine({ seed: 1, wallClock: () => t });
    const animator = new AvatarAnimator(rig);
    const camera = rig.metrics.eyeCenter.clone().setZ(1.6);
    for (let i = 0; i < 30; i++) {
      t += 1 / 30;
      animator.update(engine.update(), camera);
    }
    const handY = () => rig.bones.rightHand!.getWorldPosition(camera.clone()).y;
    const restHand = handY();
    expect(restHand).toBeLessThan(rig.metrics.chestY - 0.15);
    engine.behavior.applyBehavior({ state: "SPEAKING", gesture: "one_hand_explain", gesture_intensity: "high" }, t);
    let maxHand = restHand;
    for (let i = 0; i < 60; i++) {
      t += 1 / 30;
      animator.update(engine.update(), camera);
      maxHand = Math.max(maxHand, handY(), rig.bones.leftHand!.getWorldPosition(camera.clone()).y);
    }
    expect(maxHand).toBeGreaterThan(restHand + 0.2);
    animator.dispose();
  });
});
