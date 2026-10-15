/** Labels and badge tones for open links, registrations and round chips. */

import type { BadgeTone } from "@/components/ui/Badge";

import type { AdvanceSkipCode, FlagCode, LinkState, LinkStatus, Progression, RegistrationStatus, RoundState } from "./types";

export const LINK_STATE_LABELS: Record<LinkState, string> = {
  open: "Open",
  scheduled: "Scheduled",
  paused: "Paused",
  closed: "Closed",
  expired: "Ended",
  full: "Full",
};

export const LINK_STATE_TONES: Record<LinkState, BadgeTone> = {
  open: "success",
  scheduled: "accent",
  paused: "warning",
  closed: "outline",
  expired: "outline",
  full: "neutral",
};

export const LINK_STATUS_LABELS: Record<LinkStatus, string> = { active: "Active", paused: "Paused", closed: "Closed" };

export const PROGRESSION_LABELS: Record<Progression, string> = {
  immediate: "Continue immediately",
  after_review: "After a person reviews",
};

export const REGISTRATION_STATUS_LABELS: Record<RegistrationStatus, string> = {
  registered: "Ready to start",
  in_progress: "In progress",
  awaiting_review: "Awaiting review",
  completed: "Completed",
  expired: "Expired",
  cancelled: "Cancelled",
  blocked: "Blocked",
};

export const REGISTRATION_STATUS_TONES: Record<RegistrationStatus, BadgeTone> = {
  registered: "accent",
  in_progress: "signal",
  awaiting_review: "warning",
  completed: "success",
  expired: "outline",
  cancelled: "outline",
  blocked: "danger",
};

export const STATUS_REASON_LABELS: Record<string, string> = {
  after_review: "Waiting for a person to advance",
  policy_termination: "Round ended by an integrity rule",
  technical_failure: "Round ended by a technical problem",
  device_policy: "Same device as another registration",
  identity_unverified: "Email matches an existing candidate: confirm it's them before allowing",
  recruiter_cancelled: "Cancelled by the hiring team",
  recruiter_allowed: "Allowed by the hiring team",
  job_not_open: "Next round not unlocked: the job is closed",
  candidate_archived: "Next round not unlocked: candidate archived",
  already_invited: "Next round not unlocked: has another invitation",
  round_missing: "Next round no longer exists",
};

export const ROUND_STATE_LABELS: Record<RoundState, string> = {
  locked: "Locked",
  invited: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  expired: "Expired",
  cancelled: "Cancelled",
};

export const ROUND_STATE_TONES: Record<RoundState, BadgeTone> = {
  locked: "outline",
  invited: "accent",
  in_progress: "signal",
  completed: "success",
  expired: "neutral",
  cancelled: "neutral",
};

export const FLAG_LABELS: Record<FlagCode, string> = {
  same_device: "Same device",
  same_resume: "Same résumé",
  same_network: "Same network",
};

export const ADVANCE_SKIP_LABELS: Record<AdvanceSkipCode, string> = {
  not_awaiting_review: "not waiting for review",
  last_round: "already on the last round",
  candidate_archived: "candidate archived",
  already_invited: "already has an invitation",
  round_missing: "next round no longer exists",
  not_found: "not found",
};

export function labelOr<K extends string>(labels: Partial<Record<K, string>>, value: string | null | undefined, fallback = ""): string {
  if (!value) return fallback;
  return labels[value as K] ?? value.replace(/_/g, " ");
}

/** "Needs review": a high-severity flag still open, or waiting for a person to advance. */
export function needsReview(reg: { status: RegistrationStatus; flag_review: string; flags: Array<{ severity: string }> }): boolean {
  return reg.status === "awaiting_review" || (reg.flag_review === "open" && reg.flags.some((f) => f.severity === "high"));
}
