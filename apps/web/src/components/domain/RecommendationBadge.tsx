import { CheckCheck, CircleCheck, CircleDashed, Scale, UserRound, UserSearch } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { RECOMMENDATION_LABELS } from "@/lib/labels";
import type { Recommendation } from "@/lib/types";
import { cn } from "@/lib/utils";

const TONES: Record<Recommendation, BadgeTone> = {
  strong_proceed: "success",
  proceed: "accent",
  human_review: "neutral",
  borderline_evidence: "warning",
  insufficient_evidence: "neutral",
};

const ICONS: Record<Recommendation, ComponentType<SVGProps<SVGSVGElement>>> = {
  strong_proceed: CheckCheck,
  proceed: CircleCheck,
  human_review: UserSearch,
  borderline_evidence: Scale,
  insufficient_evidence: CircleDashed,
};

export const HUMAN_DECISION_NOTE = "AI-assisted suggestion. A person makes the hiring decision.";

export interface RecommendationBadgeProps {
  value: Recommendation | null | undefined;
  size?: "sm" | "md";
  /** `stacked` puts the human-decision note under the badge; `inline` beside it. */
  layout?: "stacked" | "inline";
  className?: string;
}

/**
 * The assistive recommendation, always paired with the note that a human
 * owns the decision. The wording describes the evidence, never the person.
 */
export function RecommendationBadge({ value, size = "md", layout = "stacked", className }: RecommendationBadgeProps) {
  const Icon = value ? ICONS[value] : CircleDashed;
  return (
    <div className={cn("flex", layout === "stacked" ? "flex-col items-start gap-1.5" : "flex-wrap items-center gap-x-2.5 gap-y-1", className)}>
      <Badge tone={value ? TONES[value] : "outline"} size={size} icon={<Icon aria-hidden="true" />}>
        {value ? RECOMMENDATION_LABELS[value] : "No suggestion yet"}
      </Badge>
      <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
        <UserRound className="size-3.5 shrink-0" aria-hidden="true" />
        {HUMAN_DECISION_NOTE}
      </span>
    </div>
  );
}
