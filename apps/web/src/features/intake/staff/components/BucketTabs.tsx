import { useRef, type KeyboardEvent } from "react";

import { formatNumber } from "@/lib/format";
import { useOverflowEdges } from "@/lib/hooks/useOverflowEdges";
import { cn } from "@/lib/utils";

import { BUCKET_LABELS } from "../labels";
import type { IntakeBucket } from "../types";

export interface BucketTabsProps {
  buckets: readonly IntakeBucket[];
  value: IntakeBucket;
  onChange: (bucket: IntakeBucket) => void;
  counts: (bucket: IntakeBucket) => number | undefined;
  className?: string;
}

/**
 * Bucket views with counts. A radio group (one view at a time) with roving focus and
 * arrow keys; scrolls sideways with edge fades when it doesn't fit.
 */
export function BucketTabs({ buckets, value, onChange, counts, className }: BucketTabsProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  useOverflowEdges(ref);

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const jump = event.key === "Home" ? 0 : event.key === "End" ? buckets.length - 1 : null;
    if (!delta && jump === null) return;
    event.preventDefault();
    const next = jump ?? (index + delta + buckets.length) % buckets.length;
    onChange(buckets[next]!);
    ref.current?.querySelectorAll<HTMLButtonElement>("[role='radio']")[next]?.focus();
  };

  return (
    <div
      ref={ref}
      role="radiogroup"
      aria-label="Intake views"
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-0.5 overflow-x-auto overflow-y-hidden rounded-control bg-surface-sunken/70 p-0.5 scrollbar-none edge-fade",
        className,
      )}
    >
      {buckets.map((bucket, index) => {
        const active = bucket === value;
        const count = counts(bucket);
        return (
          <button
            key={bucket}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            data-testid={`intake-bucket-${bucket}`}
            onClick={() => onChange(bucket)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[6px] px-3 text-[13px] font-medium whitespace-nowrap transition-colors focus-visible:outline-offset-[-2px]",
              active ? "bg-surface text-fg shadow-[0_1px_2px_rgb(16_24_40/0.08),0_0_0_1px_var(--border)]" : "text-fg-muted hover:text-fg",
            )}
          >
            {BUCKET_LABELS[bucket]}
            {count !== undefined ? <span className={cn("tabular-nums text-xs", active ? "text-fg-muted" : "text-fg-subtle")}>{formatNumber(count)}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
