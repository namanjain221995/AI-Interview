import { Info, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";
import { INTEGRITY_SEVERITY_LABELS } from "@/lib/labels";
import type { IntegritySeverity } from "@/lib/types";

const STYLE: Record<IntegritySeverity, { tone: BadgeTone; Icon: typeof Info }> = {
  info: { tone: "neutral", Icon: Info },
  low: { tone: "neutral", Icon: ShieldCheck },
  medium: { tone: "warning", Icon: ShieldQuestion },
  high: { tone: "danger", Icon: ShieldAlert },
};

export const INTEGRITY_NOTE = "An observation for a person to review, not a verdict.";

export interface IntegritySeverityBadgeProps {
  severity: IntegritySeverity;
  size?: "sm" | "md";
  /** Explain on hover that integrity events are observations, not verdicts. */
  withNote?: boolean;
  className?: string;
}

export function IntegritySeverityBadge({ severity, size = "md", withNote = true, className }: IntegritySeverityBadgeProps) {
  const { tone, Icon } = STYLE[severity] ?? STYLE.info;
  const label = `${INTEGRITY_SEVERITY_LABELS[severity] ?? severity} severity`;
  const badge = (
    <Badge tone={tone} size={size} icon={<Icon aria-hidden="true" />} className={className} tabIndex={withNote ? 0 : undefined}>
      {label}
    </Badge>
  );
  return withNote ? <Tooltip content={INTEGRITY_NOTE}>{badge}</Tooltip> : badge;
}
