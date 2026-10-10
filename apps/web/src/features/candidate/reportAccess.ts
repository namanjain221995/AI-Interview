import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

import { isApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { CandidateReportMode } from "@/lib/types";

import { getCandidateReport, getPortalSession, type CandidateReportView, type PortalInterviewView } from "./api";
import { readLocal } from "./journey";

/** The session to show results for: ?session=, this browser's last session, or the server's latest. */
export function useSessionId(interviewId: string, interview: PortalInterviewView | null | undefined): string | null {
  const [params] = useSearchParams();
  return params.get("session") || readLocal<string | null>(interviewId, "session", null) || interview?.latestSession?.id || null;
}

function asMode(value: unknown): CandidateReportMode | null {
  return value === "immediate" || value === "after_review" || value === "never" ? value : null;
}

/**
 * The invitation's candidate feedback policy. Missing or unknown values count as
 * `never` (the platform default): the portal never promises feedback it was not
 * told it will share.
 */
export function reportMode(interview: PortalInterviewView | null | undefined): CandidateReportMode {
  return asMode(interview?.policy.candidate_report_mode) ?? "never";
}

/**
 * The feedback policy for one session: the session's own `candidate_report_mode`
 * (what the report pipeline uses) when the portal session endpoint returns it,
 * else the invitation's policy. `pending` is true until the session answered, so
 * pages can hold back feedback copy instead of flashing the wrong promise.
 */
export function useReportMode(interview: PortalInterviewView | null | undefined, sessionId: string | null): { mode: CandidateReportMode; pending: boolean } {
  const session = useQuery({
    queryKey: queryKeys.portal.session(sessionId ?? "none"),
    queryFn: () => getPortalSession(sessionId!),
    enabled: Boolean(sessionId),
    meta: { errorToast: false },
  });
  const fromSession = asMode(session.data?.candidateReportMode);
  return { mode: fromSession ?? reportMode(interview), pending: Boolean(sessionId) && session.isPending };
}

/** Session statuses in which the interview is not over yet (the complete page says so instead of "saved"). */
const WRAPPING_STATUSES = new Set(["live", "paused"]);
/** The candidate left before the interview started: nothing was ended, the link still works until its deadline. */
const NOT_STARTED_STATUSES = new Set(["created", "preparing", "ready"]);
const EARLY_END_REASONS = new Set(["disconnect_timeout", "technical_failure", "cancelled", "policy_termination"]);

export type SessionOutcome = "unknown" | "not_started" | "wrapping_up" | "complete" | "ended_early" | "cancelled";

export function sessionOutcome(status: string | null | undefined, endReason: string | null | undefined): SessionOutcome {
  if (!status) return "unknown";
  if (NOT_STARTED_STATUSES.has(status)) return "not_started";
  if (WRAPPING_STATUSES.has(status)) return "wrapping_up";
  if (endReason === "cancelled") return "cancelled";
  if (status === "aborted" || status === "failed" || (endReason && EARLY_END_REASONS.has(endReason))) return "ended_early";
  return "complete";
}

/**
 * What actually happened to the session, for honest copy on the complete page (S6): a candidate who pressed
 * "End interview" while offline arrives here before the server has closed the session, so the page keeps
 * polling until it has.
 */
export function useSessionOutcome(sessionId: string | null): { outcome: SessionOutcome; endReason: string | null } {
  const session = useQuery({
    queryKey: queryKeys.portal.session(sessionId ?? "none"),
    queryFn: () => getPortalSession(sessionId!),
    enabled: Boolean(sessionId),
    refetchInterval: (q) => (q.state.data && WRAPPING_STATUSES.has(String(q.state.data.status)) ? 4000 : false),
    meta: { errorToast: false },
  });
  const status = session.data ? String(session.data.status) : null;
  return { outcome: sessionOutcome(status, session.data?.endReason), endReason: session.data?.endReason ?? null };
}

export function isReportReady(report: CandidateReportView | undefined): boolean {
  return Boolean(report && (report.status === "ready" || report.status === "released") && (report.overallScore !== null || Object.keys(report.narrative).length > 0));
}

/**
 * The candidate report once it is released. While waiting (`poll`), the cheap
 * session endpoint's `candidate_report_available` flag is polled instead of the
 * report itself (which answers 404 until release); the report is fetched once.
 * Not-yet-available answers (404 / 403 / 409) count as "pending", not errors.
 */
export function useCandidateReport(sessionId: string | null, options: { poll: boolean; intervalMs?: number }) {
  const availability = useQuery({
    queryKey: queryKeys.portal.session(sessionId ?? "none"),
    queryFn: () => getPortalSession(sessionId!),
    enabled: Boolean(sessionId) && options.poll,
    refetchInterval: (q) => (q.state.data?.candidateReportAvailable ? false : (options.intervalMs ?? 5000)),
    refetchOnWindowFocus: true,
    meta: { errorToast: false },
  });
  const available = !options.poll || Boolean(availability.data?.candidateReportAvailable);
  return useQuery({
    queryKey: queryKeys.portal.report(sessionId ?? "none"),
    enabled: Boolean(sessionId) && available,
    queryFn: async (): Promise<CandidateReportView | null> => {
      try {
        return await getCandidateReport(sessionId!);
      } catch (error) {
        if (isApiError(error) && [403, 404, 409, 425].includes(error.status)) return null;
        throw error;
      }
    },
    retry: 1,
    meta: { errorToast: false },
  });
}
