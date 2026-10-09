/**
 * System-check result for the on-device face check. It only ever warns (never
 * blocks the candidate): a detector can fail on some faces, in some light,
 * and a person - not this check - decides anything about the interview.
 */
import { FACE_GUIDANCE_COPY, FACE_OK_COPY } from "@/features/integrity/face/guidance";
import type { FaceMonitorStatus } from "@/features/integrity/face/monitor";
import type { FaceGuidance } from "@/features/integrity/face/states";
import { faceCheckPolicyOn, type ConsentFlags } from "@/features/integrity/policy";
import type { IntegrityPolicy } from "@/lib/types";

import type { CheckResult } from "./checks";

export interface FaceCheckSnapshot {
  status: FaceMonitorStatus;
  guidance: FaceGuidance | null;
  counted: number;
  /** One face clearly visible, without guidance, for long enough. */
  steady: boolean;
}

export function faceCheckResult(snapshot: FaceCheckSnapshot | null, cameraWorks: boolean): CheckResult {
  if (!cameraWorks) return { state: "pending", summary: "Checked once your camera works" };
  if (!snapshot || snapshot.status === "idle" || snapshot.status === "loading") return { state: "running", summary: "Looking for your face…" };
  if (snapshot.status === "unsupported" || snapshot.status === "error") {
    return {
      state: "warn",
      summary: "This browser can't run the face check on your device",
      detail: "The interview works without it. A recent Chrome, Edge, Firefox or Safari supports it.",
    };
  }
  if (snapshot.status === "paused") return { state: "running", summary: "The face check resumes when this tab is visible" };
  if (snapshot.steady) return { state: "pass", summary: FACE_OK_COPY.replace(/\.$/, "") };
  if (snapshot.guidance) {
    return {
      state: "warn",
      summary: FACE_GUIDANCE_COPY[snapshot.guidance],
      detail: "This is only a tip to help the interview go smoothly; you can continue either way.",
    };
  }
  return { state: "running", summary: "Hold still for a moment…" };
}

/** The face check runs in the system check only when the policy has it on and the consent covered it. */
export function faceCheckAllowed(
  integrity: Partial<IntegrityPolicy> | null | undefined,
  consent: ConsentFlags | null,
  disclosure: { consented: boolean; items: Array<{ key: string; enabled: boolean }> } | null | undefined,
): boolean {
  if (!faceCheckPolicyOn(integrity)) return false;
  if (consent && consent.face_check === true && consent.camera !== false) return true;
  return Boolean(disclosure?.consented && disclosure.items.some((item) => item.key === "face_check" && item.enabled));
}
