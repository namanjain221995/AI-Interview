/** Candidates, resumes, profiles, interviews and sessions (CONTRACTS §4). */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, isApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  Candidate,
  CandidateDeletionResult,
  CandidateInput,
  CandidateProfileRecord,
  CandidateResume,
  Interview,
  InterviewCreate,
  InterviewCreateResponse,
  InterviewStatus,
  MediaUrl,
  ResumeStatus,
  SessionDetail,
  SessionSummary,
} from "@/lib/types";

import type { AdminCandidate, InviteSecrets } from "../types";
import { asList, asPage } from "../lib/utils";

// --------------------------------------------------------------- candidates

export type ArchivedFilter = "exclude" | "include" | "only";

export interface CandidateListQuery {
  q?: string;
  job_id?: string;
  status?: InterviewStatus;
  /** Archived candidates are hidden unless asked for (default "exclude"). */
  archived?: ArchivedFilter;
  limit: number;
  offset: number;
}

/** Query params for GET /candidates. */
export function candidateListParams({ archived, ...rest }: CandidateListQuery) {
  return {
    ...rest,
    include_archived: archived === "include" ? true : undefined,
    archived_only: archived === "only" ? true : undefined,
  };
}

export function useCandidates(params: CandidateListQuery, enabled = true) {
  return useQuery({
    queryKey: queryKeys.candidates.list(params),
    queryFn: async () => asPage<AdminCandidate>(await api.get<unknown>("/candidates", { params: candidateListParams(params) })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useCandidate(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.candidates.detail(id ?? ""),
    queryFn: () => api.get<AdminCandidate>(`/candidates/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateCandidate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CandidateInput) => api.post<Candidate>("/candidates", input),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.candidates.all }),
  });
}

export function useUpdateCandidate(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<CandidateInput>) => api.patch<Candidate>(`/candidates/${id}`, input),
    meta: { silentError: true },
    onSuccess: (candidate) => {
      client.setQueryData(queryKeys.candidates.detail(id), candidate);
      void client.invalidateQueries({ queryKey: ["candidates", "list"] });
    },
  });
}

// ------------------------------------------------- archive / restore / erase

function useCandidateMutation<TArg, TResult>(id: string, run: (arg: TArg) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    meta: { silentError: true },
    onSuccess: (result) => {
      if (result && typeof result === "object" && "full_name" in result) client.setQueryData(queryKeys.candidates.detail(id), result);
      void client.invalidateQueries({ queryKey: queryKeys.candidates.all });
    },
  });
}

/** Soft delete: hidden from lists, open invitations cancelled; restorable. */
export function useArchiveCandidate(id: string) {
  return useCandidateMutation<void, AdminCandidate>(id, () => api.post<AdminCandidate>(`/candidates/${id}/archive`));
}

export function useRestoreCandidate(id: string) {
  return useCandidateMutation<void, AdminCandidate>(id, () => api.post<AdminCandidate>(`/candidates/${id}/restore`));
}

/** Reference of the data-subject request, as the API accepts it (recorded in the audit log). */
export const REQUEST_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,63}$/;

/** What `DELETE /candidates/{id}` would remove right now (ADMIN; counts only). */
export interface CandidateDeletionPreview {
  candidate_id: string;
  archived: boolean;
  interviews: number;
  sessions: number;
  resumes: number;
  reports: number;
  intake_interviews: number;
  link_registrations: number;
  stored_files: number;
  stored_bytes: number;
  blockers: Array<{ type: string; id: string; status: string }>;
}

export function useCandidateDeletionPreview(id: string, enabled: boolean) {
  return useQuery({
    queryKey: [...queryKeys.candidates.detail(id), "deletion-preview"],
    queryFn: () => api.get<CandidateDeletionPreview>(`/candidates/${id}/deletion-preview`),
    enabled,
    staleTime: 0,
    retry: false,
    meta: { errorToast: false },
  });
}

/** Query params of the erase call: an active candidate is archived and deleted in one step. */
export function eraseParams(requestReference: string | null, archiveFirst: boolean): Record<string, string | boolean> | undefined {
  const params: Record<string, string | boolean> = {};
  if (archiveFirst) params.archive_first = true;
  if (requestReference) params.request_reference = requestReference;
  return Object.keys(params).length ? params : undefined;
}

/** Permanent data-subject erasure (ADMIN). Irreversible. `archiveFirst` archives an active candidate in the same request. */
export function useEraseCandidate(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ requestReference, archiveFirst = false }: { requestReference: string | null; archiveFirst?: boolean }) =>
      api.delete<CandidateDeletionResult>(`/candidates/${id}`, { params: eraseParams(requestReference, archiveFirst) }),
    meta: { silentError: true },
    onSuccess: () => {
      client.removeQueries({ queryKey: queryKeys.candidates.detail(id) });
      void client.invalidateQueries({ queryKey: queryKeys.candidates.all });
    },
  });
}

/** Plain-language messages for the candidate lifecycle conflicts the API reports. */
export function candidateActionError(error: unknown): string {
  if (!isApiError(error)) return error instanceof Error && error.message ? error.message : "Something went wrong. Try again.";
  switch (error.code) {
    case "candidate_archived":
      return "This candidate is archived. Restore them first to invite them or add a résumé.";
    case "candidate_exists":
      return error.details.archived === true
        ? "An archived candidate already uses this email. Open that candidate and restore them instead."
        : "A candidate with this email already exists.";
    case "candidate_interview_in_progress":
      return "An interview is in progress. Wait for it to end, or cancel it, then archive the candidate.";
    case "candidate_not_archived":
      return "Archive the candidate before deleting their data permanently.";
    case "candidate_busy": {
      const seconds = typeof error.details.retry_after_seconds === "number" ? error.details.retry_after_seconds : error.retryAfterSeconds;
      const minutes = seconds ? Math.max(1, Math.ceil(seconds / 60)) : null;
      return `A session or résumé of this candidate is still being processed. Try again${minutes ? ` in about ${minutes} minute${minutes === 1 ? "" : "s"}` : " in a few minutes"}.`;
    }
    case "erasure_incomplete":
      return "Some stored files couldn't be deleted, so nothing was removed. Try again; if it keeps failing, contact your administrator.";
    default:
      if (error.status === 403) return "Only administrators can do this.";
      return error.message || "Something went wrong. Try again.";
  }
}

// ------------------------------------------------------------------ resumes

export const RESUME_TERMINAL: ReadonlySet<ResumeStatus> = new Set(["ready", "failed", "rejected"]);
export const RESUME_STEPS: readonly ResumeStatus[] = ["uploaded", "validating", "extracting", "ocr", "analyzing", "ready"];

export function useCandidateResumes(candidateId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.candidates.resumes(candidateId ?? ""),
    queryFn: async () =>
      asList<CandidateResume>(await api.get<unknown>(`/candidates/${candidateId}/resumes`, { params: { limit: 50 } })).sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      ),
    enabled: Boolean(candidateId),
    // Keep refreshing while any resume is still being processed.
    refetchInterval: (query) => (query.state.data?.some((r) => !RESUME_TERMINAL.has(r.status)) ? 2000 : false),
  });
}

/** Polls `GET /resumes/{id}` every 1.5 s until the pipeline finishes. */
export function useResumeStatus(resumeId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.resumes.detail(resumeId ?? ""),
    queryFn: () => api.get<CandidateResume>(`/resumes/${resumeId}`, { params: { include_media_url: false } }),
    enabled: Boolean(resumeId),
    refetchInterval: (query) => (query.state.data && RESUME_TERMINAL.has(query.state.data.status) ? false : 1500),
  });
}

export function uploadResume(
  candidateId: string,
  file: File,
  options: { jobId?: string | null; onProgress?: (percent: number | null) => void; signal?: AbortSignal } = {},
): Promise<CandidateResume> {
  return api.upload<CandidateResume>(`/candidates/${candidateId}/resumes`, file, {
    fields: options.jobId ? { job_id: options.jobId } : undefined,
    onProgress: (p) => options.onProgress?.(p.percent),
    signal: options.signal,
  });
}

export function useReprocessResume() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (resumeId: string) => api.post<CandidateResume>(`/resumes/${resumeId}/reprocess`),
    meta: { successToast: "Processing restarted" },
    onSuccess: (resume) => {
      if (resume?.candidate_id) void client.invalidateQueries({ queryKey: queryKeys.candidates.detail(resume.candidate_id) });
    },
  });
}

export function useCandidateProfile(candidateId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.candidates.profile(candidateId ?? ""),
    queryFn: async () => {
      try {
        // include_quick: while the AI profile of a new resume is generated, the
        // instant first read (status "quick") is shown instead of nothing.
        return await api.get<CandidateProfileRecord>(`/candidates/${candidateId}/profile`, { params: { include_quick: true } });
      } catch (error) {
        // No profile yet (no resume processed) is a normal state, not an error.
        if (error && typeof error === "object" && "status" in error && (error as { status: number }).status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(candidateId),
    // A first read is replaced by the AI profile within seconds: check again until then.
    refetchInterval: (q) => (q.state.data?.status === "quick" ? 3000 : false),
  });
}

export async function fetchMediaUrl(mediaId: string): Promise<MediaUrl> {
  return api.get<MediaUrl>(`/media/${mediaId}/url`);
}

/**
 * A short-lived signed URL for a resume file. `GET /resumes/{id}` signs one
 * (audited as a media view); `GET /media/{id}/url` is the fallback.
 */
export async function resumeFileUrl(resume: Pick<CandidateResume, "id" | "media_id">): Promise<string | null> {
  const detail = await api.get<CandidateResume & { media_url?: string | null }>(`/resumes/${resume.id}`);
  if (detail.media_url) return detail.media_url;
  const mediaId = detail.media_id ?? resume.media_id;
  return mediaId ? (await fetchMediaUrl(mediaId)).url : null;
}

// --------------------------------------------------------------- interviews

export interface InterviewListQuery {
  job_id?: string;
  candidate_id?: string;
  status?: InterviewStatus;
  limit?: number;
  offset?: number;
}

export function useInterviews(params: InterviewListQuery, enabled = true) {
  return useQuery({
    queryKey: queryKeys.interviews.list(params),
    queryFn: async () => asPage<Interview>(await api.get<unknown>("/interviews", { params: { limit: 200, ...params } })),
    enabled,
    placeholderData: keepPreviousData,
  });
}

// ----------------------------------------------------------- job pipeline (S14)

export type PipelineStage = "invited" | "in_progress" | "completed" | "closed";
export type PipelineCounts = Record<PipelineStage | "all", number>;

export interface JobPipelineRow {
  interview: Interview;
  session: SessionSummary | null;
}

export interface JobPipelinePage {
  items: JobPipelineRow[];
  total: number;
  limit: number;
  offset: number;
  counts: PipelineCounts;
}

export interface JobPipelineQuery {
  stage?: PipelineStage | "all";
  q?: string;
  limit?: number;
  offset?: number;
}

const EMPTY_COUNTS: PipelineCounts = { invited: 0, in_progress: 0, completed: 0, closed: 0, all: 0 };

/** One server page of a job's candidates with exact stage counts (the browser never assembles or caps it). */
export function useJobPipeline(jobId: string | undefined, params: JobPipelineQuery) {
  return useQuery({
    // Under "interviews" so cancelling or re-inviting refreshes it with every other interview list.
    queryKey: [...queryKeys.interviews.all, "pipeline", jobId ?? "", params] as const,
    queryFn: async (): Promise<JobPipelinePage> => {
      const raw = await api.get<Partial<JobPipelinePage>>(`/jobs/${jobId}/pipeline`, { params: { ...params, q: params.q || undefined } });
      return { items: raw.items ?? [], total: raw.total ?? 0, limit: raw.limit ?? params.limit ?? 0, offset: raw.offset ?? 0, counts: { ...EMPTY_COUNTS, ...raw.counts } };
    },
    enabled: Boolean(jobId),
    placeholderData: keepPreviousData,
  });
}

export function useCandidateInterviews(candidateId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.candidates.interviews(candidateId ?? ""),
    queryFn: async () => asList<Interview>(await api.get<unknown>(`/candidates/${candidateId}/interviews`, { params: { limit: 100 } })),
    enabled: Boolean(candidateId),
  });
}

export function useCreateInterview() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: InterviewCreate) => api.post<InterviewCreateResponse>("/interviews", input),
    meta: { silentError: true },
    onSuccess: (_data, input) => {
      void client.invalidateQueries({ queryKey: queryKeys.interviews.all });
      void client.invalidateQueries({ queryKey: queryKeys.candidates.interviews(input.candidate_id) });
      void client.invalidateQueries({ queryKey: queryKeys.jobs.detail(input.job_id) });
      void client.invalidateQueries({ queryKey: ["candidates", "list"] });
    },
  });
}

export function useCancelInterview() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<Interview>(`/interviews/${id}/cancel`),
    meta: { successToast: "Invitation cancelled" },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.interviews.all });
      void client.invalidateQueries({ queryKey: queryKeys.candidates.all });
    },
  });
}

export function useRegenerateInviteLink() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<InviteSecrets>(`/interviews/${id}/regenerate-link`),
    meta: { silentError: true },
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.interviews.all }),
  });
}

// ----------------------------------------------------------------- sessions

export function useSessions(params: { job_id?: string; candidate_id?: string; limit?: number }, enabled = true) {
  return useQuery({
    queryKey: queryKeys.sessions.list(params),
    queryFn: async () => asPage<SessionSummary>(await api.get<unknown>("/sessions", { params: { limit: 100, ...params } })),
    enabled,
  });
}

export function useSessionDetail(id: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.sessions.detail(id ?? ""),
    queryFn: () => api.get<SessionDetail>(`/sessions/${id}`),
    enabled: Boolean(id),
    staleTime: 60_000,
  });
}
