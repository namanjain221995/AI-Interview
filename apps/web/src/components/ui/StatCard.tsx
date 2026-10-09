import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import type { ReactNode } from "react";

import { Sparkline } from "@/components/charts/Sparkline";
import { cn } from "@/lib/utils";

import { Skeleton } from "./Skeleton";
import { Tooltip } from "./Tooltip";

export interface StatDelta {
  /** Display text, already signed/formatted (e.g. "+12%"). */
  label: string;
  direction: "up" | "down" | "flat";
  /** Whether "up" is good for this metric (colours the delta). Default true. */
  upIsGood?: boolean;
  /** Comparison period, e.g. "vs last week". */
  period?: string;
}

export interface StatCardProps {
  label: ReactNode;
  value: ReactNode;
  /** Small unit or suffix after the value ("/ 100", "min"). */
  unit?: ReactNode;
  delta?: StatDelta;
  /** Trend points for a sparkline. */
  trend?: readonly number[];
  trendLabel?: string;
  /** Muted helper line under the value. */
  hint?: ReactNode;
  /** Explanation shown in a tooltip on the label. */
  info?: string;
  loading?: boolean;
  /** Render without its own frame (inside StatGroup). */
  bare?: boolean;
  className?: string;
}

function DeltaBadge({ delta }: { delta: StatDelta }) {
  const good = delta.direction === "flat" ? null : (delta.direction === "up") === (delta.upIsGood ?? true);
  const Icon = delta.direction === "up" ? ArrowUpRight : delta.direction === "down" ? ArrowDownRight : Minus;
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span
        className={cn(
          "inline-flex items-center gap-0.5 font-medium",
          good === null ? "text-fg-muted" : good ? "text-success-fg" : "text-danger-fg",
        )}
      >
        <Icon className="size-3.5" aria-hidden="true" />
        {delta.label}
      </span>
      {delta.period ? <span className="text-fg-subtle">{delta.period}</span> : null}
    </span>
  );
}

export function StatCard({ label, value, unit, delta, trend, trendLabel, hint, info, loading, bare, className }: StatCardProps) {
  const labelNode = <span className="text-[13px] leading-5 font-medium text-fg-muted">{label}</span>;
  return (
    <div className={cn("flex min-w-0 flex-col gap-2", bare ? "p-5" : "rounded-lg border border-line bg-surface p-5 shadow-[0_1px_2px_rgb(16_24_40/0.04)]", className)}>
      {info ? (
        <Tooltip content={info}>
          <span tabIndex={0} className="w-fit cursor-help rounded-xs decoration-line-emphasis decoration-dotted underline-offset-4 hover:underline">
            {labelNode}
          </span>
        </Tooltip>
      ) : (
        labelNode
      )}
      <div className="flex items-end justify-between gap-3">
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <p className="flex min-w-0 items-baseline gap-1 text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-fg">
            <span className="truncate">{value}</span>
            {unit ? <span className="text-sm font-medium tracking-normal text-fg-subtle">{unit}</span> : null}
          </p>
        )}
        {!loading && trend && trend.length > 1 ? <Sparkline data={trend} label={trendLabel} className="mb-1" /> : null}
      </div>
      {loading ? <Skeleton className="h-3.5 w-28" /> : delta ? <DeltaBadge delta={delta} /> : hint ? <p className="text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
}

/**
 * A single framed strip of stats separated by hairlines (avoids a wall of cards).
 * Each cell draws its own right and bottom hairline (clipped at the frame), so a
 * short last row leaves plain surface instead of a grey block at any column count.
 */
export function StatGroup({ children, className, columns = 4 }: { children: ReactNode; className?: string; columns?: 2 | 3 | 4 }) {
  return (
    <div
      className={cn(
        "grid overflow-hidden rounded-lg border border-line bg-surface shadow-[0_1px_2px_rgb(16_24_40/0.04)] [&>*]:shadow-[1px_0_0_var(--border),0_1px_0_var(--border)]",
        columns === 4 && "grid-cols-2 xl:grid-cols-4",
        columns === 3 && "grid-cols-2 sm:grid-cols-3",
        columns === 2 && "grid-cols-1 sm:grid-cols-2",
        className,
      )}
    >
      {children}
    </div>
  );
}
