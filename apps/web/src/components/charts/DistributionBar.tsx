import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/Tooltip";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { seriesColor } from "./chartTheme";

export interface DistributionSegment {
  key: string;
  label: string;
  value: number;
  /** Explicit colour (CSS value); defaults to categorical slots in order. */
  color?: string;
}

export interface DistributionBarProps {
  segments: readonly DistributionSegment[];
  valueFormatter?: (value: number) => string;
  showLegend?: boolean;
  /** `inline` wraps legend items in rows; `list` stacks them with right-aligned values (narrow cards). */
  legendLayout?: "inline" | "list";
  height?: "sm" | "md";
  /** Rendered when every segment is zero. */
  emptyMessage?: ReactNode;
  "aria-label"?: string;
  className?: string;
}

/** Part-to-whole in one stacked bar with 2px surface gaps and a value legend. */
export function DistributionBar({
  segments,
  valueFormatter = (v) => formatNumber(v),
  showLegend = true,
  legendLayout = "inline",
  height = "md",
  emptyMessage = "No data yet",
  className,
  ...aria
}: DistributionBarProps) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const colored = segments.map((s, index) => ({ ...s, color: s.color ?? seriesColor(index) }));
  const visible = colored.filter((s) => s.value > 0);
  const label =
    aria["aria-label"] ??
    `Distribution: ${colored.map((s) => `${s.label} ${valueFormatter(s.value)}${total ? ` (${formatPercent(s.value / total)})` : ""}`).join(", ")}`;

  return (
    <div className={cn("grid gap-3", className)}>
      {total === 0 ? (
        <div className={cn("w-full rounded-full bg-track", height === "sm" ? "h-2" : "h-2.5")} role="img" aria-label="No data yet" />
      ) : (
        <div className={cn("flex w-full gap-[2px] overflow-hidden rounded-full", height === "sm" ? "h-2" : "h-2.5")} role="img" aria-label={label}>
          {visible.map((segment) => (
            <Tooltip
              key={segment.key}
              content={`${segment.label}: ${valueFormatter(segment.value)} (${formatPercent(segment.value / total)})`}
            >
              <span
                className="h-full min-w-1 transition-[flex-grow] duration-500 first:rounded-l-full last:rounded-r-full hover:opacity-80"
                style={{ flexGrow: segment.value, flexBasis: 0, background: segment.color }}
              />
            </Tooltip>
          ))}
        </div>
      )}
      {showLegend ? (
        total === 0 ? (
          <p className="text-xs text-fg-muted">{emptyMessage}</p>
        ) : legendLayout === "list" ? (
          <ul className="grid gap-2">
            {colored.map((segment) => (
              <li key={segment.key} className="flex items-center gap-2.5 text-[13px]">
                <span className="size-2.5 shrink-0 rounded-[3px]" style={{ background: segment.color }} aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate text-fg-muted">{segment.label}</span>
                <span className="font-medium text-fg tabular-nums">{valueFormatter(segment.value)}</span>
                <span className="w-10 text-right text-xs text-fg-subtle tabular-nums">{formatPercent(total ? segment.value / total : 0)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <ul className="flex flex-wrap gap-x-5 gap-y-2">
            {colored.map((segment) => (
              <li key={segment.key} className="flex items-center gap-2 text-xs">
                <span className="size-2.5 shrink-0 rounded-[3px]" style={{ background: segment.color }} aria-hidden="true" />
                <span className="text-fg-muted">{segment.label}</span>
                <span className="font-medium text-fg tabular-nums">{valueFormatter(segment.value)}</span>
                <span className="text-fg-subtle tabular-nums">{formatPercent(total ? segment.value / total : 0)}</span>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
