/**
 * Query and mutation hooks for the recruiter review screens. Every request
 * goes through `@/lib/api` and every key through `queryKeys`, so a mutation
 * can refresh exactly the screens it affects.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  AnalyticsFunnel,
  Competency,
  CompetencyAnalytics,
  IntegrityReviewUpdate,
  Job,
  MediaUrl,
  Report,
  ReviewCreate,
  ReviewDecision,
  Round,
  SessionStatus,
  SessionSummary,
} from "@/lib/types";

import {
  asList,
  asPage,
  isRecord,
  normalizeCoding,
  normalizeEvidence,
  normalizeIntegrityEvents,
  normalizeQuestions,
  normalizeReviews,
  normalizeScores,
  normalizeSessionIntegrity,
  normalizeTimeline,
  normalizeTranscript,
} from "./normalize";
import type {
  AnalyticsOverviewView,
  CompletionAnalyticsDTO,
  HintUsageAnalyticsDTO,
  IntegrityAnalyticsDTO,
  IntegrityEventView,
  IntegrityQueueFilters,
  ModelUsageItemView,
  ScoreDistributionsDTO,
  SessionDetailView,
  SessionFilters,
} from "./types";

/** Report row as the reports API returns it (policy mode and version list included). */
export type ReportView = Report & {
  candidate_report_mode?: string | null;
  versions?: Array<{ id: string; type: string; version: number; status: string; generated_at: string | null; released_to_candidate: boolean; released_at: string | null; created_at: string }>;
};

/** Session states where data is still changing and screens should refresh on their own. */
export const IN_PROGRESS_STATUSES: readonly SessionStatus[] = ["created", "preparing", "ready", "live", "paused", "completing"];
export const LIVE_STATUSES: readonly SessionStatus[] = ["live", "paused"];

export function isInProgress(status: SessionStatus | null | undefined): boolean {
  return !!status && IN_PROGRESS_STATUSES.includes(status);
}

export function isLive(status: SessionStatus | null | undefined): boolean {
  return !!status && LIVE_STATUSES.includes(status);
}

/** States where the live monitor is useful: about to start, running or paused (not wrapping up). */
export const WATCHABLE_STATUSES: readonly SessionStatus[] = ["created", "preparing", "ready", "live", "paused"];

export function isWatchable(status: SessionStatus | null | undefined): boolean {
  return !!status && WATCHABLE_STATUSES.includes(status);
}

// ------------------------------------------------------------------ sessions

export interface SessionListQuery extends SessionFilters {
  limit: number;
  offset: number;
}

/** Local calendar day (YYYY-MM-DD) -> ISO instant at local midnight, optionally a day later (exclusive end). */
export function dayStartIso(day: string | undefined, addDays = 0): string | undefined {
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return undefined;
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  const date = new Date(y, m - 1, d + addDays, 0, 0, 0, 0);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/**
 * Query params for GET /sessions. "Needs a decision" is the server's review queue
 * (`needs_review=true`: completed, no human decision yet), paged by the server.
 */
export function sessionListParams(query: SessionListQuery) {
  return {
    limit: query.limit,
    offset: query.offset,
    status: query.status && query.status.length ? query.status : undefined,
    needs_review: query.needs_review ? true : undefined,
    job_id: query.job_id,
    round_id: query.round_id,
    round_type: query.round_type && query.round_type.length ? query.round_type : undefined,
    recommendation: query.recommendation,
    created_from: dayStartIso(query.from),
    created_to: dayStartIso(query.to, 1),
    q: query.q?.trim() || undefined,
  };
}

export function useSessionList(query: SessionListQuery, options: { refetchInterval?: number | false } = {}) {
  const params = sessionListParams(query);
  return useQuery({
    queryKey: queryKeys.sessions.list(params),
    queryFn: async ({ signal }) => asPage<SessionSummary>(await api.get<unknown>("/sessions", { params, signal })),
    placeholderData: keepPreviousData,
    refetchInterval: options.refetchInterval,
  });
}

export function useSessionDetail(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.sessions.detail(sessionId),
    queryFn: ({ signal }) => api.get<SessionDetailView>(`/sessions/${sessionId}`, { signal }),
    enabled: !!sessionId,
    refetchInterval: (query) => (isInProgress(query.state.data?.status) ? 10_000 : false),
  });
}

interface PartOptions {
  enabled?: boolean;
  /** Refresh periodically (live sessions). */
  live?: boolean;
}

export function useSessionTranscript(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "transcript"),
    queryFn: async ({ signal }) => normalizeTranscript(await api.get<unknown>(`/sessions/${sessionId}/transcript`, { params: { limit: 5000 }, signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 4_000 : false,
  });
}

export function useSessionQuestions(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "questions"),
    queryFn: async ({ signal }) => normalizeQuestions(await api.get<unknown>(`/sessions/${sessionId}/questions`, { signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 8_000 : false,
  });
}

export function useSessionEvidence(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "evidence"),
    queryFn: async ({ signal }) => normalizeEvidence(await api.get<unknown>(`/sessions/${sessionId}/evidence`, { signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 10_000 : false,
  });
}

export function useSessionScores(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "scores"),
    queryFn: async ({ signal }) => normalizeScores(await api.get<unknown>(`/sessions/${sessionId}/scores`, { signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 10_000 : false,
  });
}

export function useSessionTimeline(sessionId: string, windowMinutes: number, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: [...queryKeys.sessions.part(sessionId, "timeline"), { window_minutes: windowMinutes }],
    queryFn: async ({ signal }) =>
      normalizeTimeline(await api.get<unknown>(`/sessions/${sessionId}/timeline`, { params: { window_minutes: windowMinutes }, signal })),
    enabled: !!sessionId && enabled,
    placeholderData: keepPreviousData,
    refetchInterval: live ? 15_000 : false,
  });
}

export function useSessionCoding(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "coding"),
    queryFn: async ({ signal }) => normalizeCoding(await api.get<unknown>(`/sessions/${sessionId}/coding`, { signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 10_000 : false,
  });
}

export function useSessionIntegrity(sessionId: string, { enabled = true, live = false }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "integrity"),
    queryFn: async ({ signal }) => normalizeSessionIntegrity(await api.get<unknown>(`/sessions/${sessionId}/integrity`, { signal })),
    enabled: !!sessionId && enabled,
    refetchInterval: live ? 10_000 : false,
  });
}

export function useSessionReviews(sessionId: string, { enabled = true }: PartOptions = {}) {
  return useQuery({
    queryKey: queryKeys.sessions.part(sessionId, "reviews"),
    queryFn: async ({ signal }) => normalizeReviews(await api.get<unknown>(`/sessions/${sessionId}/reviews`, { signal })),
    enabled: !!sessionId && enabled,
  });
}

export function useSessionReport(
  sessionId: string,
  type: "recruiter" | "candidate",
  { enabled = true, pollMs, version }: PartOptions & { pollMs?: number | false; version?: number | null } = {},
) {
  return useQuery({
    queryKey: version ? [...queryKeys.sessions.report(sessionId, type), { version }] : queryKeys.sessions.report(sessionId, type),
    queryFn: ({ signal }) => api.get<ReportView>(`/sessions/${sessionId}/reports/${type}`, { params: { version: version ?? undefined }, signal }),
    enabled: !!sessionId && enabled,
    meta: { errorToast: false },
    refetchInterval: (query) => {
      if (pollMs) return pollMs;
      const status = query.state.data?.status;
      return status === "pending" || status === "generating" ? 3_000 : false;
    },
  });
}

// ------------------------------------------------------------------- catalog

export function useCompetencyCatalog() {
  return useQuery({
    queryKey: queryKeys.catalog.competencies,
    queryFn: async ({ signal }) => asList<Competency>(await api.get<unknown>("/competencies", { params: { limit: 200 }, signal })),
    staleTime: 10 * 60_000,
    meta: { errorToast: false },
  });
}

export function useJobOptions() {
  return useQuery({
    queryKey: queryKeys.jobs.list({ limit: 200, purpose: "filters" }),
    queryFn: async ({ signal }) => asList<Job>(await api.get<unknown>("/jobs", { params: { limit: 200 }, signal })),
    staleTime: 5 * 60_000,
    meta: { errorToast: false },
  });
}

export function useJobRounds(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.jobs.rounds(jobId ?? ""),
    queryFn: async ({ signal }) => asList<Round>(await api.get<unknown>(`/jobs/${jobId}/rounds`, { signal })),
    enabled: !!jobId,
    staleTime: 5 * 60_000,
    meta: { errorToast: false },
  });
}

// --------------------------------------------------------------------- media

/** Signed URL for a stored file, fetched only when `enabled` (e.g. after the user presses play). */
export function useMediaUrl(mediaId: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["media", mediaId ?? "", "url"],
    queryFn: ({ signal }) => api.get<MediaUrl>(`/media/${mediaId}/url`, { signal }),
    enabled: !!mediaId && enabled,
    // Signed URLs are short-lived: re-sign well before expiry.
    staleTime: 60_000,
    gcTime: 2 * 60_000,
    meta: { errorToast: false },
  });
}

// ---------------------------------------------------------------- integrity

export function integrityQueueParams(filters: IntegrityQueueFilters & { limit: number; offset: number }) {
  return {
    limit: filters.limit,
    offset: filters.offset,
    status: filters.status && filters.status !== "all" ? filters.status : undefined,
    type: filters.type,
    severity: filters.severity,
    source: filters.source,
    session_id: filters.session_id,
    actionable: filters.actionable ? true : undefined,
    since: dayStartIso(filters.from),
    until: dayStartIso(filters.to, 1),
  };
}

export function useIntegrityQueue(filters: IntegrityQueueFilters & { limit: number; offset: number }) {
  const params = integrityQueueParams(filters);
  return useQuery({
    queryKey: queryKeys.integrity.events(params),
    queryFn: async ({ signal }) => {
      const raw = await api.get<unknown>("/integrity/events", { params, signal });
      const page = asPage<IntegrityEventView>(raw, ["events"]);
      return { items: normalizeIntegrityEvents(page.items), total: page.total };
    },
    placeholderData: keepPreviousData,
  });
}

export function useReviewIntegrityEvent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: IntegrityReviewUpdate & { id: string }) => api.patch<IntegrityEventView>(`/integrity/events/${id}`, body),
    onSuccess: (event) => {
      void client.invalidateQueries({ queryKey: queryKeys.integrity.all });
      if (event?.session_id) void client.invalidateQueries({ queryKey: queryKeys.sessions.part(event.session_id, "integrity") });
      void client.invalidateQueries({ queryKey: ["analytics", "overview"] });
    },
  });
}

export interface BulkReviewResult {
  updated: number;
  notFound: string[];
}

/**
 * Bulk review through POST /integrity/events/review (each event is audited
 * server-side). Sends at most 200 ids per request, as the API allows.
 */
export function useBulkReviewIntegrity() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ ids, review_status, review_note }: { ids: string[] } & IntegrityReviewUpdate): Promise<BulkReviewResult> => {
      let updated = 0;
      const notFound: string[] = [];
      for (let i = 0; i < ids.length; i += 200) {
        const chunk = ids.slice(i, i + 200);
        const result = await api.post<{ updated?: number; not_found?: string[] }>("/integrity/events/review", { event_ids: chunk, review_status, review_note: review_note ?? null });
        updated += result?.updated ?? chunk.length;
        notFound.push(...(result?.not_found ?? []));
      }
      return { updated, notFound };
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: queryKeys.integrity.all });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.all });
      void client.invalidateQueries({ queryKey: ["analytics", "overview"] });
    },
  });
}

// ----------------------------------------------------------- reviews/reports

export function useCreateReview(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ReviewCreate & { release_candidate_report?: boolean }) =>
      api.post<ReviewDecision & { candidate_report_released?: boolean | null }>(`/sessions/${sessionId}/reviews`, body),
    meta: { successToast: "Decision recorded" },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions.part(sessionId, "reviews") });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.detail(sessionId), exact: true });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.report(sessionId, "candidate") });
      void client.invalidateQueries({ queryKey: ["sessions", "list"] });
      void client.invalidateQueries({ queryKey: ["analytics"] });
    },
  });
}

export function useRegenerateReport(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ message?: string }>(`/sessions/${sessionId}/reports/regenerate`),
    meta: { successToast: "Regenerating the report. The new version appears here when it's ready." },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions.report(sessionId, "recruiter") });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.report(sessionId, "candidate") });
    },
  });
}

export function useReleaseCandidateReport(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    /** `acknowledgePolicyOverride` is required when the interview policy is "never". */
    mutationFn: ({ acknowledgePolicyOverride = false, note }: { acknowledgePolicyOverride?: boolean; note?: string } = {}) =>
      api.post<ReportView>(`/sessions/${sessionId}/reports/candidate/release`, { acknowledge_policy_override: acknowledgePolicyOverride, note: note ?? null }),
    meta: { successToast: "Candidate report released" },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions.report(sessionId, "candidate") });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.report(sessionId, "recruiter") });
    },
  });
}

export function useRecomputeScores(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<unknown>(`/sessions/${sessionId}/recompute-scores`),
    meta: { successToast: "Scores recomputed from the evidence" },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions.detail(sessionId) });
    },
  });
}

// ----------------------------------------------------------------- analytics

/** Analytics endpoints take `days` (1-365) and optional job/round filters. */
export interface AnalyticsScope {
  days: number;
  job_id?: string;
  round_id?: string;
}

function scopeParams(scope: AnalyticsScope) {
  return { days: scope.days, job_id: scope.job_id, round_id: scope.round_id };
}

function analyticsQuery<T>(name: string, scope: AnalyticsScope, select: (raw: unknown) => T, key = name) {
  return {
    queryKey: ["analytics", key, scopeParams(scope)] as const,
    queryFn: async ({ signal }: { signal: AbortSignal }) => select(await api.get<unknown>(`/analytics/${name}`, { params: scopeParams(scope), signal })),
    placeholderData: keepPreviousData,
  };
}

export function useAnalyticsOverview(scope: AnalyticsScope | number) {
  const s = typeof scope === "number" ? { days: scope } : scope;
  return useQuery(analyticsQuery("overview", s, (raw) => raw as AnalyticsOverviewView));
}

export function useAnalyticsFunnel(scope: AnalyticsScope | number) {
  const s = typeof scope === "number" ? { days: scope } : scope;
  return useQuery(analyticsQuery("funnel", s, (raw) => ({ stages: asList<AnalyticsFunnel["stages"][number]>(raw, ["stages"]) })));
}

export function useAnalyticsCompetencies(scope: AnalyticsScope) {
  return useQuery(
    analyticsQuery("competencies", scope, (raw) => ({ items: asList<CompetencyAnalytics["items"][number] & { average_confidence?: number; sufficient_rate?: number }>(raw, ["competencies"]) })),
  );
}

export function useAnalyticsModelUsage(scope: AnalyticsScope) {
  return useQuery(
    analyticsQuery("model-usage", scope, (raw) => ({
      items: asList<ModelUsageItemView>(raw, ["usage", "models"]),
      totals: isRecord(raw) && isRecord(raw.totals) ? raw.totals : {},
    })),
  );
}

export function useAnalyticsScores(scope: AnalyticsScope) {
  return useQuery(analyticsQuery("scores", scope, (raw) => (isRecord(raw) && isRecord(raw.overall) ? (raw as unknown as ScoreDistributionsDTO) : null)));
}

export function useAnalyticsCompletion(scope: AnalyticsScope) {
  return useQuery(analyticsQuery("completion", scope, (raw) => (isRecord(raw) && isRecord(raw.overall) ? (raw as unknown as CompletionAnalyticsDTO) : null)));
}

export function useAnalyticsIntegrity(scope: AnalyticsScope, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ ...analyticsQuery("integrity", scope, (raw) => (isRecord(raw) ? (raw as unknown as IntegrityAnalyticsDTO) : null)), enabled });
}

export function useAnalyticsHints(scope: AnalyticsScope) {
  return useQuery(analyticsQuery("hints", scope, (raw) => (isRecord(raw) ? (raw as unknown as HintUsageAnalyticsDTO) : null)));
}

/** Recent sessions in a period, for breakdowns an analytics endpoint does not return. */
export function useRecentSessionsSample(periodDays: number, limit = 200) {
  const createdFrom = dayStartIso(new Date(Date.now() - periodDays * 86_400_000).toISOString().slice(0, 10));
  const params = { limit, offset: 0, created_from: createdFrom };
  return useQuery({
    queryKey: queryKeys.sessions.list({ ...params, purpose: "analytics-sample" }),
    queryFn: async ({ signal }) => asPage<SessionSummary>(await api.get<unknown>("/sessions", { params, signal })),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}
