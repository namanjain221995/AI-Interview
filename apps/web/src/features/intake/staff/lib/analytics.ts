/**
 * Intake analytics transforms (pure): funnel steps, ordered band/decision rows,
 * competency averages on the 1-5 scale, and plain-language summaries for the chart
 * table twins. Server order is kept wherever it carries meaning.
 */

import { formatPercent } from "@/lib/format";

import type { FunnelStep } from "@/features/recruiter/review/lib/analytics";

import { BAND_LABELS, competencyLabel, OUTCOME_LABELS, TARGET_ROLE_LABELS, TRAINING_MODULE_LABELS } from "../labels";
import { DECISION_BANDS, DECISION_OUTCOMES, type IntakeAnalytics, type TargetRole } from "../types";
import { decimalToNumber, formatTotal } from "./decimals";

const FUNNEL: ReadonlyArray<{ key: keyof IntakeAnalytics["funnel"]; label: string }> = [
  { key: "invitations", label: "Invited" },
  { key: "onboarded", label: "Onboarded" },
  { key: "started", label: "Started" },
  { key: "completed", label: "Completed" },
  { key: "evaluated", label: "Evaluated" },
  { key: "decided", label: "Decided" },
];

export function funnelSteps(funnel: IntakeAnalytics["funnel"]): FunnelStep[] {
  const first = funnel.invitations;
  return FUNNEL.map((step, index) => {
    const count = funnel[step.key];
    const previous = index === 0 ? null : funnel[FUNNEL[index - 1]!.key];
    return {
      stage: step.key,
      label: step.label,
      count,
      fromPrevious: previous === null ? null : previous > 0 ? count / previous : null,
      fromStart: first > 0 ? count / first : null,
    };
  });
}

export interface CountRow {
  key: string;
  label: string;
  value: number;
  hint?: string;
}

/** Bands in kit order (strong accept first), every band present even at zero. */
export function bandRows(bands: IntakeAnalytics["bands"]): CountRow[] {
  const byKey = new Map(bands.map((b) => [b.band, b]));
  const known = DECISION_BANDS.map((band) => ({ key: band, label: byKey.get(band)?.label || BAND_LABELS[band], value: byKey.get(band)?.count ?? 0 }));
  const extra = bands.filter((b) => !(DECISION_BANDS as readonly string[]).includes(b.band)).map((b) => ({ key: b.band, label: b.label, value: b.count }));
  return [...known, ...extra];
}

export function decisionRows(decisions: IntakeAnalytics["decisions"]): CountRow[] {
  const byKey = new Map(decisions.map((d) => [d.outcome, d]));
  return DECISION_OUTCOMES.map((outcome) => ({ key: outcome, label: byKey.get(outcome)?.label || OUTCOME_LABELS[outcome], value: byKey.get(outcome)?.count ?? 0 }));
}

/** Competency averages as 1-5 numbers for bars (null averages stay in the table as "—"). */
export function competencyRows(rows: IntakeAnalytics["competency_averages"]): Array<CountRow & { average: string | null }> {
  return rows.map((row) => ({
    key: row.key,
    label: row.label || competencyLabel(row.key),
    value: decimalToNumber(row.average) ?? 0,
    average: row.average,
    hint: `${row.scored} scored${row.insufficient ? `, ${row.insufficient} without enough evidence` : ""}`,
  }));
}

export function stageRows(rows: IntakeAnalytics["stage_completion"]): CountRow[] {
  return rows.map((row) => {
    const total = row.completed + row.incomplete;
    const rate = row.rate ?? (total ? row.completed / total : 0);
    return { key: row.stage, label: row.title || row.stage, value: rate, hint: `${row.completed} of ${total} completed` };
  });
}

/** Technical weakness keys carry a language suffix ("verification_incorrect:python"). */
export function weaknessRows(rows: IntakeAnalytics["technical_weaknesses"]): CountRow[] {
  return [...rows].sort((a, b) => b.count - a.count).map((row) => ({ key: row.key, label: row.label || row.key.replace(/[_:]/g, " "), value: row.count }));
}

export function trainingRows(rows: IntakeAnalytics["training_needs"]): CountRow[] {
  return [...rows].sort((a, b) => b.count - a.count).map((row) => ({ key: row.module, label: row.label || TRAINING_MODULE_LABELS[row.module] || row.module, value: row.count }));
}

export function roleLabel(role: string, label?: string | null): string {
  return TARGET_ROLE_LABELS[role as TargetRole] ?? (label || (role ? role.replace(/_/g, " ") : "Not chosen"));
}

/** Plain-language summary of a count list for screen readers. */
export function summarize(rows: readonly CountRow[], unit = ""): string {
  if (!rows.length) return "No data in this period.";
  return rows.map((r) => `${r.label}: ${r.value}${unit}`).join(", ") + ".";
}

export function overrideText(overrides: IntakeAnalytics["overrides"], decided: number): string {
  if (!decided) return "No decisions in this period.";
  const rate = overrides.rate ?? (decided ? overrides.count / decided : null);
  return `${overrides.count} of ${decided} decisions differed from the AI recommendation${rate !== null ? ` (${formatPercent(rate)})` : ""}.`;
}

export function averageText(value: string | null): string {
  return value ? `${formatTotal(value)} / 5` : "—";
}
