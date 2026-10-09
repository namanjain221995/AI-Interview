import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip, type TooltipContentProps } from "recharts";

import { ScoreBar } from "@/components/domain/ScoreBar";
import { formatScore, truncate } from "@/lib/format";
import { useElementWidth } from "@/lib/hooks/useElementWidth";
import { cn } from "@/lib/utils";

import { ChartFrame, ChartLegend, ChartTooltipCard, type LegendItem } from "./ChartFrame";
import { AXIS_TICK, GRID_STROKE, MUTED_SERIES, seriesColor } from "./chartTheme";

export interface RadarDatum {
  key: string;
  label: string;
  /** 0-max; null when there is no evidence yet. */
  score: number | null;
  /** Optional comparison (target level, cohort average). */
  benchmark?: number | null;
}

export interface CompetencyRadarProps {
  data: RadarDatum[];
  max?: number;
  seriesLabel?: string;
  benchmarkLabel?: string;
  height?: number;
  title?: string;
  description?: string;
  tableToggle?: boolean;
  className?: string;
}

interface AngleTickProps {
  x?: number | string;
  y?: number | string;
  cx?: number;
  cy?: number;
  payload?: { value: string };
  textAnchor?: string;
}

function AngleTick({ x = 0, y = 0, cx = 0, cy = 0, payload, maxChars = 20 }: AngleTickProps & { maxChars?: number }) {
  const nx = Number(x);
  const ny = Number(y);
  const dx = nx - cx;
  const dy = ny - cy;
  const anchor = Math.abs(dx) < 8 ? "middle" : dx > 0 ? "start" : "end";
  const offsetX = anchor === "start" ? 6 : anchor === "end" ? -6 : 0;
  const offsetY = Math.abs(dx) < 8 ? (dy < 0 ? -6 : 14) : 4;
  return (
    <text x={nx + offsetX} y={ny + offsetY} textAnchor={anchor} fill={AXIS_TICK.fill} fontSize={AXIS_TICK.fontSize} fontFamily={AXIS_TICK.fontFamily}>
      {truncate(payload?.value ?? "", Math.abs(dx) < 8 ? maxChars + 8 : maxChars)}
    </text>
  );
}

/** Below this plot width the axis labels can't fit around a radar; the profile renders as bars. */
const RADAR_MIN_WIDTH = 440;

/**
 * Competency profile on a 0-max radial scale. Falls back to bars below three
 * competencies and on narrow cards (phones), where radar labels would be clipped.
 */
export function CompetencyRadar({
  data,
  max = 10,
  seriesLabel = "This interview",
  benchmarkLabel = "Target",
  height = 300,
  title,
  description,
  tableToggle = true,
  className,
}: CompetencyRadarProps) {
  const [frameRef, width] = useElementWidth<HTMLDivElement>();
  const hasBenchmark = data.some((d) => typeof d.benchmark === "number");
  const rows = data.map((d) => ({ ...d, scoreValue: d.score ?? 0, benchmarkValue: d.benchmark ?? null }));
  const color = seriesColor(0);

  const legend: LegendItem[] = [{ key: "score", label: seriesLabel, color, shape: "line" }];
  if (hasBenchmark) legend.push({ key: "benchmark", label: benchmarkLabel, color: MUTED_SERIES, shape: "dashed" });

  const strongest = [...data].filter((d) => d.score !== null).sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
  const summary = data.length
    ? `${data.length} competencies scored out of ${max}.${strongest ? ` Highest: ${strongest.label} at ${formatScore(strongest.score, max === 10 ? 10 : 100)}.` : ""}`
    : "No competency scores yet.";

  const table = {
    caption: title ?? "Competency scores",
    columns: [
      { key: "label", label: "Competency" },
      { key: "score", label: seriesLabel, numeric: true },
      ...(hasBenchmark ? [{ key: "benchmark", label: benchmarkLabel, numeric: true }] : []),
    ],
    rows: data.map((d) => ({
      label: d.label,
      score: d.score === null ? "No evidence" : formatScore(d.score, max === 10 ? 10 : 100),
      benchmark: typeof d.benchmark === "number" ? formatScore(d.benchmark, max === 10 ? 10 : 100) : "—",
    })),
  };

  // The width is measured on a wrapper that both variants share, so the switch works both ways.
  const narrow = width > 0 && width < RADAR_MIN_WIDTH;
  // Side labels get shorter on mid-size cards; the tooltip and table keep full names.
  const labelChars = width === 0 || width >= 640 ? 20 : width >= 520 ? 16 : 13;
  if (data.length < 3 || narrow) {
    return (
      <div ref={frameRef} className={cn("min-w-0", className)}>
        <ChartFrame title={title} description={description} table={table} summary={summary} tableToggle={false}>
          <div className="grid grid-cols-1 gap-4">
            {data.map((d) => (
              <ScoreBar key={d.key} label={d.label} value={d.score} max={max === 100 ? 100 : 10} target={d.benchmark ?? null} />
            ))}
          </div>
        </ChartFrame>
      </div>
    );
  }

  return (
    <div ref={frameRef} className={cn("min-w-0", className)}>
      <ChartFrame
        title={title}
        description={description}
        table={table}
        summary={summary}
        tableToggle={tableToggle}
        legend={hasBenchmark ? <ChartLegend items={legend} /> : undefined}
      >
        <ResponsiveContainer width="100%" height={height}>
          <RadarChart data={rows} outerRadius="70%" margin={{ top: 16, right: 40, bottom: 16, left: 40 }}>
            <PolarGrid stroke={GRID_STROKE} gridType="polygon" />
            <PolarAngleAxis dataKey="label" tick={(props: AngleTickProps) => <AngleTick {...props} maxChars={labelChars} />} tickLine={false} />
            <PolarRadiusAxis domain={[0, max]} tick={false} axisLine={false} tickCount={6} />
            {hasBenchmark ? (
              <Radar
                name={benchmarkLabel}
                dataKey="benchmarkValue"
                stroke={MUTED_SERIES}
                strokeWidth={1.5}
                strokeDasharray="4 3"
                fill="transparent"
                isAnimationActive={false}
              />
            ) : null}
            <Radar
              name={seriesLabel}
              dataKey="scoreValue"
              stroke={color}
              strokeWidth={2}
              fill={color}
              fillOpacity={0.1}
              dot={{ r: 3.5, fill: color, stroke: "var(--surface)", strokeWidth: 2 }}
              activeDot={{ r: 5, fill: color, stroke: "var(--surface)", strokeWidth: 2 }}
              animationDuration={500}
            />
            <Tooltip
              cursor={false}
              content={(props: TooltipContentProps) => {
                if (!props.active || !props.payload?.length) return null;
                const datum = props.payload[0]?.payload as (RadarDatum & { scoreValue: number }) | undefined;
                if (!datum) return null;
                const tooltipRows = [
                  {
                    key: "score",
                    label: seriesLabel,
                    value: datum.score === null ? "No evidence" : formatScore(datum.score, max === 10 ? 10 : 100),
                    color,
                  },
                ];
                if (hasBenchmark && typeof datum.benchmark === "number") {
                  tooltipRows.push({ key: "benchmark", label: benchmarkLabel, value: formatScore(datum.benchmark, max === 10 ? 10 : 100), color: MUTED_SERIES });
                }
                return <ChartTooltipCard title={datum.label} rows={tooltipRows} />;
              }}
            />
          </RadarChart>
        </ResponsiveContainer>
      </ChartFrame>
    </div>
  );
}
