import { useQuery } from "@tanstack/react-query";
import { CircleCheckBig, KeyRound, RefreshCw } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { StageShell } from "@/components/layout/StageShell";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { InterviewRoom } from "@/features/interview/InterviewRoom";
import { errorMessage, isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";

import { createOrResumeSession } from "../api";
import { usePortalInterview } from "../hooks";
import { writeLocal } from "../journey";

/** Page title for the states around the room (the room sets its own while it is up). */
function DocumentTitle({ title }: { title: string }) {
  useDocumentTitle(title);
  return null;
}

function StageMessage({ icon, title, body, action }: { icon: ReactNode; title: string; body: string; action?: ReactNode }) {
  return (
    <StageShell>
      <DocumentTitle title={title} />
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md text-center">
          <span className="mx-auto flex size-11 items-center justify-center rounded-full bg-neutral-soft text-fg-muted [&_svg]:size-5">{icon}</span>
          <h1 className="mt-4 text-title text-fg">{title}</h1>
          <p className="mt-2 text-[15px] leading-6 text-fg-muted">{body}</p>
          {action ? <div className="mt-6 flex justify-center">{action}</div> : null}
        </div>
      </div>
    </StageShell>
  );
}

/** /candidate/:interviewId/interview — full-screen interview room (outside the candidate frame). */
export default function InterviewPage() {
  const { interviewId = "" } = useParams();
  const navigate = useNavigate();
  const interview = usePortalInterview(interviewId);
  const session = useQuery({
    queryKey: ["portal", "interview", interviewId, "ensure-session"],
    queryFn: () => createOrResumeSession(interviewId),
    enabled: Boolean(interviewId),
    staleTime: Infinity,
    gcTime: 0,
    retry: (count, error) => !(isApiError(error) && error.status < 500) && count < 2,
    refetchOnWindowFocus: false,
    meta: { errorToast: false },
  });

  useEffect(() => {
    if (session.data) writeLocal(interviewId, "session", session.data.sessionId);
  }, [session.data, interviewId]);

  const finished = session.data && ["completed", "completing", "aborted"].includes(session.data.status);
  useEffect(() => {
    if (finished && session.data) navigate(`/candidate/${interviewId}/complete?session=${encodeURIComponent(session.data.sessionId)}`, { replace: true });
  }, [finished, session.data, interviewId, navigate]);

  const error = interview.error ?? session.error;
  const code = isApiError(error) ? error.code : "";
  useEffect(() => {
    // The server knows exactly which step is missing; take the candidate there.
    if (code === "consent_required") navigate(`/candidate/${interviewId}/consent`, { replace: true });
    else if (code === "round_required") navigate(`/candidate/${interviewId}/round`, { replace: true });
    else if (code === "interview_completed" || code === "session_ended") navigate(`/candidate/${interviewId}/complete`, { replace: true });
  }, [code, interviewId, navigate]);
  if (error) {
    if (isApiError(error) && (error.status === 401 || error.status === 403)) {
      return (
        <StageMessage
          icon={<KeyRound />}
          title="Open your invitation link to continue"
          body="For your privacy, the interview room needs the personal link from your invitation email. Opening it again signs you back in."
        />
      );
    }
    if (isApiError(error) && ["consent_required", "round_required", "interview_completed", "session_ended"].includes(error.code)) {
      return (
        <StageShell>
          <DocumentTitle title="Interview" />
          <div className="flex h-full items-center justify-center">
            <Spinner size="lg" label="Redirecting" />
          </div>
        </StageShell>
      );
    }
    if (isApiError(error) && error.status === 409) {
      return (
        <StageMessage
          icon={<CircleCheckBig />}
          title="This interview has already finished"
          body="Thank you. There's nothing more to do here."
          action={
            <Button asChild variant="outline">
              <Link to={`/candidate/${interviewId}/complete`}>See what happens next</Link>
            </Button>
          }
        />
      );
    }
    return (
      <StageMessage
        icon={<RefreshCw />}
        title="The interview room didn't open"
        body={errorMessage(error, "Check your connection and try again.")}
        action={<Button onClick={() => window.location.reload()}>Try again</Button>}
      />
    );
  }

  if (!interview.data || !session.data || finished) {
    return (
      <StageShell>
        <DocumentTitle title="Interview" />
        <div className="flex h-full flex-col items-center justify-center gap-3 text-fg-muted">
          <Spinner size="lg" label={null} />
          <p className="text-sm" role="status">
            Preparing your interview room…
          </p>
        </div>
      </StageShell>
    );
  }

  return <InterviewRoom interview={interview.data} sessionId={session.data.sessionId} sessionStatus={session.data.status} />;
}
