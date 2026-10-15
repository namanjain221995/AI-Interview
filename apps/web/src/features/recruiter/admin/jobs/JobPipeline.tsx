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
import type { Interview, Round } from "@/lib/types";
import { toast } from "sonner";

import { useCancelInterview, useRegenerateInviteLink, type JobPipelineRow, type PipelineCounts, type PipelineStage } from "../api/candidates";
import type { InviteSecrets } from "../types";
import { describeFailure } from "../lib/serverErrors";
import { ScoreCell, SearchInput, SegmentedFilter } from "../components/Controls";
import { InviteSecretsView } from "./InviteDialog";

export type PipelineRow = JobPipelineRow;

const STAGE_FILTERS: ReadonlyArray<{ value: PipelineStage | "all"; label: string }> = [
  { value: "all", label: "All" },
  { value: "invited", label: "Invited" },
  { value: "in_progress", label: "In progress" },
  { value: "completed", label: "Completed" },
  { value: "closed", label: "Expired or cancelled" },
];

export interface JobPipelineProps {
  rows: PipelineRow[] | undefined;
  rounds: Round[];
  loading: boolean;
  /** A background refetch (another page, filter or search): rows stay, dimmed. */
  fetching?: boolean;
  error: unknown;
  onRetry: () => void;
  onInvite?: () => void;
  /** Server paging: zero-based page, page size and the number of matching rows. */
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  stage: PipelineStage | "all";
  onStageChange: (stage: PipelineStage | "all") => void;
  counts: PipelineCounts;
  search: string;
  onSearchChange: (search: string) => void;
}

export function JobPipeline({ rows, rounds, loading, fetching, error, onRetry, onInvite, page, pageSize, total, onPageChange, stage, onStageChange, counts, search, onSearchChange }: JobPipelineProps) {
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
          <StatusBadge kind="interview" value={interview.status === "invited" && interview.is_expired ? "expired" : interview.status} size="sm" />
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
          {interview.status === "invited" ? <span className="text-fg-subtle">{interview.is_expired ? "Expired" : "Expires"} {formatDate(interview.invite_expires_at)}</span> : null}
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

  const filtered = stage !== "all" || search.trim().length > 0;
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line-subtle px-4 py-3">
        <SegmentedFilter<PipelineStage | "all">
          label="Filter by stage"
          value={stage}
          onChange={onStageChange}
          options={STAGE_FILTERS.map((s) => ({ value: s.value, label: s.label, count: counts[s.value] }))}
        />
        <SearchInput value={search} onChange={onSearchChange} placeholder="Search name or email" className="w-full sm:w-64" />
      </div>
      <DataTable
        columns={columns}
        data={rows}
        getRowId={(r) => r.interview.id}
        loading={loading}
        fetching={fetching}
        error={error}
        onRetry={onRetry}
        onRowClick={(r) => (r.session ? navigate(`/app/sessions/${r.session.id}`) : navigate(`/app/candidates/${r.interview.candidate_id}`))}
        rowLabel={(r) => `Open ${r.interview.candidate?.full_name ?? "interview"}`}
        defaultSort={null}
        pageSize={pageSize}
        page={page}
        total={total}
        onPageChange={onPageChange}
        caption="Candidates invited to this job"
        empty={
          filtered ? (
            <EmptyState size="sm" icon={<UserPlus />} title="No one matches" description="Try another stage or search." />
          ) : (
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
          )
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
