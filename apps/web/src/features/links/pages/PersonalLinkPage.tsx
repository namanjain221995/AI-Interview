import { useQueryClient } from "@tanstack/react-query";
import { Link2Off, Mail } from "lucide-react";
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
import { useDocumentTitle } from "@/lib/hooks";

import { linkKeys, resetPortalCache, usePersonalLinkInfo, verifyPersonalLink } from "../api";
import { LinkStatePanel } from "../components/LinkStatePanel";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Candidate-facing message for a refused personal-link check (mirrors the invite page). */
export function describePersonalError(error: unknown): { message: string; closed?: boolean } {
  if (!isApiError(error)) return { message: errorMessage(error, "We couldn't check your details. Try again.") };
  const code = error.code.toLowerCase();
  if (error.status === 423 || code.includes("locked")) {
    if (error.details.permanent === true) {
      return { message: "This personal link is locked after too many attempts. Ask the hiring team to send you a new one." };
    }
    const seconds = error.retryAfterSeconds ?? (typeof error.details.retry_after_seconds === "number" ? error.details.retry_after_seconds : null);
    const minutes = seconds ? Math.max(1, Math.ceil(seconds / 60)) : null;
    return { message: `Too many attempts. For your security, this link is paused${minutes ? ` for about ${minutes} minute${minutes === 1 ? "" : "s"}` : " for a few minutes"}. Try again later.` };
  }
  if (error.status === 429) return { message: "Too many attempts in a short time. Wait a minute, then try again." };
  if (error.status === 410 || code === "registration_cancelled") return { message: "This registration was cancelled. Contact the hiring team if you think this is a mistake.", closed: true };
  if (error.status === 404) return { message: "This personal link isn't valid any more. Ask the hiring team for a new one." };
  if (error.status === 401 || error.status === 403 || code.includes("credential") || code.includes("email")) {
    return { message: "That email doesn't match this registration. Use the address you registered with." };
  }
  if (error.isNetworkError) return { message: "We can't reach the server. Check your connection and try again." };
  return { message: error.message || "We couldn't check your details. Try again." };
}

/** /r/:token: the personal "continue later" link issued at registration. */
export default function PersonalLinkPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const info = usePersonalLinkInfo(token);
  const data = info.data;
  useDocumentTitle(data ? `Continue with ${data.organization_name}` : "Continue your interview");
  useCandidateChrome({ orgName: data?.organization_name ?? null, subtitle: data?.job_title ?? null, hideStepper: true, width: "narrow" });

  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const trimmed = email.trim();
    if (!EMAIL.test(trimmed)) {
      setFieldError("Enter the email address you registered with.");
      return;
    }
    setFieldError(null);
    setSubmitting(true);
    try {
      const result = await verifyPersonalLink(token, trimmed);
      resetPortalCache(queryClient);
      if (result.progress) queryClient.setQueryData(linkKeys.progress, result.progress);
      navigate(`/candidate/${result.interview_id}/${result.next_step || "welcome"}`, { replace: true });
    } catch (caught) {
      const described = describePersonalError(caught);
      setError(described.message);
      if (described.closed) setClosed(true);
      setSubmitting(false);
    }
  };

  let state = "loading";
  let content;
  if (info.isPending) {
    content = (
      <div className="grid gap-6" aria-busy="true" aria-label="Loading">
        <Skeleton className="h-9 w-3/4" />
        <SkeletonText lines={2} />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  } else if (info.isError) {
    const notFound = isApiError(info.error) && (info.error.status === 404 || info.error.status === 400);
    state = notFound ? "not_found" : "error";
    content = notFound ? (
      <EmptyState
        bordered
        icon={<Link2Off />}
        title="This personal link isn't valid"
        description="Check you opened the whole link you saved. If it still doesn't work, ask the hiring team to send you a new one."
      />
    ) : (
      <EmptyState
        bordered
        title="We couldn't open your link"
        description={errorMessage(info.error, "Check your connection and reload the page.")}
        actions={<Button onClick={() => void info.refetch()}>Try again</Button>}
      />
    );
  } else if (data && (data.state === "cancelled" || closed)) {
    state = "cancelled";
    content = (
      <div className="grid gap-4">
        <EmptyState
          bordered
          icon={<Link2Off />}
          title="This registration was cancelled"
          description={`Contact ${data.organization_name} if you think this is a mistake.`}
        />
        {error ? (
          <p className="sr-only" data-testid="personal-link-error">
            {error}
          </p>
        ) : null}
      </div>
    );
  } else if (data) {
    state = "active";
    content = (
      <div>
        <h1 className="text-title text-fg sm:text-[1.75rem] sm:leading-9">Continue your interview</h1>
        <p className="mt-2 text-[15px] leading-6 text-fg-muted">
          {data.job_title ? (
            <>
              <span className="font-medium text-fg">{data.job_title}</span> with {data.organization_name}
              {data.link_name ? `, ${data.link_name}` : ""}.
            </>
          ) : (
            `Your interview with ${data.organization_name}.`
          )}{" "}
          Confirm it's you to pick up where you left off, or to start your next round.
        </p>
        <form onSubmit={onSubmit} noValidate className="mt-8 grid gap-5">
          <div className="grid gap-1.5">
            <Label htmlFor="personal-email">Email address</Label>
            <Input
              id="personal-email"
              data-testid="personal-link-email-input"
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
              aria-describedby="personal-email-help"
              placeholder="you@example.com"
            />
            <p id="personal-email-help" className={fieldError ? "text-xs font-medium text-danger-fg" : "text-xs text-fg-muted"}>
              {fieldError ?? "Use the address you registered with."}
            </p>
          </div>
          <div aria-live="polite" className="empty:hidden">
            {error ? (
              <Callout tone="danger" data-testid="personal-link-error" title="We couldn't confirm your details">
                {error}
              </Callout>
            ) : null}
          </div>
          <Button type="submit" size="lg" fullWidth loading={submitting} loadingText="Checking" data-testid="personal-link-verify">
            Continue
          </Button>
        </form>
      </div>
    );
  } else {
    content = <LinkStatePanel state="not_found" />;
  }

  return (
    <div data-testid="personal-link-page" data-state={state}>
      {content}
    </div>
  );
}
