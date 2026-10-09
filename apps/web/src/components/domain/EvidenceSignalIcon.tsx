import { CircleDot, CircleMinus, CirclePlus } from "lucide-react";

import { EVIDENCE_SIGNAL_LABELS } from "@/lib/labels";
import type { EvidenceSignal } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICONS = { positive: CirclePlus, negative: CircleMinus, neutral: CircleDot } as const;
const COLORS = { positive: "text-success", negative: "text-danger", neutral: "text-fg-subtle" } as const;
const SIZES = { sm: "size-3.5", md: "size-4", lg: "size-5" } as const;

export interface EvidenceSignalIconProps {
  signal: EvidenceSignal;
  size?: keyof typeof SIZES;
  /** Render the label next to the icon. */
  showLabel?: boolean;
  className?: string;
}

/** Shape (plus / minus / dot) and colour both encode the signal, with an accessible label. */
export function EvidenceSignalIcon({ signal, size = "md", showLabel = false, className }: EvidenceSignalIconProps) {
  const Icon = ICONS[signal] ?? CircleDot;
  const label = EVIDENCE_SIGNAL_LABELS[signal] ?? signal;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <Icon className={cn("shrink-0", SIZES[size], COLORS[signal])} role="img" aria-label={label} />
      {showLabel ? <span className="text-xs font-medium text-fg-muted">{label}</span> : null}
    </span>
  );
}
