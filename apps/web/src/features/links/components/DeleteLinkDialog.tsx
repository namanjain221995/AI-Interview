import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import { DangerDeleteDialog, deleteActionError } from "@/components/domain/DangerDeleteDialog";
import { Callout } from "@/components/ui/Callout";
import { toast } from "@/components/ui/Toaster";
import { api } from "@/lib/api";

import { linkKeys } from "../api";
import type { OpenLink } from "../types";

/** Counts returned by `DELETE /open-links/{id}`. */
export interface LinkDeletionResult {
  link_id: string;
  deleted_at: string;
  previous_status: string;
  registrations_deleted: number;
}

/** Permanent link delete (ADMIN). The API refuses a link that already produced a candidate. */
export function useDeleteLink(linkId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<LinkDeletionResult>(`/open-links/${linkId}`),
    meta: { silentError: true },
    onSuccess: () => {
      client.removeQueries({ queryKey: linkKeys.detail(linkId) });
      void client.invalidateQueries({ queryKey: linkKeys.all });
    },
  });
}

/**
 * Confirm dialog for deleting an interview link for good. Safe by design: only links
 * nobody registered through can be deleted (otherwise the API answers 409 and the
 * dialog suggests closing the link). Candidates and their interviews are never touched.
 */
export function DeleteLinkDialog({ link, open, onOpenChange }: { link: OpenLink; open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const remove = useDeleteLink(link.id);
  const [error, setError] = useState<string | null>(null);
  const registered = link.counts?.registrations ?? 0;

  const confirm = async () => {
    setError(null);
    try {
      await remove.mutateAsync();
      toast.success("Interview link deleted");
      onOpenChange(false);
      navigate("/app/links", { replace: true });
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
      testId="delete-link-dialog"
      title="Delete this interview link permanently?"
      description="The shareable link stops working at once. It can't be undone."
      removes={["the link, its settings and registration form", "attempts that never became a candidate (blocked or abandoned registrations)"]}
      keeps="Candidates who registered, their interviews and reports are never deleted with a link."
      extra={
        registered > 0 ? (
          <Callout tone="warning" title="People have registered through this link">
            A link that already produced a candidate can't be deleted. Close the link instead, or delete those candidates first.
          </Callout>
        ) : null
      }
      confirmText={link.name}
      confirmLabel="Delete link"
      pending={remove.isPending}
      error={error}
      onConfirm={() => void confirm()}
    />
  );
}
