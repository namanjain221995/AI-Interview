/**
 * Candidate-facing copy for the on-device face check: short, friendly, about
 * the camera and the room - never about the person.
 */
import type { FaceGuidance } from "./states";

export const FACE_GUIDANCE_COPY: Record<FaceGuidance, string> = {
  no_face: "We can't see your face. Sit facing the camera, not too far from it.",
  multiple_faces: "Only you should be in view.",
  partly_out: "Move into the middle of the picture.",
  too_close: "Move back a little.",
  too_far: "Move a little closer.",
  low_light: "Add some light in front of you.",
};

export const FACE_OK_COPY = "Your face is clearly visible.";

/** Framing tips worth a gentle notice in the room (presence itself is handled by the server's reminders). */
export const ROOM_TIP_KINDS: ReadonlySet<FaceGuidance> = new Set<FaceGuidance>(["partly_out", "too_close", "too_far", "low_light"]);

/**
 * Decides when a framing tip is shown during the interview: only once the
 * same tip has lasted `persistMs`, at most once per `cooldownMs` per kind.
 * Feed it every guidance change.
 */
export class GuidanceThrottle {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly shownAt = new Map<FaceGuidance, number>();

  constructor(
    private readonly persistMs = 8_000,
    private readonly cooldownMs = 120_000,
    private readonly kinds: ReadonlySet<FaceGuidance> = ROOM_TIP_KINDS,
    private readonly now: () => number = () => Date.now(),
  ) {}

  change(guidance: FaceGuidance | null, notify: (kind: FaceGuidance) => void): void {
    this.clear();
    if (!guidance || !this.kinds.has(guidance)) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      const last = this.shownAt.get(guidance);
      const t = this.now();
      if (last !== undefined && t - last < this.cooldownMs) return;
      this.shownAt.set(guidance, t);
      notify(guidance);
    }, this.persistMs);
  }

  clear(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
