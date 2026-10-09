/**
 * Analytics transforms: funnel conversion, score histograms and per-job /
 * per-round summaries. Server aggregates are preferred; when the overview
 * does not break something down, it is derived from a sample of recent
 * sessions (and labelled as such on screen).
 */

import { labelFor, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { FunnelStage, SessionSummary } from "@/lib/types";

import { isRecord, numberOrNull, stringOrNull } from "../api/normalize";
import type { ScoreBucketDTO, ScoreGroupDTO } from "../api/types";

// -------------------------------------------------------------------- funnel

/** The core funnel the dashboard shows (the API returns more stages, in order). */
export const CORE_FUNNEL = ["invited", "started", "completed", "reviewed"] as const;

/** Short labels for known stage keys (the API's labels are used for anything else). */
export const FUNNEL_LABELS: Record<string, string> = {
  invited: "Invited",
  opened: "Opened invite",
  verified: "Verified",
  started: "Started",
  completed: "Completed",
  reviewed: "Reviewed",
  report_released: "Feedback shared",
};

export interface FunnelStep {
  stage: string;
  label: string;
  count: number;
  /** Share of the previous shown stage that reached this one (null for the first). */
  fromPrevious: number | null;
  /** Share of the first shown stage. */
  fromStart: number | null;
}

/**
 * Funnel steps in the API's order (it is meaningful: invited, opened,
 * verified, started, completed, reviewed, feedback released), optionally
 * limited to some stage keys. Conversion is computed between shown stages.
 */
export function funnelSteps(stages: readonly FunnelStage[], { only }: { only?: readonly string[] } = {}): FunnelStep[] {
  const shown = only ? stages.filter((s) => only.includes(s.stage)) : [...stages];
  const first = shown[0]?.count ?? 0;
  return shown.map((stage, index) => {
    const previous = index > 0 ? shown[index - 1]!.count : null;
    return {
      stage: stage.stage,
      label: FUNNEL_LABELS[stage.stage] ?? stage.label ?? stage.stage,
      count: stage.count,
      fromPrevious: previous === null ? null : previous > 0 ? stage.count / previous : 0,
      fromStart: first > 0 ? stage.count / first : null,
    };
  });
}

// ----------------------------------------------------------------- histogram

export interface ScoreBucket {
  min: number;
  max: number;
  label: string;
  count: number;
}

/** 0-100 scores into equal buckets; 100 lands in the last bucket. */
export function histogram(scores: readonly number[], bucketSize = 10): ScoreBucket[] {
  const size = Math.max(1, bucketSize);
  const count = Math.ceil(100 / size);
  const buckets: ScoreBucket[] = Array.from({ length: count }, (_, index) => {
    const min = index * size;
    const max = Math.min(100, min + size);
    return { min, max, label: `${min}–${max}`, count: 0 };
  });
  for (const raw of scores) {
    if (!Number.isFinite(raw)) continue;
    const score = Math.min(100, Math.max(0, raw));
    const index = Math.min(count - 1, Math.floor(score / size));
    buckets[index]!.count += 1;
  }
  return buckets;
}

/** Server histogram (list of buckets or `{"60-70": n}` map) normalised to ScoreBucket[]. */
export function bucketsFromServer(raw: readonly ScoreBucketDTO[] | Record<string, number> | null | undefined): ScoreBucket[] | null {
  if (!raw) return null;
  const parse = (label: string, count: number, min?: number | null, max?: number | null): ScoreBucket | null => {
    let lo = min ?? null;
    let hi = max ?? null;
    if (lo === null || hi === null) {
      const match = /(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)/.exec(label);
      if (match) {
        lo = Number(match[1]);
        hi = Number(match[2]);
      }
    }
    if (lo === null || hi === null) return null;
    return { min: lo, max: hi, label: `${lo}–${hi}`, count };
  };
  const out: ScoreBucket[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const bucket = parse(stringOrNull(item.label) ?? stringOrNull(item.bucket) ?? "", item.count ?? 0, numberOrNull(item.min), numberOrNull(item.max));
      if (bucket) out.push(bucket);
    }
  } else if (isRecord(raw)) {
    for (const [label, count] of Object.entries(raw)) {
      const bucket = parse(label, numberOrNull(count) ?? 0);
      if (bucket) out.push(bucket);
    }
  }
  if (!out.length) return null;
  // Ten-point buckets are laid onto the full 0-100 grid so the axis always spans the whole scale.
  if (out.every((b) => b.max - b.min === 10 && b.min % 10 === 0)) {
    const grid = histogram([]);
    for (const b of out) grid[Math.min(9, b.min / 10)]!.count += b.count;
    return grid;
  }
  return out.sort((a, b) => a.min - b.min);
}

// ------------------------------------------------------------------ summaries

export interface Summary {
  count: number;
  mean: number | null;
  median: number | null;
  p25: number | null;
  p75: number | null;
  min: number | null;
  max: number | null;
}

/** Linear-interpolated quantile of an ascending array. */
export function quantile(sorted: readonly number[], q: number): number | null {
  if (sorted.length === 0) return null;
  const position = (sorted.length - 1) * Math.min(1, Math.max(0, q));
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  const weight = position - lower;
  return sorted[lower]! * (1 - weight) + sorted[upper]! * weight;
}

export function summarize(values: readonly number[]): Summary {
  const sorted = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (sorted.length === 0) return { count: 0, mean: null, median: null, p25: null, p75: null, min: null, max: null };
  return {
    count: sorted.length,
    mean: sorted.reduce((sum, v) => sum + v, 0) / sorted.length,
    median: quantile(sorted, 0.5),
    p25: quantile(sorted, 0.25),
    p75: quantile(sorted, 0.75),
    min: sorted[0]!,
    max: sorted[sorted.length - 1]!,
  };
}

export interface ScoreGroup {
  key: string;
  label: string;
  /** Individual scores when known (strip plot). */
  scores: number[];
  summary: Summary;
  started: number;
  completed: number;
  completionRate: number | null;
  medianDurationSeconds: number | null;
  integrityFlags: number;
}

const ENDED = new Set(["completed", "aborted", "failed"]);

/**
 * Groups a session sample by job or round: score summary of completed,
 * scored sessions, completion rate among finished sessions, median duration
 * and integrity flags per session.
 */
export function groupSessions(sessions: readonly SessionSummary[], by: "job" | "round"): ScoreGroup[] {
  const groups = new Map<string, { label: string; items: SessionSummary[] }>();
  for (const session of sessions) {
    const key = by === "job" ? session.job_id : session.round?.round_type ?? session.round_id;
    // Rounds are compared by type across jobs ("Technical screening"), jobs by title.
    const label = by === "job" ? (session.job?.title ?? "Unknown job") : session.round ? labelFor(ROUND_TYPE_LABELS, session.round.round_type) : "Unknown round";
    if (!key) continue;
    const entry = groups.get(key) ?? { label, items: [] };
    entry.items.push(session);
    groups.set(key, entry);
  }
  return [...groups.entries()]
    .map(([key, { label, items }]) => {
      const finished = items.filter((s) => ENDED.has(s.status));
      const completed = items.filter((s) => s.status === "completed");
      const scores = completed.map((s) => s.overall_score).filter((v): v is number => typeof v === "number");
      const durations = completed.map((s) => s.elapsed_seconds).filter((v) => Number.isFinite(v) && v > 0);
      return {
        key,
        label,
        scores,
        summary: summarize(scores),
        started: items.length,
        completed: completed.length,
        completionRate: finished.length ? completed.length / finished.length : null,
        medianDurationSeconds: summarize(durations).median,
        integrityFlags: items.reduce((sum, s) => sum + (s.integrity_flags ?? 0), 0),
      };
    })
    .sort((a, b) => b.started - a.started || a.label.localeCompare(b.label));
}

/** Server breakdown (scores_by_job / scores_by_round) normalised to ScoreGroup[]. */
export function groupsFromServer(raw: readonly ScoreGroupDTO[] | null | undefined): ScoreGroup[] | null {
  if (!raw || raw.length === 0) return null;
  return raw.map((item, index) => {
    const scores = Array.isArray(item.scores) ? item.scores.filter((v) => Number.isFinite(v)) : [];
    const computed = summarize(scores);
    const count = item.count ?? item.completed ?? item.sessions ?? computed.count;
    return {
      key: item.key ?? item.id ?? item.job_id ?? item.round_id ?? item.round_type ?? String(index),
      label: item.label ?? item.title ?? item.name ?? item.round_type ?? "Unknown",
      scores,
      summary: {
        count,
        mean: item.average ?? item.average_score ?? item.mean ?? computed.mean,
        median: item.median ?? computed.median,
        p25: item.p25 ?? computed.p25,
        p75: item.p75 ?? computed.p75,
        min: item.min ?? computed.min,
        max: item.max ?? computed.max,
      },
      started: item.started ?? item.sessions ?? count,
      completed: item.completed ?? count,
      completionRate: item.completion_rate ?? null,
      medianDurationSeconds: item.median_duration_seconds ?? item.average_duration_seconds ?? null,
      integrityFlags: 0,
    };
  });
}

/** Share of finished sessions that completed, plus counts. */
export function completionStats(sessions: readonly SessionSummary[]): { finished: number; completed: number; rate: number | null } {
  const finished = sessions.filter((s) => ENDED.has(s.status)).length;
  const completed = sessions.filter((s) => s.status === "completed").length;
  return { finished, completed, rate: finished ? completed / finished : null };
}

export function integrityByType(raw: Record<string, number> | Array<{ type: string; count: number }> | null | undefined): Array<{ type: string; count: number }> {
  if (!raw) return [];
  const list = Array.isArray(raw) ? raw.map((r) => ({ type: r.type, count: r.count })) : Object.entries(raw).map(([type, count]) => ({ type, count }));
  return list.filter((r) => r.count > 0).sort((a, b) => b.count - a.count);
}
