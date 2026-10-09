/**
 * Pure helpers for the intake review page: tabs, deep links, evidence lookups and
 * stage timing.
 */

import { competencyLabel } from "../labels";
import type { ActivityKind, EvidenceRef, IntakeActivity, IntakeDetail, IntakeEvaluation, IntakeStage } from "../types";

export const REVIEW_TABS = ["overview", "stages", "reading", "topic", "scorecard", "recording", "integrity", "decision"] as const;
export type ReviewTab = (typeof REVIEW_TABS)[number];

export const REVIEW_TAB_LABELS: Record<ReviewTab, string> = {
  overview: "Overview",
  stages: "Stages & answers",
  reading: "Reading",
  topic: "Topic card",
  scorecard: "Scorecard",
  recording: "Recording",
  integrity: "Integrity",
  decision: "Decision",
};

export function readTab(value: string | null, fallback: ReviewTab = "overview"): ReviewTab {
  return value && (REVIEW_TABS as readonly string[]).includes(value) ? (value as ReviewTab) : fallback;
}

/** Interview-clock milliseconds as m:ss (h:mm:ss past an hour). */
export function formatClockMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export interface AnswerEvidence {
  competency: string;
  label: string;
  ref: EvidenceRef;
}

/** Evidence citing each answer (or activity capture), keyed by answer id then activity id. */
export function evidenceByAnswer(evaluation: IntakeEvaluation | null | undefined): Map<string, AnswerEvidence[]> {
  const map = new Map<string, AnswerEvidence[]>();
  if (!evaluation) return map;
  for (const competency of evaluation.competencies) {
    for (const ref of competency.evidence) {
      const key = ref.answer_id ?? (ref.activity_id ? `activity:${ref.activity_id}` : null);
      if (!key) continue;
      const list = map.get(key) ?? [];
      list.push({ competency: competency.key, label: competency.label || competencyLabel(competency.key), ref });
      map.set(key, list);
    }
  }
  return map;
}

export interface StageTiming {
  usedSeconds: number | null;
  budgetSeconds: number | null;
  /** used / budget, null when either is unknown. */
  ratio: number | null;
  over: boolean;
}

export function stageTiming(stage: Pick<IntakeStage, "started_s" | "ended_s" | "budget_s" | "status">, nowElapsed?: number | null): StageTiming {
  const end = stage.ended_s ?? (stage.status === "in_progress" && typeof nowElapsed === "number" ? nowElapsed : null);
  const used = stage.started_s !== null && end !== null ? Math.max(0, end - stage.started_s) : null;
  const budget = stage.budget_s && stage.budget_s > 0 ? stage.budget_s : null;
  const ratio = used !== null && budget ? used / budget : null;
  // Past the stage's soft cap (1.25 x budget, design 6.3).
  return { usedSeconds: used, budgetSeconds: budget, ratio, over: ratio !== null && ratio > 1.25 };
}

export function activityOf(detail: Pick<IntakeDetail, "activities"> | null | undefined, kind: ActivityKind): IntakeActivity | null {
  return detail?.activities.find((a) => a.kind === kind) ?? null;
}

/** Transcript to show for an activity: final when it exists, else the live one, with its provenance. */
export function activityTranscript(activity: IntakeActivity | null): { text: string | null; source: string | null; live: boolean } {
  if (!activity) return { text: null, source: null, live: false };
  if (activity.transcript.final) return { text: activity.transcript.final, source: activity.transcript.source, live: false };
  if (activity.transcript.live) return { text: activity.transcript.live, source: activity.transcript.source ?? "live_segments", live: true };
  return { text: null, source: activity.transcript.source, live: false };
}

/** Deep link inside the review page. */
export function reviewHref(intakeId: string, tab: ReviewTab, target: { question?: string | null; answer?: string | null } = {}): string {
  const params = new URLSearchParams({ tab });
  if (target.question) params.set("q", target.question);
  if (target.answer) params.set("a", target.answer);
  return `/app/intake/${encodeURIComponent(intakeId)}?${params.toString()}`;
}

/** Count of answered vs planned mandatory items in a stage. */
export function mandatoryCoverage(stage: Pick<IntakeStage, "items">): { covered: number; total: number } {
  const mandatory = stage.items.filter((i) => i.mandatory && i.status !== "not_applicable");
  return { covered: mandatory.filter((i) => i.status === "answered" || i.status === "prefilled").length, total: mandatory.length };
}
