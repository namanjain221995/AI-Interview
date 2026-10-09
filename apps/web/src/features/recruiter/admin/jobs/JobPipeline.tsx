import { useState } from "react";
import { FileText, Link2, MoreHorizontal, MonitorPlay, UserPlus, XCircle } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { confirm } from "@/components/ui/AlertDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { Dialog, DialogContent } from "@/components/ui/Dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { usePermission } from "@/lib/auth";
import { formatDate, formatRelativeOrDate } from "@/lib/format";
import type { Interview, InterviewStatus, Round, SessionSummary } from "@/lib/types";
import { toast } from "sonner";

import { useCancelInterview, useRegenerateInviteLink } from "../api/candidates";
import type { InviteSecrets } from "../types";
import { describeFailure } from "../lib/serverErrors";
import { ScoreCell } from "../components/Controls";
import { InviteSecretsView } from "./InviteDialog";

export interface PipelineRow {
  interview: Interview;
  session: SessionSummary | null;
}

export function buildPipeline(interviews: readonly Interview[], sessions: readonly SessionSummary[]): PipelineRow[] {
  const byInterview = new Map<string, SessionSummary>();
  for (const s of sessions) {
    const current = byInterview.get(s.interview_id);
    if (!current || Date.parse(s.created_at) > Date.parse(current.created_at)) byInterview.set(s.interview_id, s);
  }
  return [...interviews]
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .map((interview) => ({ interview, session: byInterview.get(interview.id) ?? null }));
}

export type PipelineStage = "invited" | "in_progress" | "completed" | "closed";

export function pipelineCounts(rows: readonly PipelineRow[]): Record<PipelineStage, number> {
  const counts: Record<PipelineStage, number> = { invited: 0, in_progress: 0, completed: 0, closed: 0 };
  for (const { interview } of rows) {
    const status: InterviewStatus = interview.status;
    if (status === "invited") counts.invited += 1;
    else if (status === "in_progress") counts.in_progress += 1;
    else if (status === "completed") counts.completed += 1;
    else counts.closed += 1;
  }
  return counts;
}

export interface JobPipelineProps {
  rows: PipelineRow[];
  rounds: Round[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  onInvite?: () => void;
}

export function JobPipeline({ rows, rounds, loading, error, onRetry, onInvite }: JobPipelineProps) {
  const navigate = useNavigate();
  const canInvite = usePermission("interviews:invite");
  const cancel = useCancelInterview();
  const regenerate = useRegenerateInviteLink();
  const [secrets, setSecrets] = useState<InviteSecrets | null>(null);
  const roundName = (id: string | null) => (id ? (rounds.find((r) => r.id === id)?.name ?? "Round") : null);

  const onRegenerate = async (interview: Interview) => {
    const ok = await confirm({
      title: "Generate a new invite link?",
      description: "The current link stops working immediately. You'll see the new link and access code once.",
      confirmLabel: "Generate new link",
    });
    if (!ok) return;
    try {
      setSecrets(await regenerate.mutateAsync(interview.id));
    } catch (e) {
      toast.error("Couldn't generate a new link", { description: describeFailure(e) });
    }
  };

  const onCancel = async (interview: Interview) => {
    const ok = await confirm({
      title: `Cancel ${interview.candidate?.full_name ? `${interview.candidate.full_name}'s` : "this"} invitation?`,
      description:
        interview.status === "in_progress"
          ? "The link stops working and the candidate is signed out of any interview in progress. Anything already recorded is kept."
          : "The link stops working. Anything already recorded is kept.",
      confirmLabel: "Cancel invitation",
      cancelLabel: "Keep it",
      tone: "danger",
    });
    if (ok) cancel.mutate(interview.id);
  };

  const columns: DataTableColumn<PipelineRow>[] = [
    {
      id: "candidate",
      header: "Candidate",
      sortValue: (r) => r.interview.candidate?.full_name ?? "",
      cell: ({ interview }) => (
        <div className="flex min-w-0 items-center gap-3">
          <Avatar name={interview.candidate?.full_name} size="md" />
          <div className="grid min-w-0">
            <Link to={`/app/candidates/${interview.candidate_id}`} className="truncate font-medium text-fg hover:underline">
              {interview.candidate?.full_name ?? "Candidate"}
            </Link>
            <span className="truncate text-xs text-fg-muted">{interview.candidate?.email}</span>
          </div>
        </div>
      ),
    },
    {
      id: "round",
      header: "Round",
      hideBelow: "md",
      cell: ({ interview, session }) => (
        <span className="text-fg-muted">{session?.round?.name ?? roundName(interview.round_id) ?? (interview.allow_round_selection ? "Candidate chooses" : "—")}</span>
      ),
    },
    {
      id: "status",
      header: "Status",
      sortValue: (r) => r.interview.status,
      cell: ({ interview, session }) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge kind="interview" value={interview.status} size="sm" />
          {session && session.status !== "completed" && interview.status === "in_progress" ? <StatusBadge kind="session" value={session.status} size="sm" /> : null}
        </div>
      ),
    },
    {
      id: "score",
      header: "Overall",
      hideBelow: "lg",
      width: "10rem",
      sortValue: (r) => r.session?.overall_score ?? null,
      cell: ({ session }) =>
        <ScoreCell value={session?.overall_score} />,
    },
    {
      id: "invited",
      header: "Invited",
      hideBelow: "sm",
      sortValue: (r) => r.interview.created_at,
      cell: ({ interview }) => (
        <div className="grid text-xs">
          <span className="text-fg">{formatRelativeOrDate(interview.created_at)}</span>
          {interview.status === "invited" ? <span className="text-fg-subtle">Expires {formatDate(interview.invite_expires_at)}</span> : null}
        </div>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      width: "3.5rem",
      cell: ({ interview, session }) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Actions for ${interview.candidate?.full_name ?? "interview"}`} data-row-action>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {session ? (
              <DropdownMenuItem onSelect={() => navigate(`/app/sessions/${session.id}`)}>
                <MonitorPlay /> Open session
              </DropdownMenuItem>
            ) : null}
            {session?.status === "completed" ? (
              <DropdownMenuItem onSelect={() => navigate(`/app/reports/${session.id}`)}>
                <FileText /> Open report
              </DropdownMenuItem>
            ) : null}
            {canInvite && (interview.status === "invited" || interview.status === "expired" || interview.status === "in_progress") ? (
              <DropdownMenuItem onSelect={() => void onRegenerate(interview)}>
                <Link2 /> New invite link
              </DropdownMenuItem>
            ) : null}
            {canInvite && (interview.status === "invited" || interview.status === "expired" || interview.status === "in_progress") ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem destructive onSelect={() => void onCancel(interview)}>
                  <XCircle /> Cancel invitation
                </DropdownMenuItem>
              </>
            ) : null}
            {!session && !canInvite ? <DropdownMenuItem disabled>No actions</DropdownMenuItem> : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        data={rows}
        getRowId={(r) => r.interview.id}
        loading={loading}
        error={error}
        onRetry={onRetry}
        onRowClick={(r) => (r.session ? navigate(`/app/sessions/${r.session.id}`) : navigate(`/app/candidates/${r.interview.candidate_id}`))}
        rowLabel={(r) => `Open ${r.interview.candidate?.full_name ?? "interview"}`}
        defaultSort={null}
        pageSize={20}
        caption="Candidates invited to this job"
        empty={
          <EmptyState
            size="sm"
            icon={<UserPlus />}
            title="No one invited yet"
            description="Invite a candidate to send them a private link to their AI interview."
            actions={
              onInvite && canInvite ? (
                <Button size="sm" leftIcon={<UserPlus />} onClick={onInvite}>
                  Invite candidate
                </Button>
              ) : undefined
            }
          />
        }
      />
      <Dialog open={secrets !== null} onOpenChange={(open) => !open && setSecrets(null)}>
        <DialogContent size="lg" onInteractOutside={(e) => e.preventDefault()}>
          {secrets ? (
            <InviteSecretsView
              title="New invite link"
              description={`The previous link for ${secrets.interview.candidate?.full_name ?? "this candidate"} no longer works.`}
              inviteUrl={secrets.invite_url}
              accessCode={secrets.access_code ?? null}
              onCopied={() => undefined}
              onDone={() => setSecrets(null)}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
