import { useEffect, useState } from "react";

import { formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";

import { scoreTone, TONE_STROKE, TONE_TRACK, type BandThresholds, type ScoreTone } from "./scoreBands";

const SIZES = {
  sm: { box: 56, stroke: 5, value: "text-base", unit: "text-[10px]" },
  md: { box: 88, stroke: 7, value: "text-2xl", unit: "text-[11px]" },
  lg: { box: 120, stroke: 8, value: "text-[2rem]", unit: "text-xs" },
} as const;

export interface ScoreRingProps {
  value: number | null | undefined;
  /** Scale maximum: 100 for overall/rubric scores, 10 for competencies. */
  max?: 10 | 100;
  size?: keyof typeof SIZES;
  /** "auto" tints by score band; any other tone fixes the colour. */
  tone?: ScoreTone | "auto";
  bands?: BandThresholds;
  /** Text under the ring, e.g. "Overall". */
  label?: string;
  /** Accessible subject, e.g. "Overall score". */
  ariaLabel?: string;
  className?: string;
}

export function ScoreRing({ value, max = 100, size = "md", tone = "auto", bands, label, ariaLabel, className }: ScoreRingProps) {
  const spec = SIZES[size];
  const radius = (spec.box - spec.stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const has = typeof value === "number" && Number.isFinite(value);
  const fraction = has ? Math.min(1, Math.max(0, value / max)) : 0;
  const resolvedTone = tone === "auto" ? scoreTone(value, max, bands) : tone;

  // Draw the arc after mount so it sweeps in once; reduced motion skips via CSS.
  const [drawn, setDrawn] = useState(0);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrawn(fraction));
    return () => cancelAnimationFrame(frame);
  }, [fraction]);

  const subject = ariaLabel ?? label ?? "Score";
  const spoken = has ? `${subject}: ${formatScore(value, max)} out of ${max}` : `${subject}: not available yet`;

  return (
    <div className={cn("inline-flex flex-col items-center gap-2", className)}>
      <div className="relative" style={{ width: spec.box, height: spec.box }} role="img" aria-label={spoken}>
        <svg width={spec.box} height={spec.box} viewBox={`0 0 ${spec.box} ${spec.box}`} className="-rotate-90" aria-hidden="true">
          <circle cx={spec.box / 2} cy={spec.box / 2} r={radius} fill="none" stroke={TONE_TRACK[resolvedTone]} strokeWidth={spec.stroke} />
          {has ? (
            <circle
              cx={spec.box / 2}
              cy={spec.box / 2}
              r={radius}
              fill="none"
              stroke={TONE_STROKE[resolvedTone]}
              strokeWidth={spec.stroke}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - drawn)}
              style={{ transition: "stroke-dashoffset 700ms cubic-bezier(0.22, 1, 0.36, 1)" }}
            />
          ) : null}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center" aria-hidden="true">
          <span className={cn("leading-none font-semibold tracking-[-0.02em] text-fg", spec.value)}>{has ? formatScore(value, max) : "—"}</span>
          {has && size !== "sm" ? <span className={cn("mt-1 font-medium text-fg-subtle", spec.unit)}>of {max}</span> : null}
        </div>
      </div>
      {label ? <span className="text-xs font-medium text-fg-muted">{label}</span> : null}
    </div>
  );
}
