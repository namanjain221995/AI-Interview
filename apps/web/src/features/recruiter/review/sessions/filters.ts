/**
 * Session list filters live in the URL (shareable, survive back/forward).
 * `?status=completed` (used by the /app/reports redirect) maps onto the
 * matching preset.
 */

import type { Recommendation, SessionStatus, SessionSummary } from "@/lib/types";

import type { SessionFilters } from "../api/types";

export const VIEW_PRESETS = ["all", "live", "completed", "needs_review", "ended_early"] as const;
export type ViewPreset = (typeof VIEW_PRESETS)[number];

export const VIEW_LABELS: Record<ViewPreset, string> = {
  all: "All",
  live: "Live now",
  completed: "Completed",
  needs_review: "Needs a decision",
  ended_early: "Ended early",
};

const PRESET_STATUSES: Record<ViewPreset, SessionStatus[] | undefined> = {
  all: undefined,
  live: ["live", "paused"],
  completed: ["completed"],
  needs_review: ["completed"],
  ended_early: ["aborted", "failed"],
};

const RECOMMENDATIONS: readonly Recommendation[] = ["strong_proceed", "proceed", "human_review", "borderline_evidence", "insufficient_evidence"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export interface SessionListState {
  view: ViewPreset;
  filters: SessionFilters;
  page: number;
}

export function readSessionListState(params: URLSearchParams): SessionListState {
  let view = (params.get("view") as ViewPreset | null) ?? "all";
  if (!VIEW_PRESETS.includes(view)) view = "all";
  const status = params.getAll("status").flatMap((s) => s.split(",")).filter(Boolean);
  if (!params.get("view") && status.length) {
    const key = status.slice().sort().join(",");
    view = key === "completed" ? "completed" : key === "live,paused" || key === "live" ? "live" : key === "aborted,failed" ? "ended_early" : view;
  }
  const recommendation = params.get("rec");
  const from = params.get("from");
  const to = params.get("to");
  return {
    view,
    page: Math.max(0, Number(params.get("page") ?? 0) || 0),
    filters: {
      status: PRESET_STATUSES[view],
      needs_review: view === "needs_review" || undefined,
      job_id: params.get("job") ?? undefined,
      round_id: params.get("round") ?? undefined,
      recommendation: recommendation && (RECOMMENDATIONS as readonly string[]).includes(recommendation) ? (recommendation as Recommendation) : undefined,
      from: from && DATE.test(from) ? from : undefined,
      to: to && DATE.test(to) ? to : undefined,
      q: params.get("q") ?? undefined,
    },
  };
}

export function writeSessionListState(state: { view: ViewPreset; filters: SessionFilters; page: number }): URLSearchParams {
  const next = new URLSearchParams();
  if (state.view !== "all") next.set("view", state.view);
  if (state.filters.job_id) next.set("job", state.filters.job_id);
  if (state.filters.round_id) next.set("round", state.filters.round_id);
  if (state.filters.recommendation) next.set("rec", state.filters.recommendation);
  if (state.filters.from) next.set("from", state.filters.from);
  if (state.filters.to) next.set("to", state.filters.to);
  if (state.filters.q) next.set("q", state.filters.q);
  if (state.page > 0) next.set("page", String(state.page));
  return next;
}

/** True when any filter beyond the view preset is active. */
export function hasExtraFilters(filters: SessionFilters): boolean {
  return !!(filters.job_id || filters.round_id || filters.recommendation || filters.from || filters.to || filters.q);
}

/**
 * Client-side safety net for filters the server may not apply: harmless when
 * the server already filtered, and keeps the visible rows honest when not.
 */
export function applyClientFilters(items: readonly SessionSummary[], filters: SessionFilters): SessionSummary[] {
  const q = filters.q?.trim().toLowerCase();
  const fromMs = filters.from ? Date.parse(`${filters.from}T00:00:00`) : null;
  const toMs = filters.to ? Date.parse(`${filters.to}T23:59:59.999`) : null;
  return items.filter((s) => {
    if (filters.status?.length && !filters.status.includes(s.status)) return false;
    if (filters.job_id && s.job_id !== filters.job_id) return false;
    if (filters.round_id && s.round_id !== filters.round_id) return false;
    if (filters.recommendation && s.recommendation !== filters.recommendation) return false;
    if (filters.needs_review && (s.status !== "completed" || s.review_decision)) return false;
    // The API filters on creation time; mirror it so client and server agree.
    const when = Date.parse(s.created_at);
    if (fromMs !== null && Number.isFinite(when) && when < fromMs) return false;
    if (toMs !== null && Number.isFinite(when) && when > toMs) return false;
    if (q && !`${s.candidate?.full_name ?? ""} ${s.candidate?.email ?? ""} ${s.job?.title ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
}
