/**
 * VRM rig adapter (three-vrm 3): drives the normalized humanoid bones (VRM
 * T-pose, identity rest rotations; VRM 0.x is rotated to face +Z), maps
 * ARKit / Oculus viseme names onto VRM expression presets (aa ih ou ee oh,
 * blink, happy...) unless the model ships "perfect sync" custom expressions
 * with the ARKit names, and uses VRM's own look-at for the eyes.
 */
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";
import { Object3D, type Vector3 } from "three";
import { HUMAN_BONES, type HumanBoneName } from "./humanoid";
import { MorphDictionary } from "./morphs";
import { captureReference, measureRig, type AvatarRig, type BoneMap, type FaceWeights } from "./rig";

type VrmUtils = typeof import("@pixiv/three-vrm").VRMUtils;

/** ARKit / viseme name -> weighted VRM preset contributions. */
export const VRM_EXPRESSION_MAP: Record<string, ReadonlyArray<readonly [string, number]>> = {
  viseme_aa: [["aa", 1]],
  viseme_E: [["ee", 1]],
  viseme_I: [["ih", 1]],
  viseme_O: [["oh", 1]],
  viseme_U: [["ou", 1]],
  viseme_FF: [["ih", 0.25]],
  viseme_TH: [
    ["ih", 0.3],
    ["aa", 0.15],
  ],
  viseme_DD: [
    ["ih", 0.4],
    ["aa", 0.15],
  ],
  viseme_kk: [
    ["aa", 0.35],
    ["ih", 0.25],
  ],
  viseme_CH: [
    ["ou", 0.35],
    ["ih", 0.3],
  ],
  viseme_SS: [["ih", 0.5]],
  viseme_nn: [
    ["aa", 0.25],
    ["ih", 0.25],
  ],
  viseme_RR: [["ou", 0.45]],
  jawOpen: [["aa", 0.5]],
  eyeBlinkLeft: [["blinkLeft", 1]],
  eyeBlinkRight: [["blinkRight", 1]],
  mouthSmileLeft: [["happy", 0.5]],
  mouthSmileRight: [["happy", 0.5]],
};

/**
 * Pure mapping used by the adapter (exported for tests): accumulates VRM
 * expression values from ARKit/viseme weights. Names present in `direct`
 * (custom expressions with ARKit names) pass through unchanged.
 */
export function mapFaceToVrm(
  weights: Readonly<FaceWeights>,
  direct: ReadonlySet<string>,
  available: ReadonlySet<string>,
  out: Map<string, number>,
): Map<string, number> {
  out.clear();
  for (const name in weights) {
    const w = weights[name]!;
    if (w <= 0) continue;
    if (direct.has(name)) {
      out.set(name, Math.min(1, (out.get(name) ?? 0) + w));
      continue;
    }
    const targets = VRM_EXPRESSION_MAP[name];
    if (!targets) continue;
    for (const [preset, gain] of targets) {
      let key = preset;
      // Models without per-eye blinks only have "blink".
      if ((preset === "blinkLeft" || preset === "blinkRight") && !available.has(preset)) key = "blink";
      if (!available.has(key)) continue;
      const v = key === "blink" ? Math.max(out.get(key) ?? 0, w * gain) : (out.get(key) ?? 0) + w * gain;
      out.set(key, Math.min(1, v));
    }
  }
  return out;
}

export function createVrmRig(gltf: GLTF, utils: VrmUtils, options: { name?: string } = {}): AvatarRig {
  const vrm = gltf.userData.vrm as VRM | undefined;
  if (!vrm) throw new Error("file is not a VRM (no VRM extension found)");

  utils.removeUnnecessaryVertices(gltf.scene);
  utils.combineSkeletons(gltf.scene);
  utils.rotateVRM0(vrm);
  vrm.scene.traverse((obj) => {
    obj.frustumCulled = false;
  });

  const container = new Object3D();
  container.name = "vrm-root";
  container.add(vrm.scene);

  vrm.humanoid.resetNormalizedPose();
  const bones: BoneMap = {};
  for (const name of HUMAN_BONES) {
    const node = vrm.humanoid.getNormalizedBoneNode(name as VRMHumanBoneName);
    if (node) bones[name as HumanBoneName] = node;
  }
  if (!bones.head || !bones.leftUpperArm || !bones.rightUpperArm) throw new Error("VRM humanoid is missing required bones");
  const reference = captureReference(container, bones);
  vrm.update(0);
  const metrics = measureRig(container, bones);

  const manager = vrm.expressionManager;
  const available = new Set<string>(manager ? Object.keys(manager.expressionMap) : []);
  const direct = new Set<string>(manager ? Object.keys(manager.customExpressionMap) : []);
  const values = new Map<string, number>();
  const touched = new Set<string>();
  if (vrm.lookAt) {
    vrm.lookAt.autoUpdate = false;
    vrm.lookAt.target = null;
  }

  return {
    kind: "vrm",
    name: options.name ?? "vrm",
    root: container,
    bones,
    basis: reference.basis,
    referenceWorld: reference.referenceWorld,
    metrics,
    morphs: new MorphDictionary(),
    gazeMode: vrm.lookAt ? "native" : "morphs",
    applyFace(weights: Readonly<FaceWeights>) {
      if (!manager) return;
      mapFaceToVrm(weights, direct, available, values);
      for (const name of touched) if (!values.has(name)) manager.setValue(name, 0);
      touched.clear();
      for (const [name, v] of values) {
        manager.setValue(name, v);
        touched.add(name);
      }
    },
    setGaze(_yaw: number, _pitch: number, worldTarget: Vector3) {
      vrm.lookAt?.lookAt(worldTarget);
    },
    update(dt: number) {
      vrm.update(dt);
    },
    dispose() {
      utils.deepDispose(vrm.scene);
    },
  };
}
