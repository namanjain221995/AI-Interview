/**
 * Chart styling shared by every chart. Colours are CSS variables, so charts
 * re-theme automatically on the dark `.theme-stage` surface.
 *
 * Categorical slots (validated with the dataviz palette checker: adjacent CVD
 * ΔE ≥ 8.4 and normal-vision ΔE ≥ 19.3 in both modes). Assign slots in this
 * fixed order by entity (e.g. competency order in the round config), never by
 * rank, and never generate a 9th colour.
 */
export const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
] as const;

export const MAX_SERIES = SERIES_COLORS.length;

export function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]!;
}

export const AXIS_TICK = { fill: "var(--text-muted)", fontSize: 12, fontFamily: "var(--font-sans)" } as const;
export const GRID_STROKE = "var(--chart-grid)";
export const AXIS_STROKE = "var(--chart-axis)";
export const MUTED_SERIES = "var(--chart-muted)";
