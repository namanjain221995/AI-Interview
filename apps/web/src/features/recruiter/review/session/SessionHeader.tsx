import { BriefcaseBusiness, CalendarClock, ClipboardCheck, Copy, Ellipsis, FileText, Layers, MessagesSquare, RefreshCcw, Timer, Trash2, UserRound } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { Avatar } from "@/components/ui/Avatar";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { Button } from "@/components/ui/Button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { toast } from "@/components/ui/Toaster";
import { useHasRole, usePermission } from "@/lib/auth";
import { formatDateTime, formatMinutes, pluralize } from "@/lib/format";
import { labelFor, ROUND_TYPE_LABELS, SESSION_END_REASON_LABELS } from "@/lib/labels";

import { useRecomputeScores } from "../api/hooks";
import { useSessionContext } from "./context";
import { DeleteSessionDialog } from "./DeleteSessionDialog";

function MetaItem({ icon, children, label }: { icon: ReactNode; children: ReactNode; label: string }) {
  return (
    <li className="flex min-w-0 items-center gap-1.5 text-[13px] text-fg-muted">
      <span className="shrink-0 text-fg-subtle [&_svg]:size-4" aria-hidden="true">
        {icon}
      </span>
      <span className="sr-only">{label}: </span>
      <span className="min-w-0 truncate">{children}</span>
    </li>
  );
}

export function SessionHeader() {
  const { session, inProgress, live, watchable, durationSeconds, plannedSeconds, questions, sessionId, nav } = useSessionContext();
  const navigate = useNavigate();
  const canRecompute = usePermission("reviews:write");
  const canSeeCandidates = usePermission("candidates:read");
  const recompute = useRecomputeScores(sessionId);
  // Permanent deletion is for administrators only (the API enforces it too).
  const canDelete = useHasRole("ADMIN", "SUPER_ADMIN");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const name = session.candidate?.full_name ?? "Candidate";
  const round = session.round;
  const questionCount = session.question_count ?? session.state_digest?.question_count ?? questions.length;
  // Intake sessions have their own review page (docs/design/INTAKE_INTERVIEW.md 11.1) and scores the intake pipeline manages.
  const intake = round?.round_type === "intake";

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast.success("Link copied");
    } catch {
      toast.error("Couldn't copy the link");
    }
  };

  return (
    <header className="flex flex-col gap-4 pb-6">
      <Breadcrumbs items={[{ label: "Sessions", to: "/app/sessions" }, { label: name }]} />
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3.5">
          <Avatar name={name} size="xl" />
          <div className="grid grid-cols-1 min-w-0 gap-1.5">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="min-w-0 text-title text-fg">{name}</h1>
              <StatusBadge kind="session" value={session.status} />
            </div>
            <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
              {session.job ? (
                <MetaItem icon={<BriefcaseBusiness />} label="Job">
                  <Link to={`/app/jobs/${session.job.id}`} className="rounded-xs hover:text-fg hover:underline">
                    {session.job.title}
                  </Link>
                </MetaItem>
              ) : null}
              {round ? (
                <MetaItem icon={<Layers />} label="Round">
                  {round.name}
                  {labelFor(ROUND_TYPE_LABELS, round.round_type).toLowerCase() !== round.name.toLowerCase() ? (
                    <span className="text-fg-subtle"> ({labelFor(ROUND_TYPE_LABELS, round.round_type)})</span>
                  ) : null}
                </MetaItem>
              ) : null}
              <MetaItem icon={<CalendarClock />} label="Started">
                {session.started_at ? formatDateTime(session.started_at) : "Not started yet"}
              </MetaItem>
              <MetaItem icon={<Timer />} label="Duration">
                {live ? `${formatMinutes(durationSeconds)} so far` : formatMinutes(durationSeconds)}
                {plannedSeconds ? <span className="text-fg-subtle"> of {formatMinutes(plannedSeconds)} planned</span> : null}
              </MetaItem>
              <MetaItem icon={<MessagesSquare />} label="Questions">
                {pluralize(questionCount, "question")}
              </MetaItem>
            </ul>
            {session.end_reason && !inProgress ? <p className="text-xs text-fg-subtle">{labelFor(SESSION_END_REASON_LABELS, session.end_reason)}</p> : null}
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {intake ? (
            <Button variant="outline" leftIcon={<ClipboardCheck />} asChild data-testid="session-intake-review-link">
              <Link to={`/app/intake/session/${sessionId}`}>Open intake review</Link>
            </Button>
          ) : null}
          {watchable ? (
            nav.tab !== "live" ? (
              <Button variant="outline" onClick={() => nav.go({ tab: "live" })} leftIcon={<span className="size-2 rounded-full bg-signal animate-pulse-soft" />}>
                Watch live
              </Button>
            ) : null
          ) : (
            <Button variant="outline" leftIcon={<FileText />} asChild>
              <Link to={`/app/reports/${sessionId}`}>{inProgress ? "Draft report" : "Open report"}</Link>
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" className="size-9 px-0" aria-label="More session actions">
                <Ellipsis />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              {canSeeCandidates ? (
                <DropdownMenuItem onSelect={() => navigate(`/app/candidates/${session.candidate_id}`)}>
                  <UserRound />
                  View candidate profile
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuItem onSelect={() => void copyLink()}>
                <Copy />
                Copy link to this session
              </DropdownMenuItem>
              {canRecompute && !inProgress && !intake ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem disabled={recompute.isPending} onSelect={() => recompute.mutate()}>
                    <RefreshCcw />
                    Recompute scores from evidence
                  </DropdownMenuItem>
                </>
              ) : null}
              {canDelete ? (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    destructive
                    disabled={live}
                    title={live ? "End the interview before deleting it" : undefined}
                    onSelect={() => setDeleteOpen(true)}
                    data-testid="session-delete-permanently"
                  >
                    <Trash2 />
                    Delete session permanently…
                  </DropdownMenuItem>
                </>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {canDelete ? (
        <DeleteSessionDialog sessionId={sessionId} candidateName={session.candidate?.full_name} intake={intake} open={deleteOpen} onOpenChange={setDeleteOpen} />
      ) : null}
    </header>
  );
}
