import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/Skeleton";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatMinutes, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { formatTotal } from "../lib/decimals";
import type { IntakeStats } from "../types";

interface Figure {
  key: string;
  label: string;
  value: ReactNode;
  unit?: string;
  info?: string;
}

function figuresOf(stats: IntakeStats): Figure[] {
  const t = stats.totals;
  return [
    { key: "invitations", label: "Invitations", value: formatNumber(t.invitations), info: "Invitations sent (drafts excluded)." },
    { key: "started", label: "Started", value: formatNumber(t.started) },
    { key: "completed", label: "Completed", value: formatNumber(t.completed) },
    { key: "incomplete", label: "Incomplete", value: formatNumber(t.incomplete), info: "Ended with a stage left unfinished, by the candidate, a lost connection or a technical problem." },
    { key: "awaiting_review", label: "Awaiting review", value: formatNumber(t.awaiting_review) },
    {
      key: "average_score",
      label: "Average score",
      value: t.average_weighted_total ? formatTotal(t.average_weighted_total) : "—",
      unit: t.average_weighted_total ? "/ 5" : undefined,
      info: "Mean weighted total of evaluations with a band. An AI measure; people make every decision.",
    },
    { key: "average_duration", label: "Average duration", value: t.average_duration_s ? formatMinutes(t.average_duration_s) : "—", info: "Interview clock of completed sessions (pauses excluded)." },
  ];
}

/** One row of inline figures with dividers (design 11.1); wraps into a grid on phones. */
export function StatsStrip({ stats, loading, className }: { stats: IntakeStats | undefined; loading?: boolean; className?: string }) {
  const figures = stats ? figuresOf(stats) : null;
  return (
    // Cells draw their own right/bottom hairlines; the outer ones are clipped, so a short
    // last row leaves plain surface instead of a grey block.
    <div className={cn("overflow-hidden rounded-lg border border-line bg-surface shadow-[0_1px_2px_rgb(16_24_40/0.04)]", className)}>
    <dl data-testid="intake-stats" aria-busy={loading || undefined} className="-mr-px -mb-px grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7">
      {(figures ?? Array.from({ length: 7 }, (_, i) => ({ key: `s${i}`, label: "", value: null }) as Figure)).map((figure) => (
        <div key={figure.key} className="grid min-w-0 content-start gap-1 border-r border-b border-line-subtle px-4 py-3" data-testid={figures ? `intake-stat-${figure.key}` : undefined}>
          {figures ? (
            <>
              <dt className="truncate text-xs text-fg-muted">
                {figure.info ? (
                  <Tooltip content={figure.info}>
                    <span tabIndex={0} className="cursor-help rounded-xs decoration-line-emphasis decoration-dotted underline-offset-4 hover:underline">
                      {figure.label}
                    </span>
                  </Tooltip>
                ) : (
                  figure.label
                )}
              </dt>
              <dd className="flex min-w-0 items-baseline gap-1 text-xl leading-7 font-semibold tracking-[-0.01em] text-fg">
                <span className="truncate">{figure.value}</span>
                {figure.unit ? <span className="text-xs font-medium tracking-normal text-fg-subtle">{figure.unit}</span> : null}
              </dd>
            </>
          ) : (
            <>
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-6 w-10" />
            </>
          )}
        </div>
      ))}
    </dl>
    </div>
  );
}
