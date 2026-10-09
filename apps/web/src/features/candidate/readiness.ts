/**
 * Is the interview ready to start? The engine's session.prepare waits for the
 * résumé profile (~minutes on the shared model), so the candidate can move on
 * after text extraction and only "Begin" waits: until the résumé profile is
 * ready or the session reports "ready". Polls both while waiting.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { queryKeys } from "@/lib/query";

import { getPortalSession, getResume, TERMINAL_RESUME_FAILURES, type PortalInterviewView, type ResumeView } from "./api";

const POLL_MS = 3000;
/** After this long, offer to start anyway (preparation stalled; the engine has a fallback plan). */
export const PREPARATION_PATIENCE_MS = 4 * 60_000;

export function resumeInProgress(view: ResumeView | null | undefined): boolean {
  return Boolean(view && view.status !== "none" && !TERMINAL_RESUME_FAILURES.has(view.status) && !view.profileReady);
}

/** Shared résumé status query (same cache entry as the résumé page). */
export function useResumeStatus(interviewId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.portal.resume(interviewId),
    queryFn: () => getResume(interviewId),
    enabled: Boolean(interviewId) && enabled,
    refetchInterval: (q) => (resumeInProgress(q.state.data) ? POLL_MS : false),
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  });
}

export interface Readiness {
  ready: boolean;
  /** Short state for the Begin button area. */
  phase: "ready" | "analysing" | "preparing" | "resume_failed" | "resume_missing";
  resume: ResumeView | null;
  sessionStatus: string | null;
  /** Preparation is taking unusually long: the candidate may start anyway. */
  canStartAnyway: boolean;
}

export function useInterviewReadiness(interview: PortalInterviewView | null | undefined, sessionId: string | null): Readiness {
  const interviewId = interview?.id ?? "";
  const resume = useResumeStatus(interviewId, Boolean(interview));
  const session = useQuery({
    queryKey: queryKeys.portal.session(sessionId ?? "none"),
    queryFn: () => getPortalSession(sessionId!),
    enabled: Boolean(sessionId),
    refetchInterval: (q) => (["created", "preparing"].includes(q.state.data?.status ?? "created") ? POLL_MS : false),
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  });
  const [waitingSince] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(id);
  }, []);

  const view = resume.data ?? null;
  const sessionStatus = session.data?.status ?? interview?.latestSession?.status ?? null;
  const sessionReady = Boolean(sessionStatus && ["ready", "live", "paused", "completing", "completed"].includes(sessionStatus));
  const required = interview?.requiresResume !== false;
  const profileReady = Boolean(view?.profileReady);
  const failed = Boolean(view && TERMINAL_RESUME_FAILURES.has(view.status));
  const missing = required && (!view || view.status === "none");

  let phase: Readiness["phase"];
  if (sessionReady || profileReady || (!required && (!view || view.status === "none") && sessionStatus !== null)) phase = "ready";
  else if (failed) phase = "resume_failed";
  else if (missing && resume.isSuccess) phase = "resume_missing";
  else if (resumeInProgress(view)) phase = "analysing";
  else phase = "preparing";

  return {
    ready: phase === "ready",
    phase,
    resume: view,
    sessionStatus,
    canStartAnyway: phase !== "ready" && now - waitingSince >= PREPARATION_PATIENCE_MS,
  };
}
