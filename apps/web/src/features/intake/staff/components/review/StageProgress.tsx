import { Badge } from "@/components/ui/Badge";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { STAGE_LABELS, STAGE_STATUS_LABELS, STAGE_STATUS_TONES } from "../../labels";
import { mandatoryCoverage, stageTiming } from "../../lib/review";
import { STAGE_KEYS, type IntakeStage } from "../../types";

/** The seven stages with status, time used against the budget, and mandatory coverage. */
export function StageProgress({ stages, nowElapsed }: { stages: readonly IntakeStage[]; nowElapsed?: number | null }) {
  // Always show seven rows in kit order, even before the session prepared its stage results.
  const rows = STAGE_KEYS.map((key, index) => stages.find((s) => s.key === key) ?? ({ key, number: index + 1, title: STAGE_LABELS[key], status: "pending", started_s: null, ended_s: null, budget_s: null, items: [], extra: {} } satisfies IntakeStage));
  return (
    <ol className="grid grid-cols-1 divide-y divide-line-subtle" aria-label="Interview stages">
      {rows.map((stage) => {
        const timing = stageTiming(stage, nowElapsed);
        const coverage = mandatoryCoverage(stage);
        const pct = timing.ratio === null ? 0 : Math.min(100, (timing.ratio / 1.25) * 100);
        return (
          <li key={stage.key} data-testid={`intake-stage-row-${stage.key}`} data-status={stage.status} className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 py-3 first:pt-0 last:pb-0 sm:grid-cols-[1.75rem_minmax(0,1fr)_12rem]">
            <span className="row-span-2 mt-0.5 flex size-6 items-center justify-center rounded-full bg-neutral-soft text-xs font-semibold text-neutral-fg tabular-nums ring-1 ring-neutral-line ring-inset" aria-hidden="true">
              {stage.number}
            </span>
            <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="min-w-0 truncate text-sm font-medium text-fg">{stage.title || STAGE_LABELS[stage.key]}</span>
              <Badge size="sm" tone={STAGE_STATUS_TONES[stage.status]} dot pulse={stage.status === "in_progress"}>
                {STAGE_STATUS_LABELS[stage.status]}
              </Badge>
            </div>
            <div className="col-start-2 grid min-w-0 grid-cols-1 gap-1 sm:col-start-3 sm:row-span-2 sm:row-start-1">
              <div className="flex items-baseline justify-between gap-2 text-xs text-fg-muted tabular-nums">
                <span>{timing.usedSeconds !== null ? formatClock(timing.usedSeconds) : "—"}</span>
                <span className="text-fg-subtle">{timing.budgetSeconds ? `of ${formatClock(timing.budgetSeconds)}` : "no budget"}</span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
                {/* The track spans the soft cap (1.25 x budget); the tick marks the budget. */}
                <div className={cn("absolute inset-y-0 left-0 rounded-full", timing.over ? "bg-warning" : "bg-fg-subtle")} style={{ width: `${pct}%` }} />
                {timing.budgetSeconds ? <div className="absolute inset-y-0 w-px bg-surface" style={{ left: "80%" }} /> : null}
              </div>
              <span className="sr-only">
                {timing.usedSeconds !== null ? `${formatClock(timing.usedSeconds)} used` : "Not started"}
                {timing.budgetSeconds ? ` of a ${formatClock(timing.budgetSeconds)} budget` : ""}
                {timing.over ? ", past the stage's time cap" : ""}
              </span>
            </div>
            <p className="col-start-2 text-xs text-fg-muted">
              {coverage.total ? `${coverage.covered} of ${coverage.total} mandatory items covered` : stage.items.length ? `${stage.items.length} items` : "Items appear once the stage starts"}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
