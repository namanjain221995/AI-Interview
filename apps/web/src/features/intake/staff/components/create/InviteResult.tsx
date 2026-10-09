import { AlertTriangle, Check, ClipboardCopy, ExternalLink, KeyRound, Mail, Plus, RotateCcw } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { confirm } from "@/components/ui/AlertDialog";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { Progress } from "@/components/ui/Progress";
import { formatDateTime } from "@/lib/format";

import { copyText } from "@/features/recruiter/admin/lib/utils";

import { invitationMessage, invitationSubject, mailtoHref } from "../../lib/invite";
import type { IntakeCreateResponse } from "../../types";
import { CopyValue } from "./CopyValue";
import { useLeaveGuard } from "./useLeaveGuard";

export type ResumeUploadState =
  | { status: "idle" }
  | { status: "uploading"; name: string; percent: number | null }
  | { status: "done"; name: string }
  | { status: "failed"; name: string; message: string };

export interface InviteResultProps {
  result: IntakeCreateResponse;
  programName: string | null;
  organizationName: string | null;
  durationMinutes: number;
  resume: ResumeUploadState;
  onRetryResume?: () => void;
  onCreateAnother: () => void;
}

/** The shown-once success panel: link, access code, invitation text and next steps. */
export function InviteResult({ result, programName, organizationName, durationMinutes, resume, onRetryResume, onCreateAnother }: InviteResultProps) {
  const [copied, setCopied] = useState({ link: false, code: false });
  const [messageCopied, setMessageCopied] = useState(false);
  const needsCode = Boolean(result.access_code);
  const unsaved = Boolean(result.invite_url) && (!copied.link || (needsCode && !copied.code));
  const guard = useLeaveGuard(unsaved, {
    title: "Leave without copying?",
    description: "The invite link and access code can't be shown again. You'd need to generate a new link from the intake page.",
  });
  const message = result.invite_url
    ? invitationMessage({
        candidateName: result.intake.candidate.full_name,
        programName,
        organizationName,
        inviteUrl: result.invite_url,
        durationMinutes,
        expiresAt: result.expires_at,
        hasAccessCode: needsCode,
      })
    : "";

  const copyMessage = async () => {
    if (!(await copyText(message))) return;
    setMessageCopied(true);
    setCopied((c) => ({ ...c, link: true }));
    setTimeout(() => setMessageCopied(false), 1800);
  };

  const createAnother = async () => {
    if (unsaved) {
      const ok = await confirm({ title: "Start another without copying?", description: "This link and access code can't be shown again.", confirmLabel: "Start another", cancelLabel: "Go back", tone: "danger" });
      if (!ok) return;
    }
    onCreateAnother();
  };

  return (
    <Card className="grid grid-cols-1 gap-5 p-5 sm:p-6" data-testid="intake-invite-result">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-success-soft text-success" aria-hidden="true">
          <Check className="size-4" />
        </span>
        <div className="grid grid-cols-1 min-w-0 gap-0.5">
          <h2 className="text-[17px] leading-6 font-semibold text-fg">Invitation ready</h2>
          <p className="text-sm text-fg-muted">
            {result.intake.candidate.full_name} is invited to the intake interview{programName ? ` for the ${programName}` : ""}.
            {result.expires_at ? ` The link works until ${formatDateTime(result.expires_at)}.` : ""}
          </p>
        </div>
      </div>

      {result.invite_url ? (
        <>
          <Callout tone="warning" icon={<AlertTriangle />} title="Copy the link now. It won't be shown again.">
            Only a fingerprint of the link is stored. If it's lost, generate a new one from the intake page. Send the access code through a different channel than the link.
          </Callout>
          <CopyValue label="Invite link" value={result.invite_url} valueTestId="intake-invite-link" copyTestId="intake-copy-link" onCopied={() => setCopied((c) => ({ ...c, link: true }))} />
          {result.access_code ? (
            <div className="grid grid-cols-1 gap-1.5">
              <CopyValue label="Access code" value={result.access_code} valueTestId="intake-access-code" copyTestId="intake-copy-code" emphasis onCopied={() => setCopied((c) => ({ ...c, code: true }))} />
              <p className="flex items-center gap-1.5 text-xs text-fg-muted">
                <KeyRound className="size-3.5" aria-hidden="true" />
                The candidate enters this with their email after opening the link.
              </p>
            </div>
          ) : (
            <p className="text-xs text-fg-muted">No access code: the candidate confirms their email address to start.</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" leftIcon={messageCopied ? <Check /> : <ClipboardCopy />} onClick={() => void copyMessage()} data-testid="intake-copy-message">
              {messageCopied ? "Message copied" : "Copy invitation message"}
            </Button>
            <Button variant="outline" leftIcon={<Mail />} asChild>
              <a href={mailtoHref(result.intake.candidate.email, invitationSubject({ programName }), message)} onClick={() => setCopied((c) => ({ ...c, link: true }))}>
                Open email draft
              </a>
            </Button>
          </div>
          <details className="group rounded-md border border-line-subtle bg-surface-subtle px-3.5 py-2.5 text-sm">
            <summary className="cursor-pointer text-[13px] font-medium text-fg-muted select-none hover:text-fg">Preview the invitation message</summary>
            <pre className="mt-2 font-sans text-[13px] leading-6 whitespace-pre-wrap text-fg">{message}</pre>
          </details>
        </>
      ) : (
        <Callout tone="info">Saved without a link. Send it from the drafts list when you're ready.</Callout>
      )}

      <ResumeStatus resume={resume} onRetry={onRetryResume} />

      <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle pt-4">
        <Button asChild leftIcon={<ExternalLink />}>
          <Link to={`/app/intake/${result.intake.id}`} onClick={() => guard.allowNext()}>
            Open intake
          </Link>
        </Button>
        <Button variant="outline" leftIcon={<Plus />} onClick={() => void createAnother()}>
          Create another
        </Button>
      </div>
    </Card>
  );
}

function ResumeStatus({ resume, onRetry }: { resume: ResumeUploadState; onRetry?: () => void }): ReactNode {
  if (resume.status === "idle") return null;
  if (resume.status === "uploading") {
    return (
      <div className="grid grid-cols-1 gap-1.5" aria-live="polite">
        <p className="text-[13px] text-fg-muted">Uploading {resume.name}…</p>
        <Progress value={resume.percent ?? undefined} aria-label="Résumé upload" />
      </div>
    );
  }
  if (resume.status === "done") {
    return (
      <p className="flex items-center gap-1.5 text-[13px] text-fg-muted" aria-live="polite">
        <Check className="size-4 text-success" aria-hidden="true" />
        {resume.name} uploaded. It's read and checked in the background.
      </p>
    );
  }
  return (
    <Callout
      tone="warning"
      title="The invitation is ready, but the résumé didn't upload"
      actions={
        onRetry ? (
          <Button size="sm" variant="outline" leftIcon={<RotateCcw />} onClick={onRetry}>
            Try the upload again
          </Button>
        ) : undefined
      }
    >
      {resume.message} The candidate can also upload it during their onboarding.
    </Callout>
  );
}
