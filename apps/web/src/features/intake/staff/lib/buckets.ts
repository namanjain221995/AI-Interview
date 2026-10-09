/**
 * Intake list state lives in the URL (shareable, survives back/forward):
 * `?bucket=&q=&job=&role=&band=&outcome=&reviewer=&batch=&from=&to=&sort=&page=`.
 * These helpers translate between the URL, the API query and the bucket tabs.
 */

import { OPTIONAL_BUCKETS } from "../labels";
import {
  DECISION_BANDS,
  DECISION_OUTCOMES,
  INTAKE_BUCKETS,
  TARGET_ROLES,
  type CountedBucket,
  type DecisionBand,
  type DecisionOutcome,
  type IntakeBucket,
  type IntakeListFilters,
  type IntakeListQuery,
  type IntakeSort,
  type IntakeStats,
  type TargetRole,
} from "../types";

export const PAGE_SIZE = 25;
export const DEFAULT_SORT: IntakeSort = "-created_at";
const SORTS: readonly IntakeSort[] = ["-created_at", "created_at", "-completed_at", "-score", "score", "candidate"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export interface IntakeListState {
  filters: IntakeListFilters;
  page: number;
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | undefined {
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

export function readListState(params: URLSearchParams): IntakeListState {
  const from = params.get("from");
  const to = params.get("to");
  return {
    page: Math.max(0, Math.floor(Number(params.get("page") ?? 0)) || 0),
    filters: {
      bucket: oneOf<IntakeBucket>(params.get("bucket"), INTAKE_BUCKETS) ?? "all",
      q: params.get("q")?.trim() || undefined,
      job_id: params.get("job") || undefined,
      target_role: oneOf<TargetRole>(params.get("role"), TARGET_ROLES),
      band: oneOf<DecisionBand>(params.get("band"), DECISION_BANDS),
      outcome: oneOf<DecisionOutcome>(params.get("outcome"), DECISION_OUTCOMES),
      reviewer_id: params.get("reviewer") || undefined,
      batch_id: params.get("batch") || undefined,
      created_from: from && DATE.test(from) ? from : undefined,
      created_to: to && DATE.test(to) ? to : undefined,
      sort: oneOf<IntakeSort>(params.get("sort"), SORTS) ?? DEFAULT_SORT,
    },
  };
}

export function writeListState({ filters, page }: IntakeListState): URLSearchParams {
  const next = new URLSearchParams();
  if (filters.bucket !== "all") next.set("bucket", filters.bucket);
  if (filters.q) next.set("q", filters.q);
  if (filters.job_id) next.set("job", filters.job_id);
  if (filters.target_role) next.set("role", filters.target_role);
  if (filters.band) next.set("band", filters.band);
  if (filters.outcome) next.set("outcome", filters.outcome);
  if (filters.reviewer_id) next.set("reviewer", filters.reviewer_id);
  if (filters.batch_id) next.set("batch", filters.batch_id);
  if (filters.created_from) next.set("from", filters.created_from);
  if (filters.created_to) next.set("to", filters.created_to);
  if (filters.sort !== DEFAULT_SORT) next.set("sort", filters.sort);
  if (page > 0) next.set("page", String(page));
  return next;
}

/** Local calendar day -> ISO instant at local midnight (optionally a day later: exclusive end). */
export function dayBoundary(day: string | undefined, addDays = 0): string | undefined {
  if (!day || !DATE.test(day)) return undefined;
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  const date = new Date(y, m - 1, d + addDays);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

type Params = Record<string, string | number | undefined>;

/** Filters shared by GET /intake/interviews and GET /intake/stats. */
function filterParams(filters: IntakeListFilters): Params {
  return {
    q: filters.q || undefined,
    job_id: filters.job_id,
    target_role: filters.target_role,
    band: filters.band,
    outcome: filters.outcome,
    reviewer_id: filters.reviewer_id,
    batch_id: filters.batch_id,
    created_from: dayBoundary(filters.created_from),
    created_to: dayBoundary(filters.created_to, 1),
  };
}

/** GET /intake/interviews params (bucket "all" is the server default and stays out of the URL). */
export function listParams(query: IntakeListQuery): Params {
  return {
    ...filterParams(query),
    bucket: query.bucket === "all" ? undefined : query.bucket,
    sort: query.sort === DEFAULT_SORT ? undefined : query.sort,
    limit: query.limit,
    offset: query.offset,
  };
}

/** GET /intake/stats params: the list filters minus bucket, sort and paging. */
export function statsParams(filters: IntakeListFilters): Params {
  return filterParams(filters);
}

/** True when a filter beyond the bucket tab narrows the list. */
export function hasExtraFilters(filters: IntakeListFilters): boolean {
  return Boolean(filters.q || filters.job_id || filters.target_role || filters.band || filters.outcome || filters.reviewer_id || filters.batch_id || filters.created_from || filters.created_to);
}

/** Count shown on a bucket tab ("all" = every invitation incl. drafts, as the list shows it). */
export function bucketCount(stats: IntakeStats | undefined, bucket: IntakeBucket): number | undefined {
  if (!stats) return undefined;
  if (bucket === "all") return stats.totals.invitations + stats.buckets.drafts;
  return stats.buckets[bucket as CountedBucket];
}

/** Tabs to render: expired and cancelled only when they hold rows or are selected. */
export function visibleBuckets(tabs: readonly IntakeBucket[], stats: IntakeStats | undefined, selected: IntakeBucket): IntakeBucket[] {
  return tabs.filter((bucket) => {
    if (!OPTIONAL_BUCKETS.has(bucket as CountedBucket)) return true;
    return bucket === selected || (bucketCount(stats, bucket) ?? 0) > 0;
  });
}
