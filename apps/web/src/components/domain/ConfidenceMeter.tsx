import { confidenceLabel, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface ConfidenceMeterProps {
  /** 0-1 */
  value: number | null | undefined;
  /** Show the word ("Moderate") next to the bars. */
  showLabel?: boolean;
  /** Prefix for the visible label, e.g. "confidence". */
  suffix?: string;
  className?: string;
}

function filledBars(value: number | null | undefined): number {
  if (value === null || value === undefined || Number.isNaN(value) || value <= 0) return 0;
  if (value >= 0.75) return 4;
  if (value >= 0.5) return 3;
  if (value >= 0.25) return 2;
  return 1;
}

/** How much evidence backs a score: four ascending bars plus a word. */
export function ConfidenceMeter({ value, showLabel = true, suffix = "confidence", className }: ConfidenceMeterProps) {
  const filled = filledBars(value);
  const word = filled === 0 ? "No evidence yet" : confidenceLabel(value);
  const spoken = filled === 0 ? "Confidence: no evidence yet" : `Confidence: ${word} (${formatPercent(value)})`;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)} role="img" aria-label={spoken}>
      <span className="flex h-3 items-end gap-[2px]" aria-hidden="true">
        {[0, 1, 2, 3].map((index) => (
          <span
            key={index}
            className={cn("w-[3px] rounded-[1px]", index < filled ? "bg-fg-muted" : "bg-line-strong")}
            style={{ height: `${5 + index * 2.33}px` }}
          />
        ))}
      </span>
      {showLabel ? (
        <span className="text-xs text-fg-muted" aria-hidden="true">
          {filled === 0 ? word : `${word} ${suffix}`}
        </span>
      ) : null}
    </span>
  );
}
