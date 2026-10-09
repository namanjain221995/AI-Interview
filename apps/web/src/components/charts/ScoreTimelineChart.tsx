import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";

import { formatScore } from "@/lib/format";
import type { ScoreSnapshot } from "@/lib/types";

import { ChartFrame, ChartLegend, ChartTooltipCard } from "./ChartFrame";
import { AXIS_STROKE, AXIS_TICK, GRID_STROKE, MAX_SERIES, seriesColor } from "./chartTheme";

export interface TimelineCompetency {
  key: string;
  label: string;
}

export interface ScoreTimelineChartProps {
  snapshots: readonly Pick<ScoreSnapshot, "elapsed_seconds" | "competency_key" | "score">[];
  /** Defines series order and therefore colour (colour follows the competency, never its rank). */
  competencies: readonly TimelineCompetency[];
  max?: number;
  /** Planned round length; extends the x-axis to the full interview. */
  durationSeconds?: number;
  height?: number;
  title?: string;
  description?: string;
  tableToggle?: boolean;
  className?: string;
}

type Row = { minute: number } & Record<string, number | null>;

function minuteLabel(value: number): string {
  return `${Math.round(value)}m`;
}

/** Competency estimates over elapsed interview minutes, one line per competency. */
export function ScoreTimelineChart({
  snapshots,
  competencies,
  max = 10,
  durationSeconds,
  height = 280,
  title,
  description,
  tableToggle = true,
  className,
}: ScoreTimelineChartProps) {
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const series = competencies.slice(0, MAX_SERIES);
  const overflow = competencies.length - series.length;
  const scale = max === 100 ? 100 : 10;

  const rows = useMemo<Row[]>(() => {
    const byMinute = new Map<number, Row>();
    for (const snap of snapshots) {
      const minute = Math.round((snap.elapsed_seconds / 60) * 10) / 10;
      const row = byMinute.get(minute) ?? ({ minute } as Row);
      row[snap.competency_key] = snap.score;
      byMinute.set(minute, row);
    }
    return [...byMinute.values()].sort((a, b) => a.minute - b.minute);
  }, [snapshots]);

  const lastMinute = rows.length ? rows[rows.length - 1]!.minute : 0;
  const domainMax = Math.max(1, durationSeconds ? Math.ceil(durationSeconds / 60) : Math.ceil(lastMinute));

  const latestByKey = new Map<string, number>();
  for (const row of rows) for (const c of series) if (typeof row[c.key] === "number") latestByKey.set(c.key, row[c.key] as number);

  const summary = rows.length
    ? `Scores for ${series.length} competencies across ${Math.round(lastMinute)} minutes. Latest: ${series
        .filter((c) => latestByKey.has(c.key))
        .map((c) => `${c.label} ${formatScore(latestByKey.get(c.key), scale)}`)
        .join(", ")}.`
    : "No score updates yet.";

  const table = {
    caption: title ?? "Competency scores over time",
    columns: [{ key: "minute", label: "Minute", numeric: true }, ...competencies.map((c) => ({ key: c.key, label: c.label, numeric: true }))],
    rows: rows.map((row) => {
      const out: Record<string, string> = { minute: minuteLabel(row.minute) };
      for (const c of competencies) out[c.key] = typeof row[c.key] === "number" ? formatScore(row[c.key] as number, scale) : "—";
      return out;
    }),
  };

  const legendItems = series.map((c, index) => ({ key: c.key, label: c.label, color: seriesColor(index), shape: "line" as const, hidden: hidden.has(c.key) }));

  return (
    <ChartFrame
      title={title}
      description={description}
      table={table}
      summary={summary}
      tableToggle={tableToggle}
      className={className}
      legend={
        <div className="grid gap-1">
          <ChartLegend
            items={legendItems}
            onToggle={(key) =>
              setHidden((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })
            }
          />
          {overflow > 0 ? <p className="text-xs text-fg-subtle">Showing {MAX_SERIES} of {competencies.length} competencies. The table lists all of them.</p> : null}
        </div>
      }
    >
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={GRID_STROKE} vertical={false} />
          <XAxis
            dataKey="minute"
            type="number"
            domain={[0, domainMax]}
            tickFormatter={minuteLabel}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: AXIS_STROKE }}
            allowDecimals={false}
            tickMargin={8}
          />
          <YAxis domain={[0, max]} tickCount={6} tick={AXIS_TICK} tickLine={false} axisLine={false} width={40} allowDecimals={false} />
          <Tooltip
            cursor={{ stroke: AXIS_STROKE, strokeWidth: 1 }}
            content={(props: TooltipContentProps) => {
              if (!props.active || !props.payload?.length) return null;
              const row = props.payload[0]?.payload as Row | undefined;
              if (!row) return null;
              const items = series
                .map((c, index) => ({ c, index }))
                .filter(({ c }) => !hidden.has(c.key) && typeof row[c.key] === "number")
                .map(({ c, index }) => ({ key: c.key, label: c.label, value: formatScore(row[c.key] as number, scale), color: seriesColor(index) }));
              if (!items.length) return null;
              return <ChartTooltipCard title={`Minute ${minuteLabel(row.minute)}`} rows={items} />;
            }}
          />
          {series.map((c, index) =>
            hidden.has(c.key) ? null : (
              <Line
                key={c.key}
                type="linear"
                dataKey={c.key}
                name={c.label}
                stroke={seriesColor(index)}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
                connectNulls
                dot={{ r: 3, fill: seriesColor(index), stroke: "var(--surface)", strokeWidth: 2 }}
                activeDot={{ r: 5, fill: seriesColor(index), stroke: "var(--surface)", strokeWidth: 2 }}
                animationDuration={500}
              />
            ),
          )}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
