import { Archive, ArchiveRestore, MoreHorizontal, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { confirmPhraseMatches } from "@/components/domain/DangerDeleteDialog";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { useConfirm } from "@/components/ui/AlertDialog";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { formatBytes, formatDateTime, pluralize } from "@/lib/format";
import type { CandidateDeletionResult } from "@/lib/types";

import { candidateActionError, REQUEST_REFERENCE, useArchiveCandidate, useCandidateDeletionPreview, useEraseCandidate, useRestoreCandidate } from "../api/candidates";
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
 * who can edit candidates; restore when archived; permanent deletion for admins
 * (an active candidate is archived and deleted in one confirmed step).
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
          {canErase ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setEraseOpen(true)} data-testid="candidate-delete-permanently">
                <Trash2 />
                Delete candidate permanently…
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      {canErase ? <EraseCandidateDialog candidate={candidate} open={eraseOpen} onOpenChange={setEraseOpen} onErased={onErased} /> : null}
    </>
  );
}

/** Always-visible place for an administrator to delete a candidate for good (sidebar card). */
export function CandidateDangerZone({ candidate, onErased }: { candidate: AdminCandidate; onErased: () => void }) {
  const [eraseOpen, setEraseOpen] = useState(false);
  return (
    <>
      <section aria-labelledby="candidate-danger-zone" className="rounded-lg border border-danger-line px-4 py-4">
        <h2 id="candidate-danger-zone" className="text-sm font-medium text-fg">
          Danger zone
        </h2>
        <p className="mt-1 text-xs leading-5 text-fg-muted">
          Delete this candidate and all their interviews, recordings, reports and files.{" "}
          {candidate.archived_at ? "" : "They're archived first, in the same step. "}This can't be undone.
        </p>
        <Button
          size="sm"
          variant="outline"
          leftIcon={<Trash2 />}
          className="mt-3 w-full border-danger-line text-danger-fg hover:bg-danger-soft"
          onClick={() => setEraseOpen(true)}
          data-testid="candidate-danger-delete"
        >
          Delete candidate permanently…
        </Button>
      </section>
      <EraseCandidateDialog candidate={candidate} open={eraseOpen} onOpenChange={setEraseOpen} onErased={onErased} />
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
 * Data-subject erasure. Irreversible, so it says exactly what goes (live counts),
 * asks the admin to type the candidate's full name, and records an optional request
 * reference. An active candidate is archived first, in the same request.
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
  const preview = useCandidateDeletionPreview(candidate.id, open);
  const [typed, setTyped] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nameId = useId();
  const refId = useId();
  const archived = Boolean(candidate.archived_at);

  useEffect(() => {
    if (!open) {
      setTyped("");
      setReference("");
      setError(null);
    }
  }, [open]);

  const matches = confirmPhraseMatches(typed, candidate.full_name);
  const referenceValid = reference.trim() === "" || REQUEST_REFERENCE.test(reference.trim());
  const counts = preview.data;

  const submit = async () => {
    if (!matches || !referenceValid) return;
    setError(null);
    try {
      const result = await erase.mutateAsync({ requestReference: reference.trim() || null, archiveFirst: !archived });
      toast.success("Candidate data deleted", { description: deletionSummary(result) });
      onOpenChange(false);
      onErased(result);
    } catch (caught) {
      setError(candidateActionError(caught));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !erase.isPending && onOpenChange(next)}>
      <DialogContent size="md" data-testid="erase-candidate-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="size-5 text-danger" aria-hidden="true" />
            Delete {candidate.full_name} permanently?
          </DialogTitle>
          <DialogDescription>
            {archived
              ? "Use this for a data-subject erasure request or to remove test data. It can't be undone."
              : "The candidate is archived first (open invitation links stop working) and then deleted, in one step. It can't be undone."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          <div className="rounded-md border border-danger-line bg-danger-soft px-3.5 py-3 text-sm leading-6 text-fg">
            <p className="font-medium">This removes, for good:</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-fg-muted marker:text-danger">
              <li>the candidate record, their résumés and profiles, and every interview invitation</li>
              <li>all sessions, transcripts, answer evaluations, scores, reports and review decisions</li>
              <li>intake interviews (evaluations, decisions) and open-link registrations</li>
              <li>stored files: résumés, answer audio, camera and screen images, recordings, report files</li>
            </ul>
            {counts ? (
              <p className="mt-2 text-fg" data-testid="erase-candidate-counts">
                Right now: {pluralize(counts.interviews, "interview")}, {pluralize(counts.sessions, "session")}, {pluralize(counts.reports, "report")},{" "}
                {pluralize(counts.resumes, "résumé")}
                {counts.intake_interviews ? `, ${pluralize(counts.intake_interviews, "intake interview")}` : ""}
                {counts.link_registrations ? `, ${pluralize(counts.link_registrations, "link registration")}` : ""}, {pluralize(counts.stored_files, "stored file")} (
                {formatBytes(counts.stored_bytes)}).
              </p>
            ) : null}
            {counts && counts.blockers.length > 0 ? (
              <p className="mt-2 font-medium text-danger-fg">An interview or résumé is still being processed; the deletion will be refused until it finishes.</p>
            ) : null}
            <p className="mt-2 text-fg-muted">The audit log keeps that a deletion happened, with counts and your reference, but no personal data.</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={nameId}>
              Type the candidate's full name <span className="font-mono text-[13px]">{candidate.full_name}</span> to confirm
            </Label>
            <Input id={nameId} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={typed !== "" && !matches ? true : undefined} />
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
            {archived ? "Delete permanently" : "Archive and delete permanently"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
