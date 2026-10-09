import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarX2, CircleCheckBig, Link2Off, LockKeyhole, Mail } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";

import { useCandidateChrome } from "@/components/layout/CandidateShell";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { errorMessage, isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";

import { getInvite, getPortalInterview, getPortalMe, verifyInvite, type InviteView } from "../api";
import { Fact } from "../components/StepLayout";
import { readLocal, resumeStep } from "../journey";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function describeVerifyError(error: unknown): string {
  if (!isApiError(error)) return errorMessage(error, "We couldn't check your details. Try again.");
  const code = error.code.toLowerCase();
  if (error.status === 423 || code.includes("locked")) {
    const minutes = error.retryAfterSeconds ? Math.max(1, Math.ceil(error.retryAfterSeconds / 60)) : null;
    return `Too many attempts. For your security, this invitation is paused${minutes ? ` for about ${minutes} minute${minutes === 1 ? "" : "s"}` : " for a few minutes"}. Try again later.`;
  }
  if (error.status === 429) return "Too many attempts in a short time. Wait a minute, then try again.";
  if (code === "access_code_required") return "This invitation needs an access code. Enter the code from your invitation.";
  if (code.includes("code")) return "That access code isn't right. Check the code in your invitation and enter it exactly as shown.";
  if (code.includes("expired") || error.status === 410) return "This invitation has expired. Ask the person who invited you for a new link.";
  if (code.includes("cancel")) return "This invitation was cancelled. Contact the person who invited you if you think this is a mistake.";
  if (error.status === 401 || error.status === 403 || code.includes("email") || code.includes("credential")) {
    return "That email doesn't match this invitation. Use the address the invitation was sent to.";
  }
  if (error.isNetworkError) return "We can't reach the server. Check your connection and try again.";
  return error.message || "We couldn't check your details. Try again.";
}

function InviteClosed({ invite }: { invite: InviteView }) {
  if (invite.status === "completed") {
    return (
      <EmptyState
        bordered
        icon={<CircleCheckBig />}
        title="You've already completed this interview"
        description={`Thank you. ${invite.organizationName} has your interview and will be in touch about next steps.`}
      />
    );
  }
  if (invite.status === "cancelled") {
    return (
      <EmptyState
        bordered
        icon={<Link2Off />}
        title="This invitation was cancelled"
        description={`Contact ${invite.organizationName} if you think this is a mistake.`}
      />
    );
  }
  return (
    <EmptyState
      bordered
      icon={<CalendarX2 />}
      title="This invitation has expired"
      description={`Invitation links are valid for a limited time. Ask ${invite.organizationName} to send you a new one.`}
    />
  );
}

export default function InviteEntryPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const invite = useQuery({ queryKey: ["candidate-access", token], queryFn: () => getInvite(token), retry: false, meta: { errorToast: false } });
  const data = invite.data;
  useDocumentTitle(data ? `Interview with ${data.organizationName}` : "Your interview");
  useCandidateChrome({ orgName: data?.organizationName ?? null, subtitle: data?.jobTitle ?? null, hideStepper: true, width: "narrow" });

  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The server may ask for a code even if the public invite info didn't (422 access_code_required).
  const [codeAsked, setCodeAsked] = useState(false);
  const needsCode = Boolean(data?.requiresAccessCode) || codeAsked;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!data) return;
    setError(null);
    const trimmed = email.trim();
    if (!EMAIL.test(trimmed)) {
      setFieldError("Enter the email address your invitation was sent to.");
      return;
    }
    if (needsCode && !code.trim()) {
      setFieldError(null);
      setError("Enter the access code from your invitation.");
      return;
    }
    setFieldError(null);
    setSubmitting(true);
    try {
      const result = await verifyInvite(token, { email: trimmed, ...(needsCode ? { access_code: code.trim() } : {}) });
      const interviewId = result.interviewId ?? (await getPortalMe()).interviewId;
      if (!interviewId) throw new Error("We couldn't open your interview. Try the link again.");
      let target = result.nextStep;
      try {
        const interview = await getPortalInterview(interviewId);
        queryClient.setQueryData(queryKeys.portal.interview(interviewId), interview);
        target = resumeStep(interview, readLocal<string[]>(interviewId, "visited", []));
      } catch {
        target = target ?? "welcome";
      }
      navigate(`/candidate/${interviewId}/${target ?? "welcome"}`, { replace: true });
    } catch (caught) {
      if (isApiError(caught) && caught.code === "access_code_required") setCodeAsked(true);
      setError(describeVerifyError(caught));
      setSubmitting(false);
    }
  };

  if (invite.isPending) {
    return (
      <div className="grid gap-6" aria-busy="true" aria-label="Loading your invitation">
        <Skeleton className="h-9 w-3/4" />
        <SkeletonText lines={2} />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (invite.isError) {
    const notFound = isApiError(invite.error) && (invite.error.status === 404 || invite.error.status === 400);
    return (
      <EmptyState
        bordered
        icon={<Link2Off />}
        title={notFound ? "This invitation link isn't valid" : "We couldn't open your invitation"}
        description={
          notFound
            ? "Check that you opened the full link from your invitation email. If it still doesn't work, ask the person who invited you for a new one."
            : errorMessage(invite.error, "Check your connection and reload the page.")
        }
        actions={notFound ? undefined : <Button onClick={() => void invite.refetch()}>Try again</Button>}
      />
    );
  }

  if (!data) return null;
  if (["expired", "cancelled", "completed"].includes(data.status)) return <InviteClosed invite={data} />;

  const greeting = data.candidateFirstName ? `Hi ${data.candidateFirstName}, welcome` : "Welcome to your interview";

  return (
    <div>
      <h1 className="text-title text-fg sm:text-[1.75rem] sm:leading-9">{greeting}</h1>
      <p className="mt-2 text-[15px] leading-6 text-fg-muted">
        {data.organizationName} has invited you to an interview for <span className="font-medium text-fg">{data.jobTitle}</span>. First, confirm it's you.
      </p>

      <dl className="mt-8 grid grid-cols-2 gap-x-6 gap-y-5 border-y border-line py-5">
        <Fact label="Role">{data.jobTitle}</Fact>
        {data.roundName ? <Fact label="Interview">{data.roundName}</Fact> : null}
        {data.durationMinutes ? <Fact label="Length">About {data.durationMinutes} minutes</Fact> : null}
        {data.expiresAt ? <Fact label="Link valid until">{formatDateTime(data.expiresAt)}</Fact> : null}
      </dl>

      <form onSubmit={onSubmit} noValidate className="mt-8 grid gap-5">
        <div className="grid gap-1.5">
          <Label htmlFor="invite-email">Email address</Label>
          <Input
            id="invite-email"
            data-testid="invite-email-input"
            type="email"
            size="lg"
            autoComplete="email"
            inputMode="email"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            leading={<Mail />}
            aria-invalid={fieldError ? true : undefined}
            aria-describedby="invite-email-help"
            placeholder="you@example.com"
          />
          <p id="invite-email-help" className={fieldError ? "text-xs font-medium text-danger-fg" : "text-xs text-fg-muted"}>
            {fieldError ?? "Use the address your invitation was sent to."}
          </p>
        </div>

        {needsCode ? (
          <div className="grid gap-1.5">
            <Label htmlFor="invite-code">Access code</Label>
            <Input
              id="invite-code"
              data-testid="invite-access-code-input"
              size="lg"
              autoComplete="one-time-code"
              autoCapitalize="characters"
              spellCheck={false}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              leading={<LockKeyhole />}
              className="font-mono tracking-[0.12em]"
            />
            <p className="text-xs text-fg-muted">It's in your invitation, sent separately from the link.</p>
          </div>
        ) : null}

        <div aria-live="polite" className="empty:hidden">
          {error ? (
            <Callout tone="danger" data-testid="invite-error" title="We couldn't confirm your details">
              {error}
            </Callout>
          ) : null}
        </div>

        <Button type="submit" size="lg" fullWidth loading={submitting} loadingText="Checking" data-testid="invite-verify-button">
          Continue
        </Button>
      </form>
    </div>
  );
}
