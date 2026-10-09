import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { DangerDeleteDialog, deleteActionError } from "@/components/domain/DangerDeleteDialog";
import { toast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";
import { formatBytes, pluralize } from "@/lib/format";
import { queryKeys } from "@/lib/query";

import { intakeKeys } from "../../api";

/** Counts returned by `DELETE /intake/interviews/{id}` (no personal data). */
export interface IntakeDeletionResult {
  intake_id: string;
  candidate_id: string;
  deleted_at: string;
  was_draft: boolean;
  interviews: number;
  evaluations: number;
  decisions: number;
  auth_sessions_revoked: number;
  sessions: { sessions: number; reports: number; media_files_deleted: number; media_bytes_deleted: number };
}

/** Permanent intake delete (ADMIN). */
export function useDeleteIntake(intakeId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<IntakeDeletionResult>(`/intake/interviews/${intakeId}`),
    meta: { silentError: true },
    onSuccess: (result) => {
      client.removeQueries({ queryKey: intakeKeys.detail(intakeId) });
      void client.invalidateQueries({ queryKey: ["intake"] });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.all });
      if (result?.candidate_id) void client.invalidateQueries({ queryKey: queryKeys.candidates.detail(result.candidate_id) });
    },
  });
}

/** Confirm dialog for deleting one intake interview for good; goes back to the intake list on success. */
export function DeleteIntakeDialog({
  intakeId,
  candidateName,
  open,
  onOpenChange,
}: {
  intakeId: string;
  candidateName: string | null | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const remove = useDeleteIntake(intakeId);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setError(null);
    try {
      const result = await remove.mutateAsync();
      const s = result.sessions;
      toast.success("Intake interview deleted", {
        description: result.was_draft
          ? "The draft was removed."
          : `Removed ${pluralize(s.sessions, "session")}, ${pluralize(result.evaluations, "evaluation")}, ${pluralize(s.media_files_deleted, "stored file")} (${formatBytes(s.media_bytes_deleted)}).`,
      });
      onOpenChange(false);
      navigate("/app/intake", { replace: true });
    } catch (caught) {
      setError(deleteActionError(caught));
    }
  };

  return (
    <DangerDeleteDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setError(null);
        onOpenChange(next);
      }}
      testId="delete-intake-dialog"
      title="Delete this intake interview permanently?"
      description="Use this to remove a test run or data you must not keep. It can't be undone."
      removes={[
        "the intake interview, its invite link and the candidate's sign-in to it",
        "the session: transcript, stage and activity results, reading and speaking captures",
        "evaluations, scorecards, decisions and the report (with its file)",
        "recordings, camera and screen images and integrity events",
      ]}
      keeps="The candidate and their résumé stay. Delete the candidate to remove everything about them."
      confirmText={candidateName?.trim() || "DELETE"}
      confirmLabel="Delete intake interview"
      pending={remove.isPending}
      error={error}
      onConfirm={() => void confirm()}
    />
  );
}
