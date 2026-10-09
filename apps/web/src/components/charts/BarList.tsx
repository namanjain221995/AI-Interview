import type { ReactNode } from "react";
import { Link } from "react-router";

import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface BarListItem {
  key: string;
  label: ReactNode;
  value: number;
  /** Secondary text under the label. */
  hint?: ReactNode;
  to?: string;
  /** De-emphasise (e.g. a value with too little evidence behind it to compare). */
  muted?: boolean;
}

export interface BarListProps {
  items: readonly BarListItem[];
  valueFormatter?: (value: number) => string;
  /** Scale maximum (defaults to the largest value). */
  max?: number;
  /** One series, one hue. */
  color?: string;
  emptyMessage?: ReactNode;
  "aria-label"?: string;
  className?: string;
}

/**
 * Ranked horizontal bars (top competencies, busiest jobs). Labels and values
 * stay in text colours; the thin bar carries magnitude.
 */
export function BarList({ items, valueFormatter = (v) => formatNumber(v), max, color = "var(--chart-1)", emptyMessage = "No data yet", className, ...aria }: BarListProps) {
  if (items.length === 0) return <p className={cn("py-6 text-center text-sm text-fg-muted", className)}>{emptyMessage}</p>;
  const scaleMax = max ?? Math.max(...items.map((i) => i.value), 0);
  return (
    <ul className={cn("grid gap-3.5", className)} aria-label={aria["aria-label"]}>
      {items.map((item) => {
        const pct = scaleMax > 0 ? Math.max(0, Math.min(100, (item.value / scaleMax) * 100)) : 0;
        const label = item.to ? (
          <Link to={item.to} className="truncate rounded-xs hover:underline hover:underline-offset-2">
            {item.label}
          </Link>
        ) : (
          <span className="truncate">{item.label}</span>
        );
        return (
          <li key={item.key} className="grid content-start gap-1.5">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="flex min-w-0 flex-col">
                <span className="flex min-w-0 font-medium text-fg">{label}</span>
                {item.hint ? <span className="text-xs text-fg-muted">{item.hint}</span> : null}
              </span>
              <span className={cn("shrink-0 font-medium tabular-nums", item.muted ? "text-fg-subtle" : "text-fg")}>{valueFormatter(item.value)}</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-track" aria-hidden="true">
              <div className="h-full rounded-full transition-[width] duration-500 ease-out" style={{ width: `${pct}%`, background: item.muted ? "var(--chart-muted)" : color }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
