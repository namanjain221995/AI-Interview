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

export function reportMode(interview: PortalInterviewView | null | undefined): CandidateReportMode {
  const mode = interview?.policy.candidate_report_mode;
  return mode === "immediate" || mode === "never" ? mode : "after_review";
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
