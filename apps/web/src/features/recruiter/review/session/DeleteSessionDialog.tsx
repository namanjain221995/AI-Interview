import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "@/components/ui/Toaster";

import { DangerDeleteDialog, deleteActionError } from "@/components/domain/DangerDeleteDialog";
import { api } from "@/lib/api";
import { formatBytes, pluralize } from "@/lib/format";
import { queryKeys } from "@/lib/query";

/** Counts returned by `DELETE /sessions/{id}` (no personal data). */
export interface SessionDeletionResult {
  session_id: string;
  interview_id: string;
  candidate_id: string;
  deleted_at: string;
  sessions: number;
  transcripts: number;
  reports: number;
  integrity_events: number;
  coding_sessions: number;
  media_files_deleted: number;
  media_bytes_deleted: number;
}

/** Permanent session delete (ADMIN). Removes the cached session everywhere on success. */
export function useDeleteSession(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<SessionDeletionResult>(`/sessions/${sessionId}`),
    meta: { silentError: true },
    onSuccess: (result) => {
      client.removeQueries({ queryKey: queryKeys.sessions.detail(sessionId) });
      void client.invalidateQueries({ queryKey: queryKeys.sessions.all });
      if (result?.candidate_id) void client.invalidateQueries({ queryKey: queryKeys.candidates.detail(result.candidate_id) });
      void client.invalidateQueries({ queryKey: ["intake"] });
    },
  });
}

/**
 * Confirm dialog for deleting one interview session for good. The admin types the
 * candidate's name (or DELETE when the session has no candidate name); on success the
 * page goes back to the sessions list.
 */
export function DeleteSessionDialog({
  sessionId,
  candidateName,
  intake,
  open,
  onOpenChange,
}: {
  sessionId: string;
  candidateName: string | null | undefined;
  intake?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const remove = useDeleteSession(sessionId);
  const [error, setError] = useState<string | null>(null);
  const confirmText = candidateName?.trim() || "DELETE";

  const confirm = async () => {
    setError(null);
    try {
      const result = await remove.mutateAsync();
      toast.success("Session deleted", {
        description: `Removed ${pluralize(result.transcripts, "transcript line")}, ${pluralize(result.reports, "report")}, ${pluralize(result.media_files_deleted, "stored file")} (${formatBytes(result.media_bytes_deleted)}).`,
      });
      onOpenChange(false);
      navigate("/app/sessions", { replace: true });
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
      testId="delete-session-dialog"
      title="Delete this session permanently?"
      description="Use this to remove a test run or data you must not keep. It can't be undone."
      removes={[
        "the transcript, questions, answers, evaluations, evidence and scores",
        "reports (and their downloadable files) and review decisions",
        "recordings, answer audio, camera and screen images, integrity events and coding work",
        ...(intake ? ["the intake stage and activity results, evaluations and decisions of this session"] : []),
      ]}
      keeps="The candidate, their résumés and the interview invitation stay (the invitation keeps its status, so it can't be taken again by accident)."
      confirmText={confirmText}
      confirmLabel="Delete session"
      pending={remove.isPending}
      error={error}
      onConfirm={() => void confirm()}
    />
  );
}
