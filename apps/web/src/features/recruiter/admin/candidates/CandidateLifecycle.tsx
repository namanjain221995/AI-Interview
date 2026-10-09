import { Archive, ArchiveRestore, MoreHorizontal, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { useConfirm } from "@/components/ui/AlertDialog";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { formatBytes, formatDateTime, pluralize } from "@/lib/format";
import type { CandidateDeletionResult } from "@/lib/types";

import { candidateActionError, REQUEST_REFERENCE, useArchiveCandidate, useEraseCandidate, useRestoreCandidate } from "../api/candidates";
import type { AdminCandidate } from "../types";

/** What a deletion removed, in one sentence (counts only, as the API returns them). */
export function deletionSummary(result: CandidateDeletionResult): string {
  const parts = [
    pluralize(result.interviews, "interview"),
    pluralize(result.sessions, "session"),
    pluralize(result.resumes, "résumé"),
    `${pluralize(result.media_files_deleted, "stored file")} (${formatBytes(result.media_bytes_deleted)})`,
  ];
  return `Removed ${parts.join(", ")}.`;
}

/**
 * Header actions for the candidate lifecycle: archive (soft, restorable) for anyone
 * who can edit candidates; restore and permanent deletion when archived.
 */
export function CandidateLifecycleMenu({
  candidate,
  canWrite,
  canErase,
  onErased,
}: {
  candidate: AdminCandidate;
  canWrite: boolean;
  canErase: boolean;
  onErased: () => void;
}) {
  const confirm = useConfirm();
  const archive = useArchiveCandidate(candidate.id);
  const restore = useRestoreCandidate(candidate.id);
  const [eraseOpen, setEraseOpen] = useState(false);
  const archived = Boolean(candidate.archived_at);
  if (!canWrite) return null;

  const askArchive = () =>
    void confirm({
      title: `Archive ${candidate.full_name}?`,
      description:
        "They're hidden from candidate lists and any open invitation links stop working. Their interviews, reports and résumés are kept, and you can restore them at any time.",
      confirmLabel: "Archive candidate",
      onConfirm: async () => {
        try {
          await archive.mutateAsync();
          toast.success("Candidate archived", { description: "Open invitations were cancelled." });
        } catch (error) {
          toast.error("Couldn't archive the candidate", { description: candidateActionError(error) });
          throw error;
        }
      },
    });

  const doRestore = async () => {
    try {
      await restore.mutateAsync();
      toast.success("Candidate restored", { description: "Generate a new invitation link if they should interview again." });
    } catch (error) {
      toast.error("Couldn't restore the candidate", { description: candidateActionError(error) });
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" className="px-2.5" aria-label="More actions">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-52">
          {archived ? (
            <DropdownMenuItem onSelect={() => void doRestore()}>
              <ArchiveRestore />
              Restore candidate
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onSelect={askArchive}>
              <Archive />
              Archive candidate
            </DropdownMenuItem>
          )}
          {archived && canErase ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setEraseOpen(true)}>
                <Trash2 />
                Delete permanently…
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      {canErase ? <EraseCandidateDialog candidate={candidate} open={eraseOpen} onOpenChange={setEraseOpen} onErased={onErased} /> : null}
    </>
  );
}

/** Shown on an archived candidate: what archiving means, and the way back. */
export function ArchivedCandidateBanner({ candidate, canWrite, canErase, onErased }: { candidate: AdminCandidate; canWrite: boolean; canErase: boolean; onErased: () => void }) {
  const restore = useRestoreCandidate(candidate.id);
  const [eraseOpen, setEraseOpen] = useState(false);
  if (!candidate.archived_at) return null;
  const doRestore = async () => {
    try {
      await restore.mutateAsync();
      toast.success("Candidate restored", { description: "Generate a new invitation link if they should interview again." });
    } catch (error) {
      toast.error("Couldn't restore the candidate", { description: candidateActionError(error) });
    }
  };
  return (
    <>
      <Callout
        tone="neutral"
        icon={<Archive />}
        title={`Archived ${formatDateTime(candidate.archived_at)}`}
        actions={
          canWrite ? (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" leftIcon={<ArchiveRestore />} onClick={() => void doRestore()} loading={restore.isPending} loadingText="Restoring…">
                Restore
              </Button>
              {canErase ? (
                <Button size="sm" variant="ghost" leftIcon={<Trash2 />} className="text-danger-fg hover:bg-danger-soft" onClick={() => setEraseOpen(true)}>
                  Delete permanently…
                </Button>
              ) : null}
            </div>
          ) : undefined
        }
      >
        Hidden from candidate lists. They can't be invited and no résumé can be added until they're restored. Past interviews and reports are kept.
      </Callout>
      {canErase ? <EraseCandidateDialog candidate={candidate} open={eraseOpen} onOpenChange={setEraseOpen} onErased={onErased} /> : null}
    </>
  );
}

/**
 * Data-subject erasure. Irreversible, so it says exactly what goes, asks the
 * admin to type the candidate's email, and records an optional request reference.
 */
export function EraseCandidateDialog({
  candidate,
  open,
  onOpenChange,
  onErased,
}: {
  candidate: AdminCandidate;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onErased: (result: CandidateDeletionResult) => void;
}) {
  const erase = useEraseCandidate(candidate.id);
  const [typed, setTyped] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const emailId = useId();
  const refId = useId();

  useEffect(() => {
    if (!open) {
      setTyped("");
      setReference("");
      setError(null);
    }
  }, [open]);

  const matches = typed.trim().toLowerCase() === candidate.email.trim().toLowerCase();
  const referenceValid = reference.trim() === "" || REQUEST_REFERENCE.test(reference.trim());

  const submit = async () => {
    if (!matches || !referenceValid) return;
    setError(null);
    try {
      const result = await erase.mutateAsync(reference.trim() || null);
      toast.success("Candidate data deleted", { description: deletionSummary(result) });
      onOpenChange(false);
      onErased(result);
    } catch (caught) {
      setError(candidateActionError(caught));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !erase.isPending && onOpenChange(next)}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="size-5 text-danger" aria-hidden="true" />
            Delete {candidate.full_name}'s data permanently?
          </DialogTitle>
          <DialogDescription>Use this for a data-subject erasure request. It can't be undone.</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          <div className="rounded-md border border-danger-line bg-danger-soft px-3.5 py-3 text-sm leading-6 text-fg">
            <p className="font-medium">This removes, for good:</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-fg-muted marker:text-danger">
              <li>the candidate record and every interview invitation</li>
              <li>all sessions, transcripts, answer evaluations, scores and reports</li>
              <li>stored files: résumés, answer audio, camera and screen images, recordings, report files</li>
            </ul>
            <p className="mt-2 text-fg-muted">The audit log keeps that a deletion happened, with counts and your reference, but no personal data.</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={emailId}>
              Type <span className="font-mono text-[13px]">{candidate.email}</span> to confirm
            </Label>
            <Input id={emailId} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={typed !== "" && !matches ? true : undefined} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={refId}>
              Request reference <span className="font-normal text-fg-muted">(optional)</span>
            </Label>
            <Input
              id={refId}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="e.g. DSR-2026-0142"
              autoComplete="off"
              maxLength={64}
              aria-invalid={!referenceValid ? true : undefined}
              aria-describedby={`${refId}-help`}
            />
            <p id={`${refId}-help`} className={referenceValid ? "text-xs text-fg-muted" : "text-xs font-medium text-danger-fg"}>
              {referenceValid ? "Your ticket or request id. Letters, digits and . _ : # / - only; no names or emails." : "Use letters, digits and . _ : # / - only (no spaces), starting with a letter or digit."}
            </p>
          </div>
          {error ? (
            <p role="alert" className="rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={erase.isPending} autoFocus>
            Keep the data
          </Button>
          <Button variant="danger" leftIcon={<Trash2 />} onClick={() => void submit()} disabled={!matches || !referenceValid} loading={erase.isPending} loadingText="Deleting…">
            Delete permanently
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
