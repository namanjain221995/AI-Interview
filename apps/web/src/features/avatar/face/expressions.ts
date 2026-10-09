/**
 * Facial expression presets for the interviewer (ARKit blendshape weights at
 * full intensity) and a smoothing mixer. These describe the avatar's own
 * displayed expression only; nothing here reads or infers anything about the
 * candidate.
 */
import type { AvatarExpression } from "@/lib/realtime/protocol";
import { damp } from "../core/math";

export type ExpressionPreset = Readonly<Record<string, number>>;

const smile = (v: number) => ({ mouthSmileLeft: v, mouthSmileRight: v });
const cheeks = (v: number) => ({ cheekSquintLeft: v, cheekSquintRight: v });
const squint = (v: number) => ({ eyeSquintLeft: v, eyeSquintRight: v });

export const EXPRESSION_PRESETS: Record<AvatarExpression, ExpressionPreset> = {
  // A relaxed, approachable resting face rather than a blank stare.
  neutral: { ...smile(0.12), browInnerUp: 0.04 },
  warm: { ...smile(0.48), ...cheeks(0.2), ...squint(0.1), browInnerUp: 0.08 },
  curious: { ...smile(0.12), browInnerUp: 0.45, browOuterUpLeft: 0.38, browOuterUpRight: 0.2, eyeWideLeft: 0.12, eyeWideRight: 0.12 },
  thoughtful: {
    browInnerUp: 0.22,
    browDownLeft: 0.22,
    browDownRight: 0.12,
    mouthPressLeft: 0.22,
    mouthPressRight: 0.22,
    mouthRollLower: 0.1,
    ...squint(0.16),
  },
  encouraging: { ...smile(0.56), ...cheeks(0.24), ...squint(0.08), browInnerUp: 0.26, browOuterUpLeft: 0.12, browOuterUpRight: 0.12 },
  serious: { browDownLeft: 0.26, browDownRight: 0.26, mouthPressLeft: 0.16, mouthPressRight: 0.16, ...squint(0.08) },
  pleased: { ...smile(0.68), ...cheeks(0.36), ...squint(0.16), browOuterUpLeft: 0.08, browOuterUpRight: 0.08 },
};

export const EXPRESSION_CHANNELS: readonly string[] = [
  ...new Set(Object.values(EXPRESSION_PRESETS).flatMap((p) => Object.keys(p))),
];

/** Per-channel exponential blend toward the active preset (no snapping). */
export class ExpressionMixer {
  readonly weights: Record<string, number> = {};

  constructor() {
    for (const ch of EXPRESSION_CHANNELS) this.weights[ch] = 0;
  }

  update(expression: AvatarExpression, intensity: number, dt: number): Record<string, number> {
    const preset = EXPRESSION_PRESETS[expression] ?? EXPRESSION_PRESETS.neutral;
    for (const ch of EXPRESSION_CHANNELS) {
      const target = (preset[ch] ?? 0) * intensity;
      const cur = this.weights[ch]!;
      // ~0.22 s onset, ~0.3 s release.
      this.weights[ch] = damp(cur, target, target > cur ? 4.5 : 3.4, dt);
    }
    return this.weights;
  }
}
