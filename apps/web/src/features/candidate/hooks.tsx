import { useQuery } from "@tanstack/react-query";
import { KeyRound, Link2Off, RefreshCw } from "lucide-react";
import { useEffect, useMemo, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";

import { useCandidateChrome, type CandidateChrome } from "@/components/layout/CandidateShell";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { errorMessage, isApiError } from "@/lib/api";
import { queryKeys } from "@/lib/query";

import { getPortalInterview, getPortalMe, type PortalInterviewView } from "./api";
import { completedSteps, journeySteps, markVisited, readLocal, requiredRedirect, stepAfter, stepBefore, type StepKey } from "./journey";

export function usePortalInterview(interviewId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.portal.interview(interviewId ?? "none"),
    queryFn: () => getPortalInterview(interviewId!),
    enabled: Boolean(interviewId),
    staleTime: 15_000,
    meta: { errorToast: false },
  });
}

export function usePortalMe(enabled = true) {
  return useQuery({
    queryKey: queryKeys.portal.me,
    queryFn: getPortalMe,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
    meta: { errorToast: false },
  });
}

export interface JourneyContext {
  interviewId: string;
  interview: PortalInterviewView;
  goTo: (step: StepKey | "complete" | "report", options?: { replace?: boolean }) => void;
  next: () => void;
  back: (() => void) | null;
}

/**
 * Shared behaviour of every journey page: loads the portal interview, renders
 * the candidate chrome (org, job, stepper with resumable progress), redirects
 * when a prerequisite (consent) is missing, and offers navigation helpers.
 */
export function useJourney(step: StepKey, chrome: Pick<CandidateChrome, "width" | "hideStepper"> = {}) {
  const { interviewId = "" } = useParams();
  const navigate = useNavigate();
  const query = usePortalInterview(interviewId);
  const interview = query.data ?? null;
  const visited = readLocal<string[]>(interviewId, "visited", []);
  const steps = useMemo(() => journeySteps(interview), [interview]);
  const completed = useMemo(() => (interview ? completedSteps(interview, visited) : []), [interview, visited]);

  useCandidateChrome({
    orgName: interview?.organizationName || null,
    subtitle: interview?.job.title || null,
    steps,
    completed,
    width: chrome.width,
    hideStepper: chrome.hideStepper,
  });

  // Local evidence (this browser just completed the step) counts too, so a slow server refresh never bounces the candidate back.
  const redirect = interview ? requiredRedirect(step, { stepsCompleted: [...interview.stepsCompleted, ...visited] }) : null;
  useEffect(() => {
    if (redirect) navigate(`/candidate/${interviewId}/${redirect}`, { replace: true });
  }, [redirect, interviewId, navigate]);

  const goTo = (target: StepKey | "complete" | "report", options: { replace?: boolean } = {}) => {
    navigate(`/candidate/${interviewId}/${target}`, options);
  };
  const ctx: JourneyContext | null = interview
    ? {
        interviewId,
        interview,
        goTo,
        next: () => {
          markVisited(interviewId, step);
          goTo(stepAfter(step, interview));
        },
        back: stepBefore(step, interview) ? () => goTo(stepBefore(step, interview)!) : null,
      }
    : null;
  return { query, ctx, redirecting: Boolean(redirect) };
}

/** Loading / error frame for journey pages. */
export function JourneyBoundary({ query, children }: { query: ReturnType<typeof usePortalInterview>; children: ReactNode }) {
  if (query.isPending) {
    return (
      <div aria-busy="true" aria-label="Loading" className="grid gap-6">
        <Skeleton className="h-9 w-2/3" />
        <SkeletonText lines={3} />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) return <JourneyError error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children}</>;
}

export function JourneyError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (isApiError(error) && (error.status === 401 || error.status === 403)) {
    return (
      <EmptyState
        bordered
        icon={<KeyRound />}
        title="Open your invitation link to continue"
        description="For your privacy, this page needs the personal link from your invitation email. Opening it again signs you back in and brings you to the step you were on."
      />
    );
  }
  if (isApiError(error) && error.status === 404) {
    return (
      <EmptyState
        bordered
        icon={<Link2Off />}
        title="We couldn't find this interview"
        description="The link may be incomplete. Open the exact link from your invitation email, or contact the person who invited you."
      />
    );
  }
  return (
    <EmptyState
      bordered
      icon={<RefreshCw />}
      title="This page didn't load"
      description={errorMessage(error, "Check your connection and try again.")}
      actions={onRetry ? <Button onClick={onRetry}>Try again</Button> : undefined}
    />
  );
}
