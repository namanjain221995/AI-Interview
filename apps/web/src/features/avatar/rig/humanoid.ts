/**
 * Canonical humanoid bone vocabulary (VRM 1.0 names) and name normalisation
 * for common GLB rigs: Mixamo ("mixamorig:Hips", sanitised by GLTFLoader to
 * "mixamorigHips"), Ready Player Me (Mixamo names without prefix) and
 * MPFB/MakeHuman exports (same names plus Jaw/Eye bones).
 */

export const HUMAN_BONES = [
  "hips",
  "spine",
  "chest",
  "upperChest",
  "neck",
  "head",
  "jaw",
  "leftEye",
  "rightEye",
  "leftShoulder",
  "leftUpperArm",
  "leftLowerArm",
  "leftHand",
  "rightShoulder",
  "rightUpperArm",
  "rightLowerArm",
  "rightHand",
  "leftThumbMetacarpal",
  "leftThumbProximal",
  "leftThumbDistal",
  "leftIndexProximal",
  "leftIndexIntermediate",
  "leftIndexDistal",
  "leftMiddleProximal",
  "leftMiddleIntermediate",
  "leftMiddleDistal",
  "leftRingProximal",
  "leftRingIntermediate",
  "leftRingDistal",
  "leftLittleProximal",
  "leftLittleIntermediate",
  "leftLittleDistal",
  "rightThumbMetacarpal",
  "rightThumbProximal",
  "rightThumbDistal",
  "rightIndexProximal",
  "rightIndexIntermediate",
  "rightIndexDistal",
  "rightMiddleProximal",
  "rightMiddleIntermediate",
  "rightMiddleDistal",
  "rightRingProximal",
  "rightRingIntermediate",
  "rightRingDistal",
  "rightLittleProximal",
  "rightLittleIntermediate",
  "rightLittleDistal",
  "leftUpperLeg",
  "leftLowerLeg",
  "leftFoot",
  "rightUpperLeg",
  "rightLowerLeg",
  "rightFoot",
] as const;

export type HumanBoneName = (typeof HUMAN_BONES)[number];

const HUMAN_BONE_SET: ReadonlySet<string> = new Set(HUMAN_BONES);

export function isHumanBoneName(name: string): name is HumanBoneName {
  return HUMAN_BONE_SET.has(name);
}

export type Side = "left" | "right";

/** The bone on the opposite side (identity for centre bones). */
export function mirrorBoneName(name: HumanBoneName): HumanBoneName {
  if (name.startsWith("left")) return ("right" + name.slice(4)) as HumanBoneName;
  if (name.startsWith("right")) return ("left" + name.slice(5)) as HumanBoneName;
  return name;
}

export const FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"] as const;
export type FingerName = (typeof FINGERS)[number];

/** Finger chain from proximal to distal for one side. */
export function fingerChain(side: Side, finger: FingerName): HumanBoneName[] {
  const segs = finger === "Thumb" ? ["Metacarpal", "Proximal", "Distal"] : ["Proximal", "Intermediate", "Distal"];
  return segs.map((s) => `${side}${finger}${s}` as HumanBoneName);
}

/** Mixamo-style base names (prefix stripped) -> canonical bone. */
const MIXAMO_MAP: Record<string, HumanBoneName> = {
  hips: "hips",
  spine: "spine",
  spine1: "chest",
  spine2: "upperChest",
  neck: "neck",
  head: "head",
  jaw: "jaw",
  lefteye: "leftEye",
  righteye: "rightEye",
  eyeleft: "leftEye",
  eyeright: "rightEye",
  leftshoulder: "leftShoulder",
  leftarm: "leftUpperArm",
  leftforearm: "leftLowerArm",
  lefthand: "leftHand",
  rightshoulder: "rightShoulder",
  rightarm: "rightUpperArm",
  rightforearm: "rightLowerArm",
  righthand: "rightHand",
  leftupleg: "leftUpperLeg",
  leftleg: "leftLowerLeg",
  leftfoot: "leftFoot",
  rightupleg: "rightUpperLeg",
  rightleg: "rightLowerLeg",
  rightfoot: "rightFoot",
};

const FINGER_ALIASES: Record<string, FingerName> = {
  thumb: "Thumb",
  index: "Index",
  middle: "Middle",
  ring: "Ring",
  pinky: "Little",
  little: "Little",
};

/**
 * Normalise a node name from a GLB rig to a canonical humanoid bone, or null.
 * Handles "mixamorig:", "mixamorig_", "mixamorig" and "Armature|" prefixes,
 * underscores/dots/spaces, and Mixamo finger numbering (1..3).
 */
export function normalizeBoneName(raw: string): HumanBoneName | null {
  if (!raw) return null;
  let name = raw.trim();
  const pipe = name.lastIndexOf("|");
  if (pipe >= 0) name = name.slice(pipe + 1);
  name = name.replace(/^mixamorig[:_]?\d*[:_]?/i, "");
  const key = name.replace(/[\s_.:-]/g, "").toLowerCase();
  if (key in MIXAMO_MAP) return MIXAMO_MAP[key]!;

  const finger = /^(left|right)hand(thumb|index|middle|ring|pinky|little)([1-4])$/.exec(key);
  if (finger) {
    const side = finger[1] as Side;
    const which = FINGER_ALIASES[finger[2]!]!;
    const n = Number(finger[3]);
    if (n > 3) return null; // *4 are end sites
    const chain = fingerChain(side, which);
    return chain[n - 1] ?? null;
  }
  // Already canonical (e.g. "leftUpperArm" from a VRM-like export)?
  const canonical = HUMAN_BONES.find((b) => b.toLowerCase() === key);
  return canonical ?? null;
}
