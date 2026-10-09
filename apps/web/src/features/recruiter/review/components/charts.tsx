/**
 * Lightweight charts for the dashboard and analytics (plain HTML/SVG, so the
 * dashboard never loads the Recharts chunk). Each wraps ChartFrame for a
 * table twin and a plain-language summary.
 */

import { ChartFrame } from "@/components/charts/ChartFrame";
import { DEFAULT_BANDS } from "@/components/domain/scoreBands";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatNumber, formatPercent, formatScore, pluralize } from "@/lib/format";

import type { FunnelStep, ScoreBucket, ScoreGroup } from "../lib/analytics";

// ------------------------------------------------------------------ funnel

export function Funnel({ steps, className, title, description }: { steps: FunnelStep[]; className?: string; title?: string; description?: string }) {
  const first = steps[0]?.count ?? 0;
  const summary = steps.length
    ? `${steps.map((s) => `${s.label} ${s.count}`).join(", ")}.${steps.length > 1 && first ? ` ${formatPercent(steps[steps.length - 1]!.count / first)} of invited candidates reached ${steps[steps.length - 1]!.label.toLowerCase()}.` : ""}`
    : "No funnel data yet.";
  return (
    <ChartFrame
      title={title}
      description={description}
      tableToggle={false}
      summary={summary}
      className={className}
      table={{
        caption: title ?? "Interview funnel",
        columns: [
          { key: "stage", label: "Stage" },
          { key: "count", label: "Candidates", numeric: true },
          { key: "rate", label: "From previous stage", numeric: true },
        ],
        rows: steps.map((s) => ({ stage: s.label, count: formatNumber(s.count), rate: s.fromPrevious === null ? "—" : formatPercent(s.fromPrevious) })),
      }}
    >
      {steps.length === 0 || first === 0 ? (
        <p className="py-6 text-center text-sm text-fg-muted">No candidates in this period yet.</p>
      ) : (
        <ol className="grid grid-cols-1 gap-1">
          {steps.map((step, index) => {
            const pct = first ? (step.count / first) * 100 : 0;
            return (
              <li key={step.stage} className="grid grid-cols-1 gap-1">
                {index > 0 && step.fromPrevious !== null ? (
                  <span className="pl-0.5 text-[11px] text-fg-subtle tabular-nums">{formatPercent(step.fromPrevious)} continued</span>
                ) : null}
                <div className="grid grid-cols-[7.5rem_minmax(0,1fr)_3rem] items-center gap-3">
                  <span className="truncate text-[13px] text-fg-muted">{step.label}</span>
                  <span className="relative h-6 overflow-hidden rounded-[5px] bg-track">
                    <span
                      className="absolute inset-y-0 left-0 rounded-[5px] transition-[width] duration-500"
                      style={{ width: `${Math.max(pct, step.count > 0 ? 1.5 : 0)}%`, background: "var(--chart-1)", opacity: 1 - index * 0.16 }}
                    />
                  </span>
                  <span className="text-right text-sm font-semibold text-fg tabular-nums">{formatNumber(step.count)}</span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </ChartFrame>
  );
}

// --------------------------------------------------------------- histogram

export function ScoreHistogram({
  buckets,
  className,
  title,
  description,
  height = 132,
  note,
}: {
  buckets: ScoreBucket[];
  className?: string;
  title?: string;
  description?: string;
  height?: number;
  note?: string;
}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const total = buckets.reduce((sum, b) => sum + b.count, 0);
  const peak = buckets.reduce<ScoreBucket | null>((best, b) => (!best || b.count > best.count ? b : best), null);
  return (
    <ChartFrame
      title={title}
      description={description}
      tableToggle={false}
      className={className}
      summary={total ? `${pluralize(total, "scored interview")}. Most common range ${peak?.label ?? "—"}.` : "No scored interviews yet."}
      table={{
        caption: title ?? "Overall score distribution",
        columns: [
          { key: "range", label: "Score range" },
          { key: "count", label: "Interviews", numeric: true },
        ],
        rows: buckets.map((b) => ({ range: b.label, count: formatNumber(b.count) })),
      }}
    >
      {total === 0 ? (
        <p className="py-6 text-center text-sm text-fg-muted">No scored interviews yet.</p>
      ) : (
        <div className="grid grid-cols-1 gap-1.5">
          <div className="relative flex items-end gap-1" style={{ height }}>
            {/* Band thresholds from the default passing guidance. */}
            {[DEFAULT_BANDS.borderline, DEFAULT_BANDS.proceed, DEFAULT_BANDS.strong].map((t) => (
              <span key={t} aria-hidden="true" className="absolute inset-y-0 w-px border-l border-dashed border-line-strong" style={{ left: `${t}%` }} />
            ))}
            {/* One neutral series: the dashed guidance lines already mark the bands, and a
                distribution of people should not read as an alarm. */}
            {buckets.map((b) => (
              <Tooltip key={b.label} content={`${b.label}: ${pluralize(b.count, "interview")}`}>
                <span tabIndex={-1} className="relative flex h-full flex-1 items-end">
                  <span
                    className="w-full rounded-t-[4px] transition-[height] duration-500"
                    style={{ height: b.count ? `${Math.max(4, (b.count / max) * 100)}%` : 0, background: b.count ? "var(--chart-1)" : undefined, opacity: 0.8 }}
                  />
                </span>
              </Tooltip>
            ))}
          </div>
          <div className="relative h-4 text-[11px] text-fg-subtle tabular-nums" aria-hidden="true">
            <span className="absolute left-0">0</span>
            {[DEFAULT_BANDS.borderline, DEFAULT_BANDS.proceed, DEFAULT_BANDS.strong].map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: `${t}%` }}>
                {t}
              </span>
            ))}
            <span className="absolute right-0">100</span>
          </div>
          {note ? <p className="text-xs text-fg-subtle">{note}</p> : null}
        </div>
      )}
    </ChartFrame>
  );
}

// -------------------------------------------------------------- strip plot

/**
 * One row per group on a shared 0-100 axis: a dot per interview (when known),
 * the interquartile range as a band and the median as a tick.
 */
export function ScoreStripPlot({ groups, className, title, description }: { groups: ScoreGroup[]; className?: string; title?: string; description?: string }) {
  const rows = groups.filter((g) => g.summary.count > 0);
  return (
    <ChartFrame
      title={title}
      description={description}
      className={className}
      summary={rows.length ? rows.map((g) => `${g.label}: median ${formatScore(g.summary.median)} across ${pluralize(g.summary.count, "interview")}`).join(". ") : "No scored interviews yet."}
      table={{
        caption: title ?? "Scores by group",
        columns: [
          { key: "group", label: "Group" },
          { key: "count", label: "Scored", numeric: true },
          { key: "median", label: "Median", numeric: true },
          { key: "range", label: "Middle half", numeric: true },
          { key: "mean", label: "Average", numeric: true },
        ],
        rows: rows.map((g) => ({
          group: g.label,
          count: formatNumber(g.summary.count),
          median: formatScore(g.summary.median),
          range: g.summary.p25 !== null && g.summary.p75 !== null ? `${formatScore(g.summary.p25)}–${formatScore(g.summary.p75)}` : "—",
          mean: formatScore(g.summary.mean),
        })),
      }}
    >
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-fg-muted">No scored interviews yet.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {rows.map((g) => {
            const { p25, p75, median } = g.summary;
            return (
              <div key={g.key} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_2.5rem] items-center gap-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_3rem]">
                <span className="grid grid-cols-1 min-w-0">
                  <span className="truncate text-[13px] font-medium text-fg">{g.label}</span>
                  <span className="text-[11px] text-fg-subtle">{pluralize(g.summary.count, "interview")}</span>
                </span>
                <span className="relative h-7">
                  <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-line" aria-hidden="true" />
                  {p25 !== null && p75 !== null ? (
                    <span className="absolute top-1/2 h-3 -translate-y-1/2 rounded-sm bg-accent-soft ring-1 ring-accent-line" style={{ left: `${p25}%`, width: `${Math.max(0.8, p75 - p25)}%` }} aria-hidden="true" />
                  ) : null}
                  {g.scores.map((score, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      className="absolute top-1/2 size-[7px] -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface"
                      style={{ left: `${Math.min(100, Math.max(0, score))}%`, background: "var(--chart-1)" }}
                    />
                  ))}
                  {median !== null ? <span className="absolute top-1/2 h-5 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg" style={{ left: `${median}%` }} aria-hidden="true" /> : null}
                </span>
                <span className="text-right text-sm font-semibold text-fg tabular-nums">{formatScore(median)}</span>
              </div>
            );
          })}
          <div className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_2.5rem] gap-3 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_3rem]" aria-hidden="true">
            <span />
            <span className="flex justify-between text-[11px] text-fg-subtle tabular-nums">
              <span>0</span>
              <span>25</span>
              <span>50</span>
              <span>75</span>
              <span>100</span>
            </span>
            <span className="text-right text-[11px] text-fg-subtle">Median</span>
          </div>
        </div>
      )}
    </ChartFrame>
  );
}
