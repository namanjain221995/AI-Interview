/**
 * Animation vocabulary shared by the behaviour controller (pure logic) and the
 * clip builder (three.js). Pure data, no rendering dependencies.
 */

export const BASE_POSES = [
  "idle_neutral",
  "idle_shift",
  "listening_attentive",
  "listening_tilt",
  "thinking",
  "speaking_neutral",
  "speaking_engaged",
  "interrupted_lean",
  "closing",
] as const;
export type BasePoseId = (typeof BASE_POSES)[number];

export const ARM_GESTURES = [
  "small_open_palm_r",
  "small_open_palm_l",
  "one_hand_explain_r",
  "one_hand_explain_l",
  "two_hand_explain_a",
  "two_hand_explain_b",
  "small_emphasis_r",
  "small_emphasis_l",
  "question_gesture_a",
  "question_gesture_b",
  "goodbye_wave",
] as const;
export type ArmGestureId = (typeof ARM_GESTURES)[number];

export const HEAD_GESTURES = ["nod", "nod_small", "backchannel_nod", "head_tilt", "positive_nod"] as const;
export type HeadGestureId = (typeof HEAD_GESTURES)[number];

/** Clip lengths in seconds at timeScale 1 (the clip builder uses the same values). */
export const ARM_GESTURE_DURATION: Record<ArmGestureId, number> = {
  small_open_palm_r: 2.3,
  small_open_palm_l: 2.3,
  one_hand_explain_r: 2.9,
  one_hand_explain_l: 2.9,
  two_hand_explain_a: 3.1,
  two_hand_explain_b: 3.1,
  small_emphasis_r: 1.6,
  small_emphasis_l: 1.6,
  question_gesture_a: 2.5,
  question_gesture_b: 2.5,
  goodbye_wave: 3.2,
};

export const HEAD_GESTURE_DURATION: Record<HeadGestureId, number> = {
  nod: 1.15,
  nod_small: 0.85,
  backchannel_nod: 0.9,
  head_tilt: 2.2,
  positive_nod: 1.3,
};

/** Base loop lengths (seconds); periodic motion inside each clip divides these. */
export const BASE_POSE_DURATION: Record<BasePoseId, number> = {
  idle_neutral: 12,
  idle_shift: 12,
  listening_attentive: 10,
  listening_tilt: 10,
  thinking: 8,
  speaking_neutral: 8,
  speaking_engaged: 8,
  interrupted_lean: 6,
  closing: 8,
};
