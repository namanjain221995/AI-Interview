import { CircleDashed, Scale, UserCheck } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";

import { BAND_LABELS, BAND_TONES, BUCKET_BADGE, OUTCOME_LABELS, OUTCOME_TONES, SEVERITY_LABELS, SEVERITY_TONES } from "../labels";
import type { DecisionBand, DecisionOutcome, IntakeBucket, ReviewItemSeverity } from "../types";

/** The AI band. Always reads as a recommendation (`ai` prefix in the accessible name). */
export function BandBadge({ band, label, size = "sm", className, testId }: { band: DecisionBand | null | undefined; label?: string | null; size?: "sm" | "md"; className?: string; testId?: string }) {
  if (!band) {
    return (
      <span className={cn("text-xs text-fg-subtle", className)} data-testid={testId} data-band="none">
        —
      </span>
    );
  }
  return (
    <Badge
      tone={BAND_TONES[band]}
      size={size}
      icon={band === "insufficient_evidence" ? <CircleDashed aria-hidden="true" /> : <Scale aria-hidden="true" />}
      className={className}
      data-testid={testId}
      data-band={band}
      aria-label={`AI recommendation: ${label || BAND_LABELS[band]}`}
      title="AI recommendation. A human reviewer decides."
    >
      {label || BAND_LABELS[band]}
    </Badge>
  );
}

/** A recorded human decision. */
export function OutcomeBadge({ outcome, label, size = "sm", className }: { outcome: DecisionOutcome; label?: string | null; size?: "sm" | "md"; className?: string }) {
  return (
    <Badge tone={OUTCOME_TONES[outcome]} size={size} icon={<UserCheck aria-hidden="true" />} className={className} data-outcome={outcome}>
      {label || OUTCOME_LABELS[outcome]}
    </Badge>
  );
}

export function BucketBadge({ bucket, size = "sm" }: { bucket: IntakeBucket; size?: "sm" | "md" }) {
  const style = BUCKET_BADGE[bucket];
  return (
    <Badge tone={style.tone} size={size} dot pulse={bucket === "in_progress"}>
      {style.label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: ReviewItemSeverity }) {
  return (
    <Badge tone={SEVERITY_TONES[severity]} size="sm">
      {SEVERITY_LABELS[severity]}
    </Badge>
  );
}
