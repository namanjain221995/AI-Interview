import { BriefcaseBusiness, CalendarClock, Copy, Ellipsis, FileText, Link2, MonitorPlay, RefreshCcw, Target, Timer, Trash2, UserRound, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";

import { confirm } from "@/components/ui/AlertDialog";
import { Avatar } from "@/components/ui/Avatar";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { toast } from "@/components/ui/Toaster";
import { isApiError } from "@/lib/api";
import { useHasRole, usePermission } from "@/lib/auth";
import { formatDateTime, formatMinutes } from "@/lib/format";

import { copyText } from "@/features/recruiter/admin/lib/utils";

import { useCancelIntake, useReevaluateIntake, useRegenerateIntakeLink } from "../../api";
import { TARGET_ROLE_LABELS } from "../../labels";
import { formatTotal } from "../../lib/decimals";
import type { IntakeCreateResponse, IntakeDetail, IntakeEvaluation } from "../../types";
import { CopyValue } from "../create/CopyValue";
import { DeleteIntakeDialog } from "./DeleteIntakeDialog";
import { BandBadge, BucketBadge } from "../IntakeBadges";

function Meta({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
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

const ENDED = new Set(["completing", "completed", "aborted", "failed"]);

function NewLinkDialog({ result, onClose }: { result: IntakeCreateResponse | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const close = async () => {
    if (result?.invite_url && !copied) {
      const ok = await confirm({ title: "Close without copying?", description: "This link can't be shown again.", confirmLabel: "Close anyway", cancelLabel: "Go back", tone: "danger" });
      if (!ok) return;
    }
    setCopied(false);
    onClose();
  };
  return (
    <Dialog open={Boolean(result)} onOpenChange={(open) => (!open ? void close() : undefined)}>
      <DialogContent size="md" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>New invite link</DialogTitle>
          <DialogDescription>The previous link no longer works.{result?.expires_at ? ` This one works until ${formatDateTime(result.expires_at)}.` : ""}</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid grid-cols-1 gap-4">
          <Callout tone="warning" title="Copy it now. It won't be shown again." />
          {result?.invite_url ? <CopyValue label="Invite link" value={result.invite_url} valueTestId="intake-invite-link" copyTestId="intake-copy-link" onCopied={() => setCopied(true)} /> : null}
          {result?.access_code ? <CopyValue label="Access code" value={result.access_code} valueTestId="intake-access-code" emphasis /> : null}
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => void close()}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ReviewHeader({ detail, evaluation }: { detail: IntakeDetail; evaluation: IntakeEvaluation | null }) {
  const navigate = useNavigate();
  const canReview = usePermission("reviews:write");
  const canInvite = usePermission("interviews:invite");
  const canSeeCandidates = usePermission("candidates:read");
  const reevaluate = useReevaluateIntake(detail.id);
  const regenerate = useRegenerateIntakeLink(detail.id);
  const cancel = useCancelIntake(detail.id);
  const [newLink, setNewLink] = useState<IntakeCreateResponse | null>(null);
  // Permanent deletion is for administrators only (the API enforces it too).
  const canDelete = useHasRole("ADMIN", "SUPER_ADMIN");
  const [deleteOpen, setDeleteOpen] = useState(false);

  const session = detail.session;
  const ended = Boolean(session && ENDED.has(session.status));
  const notStarted = detail.status === "active" && (!session || ["created", "preparing", "ready"].includes(session.status)) && detail.interview?.status !== "completed";
  const role = detail.onboarding?.target_role ?? detail.target_role;
  const planned = detail.settings.duration_minutes * 60;

  const startReevaluate = async () => {
    const ok = await confirm({
      title: "Evaluate this interview again?",
      description: "A new evaluation version is built from the same interview. The current one stays available, and decisions already recorded are kept.",
      confirmLabel: "Re-evaluate",
    });
    if (!ok) return;
    reevaluate.mutate(undefined, {
      onSuccess: () => toast.success("Re-evaluation started", { description: "The scorecard updates here when it's ready, usually within a few minutes." }),
      onError: (error) => {
        const code = isApiError(error) ? error.code : null;
        toast.error(code === "evaluation_running" ? "An evaluation is already running" : code === "session_not_finished" ? "The interview hasn't finished yet" : "Couldn't start the evaluation", {
          description: code === "evaluation_running" ? "Wait for it to finish, then try again if needed." : undefined,
        });
      },
    });
  };

  const startRegenerate = async () => {
    const ok = await confirm({ title: "Generate a new invite link?", description: "The current link stops working. You'll see the new one once.", confirmLabel: "Generate new link" });
    if (!ok) return;
    regenerate.mutate(
      { expires_in_hours: detail.settings.expires_in_hours },
      {
        onSuccess: setNewLink,
        onError: (error) => {
          const code = isApiError(error) ? error.code : null;
          toast.error(code === "intake_cancelled" ? "This intake was cancelled" : code === "intake_not_sent" ? "This intake hasn't been sent yet" : "Couldn't generate a new link", {
            description: code === "intake_not_sent" ? "Send the draft from the create page first." : undefined,
          });
        },
      },
    );
  };

  const startCancel = async () => {
    const ok = await confirm({
      title: "Cancel this intake interview?",
      description: session && ["live", "paused"].includes(session.status) ? "The interview is running: cancelling ends it now." : "The invite link stops working. This can't be undone.",
      confirmLabel: "Cancel intake",
      tone: "danger",
    });
    if (ok) cancel.mutate({});
  };

  const copyPageLink = async () => {
    if (await copyText(window.location.href)) toast.success("Link copied");
  };

  return (
    <header className="flex flex-col gap-4 pb-6">
      <Breadcrumbs items={[{ label: "Intake interviews", to: "/app/intake" }, { label: detail.candidate.full_name }]} />
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 items-start gap-3.5">
          <Avatar name={detail.candidate.full_name} size="xl" />
          <div className="grid min-w-0 grid-cols-1 gap-1.5">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="min-w-0 text-title text-fg">{detail.candidate.full_name}</h1>
              <BucketBadge bucket={detail.bucket} size="md" />
            </div>
            <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5">
              {detail.job.title ? (
                <Meta icon={<BriefcaseBusiness />} label="Job">
                  <Link to={`/app/jobs/${detail.job.id}`} className="rounded-xs hover:text-fg hover:underline">
                    {detail.job.title}
                  </Link>
                </Meta>
              ) : null}
              <Meta icon={<Target />} label="Target role">
                {role ? TARGET_ROLE_LABELS[role] : "Candidate chooses"}
              </Meta>
              <Meta icon={<CalendarClock />} label="Started">
                {session?.started_at ? formatDateTime(session.started_at) : detail.status === "draft" ? "Draft, not sent" : "Not started"}
              </Meta>
              {session?.elapsed_seconds ? (
                <Meta icon={<Timer />} label="Duration">
                  {formatMinutes(session.elapsed_seconds)}
                  <span className="text-fg-subtle"> of {formatMinutes(planned)} planned</span>
                </Meta>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-start gap-3 lg:items-end">
          {evaluation?.status === "ready" ? (
            <div className="flex flex-wrap items-center gap-2.5" aria-label="AI recommendation">
              <span className="text-sm text-fg-muted">
                <span className="text-xl font-semibold text-fg">{formatTotal(evaluation.weighted_total ?? evaluation.partial_total)}</span> / 5
              </span>
              <BandBadge band={evaluation.band} label={evaluation.band_label} size="md" />
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {canReview && ended ? (
              <Button variant="outline" leftIcon={<RefreshCcw />} onClick={() => void startReevaluate()} loading={reevaluate.isPending} data-testid="intake-reevaluate">
                Re-evaluate
              </Button>
            ) : null}
            {session ? (
              <Button variant="outline" leftIcon={<FileText />} asChild data-testid="intake-report-link">
                <Link to={`/app/reports/${session.id}`}>Printable report</Link>
              </Button>
            ) : null}
            {session ? (
              <Button variant="outline" leftIcon={<MonitorPlay />} asChild>
                <Link to={`/app/sessions/${session.id}`}>Open session</Link>
              </Button>
            ) : null}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" className="size-9 px-0" aria-label="More intake actions">
                  <Ellipsis />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-60">
                <DropdownMenuItem onSelect={() => void copyPageLink()}>
                  <Copy />
                  Copy link to this page
                </DropdownMenuItem>
                {canSeeCandidates && detail.candidate.id ? (
                  <DropdownMenuItem onSelect={() => navigate(`/app/candidates/${detail.candidate.id}`)}>
                    <UserRound />
                    View candidate profile
                  </DropdownMenuItem>
                ) : null}
                {canInvite && notStarted ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => void startRegenerate()} disabled={regenerate.isPending}>
                      <Link2 />
                      Generate a new invite link
                    </DropdownMenuItem>
                  </>
                ) : null}
                {canInvite && (detail.status === "draft" || (detail.status === "active" && !ended)) ? (
                  <DropdownMenuItem onSelect={() => void startCancel()} disabled={cancel.isPending} className="text-danger-fg">
                    <XCircle />
                    Cancel intake
                  </DropdownMenuItem>
                ) : null}
                {canDelete ? (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      destructive
                      disabled={Boolean(session && ["live", "paused"].includes(session.status))}
                      onSelect={() => setDeleteOpen(true)}
                      data-testid="intake-delete-permanently"
                    >
                      <Trash2 />
                      Delete intake permanently…
                    </DropdownMenuItem>
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>
      <NewLinkDialog result={newLink} onClose={() => setNewLink(null)} />
      {canDelete ? <DeleteIntakeDialog intakeId={detail.id} candidateName={detail.candidate.full_name} open={deleteOpen} onOpenChange={setDeleteOpen} /> : null}
    </header>
  );
}
