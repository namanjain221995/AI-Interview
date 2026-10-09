import type { ReactNode } from "react";

import { formatScore, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { ConfidenceMeter } from "./ConfidenceMeter";
import { scoreTone, TONE_BG, type BandThresholds, type ScoreTone } from "./scoreBands";

export interface ScoreBarProps {
  label: ReactNode;
  value: number | null | undefined;
  /** 10 for competencies (default), 100 for rubric categories. */
  max?: 10 | 100;
  /** 0-1; renders a confidence meter under the bar. */
  confidence?: number | null;
  evidenceCount?: number;
  /** Marks competencies that still lack sufficient evidence. */
  insufficient?: boolean;
  tone?: ScoreTone | "auto";
  bands?: BandThresholds;
  /** Optional target marker on the bar (same scale as value). */
  target?: number | null;
  className?: string;
}

export function ScoreBar({ label, value, max = 10, confidence, evidenceCount, insufficient, tone = "auto", bands, target, className }: ScoreBarProps) {
  const has = typeof value === "number" && Number.isFinite(value);
  const pct = has ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  const resolvedTone = tone === "auto" ? scoreTone(value, max, bands) : tone;
  const targetPct = typeof target === "number" ? Math.min(100, Math.max(0, (target / max) * 100)) : null;

  return (
    <div className={cn("grid content-start gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-sm font-medium text-fg">{label}</span>
        <span className="shrink-0 text-sm font-semibold text-fg tabular-nums">
          {has ? formatScore(value, max) : "—"}
          <span className="ml-0.5 text-xs font-normal text-fg-subtle">/{max}</span>
        </span>
      </div>
      <div
        className="relative h-1.5 w-full rounded-full bg-track"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={has ? value : undefined}
        aria-valuetext={has ? `${formatScore(value, max)} out of ${max}` : "No score yet"}
        aria-label={typeof label === "string" ? label : undefined}
      >
        {has ? (
          <div
            className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration-500 ease-out", TONE_BG[resolvedTone], insufficient && "opacity-50")}
            style={{ width: `${pct}%` }}
          />
        ) : null}
        {targetPct !== null ? (
          <span aria-hidden="true" className="absolute -top-1 -bottom-1 w-0.5 rounded-full bg-fg/70" style={{ left: `calc(${targetPct}% - 1px)` }} />
        ) : null}
      </div>
      {confidence !== undefined || evidenceCount !== undefined || insufficient ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {confidence !== undefined ? <ConfidenceMeter value={confidence} /> : null}
          {evidenceCount !== undefined ? <span className="text-xs text-fg-muted">{pluralize(evidenceCount, "evidence item")}</span> : null}
          {insufficient ? <span className="text-xs font-medium text-warning-fg">Needs more evidence</span> : null}
        </div>
      ) : null}
    </div>
  );
}
