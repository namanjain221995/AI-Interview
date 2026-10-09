import { CircleCheck, CircleHelp, CirclePause, CircleX } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { REVIEW_DECISION_LABELS } from "@/lib/labels";
import type { Recommendation, ReviewDecisionValue } from "@/lib/types";

const TONES: Record<ReviewDecisionValue, BadgeTone> = {
  advance: "success",
  hold: "warning",
  reject: "danger",
  needs_more_information: "accent",
};

const ICONS: Record<ReviewDecisionValue, ComponentType<SVGProps<SVGSVGElement>>> = {
  advance: CircleCheck,
  hold: CirclePause,
  reject: CircleX,
  needs_more_information: CircleHelp,
};

export const DECISION_DESCRIPTIONS: Record<ReviewDecisionValue, string> = {
  advance: "Move the candidate to the next step.",
  hold: "Pause for now; revisit with other candidates.",
  reject: "Close this application for the role.",
  needs_more_information: "Gather more evidence first, e.g. a follow-up round.",
};

export const DECISION_ORDER: readonly ReviewDecisionValue[] = ["advance", "hold", "needs_more_information", "reject"];

/** The human decision (never AI). */
export function DecisionBadge({ value, size = "md" }: { value: ReviewDecisionValue; size?: "sm" | "md" }) {
  const Icon = ICONS[value] ?? CircleHelp;
  return (
    <Badge tone={TONES[value] ?? "neutral"} size={size} icon={<Icon aria-hidden="true" />}>
      {REVIEW_DECISION_LABELS[value] ?? value}
    </Badge>
  );
}

const POSITIVE: readonly Recommendation[] = ["strong_proceed", "proceed"];

/**
 * Whether a decision goes against the AI suggestion's direction (used only for
 * an inline prompt to explain the reasoning; the server records `overrides_ai`).
 */
export function differsFromSuggestion(decision: ReviewDecisionValue, recommendation: Recommendation | null | undefined): boolean {
  if (!recommendation) return false;
  const positive = POSITIVE.includes(recommendation);
  if (decision === "advance") return !positive && recommendation !== "human_review";
  if (decision === "reject") return positive;
  return false;
}
