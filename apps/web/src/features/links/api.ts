/**
 * Open interview links API (OPEN_LINKS.md section 9): staff link management,
 * the public registration page, the personal "continue later" link and the
 * candidate's round progress. Responses pass through light normalisers that fill
 * missing optional parts, so a partial DTO never crashes a page.
 *
 * Public calls (`/link-access`, `/link-return`) use `skipAuthRefresh`: a 401 there
 * means "wrong details", never "expired session".
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api, isApiError } from "@/lib/api";
import type { Page } from "@/lib/types";

import { normalizeStepKey } from "@/features/candidate/api";

import { normalizeSchema } from "./formSchema";
import type {
  AdvanceResult,
  ContinueResponse,
  DataUse,
  Flag,
  FormCatalog,
  FormSchema,
  FormValidation,
  LinkCounts,
  LinkInfo,
  LinkListQuery,
  LinkProgress,
  LinkStatus,
  LinkWithCode,
  OpenLink,
  OpenLinkCreate,
  OpenLinkSummary,
  OpenLinkUpdate,
  PersonalLinkInfo,
  PolicySummary,
  RegisterRequest,
  RegisterResponse,
  Registration,
  RegistrationDetail,
  RegistrationListQuery,
  RegistrationRound,
  VerifyPersonalResponse,
} from "./types";

// ---------------------------------------------------------------- keys

export const linkKeys = {
  all: ["open-links"] as const,
  catalog: ["open-links", "catalog"] as const,
  lists: ["open-links", "list"] as const,
  list: (params: object) => ["open-links", "list", params] as const,
  detail: (id: string) => ["open-links", "detail", id] as const,
  registrations: (id: string) => ["open-links", "detail", id, "registrations"] as const,
  registrationList: (id: string, params: object) => ["open-links", "detail", id, "registrations", "list", params] as const,
  registration: (id: string, registrationId: string) => ["open-links", "detail", id, "registrations", "one", registrationId] as const,
  byCandidate: (candidateId: string) => ["open-links", "by-candidate", candidateId] as const,
  formCheck: (payload: object) => ["open-links", "form-check", payload] as const,
  /** Public pages: never shared with the staff cache. */
  publicInfo: (token: string) => ["link-access", token] as const,
  personalInfo: (token: string) => ["link-return", token] as const,
  /** Under "portal" so clearing the candidate's portal cache clears it too. */
  progress: ["portal", "link", "progress"] as const,
};

// --------------------------------------------------------- normalisers

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {});
const arr = <T = unknown>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const strOr = (v: unknown, fallback: string): string => (typeof v === "string" ? v : fallback);
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function normalizeCounts(raw: unknown): LinkCounts {
  const r = rec(raw);
  return {
    registrations: num(r.registrations),
    in_progress: num(r.in_progress),
    awaiting_review: num(r.awaiting_review),
    completed: num(r.completed),
    flagged: num(r.flagged),
    blocked: num(r.blocked),
  };
}

export function normalizePolicy(raw: unknown): PolicySummary {
  const r = rec(raw);
  const days = rec(r.retention_days);
  return {
    video_recording: r.video_recording === true,
    audio_recording: r.audio_recording === true,
    screen_recording: r.screen_recording === true,
    camera_monitoring: r.camera_monitoring === true,
    screen_monitoring: r.screen_monitoring === true,
    candidate_report_mode: strOr(r.candidate_report_mode, "after_review"),
    retention_days: { resume: num(days.resume), audio: num(days.audio), video: num(days.video), transcript: num(days.transcript), report: num(days.report) },
  };
}

export function normalizeSummary(raw: unknown): OpenLinkSummary {
  const r = rec(raw);
  const job = rec(r.job);
  return {
    id: strOr(r.id, ""),
    name: strOr(r.name, "Interview link"),
    job: { id: strOr(job.id, strOr(r.job_id, "")), title: strOr(job.title, strOr(r.job_title, "")), status: strOr(job.status, "open") },
    status: strOr(r.status, "active") as LinkStatus,
    state: strOr(r.state, "open") as OpenLinkSummary["state"],
    url: strOr(r.url, ""),
    rounds: arr<Rec>(r.rounds).map((round, i) => ({
      id: strOr(round.id, String(i)),
      name: strOr(round.name, `Round ${i + 1}`),
      round_type: strOr(round.round_type, "custom"),
      sequence: num(round.sequence, i),
      duration_minutes: num(round.duration_minutes, 30),
      missing: round.missing === true,
    })),
    progression: (r.progression === "after_review" ? "after_review" : "immediate") as OpenLinkSummary["progression"],
    opens_at: strOrNull(r.opens_at),
    closes_at: strOrNull(r.closes_at),
    max_registrations: typeof r.max_registrations === "number" ? r.max_registrations : null,
    counts: normalizeCounts(r.counts),
    created_at: strOr(r.created_at, ""),
    updated_at: strOr(r.updated_at, strOr(r.created_at, "")),
  };
}

export function normalizeLink(raw: unknown): OpenLink {
  const r = rec(raw);
  const dup = rec(r.duplicate_policy);
  const creator = rec(r.created_by);
  return {
    ...normalizeSummary(raw),
    description: strOrNull(r.description),
    round_window_hours: num(r.round_window_hours, 72),
    require_resume: r.require_resume !== false,
    requires_access_code: r.requires_access_code === true || r.require_access_code === true,
    per_ip_hourly_limit: num(r.per_ip_hourly_limit, 120),
    duplicate_policy: {
      scope: dup.scope === "link" ? "link" : "job",
      device: dup.device === "block" ? "block" : "flag",
      network: dup.network === "off" ? "off" : "flag",
    },
    form_schema: normalizeSchema(r.form_schema),
    acknowledged_warnings: arr<string>(r.acknowledged_warnings).filter((w) => typeof w === "string"),
    policy_summary: normalizePolicy(r.policy_summary),
    warnings: arr<string>(r.warnings).filter((w) => typeof w === "string"),
    created_by: typeof creator.id === "string" ? { id: creator.id, full_name: strOr(creator.full_name, "") } : null,
  };
}

function normalizeRegRound(raw: unknown, index: number): RegistrationRound {
  const r = rec(raw);
  return {
    sequence: num(r.sequence, index),
    round_id: strOr(r.round_id, ""),
    name: strOr(r.name, strOr(r.round_name, `Round ${index + 1}`)),
    state: strOr(r.state, "locked") as RegistrationRound["state"],
    attempt: num(r.attempt, 1),
    interview_id: strOrNull(r.interview_id),
    session_id: strOrNull(r.session_id),
    session_status: strOrNull(r.session_status),
    end_reason: strOrNull(r.end_reason),
    started_at: strOrNull(r.started_at),
    completed_at: strOrNull(r.completed_at),
    expires_at: strOrNull(r.expires_at),
  };
}

export function normalizeRegistration(raw: unknown): Registration {
  const r = rec(raw);
  const candidate = rec(r.candidate);
  const link = rec(r.link);
  return {
    id: strOr(r.id, ""),
    link_id: strOr(r.link_id, strOr(link.id, "")),
    candidate: typeof candidate.id === "string" ? { id: candidate.id, full_name: strOr(candidate.full_name, ""), email: strOr(candidate.email, "") } : null,
    full_name: strOr(r.full_name, strOr(candidate.full_name, "")),
    email: strOr(r.email, strOr(candidate.email, "")),
    phone: strOrNull(r.phone),
    status: strOr(r.status, "registered") as Registration["status"],
    status_reason: strOrNull(r.status_reason),
    current_round_index: num(r.current_round_index),
    rounds: arr(r.rounds).map(normalizeRegRound),
    flags: arr<Flag>(r.flags).filter((f) => f && typeof f === "object"),
    flag_review: strOr(r.flag_review, "none") as Registration["flag_review"],
    created_at: strOr(r.created_at, ""),
    updated_at: strOr(r.updated_at, strOr(r.created_at, "")),
    ...(typeof link.id === "string" ? { link: { id: link.id, name: strOr(link.name, ""), job_title: strOr(link.job_title, "") } } : {}),
  };
}

export function normalizeRegistrationDetail(raw: unknown): RegistrationDetail {
  const r = rec(raw);
  return {
    ...normalizeRegistration(raw),
    answers: arr<Rec>(r.answers).map((a) => ({
      key: strOr(a.key, ""),
      label: strOr(a.label, strOr(a.key, "")),
      type: strOr(a.type, "text") as RegistrationDetail["answers"][number]["type"],
      value: a.value,
    })),
    attempts: arr(r.attempts).map(normalizeRegRound),
    privacy_ack_at: strOrNull(r.privacy_ack_at),
    personal_link_issued_at: strOrNull(r.personal_link_issued_at),
    resume_id: strOrNull(r.resume_id),
    flag_reviewed_at: strOrNull(r.flag_reviewed_at),
    flag_review_note: strOrNull(r.flag_review_note),
  };
}

function page<T>(raw: unknown, map: (item: unknown) => T): Page<T> {
  const r = rec(raw);
  const items = Array.isArray(raw) ? raw : arr(r.items);
  return { items: items.map(map), total: num(r.total, items.length) };
}

export function normalizeLinkInfo(raw: unknown): LinkInfo {
  const r = rec(raw);
  const form = r.form && typeof r.form === "object" ? rec(r.form) : null;
  const dataUse = rec(r.data_use);
  return {
    organization_name: strOr(r.organization_name, "The hiring team"),
    job_title: strOr(r.job_title, "this role"),
    job_location: strOrNull(r.job_location),
    link_name: strOr(r.link_name, ""),
    description: strOrNull(r.description),
    state: strOr(r.state, "open") as LinkInfo["state"],
    opens_at: strOrNull(r.opens_at),
    closes_at: strOrNull(r.closes_at),
    rounds: arr<Rec>(r.rounds)
      .map((round, i) => ({
        name: strOr(round.name, `Round ${i + 1}`),
        round_type: strOr(round.round_type, "custom"),
        duration_minutes: num(round.duration_minutes, 30),
        sequence: num(round.sequence, i),
      }))
      .sort((a, b) => a.sequence - b.sequence),
    progression: r.progression === "after_review" ? "after_review" : "immediate",
    requires_access_code: r.requires_access_code === true,
    require_resume: r.require_resume !== false,
    interviewer_name: strOrNull(r.interviewer_name),
    form: form ? { fields: normalizeSchema({ fields: form.fields }).fields.map(({ builtin: _b, enabled: _e, ...f }) => f) } : null,
    data_use: { ...normalizePolicy(dataUse), notice_markdown: strOr(dataUse.notice_markdown, ""), ai_assessment: true, human_decision: true } as DataUse,
    honeypot_field: "website",
  };
}

export function normalizeProgress(raw: unknown): LinkProgress {
  const r = rec(raw);
  return {
    organization_name: strOr(r.organization_name, ""),
    job_title: strOr(r.job_title, ""),
    link_name: strOr(r.link_name, ""),
    progression: r.progression === "after_review" ? "after_review" : "immediate",
    rounds: arr<Rec>(r.rounds)
      .map((round, i) => ({
        sequence: num(round.sequence, i),
        name: strOr(round.name, `Round ${i + 1}`),
        round_type: strOr(round.round_type, "custom"),
        duration_minutes: num(round.duration_minutes, 30),
        state: strOr(round.state, "locked") as LinkProgress["rounds"][number]["state"],
        interview_id: strOrNull(round.interview_id),
        is_current: round.is_current === true,
      }))
      .sort((a, b) => a.sequence - b.sequence),
    next_action: strOr(r.next_action, "continue_current") as LinkProgress["next_action"],
    next_interview_id: strOrNull(r.next_interview_id),
    next_round_name: strOrNull(r.next_round_name),
    deadline: strOrNull(r.deadline),
  };
}

// --------------------------------------------------------- staff: links

export function useFormCatalog() {
  return useQuery({
    queryKey: linkKeys.catalog,
    queryFn: async (): Promise<FormCatalog> => {
      const r = rec(await api.get<unknown>("/open-links/form/catalog"));
      const limits = rec(r.limits);
      return {
        builtins: normalizeSchema({ fields: r.builtins }).fields,
        types: arr<string>(r.types) as FormCatalog["types"],
        limits: { max_fields: num(limits.max_fields, 30), max_options: num(limits.max_options, 50) },
        default_schema: normalizeSchema(r.default_schema),
      };
    },
    staleTime: 10 * 60_000,
    retry: false,
    meta: { errorToast: false },
  });
}

export function validateForm(body: { form_schema: FormSchema; acknowledged_warnings: string[] }, signal?: AbortSignal): Promise<FormValidation> {
  return api.post<unknown>("/open-links/form/validate", body, { signal }).then((raw) => {
    const r = rec(raw);
    return {
      ok: r.ok !== false,
      errors: arr<FormValidation["errors"][number]>(r.errors),
      warnings: arr<FormValidation["warnings"][number]>(r.warnings),
      normalized: normalizeSchema(r.normalized),
    };
  });
}

/** Server-side form check; the caller debounces the payload. Failures are silent (local mirror still applies). */
export function useServerFormCheck(payload: { form_schema: FormSchema; acknowledged_warnings: string[] } | null) {
  return useQuery({
    queryKey: linkKeys.formCheck(payload ?? {}),
    queryFn: ({ signal }) => validateForm(payload!, signal),
    enabled: payload !== null,
    staleTime: 60_000,
    retry: false,
    placeholderData: keepPreviousData,
    meta: { errorToast: false },
  });
}

export function fetchLinks(params: LinkListQuery): Promise<Page<OpenLinkSummary>> {
  return api.get<unknown>("/open-links", { params: { ...params } }).then((raw) => page(raw, normalizeSummary));
}

export function useLinks(params: LinkListQuery, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: linkKeys.list(params),
    queryFn: () => fetchLinks(params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useLink(id: string | undefined) {
  return useQuery({
    queryKey: linkKeys.detail(id ?? ""),
    queryFn: () => api.get<unknown>(`/open-links/${id}`).then(normalizeLink),
    enabled: Boolean(id),
  });
}

function storeLink(client: QueryClient, link: OpenLink) {
  client.setQueryData(linkKeys.detail(link.id), link);
  void client.invalidateQueries({ queryKey: linkKeys.lists });
}

function withCode(raw: unknown): LinkWithCode {
  const r = rec(raw);
  return { link: normalizeLink(r.link ?? raw), access_code: strOrNull(r.access_code) };
}

export function useCreateLink() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: OpenLinkCreate) => api.post<unknown>("/open-links", body).then(withCode),
    onSuccess: (result) => storeLink(client, result.link),
    meta: { silentError: true },
  });
}

export function useUpdateLink(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: OpenLinkUpdate) => api.patch<unknown>(`/open-links/${id}`, body).then(normalizeLink),
    onSuccess: (link) => storeLink(client, link),
    meta: { silentError: true },
  });
}

export function useSetLinkStatus(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (status: LinkStatus) => api.post<unknown>(`/open-links/${id}/status`, { status }).then(normalizeLink),
    onSuccess: (link) => storeLink(client, link),
  });
}

export function useRegenerateToken(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<unknown>(`/open-links/${id}/regenerate-token`).then(normalizeLink),
    onSuccess: (link) => storeLink(client, link),
  });
}

export function useSetAccessCode(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.post<unknown>(`/open-links/${id}/access-code`, { enabled }).then(withCode),
    onSuccess: (result) => storeLink(client, result.link),
  });
}

// ------------------------------------------------- staff: registrations

export function useRegistrations(linkId: string | undefined, params: RegistrationListQuery) {
  return useQuery({
    queryKey: linkKeys.registrationList(linkId ?? "", params),
    queryFn: () => api.get<unknown>(`/open-links/${linkId}/registrations`, { params: { ...params } }).then((raw) => page(raw, normalizeRegistration)),
    enabled: Boolean(linkId),
    placeholderData: keepPreviousData,
  });
}

export function useRegistration(linkId: string | undefined, registrationId: string | null | undefined) {
  return useQuery({
    queryKey: linkKeys.registration(linkId ?? "", registrationId ?? ""),
    queryFn: () => api.get<unknown>(`/open-links/${linkId}/registrations/${registrationId}`).then(normalizeRegistrationDetail),
    enabled: Boolean(linkId && registrationId),
  });
}

function refreshRegistrations(client: QueryClient, linkId: string, detail?: RegistrationDetail) {
  if (detail) client.setQueryData(linkKeys.registration(linkId, detail.id), detail);
  void client.invalidateQueries({ queryKey: linkKeys.registrations(linkId) });
  void client.invalidateQueries({ queryKey: linkKeys.detail(linkId), exact: true });
  void client.invalidateQueries({ queryKey: linkKeys.lists });
}

export function useAdvanceRegistrations(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { registration_ids: string[]; note?: string }) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/advance`, body).then((raw): AdvanceResult => {
        const r = rec(raw);
        return { advanced: arr(r.advanced), skipped: arr(r.skipped) };
      }),
    onSuccess: () => refreshRegistrations(client, linkId),
  });
}

export function useRetakeRegistration(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, ...body }: { registrationId: string; round_index: number; reason: string }) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/${registrationId}/retake`, body).then(normalizeRegistrationDetail),
    onSuccess: (detail) => refreshRegistrations(client, linkId, detail),
    meta: { silentError: true },
  });
}

export function useCancelRegistration(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, reason }: { registrationId: string; reason?: string }) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/${registrationId}/cancel`, reason ? { reason } : {}).then(normalizeRegistrationDetail),
    onSuccess: (detail) => refreshRegistrations(client, linkId, detail),
    meta: { silentError: true },
  });
}

export function useUnblockRegistration(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, reason }: { registrationId: string; reason: string }) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/${registrationId}/unblock`, { reason }).then((raw) => {
        const r = rec(raw);
        return { registration: normalizeRegistrationDetail(r.registration), personal_url: strOr(r.personal_url, "") };
      }),
    onSuccess: (result) => refreshRegistrations(client, linkId, result.registration),
    meta: { silentError: true },
  });
}

export function useReissuePersonalLink(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (registrationId: string) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/${registrationId}/personal-link`).then((raw) => {
        const r = rec(raw);
        return { personal_url: strOr(r.personal_url, ""), issued_at: strOr(r.issued_at, new Date().toISOString()) };
      }),
    onSuccess: (_result, registrationId) => void client.invalidateQueries({ queryKey: linkKeys.registration(linkId, registrationId) }),
  });
}

export function useFlagReview(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ registrationId, ...body }: { registrationId: string; decision: "cleared" | "confirmed"; note?: string }) =>
      api.post<unknown>(`/open-links/${linkId}/registrations/${registrationId}/flag-review`, body).then(normalizeRegistrationDetail),
    onSuccess: (detail) => refreshRegistrations(client, linkId, detail),
  });
}

function filenameFrom(disposition: string | null, fallback: string): string {
  const match = disposition ? /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition) : null;
  return match?.[1] ? decodeURIComponent(match[1]) : fallback;
}

/** Downloads the CSV export through the authenticated API (no-store on the server). */
export async function exportRegistrations(linkId: string, params: { status?: string; needs_review?: boolean }): Promise<string> {
  const response = await api.raw("GET", `/open-links/${linkId}/registrations/export`, { params: { ...params }, headers: { Accept: "text/csv" } });
  const blob = await response.blob();
  const name = filenameFrom(response.headers.get("content-disposition"), "registrations.csv");
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

export function useCandidateRegistrations(candidateId: string | undefined) {
  return useQuery({
    queryKey: linkKeys.byCandidate(candidateId ?? ""),
    queryFn: () => api.get<unknown>("/open-links/registrations", { params: { candidate_id: candidateId } }).then((raw) => page(raw, normalizeRegistration)),
    enabled: Boolean(candidateId),
    retry: false,
    meta: { errorToast: false },
  });
}

// --------------------------------------------------------------- public

export function getLinkInfo(token: string): Promise<LinkInfo> {
  return api.get<unknown>(`/link-access/${encodeURIComponent(token)}`, { skipAuthRefresh: true }).then(normalizeLinkInfo);
}

export function useLinkInfo(token: string) {
  return useQuery({
    queryKey: linkKeys.publicInfo(token),
    queryFn: () => getLinkInfo(token),
    enabled: Boolean(token),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  });
}

export async function registerForLink(token: string, body: RegisterRequest): Promise<RegisterResponse> {
  const r = rec(await api.post<unknown>(`/link-access/${encodeURIComponent(token)}/register`, body, { skipAuthRefresh: true }));
  return {
    outcome: r.outcome === "resumed" ? "resumed" : "registered",
    interview_id: strOr(r.interview_id, ""),
    next_step: normalizeStepKey(strOr(r.next_step, "welcome")),
    csrf_token: strOr(r.csrf_token, ""),
    personal_url: strOrNull(r.personal_url),
  };
}

export function usePersonalLinkInfo(token: string) {
  return useQuery({
    queryKey: linkKeys.personalInfo(token),
    queryFn: () =>
      api.get<unknown>(`/link-return/${encodeURIComponent(token)}`, { skipAuthRefresh: true }).then((raw): PersonalLinkInfo => {
        const r = rec(raw);
        return {
          organization_name: strOr(r.organization_name, "The hiring team"),
          job_title: strOr(r.job_title, ""),
          link_name: strOr(r.link_name, ""),
          state: r.state === "cancelled" ? "cancelled" : "active",
        };
      }),
    enabled: Boolean(token),
    retry: false,
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  });
}

export async function verifyPersonalLink(token: string, email: string): Promise<VerifyPersonalResponse> {
  const r = rec(await api.post<unknown>(`/link-return/${encodeURIComponent(token)}/verify`, { email }, { skipAuthRefresh: true }));
  return {
    interview_id: strOr(r.interview_id, ""),
    next_step: normalizeStepKey(strOr(r.next_step, "welcome")),
    csrf_token: strOr(r.csrf_token, ""),
    progress: r.progress ? normalizeProgress(r.progress) : null,
  };
}

// --------------------------------------------------------------- portal

export function useLinkProgress(options: { enabled?: boolean; pollMs?: (data: LinkProgress | undefined) => number | false } = {}) {
  const { pollMs } = options;
  return useQuery({
    queryKey: linkKeys.progress,
    queryFn: () => api.get<unknown>("/portal/link/progress").then(normalizeProgress),
    enabled: options.enabled ?? true,
    retry: false,
    staleTime: 5_000,
    refetchInterval: pollMs ? (query) => (query.state.error ? false : pollMs(query.state.data)) : false,
    meta: { errorToast: false },
  });
}

export function continueToRound(interviewId: string): Promise<ContinueResponse> {
  return api.post<unknown>("/portal/link/continue", { interview_id: interviewId }).then((raw) => {
    const r = rec(raw);
    return { interview_id: strOr(r.interview_id, interviewId), next_step: normalizeStepKey(strOr(r.next_step, "welcome")), csrf_token: strOr(r.csrf_token, "") };
  });
}

/** Drop every cached candidate-portal response: the session cookie now points at another interview. */
export function resetPortalCache(client: QueryClient): void {
  client.removeQueries({ queryKey: ["portal"] });
}

/** 404 on `/portal/link/progress`: this interview did not come from an open link. */
export function isNotLinkRegistration(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.code === "not_link_registration");
}
