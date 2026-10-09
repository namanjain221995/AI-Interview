/**
 * Score bands used to tint score visuals. Thresholds mirror the default
 * PassingGuidance (strong 80 / proceed 65 / borderline 50 on the 0-100 scale).
 * A colour is never the only signal: every score visual also prints the value.
 */

export type ScoreTone = "success" | "accent" | "warning" | "danger" | "neutral";

export interface BandThresholds {
  strong: number;
  proceed: number;
  borderline: number;
}

export const DEFAULT_BANDS: BandThresholds = { strong: 80, proceed: 65, borderline: 50 };

/** Normalises any score to 0-100 using its scale. */
export function toPercentScale(value: number, max: number): number {
  if (max <= 0) return 0;
  return (value / max) * 100;
}

export function scoreTone(value: number | null | undefined, max = 100, bands: BandThresholds = DEFAULT_BANDS): ScoreTone {
  if (value === null || value === undefined || Number.isNaN(value)) return "neutral";
  const pct = toPercentScale(value, max);
  if (pct >= bands.strong) return "success";
  if (pct >= bands.proceed) return "accent";
  if (pct >= bands.borderline) return "warning";
  return "danger";
}

export const TONE_STROKE: Record<ScoreTone, string> = {
  success: "var(--success)",
  accent: "var(--accent)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  neutral: "var(--primary)",
};

export const TONE_TRACK: Record<ScoreTone, string> = {
  success: "var(--success-soft)",
  accent: "var(--accent-soft)",
  warning: "var(--warning-soft)",
  danger: "var(--danger-soft)",
  neutral: "var(--track)",
};

export const TONE_BG: Record<ScoreTone, string> = {
  success: "bg-success",
  accent: "bg-accent",
  warning: "bg-warning",
  danger: "bg-danger",
  neutral: "bg-primary",
};
