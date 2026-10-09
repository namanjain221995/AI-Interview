import { ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { isApiError } from "@/lib/api";

/** Case- and spacing-insensitive match of what the admin typed against the expected phrase. */
export function confirmPhraseMatches(typed: string, expected: string): boolean {
  const norm = (value: string) => value.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
  return norm(expected) !== "" && norm(typed) === norm(expected);
}

/** Plain-language message for the conflicts the admin-only delete endpoints report. */
export function deleteActionError(error: unknown): string {
  if (!isApiError(error)) return error instanceof Error && error.message ? error.message : "Something went wrong. Try again.";
  const seconds = typeof error.details.retry_after_seconds === "number" ? error.details.retry_after_seconds : error.retryAfterSeconds;
  const minutes = seconds ? Math.max(1, Math.ceil(seconds / 60)) : null;
  const later = minutes ? ` in about ${minutes} minute${minutes === 1 ? "" : "s"}` : " in a few minutes";
  switch (error.code) {
    case "session_busy":
      return `The session is still running or being scored. Try again${later}.`;
    case "intake_busy":
      return `A session of this intake interview is still running or being evaluated. Try again${later}.`;
    case "link_has_registrations":
      return "People have already registered through this link, so it can't be deleted. Close the link instead, or delete those candidates first.";
    case "deletion_incomplete":
    case "erasure_incomplete":
      return "Some stored files couldn't be deleted, so nothing was removed. Try again; if it keeps failing, contact your administrator.";
    default:
      if (error.status === 403) return "Only administrators can do this.";
      if (error.status === 404) return "It no longer exists (someone may have deleted it already).";
      return error.message || "Something went wrong. Try again.";
  }
}

/**
 * Confirmation for an irreversible, admin-only delete: says what goes and what stays,
 * and keeps the danger button disabled until `confirmText` is typed.
 */
export function DangerDeleteDialog({
  open,
  onOpenChange,
  title,
  description,
  removes,
  keeps,
  confirmText,
  confirmLabel,
  pending,
  error,
  onConfirm,
  extra,
  testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  /** What is deleted for good. */
  removes: ReactNode[];
  /** What stays (optional). */
  keeps?: ReactNode;
  /** The phrase the admin types to confirm (e.g. the candidate's full name). */
  confirmText: string;
  confirmLabel: string;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  extra?: ReactNode;
  testId?: string;
}) {
  const [typed, setTyped] = useState("");
  const inputId = useId();
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const matches = confirmPhraseMatches(typed, confirmText);

  return (
    <Dialog open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <DialogContent size="md" data-testid={testId}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="size-5 shrink-0 text-danger" aria-hidden="true" />
            {title}
          </DialogTitle>
          <DialogDescription>{description ?? "This can't be undone."}</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-4">
          <div className="rounded-md border border-danger-line bg-danger-soft px-3.5 py-3 text-sm leading-6 text-fg">
            <p className="font-medium">This removes, for good:</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-fg-muted marker:text-danger">
              {removes.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
            {keeps ? <p className="mt-2 text-fg-muted">{keeps}</p> : null}
            <p className="mt-2 text-fg-muted">The audit log records that the deletion happened, with counts only.</p>
          </div>
          {extra}
          <div className="grid gap-1.5">
            <Label htmlFor={inputId}>
              Type <span className="font-mono text-[13px] break-all">{confirmText}</span> to confirm
            </Label>
            <Input
              id={inputId}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={typed !== "" && !matches ? true : undefined}
            />
          </div>
          {error ? (
            <p role="alert" className="rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending} autoFocus>
            Cancel
          </Button>
          <Button variant="danger" leftIcon={<Trash2 />} onClick={() => matches && onConfirm()} disabled={!matches} loading={pending} loadingText="Deleting…">
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
