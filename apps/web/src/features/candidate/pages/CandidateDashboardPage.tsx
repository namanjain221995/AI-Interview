import { CalendarClock, KeyRound } from "lucide-react";
import { Link } from "react-router";

import { useCandidateChrome } from "@/components/layout/CandidateShell";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { errorMessage, isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import type { InterviewStatus } from "@/lib/types";

import { NextRoundCard } from "@/features/links/components/NextRoundCard";

import { type PortalInterviewView } from "../api";
import { usePortalInterview, usePortalMe } from "../hooks";
import { readLocal, resumeStep } from "../journey";
import { isReportReady, reportMode, useCandidateReport, useReportMode } from "../reportAccess";
import { selectedRound } from "../round";

export default function CandidateDashboardPage() {
  useDocumentTitle("Your interviews");
  const me = usePortalMe();
  const interviewId = me.data?.interviewId ?? undefined;
  const interview = usePortalInterview(interviewId);
  useCandidateChrome({ orgName: me.data?.organizationName ?? interview.data?.organizationName ?? null, hideStepper: true });

  if (me.isPending || (interviewId && interview.isPending)) {
    return (
      <div className="grid gap-4" aria-busy="true" aria-label="Loading">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-36 w-full" />
      </div>
    );
  }

  const authError = (me.error && isApiError(me.error) && (me.error.status === 401 || me.error.status === 403)) || !interviewId;
  if (authError) {
    return (
      <div>
        <h1 className="text-title text-fg">Your interviews</h1>
        <EmptyState
          bordered
          className="mt-8"
          icon={<KeyRound />}
          title="Open your invitation link to begin"
          description="Each interview starts from the personal link in your invitation email. It signs you in and brings you to the right step."
        />
      </div>
    );
  }
  if (me.error || interview.error) {
    return (
      <EmptyState
        bordered
        icon={<CalendarClock />}
        title="Your interviews didn't load"
        description={errorMessage(me.error ?? interview.error, "Check your connection and try again.")}
        actions={<Button onClick={() => void interview.refetch()}>Try again</Button>}
      />
    );
  }

  return (
    <div>
      <h1 className="text-title text-fg">{me.data?.firstName ? `Hi ${me.data.firstName}` : "Your interviews"}</h1>
      <p className="mt-2 text-fg-muted">{interview.data && reportMode(interview.data) !== "never" ? "Your interview, where you left off, and your feedback once it's shared." : "Your interview and where you left off."}</p>
      {interview.data ? <InterviewCard interview={interview.data} /> : null}
      {interview.data ? <NextRoundCard className="mt-6" /> : null}
    </div>
  );
}

function InterviewCard({ interview }: { interview: PortalInterviewView }) {
  const round = selectedRound(interview);
  const sessionId = readLocal<string | null>(interview.id, "session", null) ?? interview.latestSession?.id ?? null;
  const done = interview.status === "completed" || ["completed", "completing"].includes(interview.latestSession?.status ?? "");
  const { mode, pending: modePending } = useReportMode(interview, done ? sessionId : null);
  const sharesFeedback = !modePending && mode !== "never";
  const report = useCandidateReport(done && sharesFeedback ? sessionId : null, { poll: false });
  const reportReady = isReportReady(report.data ?? undefined);
  const next = resumeStep(interview, readLocal<string[]>(interview.id, "visited", []));
  const closed = interview.status === "expired" || interview.status === "cancelled";

  return (
    <article className="mt-8 rounded-xl border border-line bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-heading text-fg">{interview.job.title || "Interview"}</h2>
          <p className="mt-0.5 text-sm text-fg-muted">
            {interview.organizationName}
            {round ? `, ${round.name}` : ""}
          </p>
        </div>
        <StatusBadge kind="interview" value={interview.status as InterviewStatus} />
      </div>
      <dl className="mt-5 grid gap-4 border-t border-line pt-4 text-sm sm:grid-cols-3">
        {round ? (
          <div>
            <dt className="text-[13px] text-fg-muted">Length</dt>
            <dd className="font-medium text-fg">About {round.durationMinutes} minutes</dd>
          </div>
        ) : null}
        {interview.expiresAt && !done ? (
          <div>
            <dt className="text-[13px] text-fg-muted">Link valid until</dt>
            <dd className="font-medium text-fg">{formatDateTime(interview.expiresAt)}</dd>
          </div>
        ) : null}
        {sharesFeedback ? (
          <div>
            <dt className="text-[13px] text-fg-muted">Feedback</dt>
            <dd className="font-medium text-fg">{reportReady ? "Ready" : done ? (mode === "immediate" ? "Being prepared" : "After review") : "After the interview"}</dd>
          </div>
        ) : null}
      </dl>
      <div className="mt-5 flex flex-wrap gap-2">
        {sharesFeedback && reportReady ? (
          <Button asChild>
            <Link to={`/candidate/${interview.id}/report${sessionId ? `?session=${encodeURIComponent(sessionId)}` : ""}`}>View your feedback</Link>
          </Button>
        ) : null}
        {!done && !closed ? (
          <Button asChild>
            <Link to={`/candidate/${interview.id}/${next}`}>{next === "welcome" ? "Start" : "Continue where you left off"}</Link>
          </Button>
        ) : null}
        {done && !reportReady ? (
          <Button asChild variant="outline">
            <Link to={`/candidate/${interview.id}/complete`}>What happens next</Link>
          </Button>
        ) : null}
      </div>
    </article>
  );
}
