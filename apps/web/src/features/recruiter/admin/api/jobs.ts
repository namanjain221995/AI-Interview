/** Jobs, requirements and rounds (CONTRACTS §4 Catalog). */

import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Job, JobRequirement, JobStatus, Page, Round } from "@/lib/types";

import { asList, asPage } from "../lib/utils";
import { withDefaults } from "../rounds/roundConfig";

export interface JobListQuery {
  status?: JobStatus;
  q?: string;
  limit?: number;
  offset?: number;
}

export function fetchJobs(params: JobListQuery): Promise<Page<Job>> {
  return api.get<unknown>("/jobs", { params: { ...params } }).then((raw) => asPage<Job>(raw));
}

export function useJobs(params: JobListQuery, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.jobs.list(params),
    queryFn: () => fetchJobs(params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export const JOB_STATUS_FILTERS: ReadonlyArray<JobStatus | "all"> = ["all", "open", "draft", "closed", "archived"];

/** Totals per status for the filter tabs (one tiny request each). */
export function useJobStatusCounts(q?: string) {
  const results = useQueries({
    queries: JOB_STATUS_FILTERS.map((status) => {
      const params: JobListQuery = { limit: 1, offset: 0, q: q || undefined, status: status === "all" ? undefined : status };
      return {
        queryKey: queryKeys.jobs.list({ ...params, countOnly: true }),
        queryFn: () => fetchJobs(params),
        staleTime: 30_000,
        placeholderData: keepPreviousData,
      };
    }),
  });
  const counts: Partial<Record<JobStatus | "all", number>> = {};
  JOB_STATUS_FILTERS.forEach((status, index) => {
    const total = results[index]?.data?.total;
    if (typeof total === "number") counts[status] = total;
  });
  return counts;
}

export function useJob(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.jobs.detail(id ?? ""),
    queryFn: () => api.get<Job>(`/jobs/${id}`),
    enabled: Boolean(id),
  });
}

export function fetchRequirements(jobId: string): Promise<JobRequirement[]> {
  return api
    .get<unknown>(`/jobs/${jobId}/requirements`, { params: { limit: 200 } })
    .then((raw) => asList<JobRequirement>(raw).sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)));
}

export function useJobRequirements(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.jobs.requirements(jobId ?? ""),
    queryFn: () => fetchRequirements(jobId!),
    enabled: Boolean(jobId),
  });
}

export function normalizeRound(round: Round): Round {
  return {
    ...round,
    config: withDefaults(round.config, { round_type: round.round_type ?? round.config?.round_type, name: round.config?.name || round.name }),
  };
}

export function fetchRounds(jobId: string): Promise<Round[]> {
  return api
    .get<unknown>(`/jobs/${jobId}/rounds`, { params: { limit: 200 } })
    .then((raw) => asList<Round>(raw).map(normalizeRound).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0)));
}

export function useJobRounds(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.jobs.rounds(jobId ?? ""),
    queryFn: () => fetchRounds(jobId!),
    enabled: Boolean(jobId),
  });
}

export function useUpdateJobStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ job, status }: { job: Job; status: JobStatus }) =>
      status === "archived" ? api.post<Job>(`/jobs/${job.id}/archive`) : api.patch<Job>(`/jobs/${job.id}`, { status }),
    onSuccess: (job, { status }) => {
      if (job && typeof job === "object" && "id" in job) client.setQueryData(queryKeys.jobs.detail(job.id), job);
      void client.invalidateQueries({ queryKey: queryKeys.jobs.all });
      return status;
    },
  });
}
