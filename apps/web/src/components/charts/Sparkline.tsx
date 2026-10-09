import { useId } from "react";

import { cn } from "@/lib/utils";

export interface SparklineProps {
  data: readonly number[];
  width?: number;
  height?: number;
  /** Line colour; defaults to the de-emphasis grey with the latest point in the accent. */
  color?: string;
  /** Fixed y-domain (e.g. [0, 100]); defaults to the data range. */
  domain?: [number, number];
  /** Accessible summary; without it the sparkline is decorative. */
  label?: string;
  showArea?: boolean;
  className?: string;
}

/** Tiny trend line for stat tiles. Pure SVG: no chart library on the critical path. */
export function Sparkline({ data, width = 96, height = 28, color, domain, label, showArea = true, className }: SparklineProps) {
  const gradientId = useId();
  if (data.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden="true" />;
  }
  const pad = 3;
  const min = domain ? domain[0] : Math.min(...data);
  const max = domain ? domain[1] : Math.max(...data);
  const span = max - min || 1;
  const step = (width - pad * 2) / (data.length - 1);
  const points = data.map((value, index) => {
    const x = pad + index * step;
    const y = pad + (height - pad * 2) * (1 - (value - min) / span);
    return [x, y] as const;
  });
  const line = points.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L${points[points.length - 1]![0].toFixed(2)},${height} L${points[0]![0].toFixed(2)},${height} Z`;
  const last = points[points.length - 1]!;
  const stroke = color ?? "var(--chart-muted)";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn("shrink-0 overflow-visible", className)}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {showArea ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color ?? "var(--accent)"} stopOpacity="0.12" />
              <stop offset="100%" stopColor={color ?? "var(--accent)"} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradientId})`} />
        </>
      ) : null}
      <path d={line} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={3} fill={color ?? "var(--accent)"} stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  );
}
