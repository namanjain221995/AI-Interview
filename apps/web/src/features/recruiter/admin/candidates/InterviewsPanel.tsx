import { FileText, MonitorPlay, Send } from "lucide-react";
import { Link } from "react-router";

import { RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatDate, formatDuration, formatRelativeOrDate } from "@/lib/format";
import { REVIEW_DECISION_LABELS, SESSION_END_REASON_LABELS } from "@/lib/labels";
import type { Interview, SessionSummary } from "@/lib/types";

import { ScoreCell } from "../components/Controls";

export interface InterviewsPanelProps {
  interviews: Interview[];
  sessions: SessionSummary[];
  loading: boolean;
  onInvite?: () => void;
}

/** Each invitation with its sessions: status, score, assistive recommendation and links. */
export function InterviewsPanel({ interviews, sessions, loading, onInvite }: InterviewsPanelProps) {
  if (loading) {
    return (
      <div className="grid gap-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (interviews.length === 0 && sessions.length === 0) {
    return (
      <EmptyState
        bordered
        icon={<Send />}
        title="Not invited yet"
        description="Invite this candidate to a job to send them a private interview link."
        actions={onInvite ? <Button size="sm" onClick={onInvite}>Invite to a job</Button> : undefined}
      />
    );
  }
  const sorted = [...interviews].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const orphanSessions = sessions.filter((s) => !interviews.some((i) => i.id === s.interview_id));

  return (
    <div className="grid gap-3">
      {sorted.map((interview) => {
        const own = sessions.filter((s) => s.interview_id === interview.id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
        return (
          <Card key={interview.id}>
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line-subtle px-4 py-3">
              <div className="grid min-w-0 gap-0.5">
                <Link to={`/app/jobs/${interview.job_id}`} className="truncate text-sm font-semibold text-fg hover:underline">
                  {interview.job?.title ?? "Job"}
                </Link>
                <p className="text-xs text-fg-muted">
                  {interview.round?.name ?? (interview.allow_round_selection ? "Candidate chooses the round" : "Round")}. Invited {formatRelativeOrDate(interview.created_at)}
                  {interview.status === "invited" ? `, link valid until ${formatDate(interview.invite_expires_at)}` : ""}
                </p>
              </div>
              <StatusBadge kind="interview" value={interview.status} size="sm" />
            </div>
            {own.length === 0 ? (
              <p className="px-4 py-3 text-sm text-fg-muted">{interview.status === "invited" ? "Hasn't started yet." : "No interview session recorded."}</p>
            ) : (
              <ul className="divide-y divide-line-subtle">
                {own.map((session) => (
                  <SessionRow key={session.id} session={session} />
                ))}
              </ul>
            )}
          </Card>
        );
      })}
      {orphanSessions.length ? (
        <Card>
          <ul className="divide-y divide-line-subtle">
            {orphanSessions.map((session) => (
              <SessionRow key={session.id} session={session} showJob />
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function SessionRow({ session, showJob }: { session: SessionSummary; showJob?: boolean }) {
  return (
    <li className="grid gap-3 px-4 py-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
      <div className="grid min-w-0 gap-1">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge kind="session" value={session.status} size="sm" />
          {showJob && session.job ? <span className="font-medium text-fg">{session.job.title}</span> : null}
          <span className="text-fg-muted">{session.round?.name}</span>
          <span className="text-xs text-fg-subtle">
            {session.started_at ? formatRelativeOrDate(session.started_at) : "Not started"}
            {session.elapsed_seconds ? `, ${formatDuration(session.elapsed_seconds)}` : ""}
            {session.end_reason ? `, ${SESSION_END_REASON_LABELS[session.end_reason]?.toLowerCase() ?? session.end_reason}` : ""}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
            Overall
            <ScoreCell value={session.overall_score} />
          </span>
          {session.recommendation ? <RecommendationBadge value={session.recommendation} size="sm" layout="inline" /> : null}
          {session.review_decision ? <span className="text-xs text-fg-muted">Human decision: {REVIEW_DECISION_LABELS[session.review_decision]}</span> : null}
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        <Button asChild size="sm" variant="outline" leftIcon={<MonitorPlay />}>
          <Link to={`/app/sessions/${session.id}`}>Session</Link>
        </Button>
        {session.status === "completed" ? (
          <Button asChild size="sm" variant="ghost" leftIcon={<FileText />}>
            <Link to={`/app/reports/${session.id}`}>Report</Link>
          </Button>
        ) : null}
      </div>
    </li>
  );
}
