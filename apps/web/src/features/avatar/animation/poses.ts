/**
 * Pose library. Every pose is a set of Euler angles (degrees) per canonical
 * bone, expressed in the character frame of the canonical T-pose (see
 * rig/rig.ts) and applied about the bone's pivot relative to its parent:
 *
 *   spine / neck / head   rx + leans/nods forward, ry + turns to the character's
 *                         left, rz + tilts toward the character's right shoulder
 *   left upper arm (XYZ)  rz - lowers the arm, rx - swings it forward,
 *                         ry + rotates it outward (forearm swings out)
 *   left forearm  (YXZ)   ry - flexes the elbow (hand comes forward/up),
 *                         rx - supinates (palm turns up), rx + pronates
 *   left hand     (XYZ)   rz - flexes the wrist (palm side), rz + extends,
 *                         ry - radial deviation (toward the thumb)
 *   left fingers  (XYZ)   rz - curls toward the palm, ry spreads
 *   left shoulder (XYZ)   rz + shrugs, rz - drops, ry - protracts
 *
 * Right-side values are produced by mirroring (rx, -ry, -rz), so poses are
 * authored for the left side and stay symmetric by construction.
 */
import { BASE_POSE_DURATION, HEAD_GESTURE_DURATION, ARM_GESTURE_DURATION, type ArmGestureId, type BasePoseId, type HeadGestureId } from "./catalog";
import { mirrorEuler, smootherstep, type EulerDeg } from "../core/math";
import { mirrorBoneName, type HumanBoneName } from "../rig/humanoid";

export type PoseSpec = Partial<Record<HumanBoneName, EulerDeg>>;
type EulerOrder = "XYZ" | "YXZ" | "ZYX" | "XZY" | "YZX" | "ZXY";

/** Euler order per bone (default XYZ). Forearms twist about their own axis before flexing. */
export const BONE_EULER_ORDER: Partial<Record<HumanBoneName, EulerOrder>> = {
  leftLowerArm: "YXZ",
  rightLowerArm: "YXZ",
};

export function eulerOrderFor(bone: HumanBoneName): EulerOrder {
  return BONE_EULER_ORDER[bone] ?? "XYZ";
}

/** Copy left-side entries to the right side (mirrored); explicit right entries win. */
export function symmetric(pose: PoseSpec, rightScale = 1): PoseSpec {
  const out: PoseSpec = { ...pose };
  for (const [bone, e] of Object.entries(pose) as [HumanBoneName, EulerDeg][]) {
    if (!bone.startsWith("left")) continue;
    const right = mirrorBoneName(bone);
    if (pose[right]) continue;
    const m = mirrorEuler(e);
    out[right] = [m[0] * rightScale, m[1] * rightScale, m[2] * rightScale];
  }
  return out;
}

/** Mirror a whole pose (left <-> right). */
export function mirrorPose(pose: PoseSpec): PoseSpec {
  const out: PoseSpec = {};
  for (const [bone, e] of Object.entries(pose) as [HumanBoneName, EulerDeg][]) {
    out[mirrorBoneName(bone)] = mirrorEuler(e);
  }
  return out;
}

/** b overrides a per bone. */
export function overridePose(a: PoseSpec, b: PoseSpec): PoseSpec {
  return { ...a, ...b };
}

/** Per-bone Euler sum (small offsets layered on a pose). */
export function addPose(a: PoseSpec, b: PoseSpec): PoseSpec {
  const out: PoseSpec = { ...a };
  for (const [bone, e] of Object.entries(b) as [HumanBoneName, EulerDeg][]) {
    const base = out[bone] ?? [0, 0, 0];
    out[bone] = [base[0] + e[0], base[1] + e[1], base[2] + e[2]];
  }
  return out;
}

export function scalePose(a: PoseSpec, k: number): PoseSpec {
  const out: PoseSpec = {};
  for (const [bone, e] of Object.entries(a) as [HumanBoneName, EulerDeg][]) out[bone] = [e[0] * k, e[1] * k, e[2] * k];
  return out;
}

// ------------------------------------------------------------------ rest

const RELAXED_FINGERS_LEFT: PoseSpec = {
  leftIndexProximal: [0, -3, -12],
  leftIndexIntermediate: [0, 0, -16],
  leftIndexDistal: [0, 0, -10],
  leftMiddleProximal: [0, 0, -16],
  leftMiddleIntermediate: [0, 0, -20],
  leftMiddleDistal: [0, 0, -12],
  leftRingProximal: [0, 3, -19],
  leftRingIntermediate: [0, 0, -23],
  leftRingDistal: [0, 0, -14],
  leftLittleProximal: [0, 6, -23],
  leftLittleIntermediate: [0, 0, -25],
  leftLittleDistal: [0, 0, -15],
  // Thumb lifted into the palm plane and resting alongside the index finger.
  leftThumbMetacarpal: [-28, 16, 0],
  leftThumbProximal: [6, 8, 0],
  leftThumbDistal: [10, 6, 0],
};

const OPEN_FINGERS_LEFT: PoseSpec = {
  leftIndexProximal: [0, -1, -5],
  leftIndexIntermediate: [0, 0, -6],
  leftIndexDistal: [0, 0, -4],
  leftMiddleProximal: [0, 1, -6],
  leftMiddleIntermediate: [0, 0, -7],
  leftMiddleDistal: [0, 0, -4],
  leftRingProximal: [0, 3, -8],
  leftRingIntermediate: [0, 0, -9],
  leftRingDistal: [0, 0, -5],
  leftLittleProximal: [0, 5, -10],
  leftLittleIntermediate: [0, 0, -9],
  leftLittleDistal: [0, 0, -6],
  leftThumbMetacarpal: [-34, -6, 0],
  leftThumbProximal: [0, -4, 0],
  leftThumbDistal: [2, -4, 0],
};

/** Arms lowered naturally from the T-pose, soft elbows, relaxed hands. */
export const REST_POSE: PoseSpec = symmetric({
  leftShoulder: [0, 0, -5],
  leftUpperArm: [-5, 4, -76],
  leftLowerArm: [10, -16, 0],
  leftHand: [0, 4, -10],
  ...RELAXED_FINGERS_LEFT,
});

/** Bones every base clip animates (so crossfades never fall back to the bind pose). */
export const BASE_BONES: readonly HumanBoneName[] = [
  "hips",
  "spine",
  "chest",
  "upperChest",
  "neck",
  "head",
  ...(Object.keys(REST_POSE) as HumanBoneName[]),
];

// ------------------------------------------------------------------ specs

export interface Keyframe {
  t: number;
  pose: PoseSpec;
}

/** Time-sampled pose generator. `rest` fills bones a key does not mention. */
export interface PoseTimeline {
  duration: number;
  /** Bones the timeline animates. */
  bones: readonly HumanBoneName[];
  sample(t: number): PoseSpec;
}

const TAU = Math.PI * 2;
const wave = (t: number, period: number, phase = 0) => Math.sin((TAU * t) / period + phase);

/** Keyframed timeline with smootherstep easing between keys (in Euler space). */
export function keyframed(duration: number, keys: Keyframe[], rest: PoseSpec = {}): PoseTimeline {
  const sorted = [...keys].sort((a, b) => a.t - b.t);
  const boneSet = new Set<HumanBoneName>();
  for (const k of sorted) for (const b of Object.keys(k.pose) as HumanBoneName[]) boneSet.add(b);
  const bones = [...boneSet];
  const valueAt = (key: Keyframe, bone: HumanBoneName): EulerDeg => key.pose[bone] ?? rest[bone] ?? [0, 0, 0];
  return {
    duration,
    bones,
    sample(t: number): PoseSpec {
      const out: PoseSpec = {};
      if (sorted.length === 0) return out;
      let i = 0;
      while (i < sorted.length - 1 && sorted[i + 1]!.t <= t) i++;
      const a = sorted[i]!;
      const b = sorted[Math.min(i + 1, sorted.length - 1)]!;
      const span = b.t - a.t;
      const s = span > 0 ? smootherstep((t - a.t) / span) : 0;
      for (const bone of bones) {
        const va = valueAt(a, bone);
        const vb = valueAt(b, bone);
        out[bone] = [va[0] + (vb[0] - va[0]) * s, va[1] + (vb[1] - va[1]) * s, va[2] + (vb[2] - va[2]) * s];
      }
      return out;
    },
  };
}

/** Static pose plus periodic offsets (periods divide `duration` so it loops). */
export function periodic(duration: number, pose: PoseSpec, motion: (t: number) => PoseSpec): PoseTimeline {
  const probe = motion(0.37);
  const bones = [...new Set([...BASE_BONES, ...(Object.keys(pose) as HumanBoneName[]), ...(Object.keys(probe) as HumanBoneName[])])];
  return {
    duration,
    bones,
    sample: (t: number) => addPose(pose, motion(t)),
  };
}

// ------------------------------------------------------------------ base poses

const D = BASE_POSE_DURATION;

const idleSway = (scale: number) => (t: number): PoseSpec => ({
  spine: [0.35 * scale * wave(t, 12), 0.5 * scale * wave(t, 6, 1.1), 0.3 * scale * wave(t, 12, 2.3)],
  neck: [0.3 * scale * wave(t, 4, 0.4), 0.6 * scale * wave(t, 6, 2.0), 0.4 * scale * wave(t, 12, 0.7)],
  head: [0.4 * scale * wave(t, 3, 1.7), 0.5 * scale * wave(t, 4, 0.3), 0.3 * scale * wave(t, 6, 2.6)],
});

export const BASE_POSE_SPECS: Record<BasePoseId, PoseTimeline> = {
  idle_neutral: periodic(D.idle_neutral, REST_POSE, idleSway(1)),
  idle_shift: periodic(
    D.idle_shift,
    addPose(REST_POSE, {
      hips: [0, 1.2, 1.8],
      spine: [0, -0.6, -1.1],
      chest: [0, -0.4, -0.7],
      neck: [0, 0, 0.6],
      head: [0.5, 1.5, 1.2],
      leftUpperArm: [0, 0, -1.6],
      rightUpperArm: [0, 0, -1.2],
    }),
    idleSway(0.9),
  ),
  listening_attentive: periodic(
    D.listening_attentive,
    addPose(REST_POSE, {
      spine: [2.2, 0, 0],
      chest: [1.6, 0, 0],
      upperChest: [1.0, 0, 0],
      neck: [-1.6, 0, 0],
      head: [2.4, 0.5, -2.6],
      leftShoulder: [0, -1.5, 0],
      rightShoulder: [0, 1.5, 0],
    }),
    (t) => ({
      neck: [0.3 * wave(t, 5), 0.4 * wave(t, 10, 1.3), 0],
      head: [0.5 * wave(t, 5, 0.6), 0.6 * wave(t, 10, 2.1), 0.5 * wave(t, 10)],
      spine: [0.3 * wave(t, 10, 0.9), 0, 0.2 * wave(t, 5, 1.4)],
    }),
  ),
  listening_tilt: periodic(
    D.listening_tilt,
    addPose(REST_POSE, {
      spine: [2.0, 0.4, 0],
      chest: [1.5, 0, 0],
      upperChest: [0.8, 0, 0],
      neck: [-1.2, 1.2, 0.8],
      head: [2.0, 2.2, 3.4],
      leftShoulder: [0, -1.2, 0],
      rightShoulder: [0, 1.2, 0],
    }),
    (t) => ({
      neck: [0.3 * wave(t, 5, 0.3), 0.4 * wave(t, 10), 0],
      head: [0.5 * wave(t, 5, 1.1), 0.5 * wave(t, 10, 0.4), 0.4 * wave(t, 10, 2.2)],
      spine: [0.3 * wave(t, 10, 2.0), 0, 0],
    }),
  ),
  thinking: periodic(
    D.thinking,
    addPose(REST_POSE, {
      spine: [0.4, 0, 0],
      chest: [0, 0.8, 0],
      neck: [-1.0, 1.8, 0.6],
      head: [-2.6, 3.0, 3.6],
    }),
    (t) => ({
      head: [0.6 * wave(t, 8, 0.2), 0.8 * wave(t, 4, 1.0), 0.4 * wave(t, 8, 2.0)],
      neck: [0.3 * wave(t, 4, 2.4), 0, 0],
    }),
  ),
  speaking_neutral: periodic(
    D.speaking_neutral,
    addPose(REST_POSE, { spine: [1.0, 0, 0], chest: [0.6, 0, 0], head: [0.6, 0, 0] }),
    (t) => ({
      spine: [0.3 * wave(t, 8), 0.5 * wave(t, 4, 1.2), 0],
      neck: [0.4 * wave(t, 4, 0.5), 0.5 * wave(t, 8, 2.2), 0.3 * wave(t, 8, 0.8)],
      head: [0.5 * wave(t, 2, 1.0), 0.6 * wave(t, 4, 2.7), 0.5 * wave(t, 8, 1.9)],
    }),
  ),
  speaking_engaged: periodic(
    D.speaking_engaged,
    addPose(REST_POSE, {
      spine: [1.8, 0, 0],
      chest: [1.3, 0, 0],
      upperChest: [0.6, 0, 0],
      neck: [-0.6, 0, 0],
      head: [1.0, 0.6, -1.6],
    }),
    (t) => ({
      spine: [0.3 * wave(t, 8, 0.5), 0.6 * wave(t, 4, 0.2), 0],
      neck: [0.4 * wave(t, 4, 1.5), 0.6 * wave(t, 8, 0.6), 0.3 * wave(t, 8, 1.2)],
      head: [0.6 * wave(t, 2, 0.2), 0.7 * wave(t, 4, 1.4), 0.6 * wave(t, 8, 2.8)],
    }),
  ),
  interrupted_lean: periodic(
    D.interrupted_lean,
    addPose(REST_POSE, {
      spine: [3.4, 0, 0],
      chest: [2.8, 0, 0],
      upperChest: [1.4, 0, 0],
      neck: [-2.2, 0, 0],
      head: [2.6, 0, -1.4],
      leftShoulder: [0, -2, 0],
      rightShoulder: [0, 2, 0],
    }),
    (t) => ({ head: [0.3 * wave(t, 3), 0.3 * wave(t, 6, 1.0), 0] }),
  ),
  closing: periodic(
    D.closing,
    addPose(REST_POSE, { spine: [0.8, 0, 0], chest: [0.4, 0, 0], head: [1.2, 0.5, -2.2] }),
    idleSway(0.7),
  ),
};

// ------------------------------------------------------------------ gestures (additive vs REST)

const G = ARM_GESTURE_DURATION;
const at = (t: number, pose: PoseSpec): Keyframe => ({ t, pose });
const fingers = (open: number): PoseSpec => {
  // Blend relaxed (0) .. open (1) finger poses for the left hand.
  const out: PoseSpec = {};
  for (const bone of Object.keys(RELAXED_FINGERS_LEFT) as HumanBoneName[]) {
    const a = RELAXED_FINGERS_LEFT[bone]!;
    const b = OPEN_FINGERS_LEFT[bone] ?? a;
    out[bone] = [a[0] + (b[0] - a[0]) * open, a[1] + (b[1] - a[1]) * open, a[2] + (b[2] - a[2]) * open];
  }
  return out;
};
const leftArm = (upper: EulerDeg, lower: EulerDeg, hand: EulerDeg, open = 0.6, shoulder: EulerDeg = [0, 0, -5]): PoseSpec => ({
  leftShoulder: shoulder,
  leftUpperArm: upper,
  leftLowerArm: lower,
  leftHand: hand,
  ...fingers(open),
});

// Gesture poses (measured on the reference rig): elbows stay near the body
// (upper arm rz ≈ -74 with slight internal rotation) and the hands rise to
// chest height in front of the torso (x ≈ 0.16-0.2 m, y ≈ 1.25-1.33 m for a
// 1.78 m figure) so they enter the bust framing (bottom edge ≈ 1.2 m) without
// flaring outward. Palms-up gestures need ~100° of elbow flexion to clear it.

const smallOpenPalmLeft = keyframed(
  G.small_open_palm_l,
  [
    at(0, {}),
    at(0.6, leftArm([-28, -12, -74], [-80, -98, 0], [0, -6, 24], 0.95)),
    at(1.45, leftArm([-30, -13, -74], [-85, -104, 0], [0, -6, 30], 1)),
    at(G.small_open_palm_l, {}),
  ],
  REST_POSE,
);

const oneHandExplainLeft = keyframed(
  G.one_hand_explain_l,
  [
    at(0, {}),
    at(0.5, leftArm([-28, -12, -74], [-40, -100, 0], [0, -6, 8], 0.75)),
    at(0.95, leftArm([-29, -13, -73], [-44, -108, 0], [0, -4, 12], 0.85)),
    at(1.3, leftArm([-28, -12, -74], [-38, -96, 0], [0, -6, 3], 0.7)),
    at(1.75, leftArm([-30, -14, -73], [-46, -106, 0], [0, -5, 11], 0.85)),
    at(2.15, leftArm([-28, -12, -74], [-40, -99, 0], [0, -6, 5], 0.75)),
    at(G.one_hand_explain_l, {}),
  ],
  REST_POSE,
);

const twoHands = (upper: EulerDeg, lower: EulerDeg, hand: EulerDeg, open: number, rightScale = 1): PoseSpec =>
  symmetric(leftArm(upper, lower, hand, open), rightScale);

const twoHandExplainA = keyframed(
  G.two_hand_explain_a,
  [
    at(0, {}),
    at(0.6, twoHands([-24, -12, -74], [-60, -96, 0], [0, -8, 14], 0.85)),
    at(1.15, twoHands([-25, -6, -73], [-64, -100, 0], [0, -8, 16], 0.95, 0.97)),
    at(1.7, twoHands([-24, -16, -74], [-58, -95, 0], [0, -6, 12], 0.85)),
    at(2.25, twoHands([-25, -9, -73], [-62, -98, 0], [0, -8, 14], 0.9, 0.98)),
    at(G.two_hand_explain_a, {}),
  ],
  REST_POSE,
);

const twoHandExplainB = keyframed(
  G.two_hand_explain_b,
  [
    at(0, {}),
    at(0.65, twoHands([-26, -16, -74], [-10, -100, 0], [0, -4, 4], 0.7)),
    at(1.2, twoHands([-27, -11, -73], [-14, -106, 0], [0, -4, 8], 0.8)),
    at(1.75, twoHands([-26, -18, -74], [-10, -98, 0], [0, -4, 2], 0.7, 0.96)),
    at(2.3, twoHands([-27, -13, -73], [-12, -104, 0], [0, -4, 6], 0.75)),
    at(G.two_hand_explain_b, {}),
  ],
  REST_POSE,
);

const smallEmphasisLeft = keyframed(
  G.small_emphasis_l,
  [
    at(0, {}),
    at(0.45, leftArm([-28, -11, -74], [-30, -106, 0], [0, -2, 6], 0.45)),
    at(0.72, leftArm([-27, -11, -74], [-32, -95, 0], [0, -2, -10], 0.4)),
    at(0.98, leftArm([-28, -11, -74], [-30, -103, 0], [0, -2, 0], 0.45)),
    at(G.small_emphasis_l, {}),
  ],
  REST_POSE,
);

const questionA = keyframed(
  G.question_gesture_a,
  [
    at(0, {}),
    at(0.6, twoHands([-27, -6, -73], [-85, -99, 0], [0, -10, 26], 1)),
    at(1.0, { ...twoHands([-28, -4, -72], [-88, -102, 0], [0, -10, 28], 1), ...symmetric({ leftShoulder: [0, 0, -1] }) }),
    at(1.6, twoHands([-27, -5, -73], [-86, -100, 0], [0, -10, 26], 1)),
    at(G.question_gesture_a, {}),
  ],
  REST_POSE,
);

const questionB = keyframed(
  G.question_gesture_b,
  [
    at(0, { head: [0, 0, 0] }),
    at(0.6, { ...leftArm([-28, -12, -74], [-82, -100, 0], [0, -6, 28], 1), head: [0.5, -1.5, -4] }),
    at(1.6, { ...leftArm([-29, -13, -74], [-86, -103, 0], [0, -6, 30], 1), head: [0.5, -1.5, -4.5] }),
    at(G.question_gesture_b, { head: [0, 0, 0] }),
  ],
  REST_POSE,
);

// Wave beside the shoulder: forearm up and pronated so the palm faces the viewer;
// the wave itself is radial/ulnar deviation of the wrist.
const WAVE_UPPER: EulerDeg = [-25, 6, -55];
const WAVE_LOWER: EulerDeg = [60, -125, 0];
const wavePose = (dev: number): PoseSpec => leftArm(WAVE_UPPER, WAVE_LOWER, [0, dev, 20], 1, [0, 0, -2]);
const goodbyeLeft = keyframed(
  G.goodbye_wave,
  [
    at(0, {}),
    at(0.7, wavePose(0)),
    at(0.95, wavePose(16)),
    at(1.2, wavePose(-14)),
    at(1.45, wavePose(16)),
    at(1.7, wavePose(-12)),
    at(1.95, wavePose(6)),
    at(2.4, leftArm([-24, 6, -57], [58, -120, 0], [0, 0, 16], 1, [0, 0, -2])),
    at(G.goodbye_wave, {}),
  ],
  REST_POSE,
);

/** Mirror a timeline onto the other side. */
function mirrored(tl: PoseTimeline): PoseTimeline {
  return {
    duration: tl.duration,
    bones: tl.bones.map(mirrorBoneName),
    sample: (t) => mirrorPose(tl.sample(t)),
  };
}

/**
 * Scale a gesture's deviation from the rest pose in joint-angle (Euler) space.
 * Unlike an additive slerp weight this keeps partial gestures anatomically
 * plausible (each joint moves part of the way along its own axes).
 */
export function scaledTimeline(tl: PoseTimeline, k: number, rest: PoseSpec = REST_POSE): PoseTimeline {
  if (k === 1) return tl;
  return {
    duration: tl.duration,
    bones: tl.bones,
    sample(t: number): PoseSpec {
      const pose = tl.sample(t);
      const out: PoseSpec = {};
      for (const [bone, e] of Object.entries(pose) as [HumanBoneName, EulerDeg][]) {
        const r = rest[bone] ?? [0, 0, 0];
        out[bone] = [r[0] + (e[0] - r[0]) * k, r[1] + (e[1] - r[1]) * k, r[2] + (e[2] - r[2]) * k];
      }
      return out;
    },
  };
}

/** Gesture intensity levels: clips are pre-built per level and played at full weight. */
export const GESTURE_LEVELS = [
  { name: "low", scale: 0.8, maxIntensity: 0.6 },
  { name: "medium", scale: 0.9, maxIntensity: 0.85 },
  { name: "high", scale: 1.0, maxIntensity: Infinity },
] as const;
export type GestureLevel = (typeof GESTURE_LEVELS)[number]["name"];

export function gestureLevelFor(intensity: number): GestureLevel {
  return (GESTURE_LEVELS.find((l) => intensity <= l.maxIntensity) ?? GESTURE_LEVELS[2]).name;
}

export const ARM_GESTURE_SPECS: Record<ArmGestureId, PoseTimeline> = {
  small_open_palm_l: smallOpenPalmLeft,
  small_open_palm_r: mirrored(smallOpenPalmLeft),
  one_hand_explain_l: oneHandExplainLeft,
  one_hand_explain_r: mirrored(oneHandExplainLeft),
  two_hand_explain_a: twoHandExplainA,
  two_hand_explain_b: twoHandExplainB,
  small_emphasis_l: smallEmphasisLeft,
  small_emphasis_r: mirrored(smallEmphasisLeft),
  question_gesture_a: questionA,
  question_gesture_b: mirrored(questionB),
  // Most people wave with the right hand.
  goodbye_wave: mirrored(goodbyeLeft),
};

// ------------------------------------------------------------------ head gestures (additive vs REST)

const H = HEAD_GESTURE_DURATION;

export const HEAD_GESTURE_SPECS: Record<HeadGestureId, PoseTimeline> = {
  nod: keyframed(H.nod, [
    at(0, { head: [0, 0, 0], neck: [0, 0, 0] }),
    at(0.28, { head: [7, 0, 0], neck: [2, 0, 0] }),
    at(0.55, { head: [-1, 0, 0], neck: [0, 0, 0] }),
    at(0.78, { head: [4, 0, 0], neck: [1, 0, 0] }),
    at(H.nod, { head: [0, 0, 0], neck: [0, 0, 0] }),
  ]),
  nod_small: keyframed(H.nod_small, [
    at(0, { head: [0, 0, 0], neck: [0, 0, 0] }),
    at(0.28, { head: [4.5, 0, 0], neck: [1, 0, 0] }),
    at(H.nod_small, { head: [0, 0, 0], neck: [0, 0, 0] }),
  ]),
  backchannel_nod: keyframed(H.backchannel_nod, [
    at(0, { head: [0, 0, 0] }),
    at(0.3, { head: [3.5, 0, 0] }),
    at(0.55, { head: [0.5, 0, 0] }),
    at(0.72, { head: [2, 0, 0] }),
    at(H.backchannel_nod, { head: [0, 0, 0] }),
  ]),
  head_tilt: keyframed(H.head_tilt, [
    at(0, { head: [0, 0, 0], neck: [0, 0, 0] }),
    at(0.55, { head: [1, 2, 6], neck: [0, 1, 2] }),
    at(1.6, { head: [1, 2.5, 6.5], neck: [0, 1, 2] }),
    at(H.head_tilt, { head: [0, 0, 0], neck: [0, 0, 0] }),
  ]),
  positive_nod: keyframed(H.positive_nod, [
    at(0, { head: [0, 0, 0], neck: [0, 0, 0] }),
    at(0.32, { head: [6, 0, 1.5], neck: [1.5, 0, 0] }),
    at(0.62, { head: [0, 0, 1], neck: [0, 0, 0] }),
    at(0.85, { head: [3, 0, 0.5], neck: [0.5, 0, 0] }),
    at(H.positive_nod, { head: [0, 0, 0], neck: [0, 0, 0] }),
  ]),
};

// ------------------------------------------------------------------ breathing (additive loop)

export const BREATH_PERIOD = 4.2;

/** Inhale ~40% of the cycle, exhale ~60%: phase-warped sine in [0, 1]. */
export function breathCurve(t: number): number {
  const p = (((t / BREATH_PERIOD) % 1) + 1) % 1;
  const warped = p < 0.4 ? (p / 0.4) * 0.5 : 0.5 + ((p - 0.4) / 0.6) * 0.5;
  return 0.5 - 0.5 * Math.cos(TAU * warped);
}

export const BREATHING_SPEC: PoseTimeline = {
  duration: BREATH_PERIOD,
  bones: ["spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder"],
  sample(t: number): PoseSpec {
    const b = breathCurve(t);
    return symmetric({
      spine: [-0.25 * b, 0, 0],
      chest: [-0.55 * b, 0, 0],
      upperChest: [-0.45 * b, 0, 0],
      neck: [0.55 * b, 0, 0],
      head: [0.35 * b, 0, 0],
      leftShoulder: [0, 0, 0.7 * b],
    });
  },
};
