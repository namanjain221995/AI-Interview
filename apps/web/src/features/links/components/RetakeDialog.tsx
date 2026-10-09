import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/Button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";

import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";

import { useRetakeRegistration } from "../api";
import { ROUND_STATE_LABELS } from "../status";
import type { Registration } from "../types";

/** A new attempt is possible for the current round only, once its latest attempt is finished, expired or cancelled. */
export function retakeableRounds(registration: Registration) {
  return registration.rounds.filter(
    (r) => r.sequence === registration.current_round_index && (r.state === "completed" || r.state === "expired" || r.state === "cancelled"),
  );
}

export interface RetakeDialogProps {
  linkId: string;
  registration: Registration | null;
  onOpenChange: (open: boolean) => void;
}

/** "Allow another attempt": a recruiter-only second chance, always with a reason. */
export function RetakeDialog({ linkId, registration, onOpenChange }: RetakeDialogProps) {
  const retake = useRetakeRegistration(linkId);
  const options = registration ? retakeableRounds(registration) : [];
  const [round, setRound] = useState<string>("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!registration) return;
    const current = options.find((r) => r.sequence === registration.current_round_index) ?? options[options.length - 1];
    setRound(current ? String(current.sequence) : "");
    setReason("");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registration?.id]);

  const trimmed = reason.trim();
  const submit = async () => {
    if (!registration) return;
    if (trimmed.length < 3) {
      setError("Write a short reason (it goes into the audit log).");
      return;
    }
    try {
      await retake.mutateAsync({ registrationId: registration.id, round_index: Number(round), reason: trimmed });
      toast.success("Another attempt is open", { description: "Ask the candidate to open their personal link." });
      onOpenChange(false);
    } catch (caught) {
      if (isApiError(caught) && caught.code === "retake_not_allowed") setError("This round is in progress or not unlocked yet, so another attempt can't be opened.");
      else if (isApiError(caught) && caught.code === "candidate_archived") setError("The candidate is archived. Restore them first.");
      else setError(describeFailure(caught));
    }
  };

  return (
    <Dialog open={Boolean(registration)} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Allow another attempt</DialogTitle>
          <DialogDescription>
            {registration?.full_name} gets a new interview for the round you choose. Earlier attempts, reports and recordings stay.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="grid grid-cols-1 gap-4">
          {options.length === 0 ? (
            <p className="text-sm text-fg-muted">The current round must be finished, expired or cancelled before another attempt can be opened.</p>
          ) : (
            <div className="grid gap-1.5">
              <Label htmlFor="retake-round">Round</Label>
              <Select
                id="retake-round"
                value={round || null}
                onValueChange={setRound}
                disabled={options.length === 1}
                options={options.map((r) => ({ value: String(r.sequence), label: `${r.sequence + 1}. ${r.name}`, description: `${ROUND_STATE_LABELS[r.state]}, attempt ${r.attempt}` }))}
              />
            </div>
          )}
          <div className="grid gap-1.5">
            <Label htmlFor="retake-reason" required>
              Reason
            </Label>
            <Textarea
              id="retake-reason"
              value={reason}
              maxLength={500}
              rows={3}
              onChange={(e) => {
                setReason(e.target.value);
                setError(null);
              }}
              placeholder="e.g. Connection dropped in the middle of the round"
              aria-invalid={error ? true : undefined}
            />
            {error ? <p className="text-xs font-medium text-danger-fg">{error}</p> : <p className="text-xs text-fg-muted">Recorded in the audit log.</p>}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} loading={retake.isPending} disabled={options.length === 0} data-testid="registration-retake-confirm">
            Open another attempt
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
