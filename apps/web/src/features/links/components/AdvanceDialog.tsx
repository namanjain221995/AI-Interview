import { Copy, Info } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Label } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";

import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import { copyText } from "@/features/recruiter/admin/lib/utils";

import { useAdvanceRegistrations } from "../api";
import { ADVANCE_SKIP_LABELS, labelOr } from "../status";
import type { AdvanceResult, Registration } from "../types";

export interface AdvanceDialogProps {
  linkId: string;
  /** Registrations to advance (only those awaiting review are sent). */
  registrations: Registration[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone?: (result: AdvanceResult) => void;
}

/** Summary line for the result toast: who was skipped and why. */
export function describeSkipped(result: AdvanceResult, people: Registration[]): string | undefined {
  if (!result.skipped.length) return undefined;
  const names = new Map(people.map((p) => [p.id, p.full_name]));
  return result.skipped
    .map((s) => `${names.get(s.registration_id) ?? "One person"}: ${labelOr(ADVANCE_SKIP_LABELS, s.code, "skipped")}`)
    .join("; ");
}

/** Confirms advancing people to their next round (single or bulk). */
export function AdvanceDialog({ linkId, registrations, open, onOpenChange, onDone }: AdvanceDialogProps) {
  const advance = useAdvanceRegistrations(linkId);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (open) setNote("");
  }, [open]);
  const eligible = registrations.filter((r) => r.status === "awaiting_review");
  const others = registrations.length - eligible.length;
  const count = eligible.length;

  const copyEmails = async () => {
    const ok = await copyText(eligible.map((r) => r.email).join(", "));
    if (ok) toast.success(`Copied ${count} ${count === 1 ? "email" : "emails"}`);
    else toast.error("Couldn't copy the emails");
  };

  const confirm = async () => {
    try {
      const result = await advance.mutateAsync({ registration_ids: eligible.map((r) => r.id), ...(note.trim() ? { note: note.trim() } : {}) });
      const n = result.advanced.length;
      toast.success(n ? `Advanced ${n} ${n === 1 ? "person" : "people"} to the next round` : "Nobody was advanced", { description: describeSkipped(result, registrations), duration: result.skipped.length ? 10_000 : undefined });
      onDone?.(result);
      onOpenChange(false);
    } catch (error) {
      toast.error("Couldn't advance", { description: describeFailure(error) });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>{count === 1 ? `Advance ${eligible[0]!.full_name}?` : `Advance ${count} people?`}</DialogTitle>
          <DialogDescription>Their next round unlocks now. A person made this decision; scores never move anyone automatically.</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid grid-cols-1 gap-4">
          <Callout tone="info" icon={<Info />} title="Tell them it's ready">
            We don't send messages. Ask candidates to open their personal link to start the next round.
            <Button variant="outline" size="sm" className="mt-3 flex w-fit" leftIcon={<Copy />} onClick={() => void copyEmails()} disabled={count === 0}>
              Copy candidate emails
            </Button>
          </Callout>
          {others > 0 ? (
            <p className="text-sm text-fg-muted">
              {others} selected {others === 1 ? "person isn't" : "people aren't"} waiting for review and will be skipped.
            </p>
          ) : null}
          <div className="grid gap-1.5">
            <Label htmlFor="advance-note" optional>
              Note for the audit log
            </Label>
            <Textarea id="advance-note" value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Reviewed round 1 reports with the panel" />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void confirm()} loading={advance.isPending} disabled={count === 0} data-testid="advance-confirm">
            {count === 1 ? "Advance" : `Advance ${count}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
