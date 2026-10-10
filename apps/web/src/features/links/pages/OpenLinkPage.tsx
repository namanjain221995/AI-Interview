import { useQueryClient } from "@tanstack/react-query";
import { Globe2, MapPin, Timer, UserRound } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router";

import { useCandidateChrome } from "@/components/layout/CandidateShell";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { Markdown } from "@/components/ui/Markdown";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { errorMessage, isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";

import { fetchPowChallenge, registerForLink, resetPortalCache, useLinkInfo } from "../api";
import { solveInBackground, type PowRun } from "../pow";
import { LinkStatePanel, RegistrationOutcome, type LinkPanelState, type RegistrationOutcomePanel } from "../components/LinkStatePanel";
import { PersonalLinkNotice } from "../components/PersonalLinkNotice";
import { RegistrationForm } from "../components/RegistrationForm";
import { LINK_STATES, type LinkInfo, type LinkState, type RegisterRequest, type RegisterResponse } from "../types";

type Outcome = { kind: "panel"; panel: RegistrationOutcomePanel; minutes?: number | null; round?: number | null } | { kind: "state"; state: LinkPanelState };

/** Maps a refused registration onto what the candidate sees (OPEN_LINKS.md section 7.1). */
export function describeRegisterError(error: unknown): { outcome?: Outcome; fields?: Record<string, string>; message?: string; askCode?: boolean } {
  if (!isApiError(error)) return { message: errorMessage(error, "We couldn't register you. Check your connection and try again.") };
  const code = error.code;
  const minutes = error.retryAfterSeconds ? Math.max(1, Math.ceil(error.retryAfterSeconds / 60)) : null;
  if (code === "already_completed") return { outcome: { kind: "panel", panel: "already_completed" } };
  if (code === "already_registered") {
    const state = typeof error.details.state === "string" ? error.details.state : null;
    const round = typeof error.details.round === "number" ? error.details.round : null;
    return { outcome: { kind: "panel", panel: state === "awaiting_review" ? "awaiting_review" : "already_registered", round } };
  }
  if (code === "already_invited") return { outcome: { kind: "panel", panel: "already_invited" } };
  if (code === "registration_blocked" || code === "registration_unavailable" || code === "registration_rejected") return { outcome: { kind: "panel", panel: "unavailable" } };
  if (code === "link_unavailable") {
    const state = typeof error.details.state === "string" && (LINK_STATES as readonly string[]).includes(error.details.state) ? (error.details.state as LinkState) : "closed";
    return { outcome: { kind: "state", state: state === "open" ? "closed" : state } };
  }
  if (code === "link_not_found" || error.status === 404) return { outcome: { kind: "state", state: "not_found" } };
  if (error.status === 423 || code === "access_code_locked") return { outcome: { kind: "panel", panel: "wait", minutes } };
  if (error.status === 429) return { message: `Too many attempts in a short time. Wait ${minutes ? `about ${minutes} minute${minutes === 1 ? "" : "s"}` : "a minute"}, then try again.` };
  if (code === "access_code_required") return { askCode: true, fields: { access_code: "Enter the access code you were given with the link." } };
  if (code === "invalid_access_code") return { fields: { access_code: "That access code isn't right. Check it and enter it exactly as shown." } };
  if (code === "privacy_ack_required") return { fields: { privacy_ack: "Tick this box to confirm you've read how your data is used." } };
  if (code === "origin_not_allowed") return { message: "Open the registration link directly in your browser, then try again." };
  if (code === "pow_required" || code === "pow_invalid") return { message: "Your browser couldn't finish a quick security check. Reload the page and try again." };
  if (code === "validation_failed" || error.status === 422) {
    const fields = error.fieldErrors();
    return Object.keys(fields).length ? { fields } : { message: error.message || "Some answers need attention." };
  }
  if (error.isNetworkError) return { message: "We can't reach the server. Check your connection and try again." };
  return { message: error.message || "We couldn't register you. Try again." };
}

function LinkHeader({ info }: { info: LinkInfo }) {
  const multi = info.rounds.length > 1;
  return (
    <header>
      {info.link_name ? <p className="text-sm font-medium text-fg-muted">{info.link_name}</p> : null}
      <h1 className="mt-1 text-title text-fg sm:text-[1.75rem] sm:leading-9">{info.job_title}</h1>
      <p className="mt-1 text-[15px] leading-6 text-fg-muted">Interview with {info.organization_name}</p>
      {info.description ? (
        <Markdown size="sm" className="mt-4 text-fg-muted">
          {info.description}
        </Markdown>
      ) : null}

      <dl className="mt-6 grid grid-cols-1 gap-x-6 gap-y-4 border-y border-line py-5 text-sm sm:grid-cols-2">
        <div className="grid gap-0.5 sm:col-span-2">
          <dt className="text-[13px] text-fg-muted">{multi ? `${info.rounds.length} rounds, one after another` : "Interview"}</dt>
          <dd>
            {multi ? (
              <ol className="mt-1 grid grid-cols-1 gap-1.5">
                {info.rounds.map((round, i) => (
                  <li key={`${round.sequence}-${round.name}`} className="flex items-baseline gap-2.5">
                    <span className="w-4 shrink-0 text-[13px] font-semibold text-fg-subtle tabular-nums">{i + 1}</span>
                    <span className="min-w-0 font-medium text-fg">{round.name}</span>
                    <span className="shrink-0 text-fg-muted">about {round.duration_minutes} min</span>
                  </li>
                ))}
              </ol>
            ) : info.rounds[0] ? (
              <span className="font-medium text-fg">
                {info.rounds[0].name}, about {info.rounds[0].duration_minutes} minutes
              </span>
            ) : null}
          </dd>
        </div>
        <div className="flex items-start gap-2">
          <Globe2 className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          <div className="grid gap-0.5">
            <dt className="text-[13px] text-fg-muted">Where</dt>
            <dd className="font-medium text-fg">Online, in your browser</dd>
          </div>
        </div>
        {info.interviewer_name ? (
          <div className="flex items-start gap-2">
            <UserRound className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
            <div className="grid gap-0.5">
              <dt className="text-[13px] text-fg-muted">Interviewer</dt>
              <dd className="font-medium text-fg">{info.interviewer_name}, an AI interviewer</dd>
            </div>
          </div>
        ) : null}
        {info.job_location ? (
          <div className="flex items-start gap-2">
            <MapPin className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
            <div className="grid gap-0.5">
              <dt className="text-[13px] text-fg-muted">Job location</dt>
              <dd className="font-medium text-fg">{info.job_location}</dd>
            </div>
          </div>
        ) : null}
        {info.closes_at && info.state === "open" ? (
          <div className="flex items-start gap-2">
            <Timer className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
            <div className="grid gap-0.5">
              <dt className="text-[13px] text-fg-muted">Registration closes</dt>
              <dd className="font-medium text-fg">{formatDateTime(info.closes_at)}</dd>
            </div>
          </div>
        ) : null}
      </dl>
    </header>
  );
}

/**
 * Proof-of-work for this link (S15): a challenge is fetched and solved in the background as soon as the form
 * is shown; `take()` hands out the solution (each is single use) and starts preparing the next one.
 */
function useProofOfWork(token: string, enabled: boolean) {
  const current = useRef<{ solution: Promise<string | null>; cancel: () => void } | null>(null);
  const prepare = useCallback(() => {
    current.current?.cancel();
    let run: PowRun | null = null;
    let cancelled = false;
    const solution = fetchPowChallenge(token)
      .then((challenge) => {
        if (cancelled) return null;
        run = solveInBackground(challenge);
        return run.promise;
      })
      .catch(() => null);
    current.current = {
      solution,
      cancel: () => {
        cancelled = true;
        run?.cancel();
      },
    };
  }, [token]);
  useEffect(() => {
    if (enabled) prepare();
    return () => current.current?.cancel();
  }, [enabled, prepare]);
  return useCallback(async (): Promise<string | null> => {
    if (!current.current) prepare();
    const solution = current.current!.solution;
    current.current = null;
    const value = await solution;
    prepare(); // the next attempt needs a fresh one
    return value;
  }, [prepare]);
}

/** /j/:token: public registration for an open interview link. */
export default function OpenLinkPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const info = useLinkInfo(token);
  const data = info.data;
  useDocumentTitle(data ? `${data.job_title} at ${data.organization_name}` : "Register for an interview");
  useCandidateChrome({ orgName: data?.organization_name ?? null, subtitle: data?.job_title ?? null, hideStepper: true, width: "narrow" });

  const [submitting, setSubmitting] = useState(false);
  const [registered, setRegistered] = useState<RegisterResponse | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string> | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [askCode, setAskCode] = useState(false);

  const enter = (result: RegisterResponse) => navigate(`/candidate/${result.interview_id}/${result.next_step || "welcome"}`, { replace: true });
  const takeProof = useProofOfWork(token, info.isSuccess);

  /** One registration; a rejected or expired proof of work is retried once with a fresh one. */
  const registerWithProof = async (body: RegisterRequest): Promise<RegisterResponse> => {
    try {
      return await registerForLink(token, { ...body, pow: (await takeProof()) ?? undefined });
    } catch (error) {
      if (!(isApiError(error) && (error.code === "pow_invalid" || error.code === "pow_required"))) throw error;
      return registerForLink(token, { ...body, pow: (await takeProof()) ?? undefined });
    }
  };

  const submit = async (body: RegisterRequest) => {
    setSubmitting(true);
    setFormError(null);
    setFieldErrors(undefined);
    try {
      const result = await registerWithProof(body);
      resetPortalCache(queryClient);
      if (result.outcome === "resumed" || !result.personal_url) {
        enter(result);
        return;
      }
      setRegistered(result);
      window.scrollTo({ top: 0 });
    } catch (error) {
      const described = describeRegisterError(error);
      if (described.askCode) setAskCode(true);
      if (described.outcome) setOutcome(described.outcome);
      if (described.fields) setFieldErrors(described.fields);
      if (described.message) setFormError(described.message);
    } finally {
      setSubmitting(false);
    }
  };

  let state: string = "loading";
  let body: ReactNode;
  if (info.isPending) {
    body = (
      <div className="grid gap-6" aria-busy="true" aria-label="Loading the registration form">
        <Skeleton className="h-9 w-3/4" />
        <SkeletonText lines={2} />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  } else if (info.isError) {
    const notFound = isApiError(info.error) && (info.error.status === 404 || info.error.status === 400);
    state = notFound ? "not_found" : "error";
    body = notFound ? (
      <LinkStatePanel state="not_found" />
    ) : (
      <EmptyState
        bordered
        title="We couldn't open this link"
        description={errorMessage(info.error, "Check your connection and reload the page.")}
        actions={<Button onClick={() => void info.refetch()}>Try again</Button>}
      />
    );
  } else if (registered?.personal_url && data) {
    state = "registered";
    body = <PersonalLinkNotice url={registered.personal_url} organizationName={data.organization_name} onContinue={() => enter(registered)} />;
  } else if (data) {
    state = outcome?.kind === "state" ? outcome.state : data.state;
    const panel =
      outcome?.kind === "panel" ? (
        <div data-testid="open-link-error" data-outcome={outcome.panel}>
          <RegistrationOutcome kind={outcome.panel} minutes={outcome.minutes} round={outcome.round} />
        </div>
      ) : outcome?.kind === "state" ? (
        <div data-testid="open-link-error" data-outcome={outcome.state}>
          <LinkStatePanel state={outcome.state} opensAt={data.opens_at} />
        </div>
      ) : data.state !== "open" || !data.form ? (
        <LinkStatePanel state={data.state === "open" ? "closed" : data.state} opensAt={data.opens_at} />
      ) : null;
    body = (
      <div className="grid grid-cols-1 gap-8">
        <LinkHeader info={data} />
        {panel ?? (
          <section aria-labelledby="register-heading" className="grid grid-cols-1 gap-5">
            <div>
              <h2 id="register-heading" className="text-heading text-fg">
                Register
              </h2>
              <p className="mt-1 text-sm leading-6 text-fg-muted">
                {data.require_resume ? "After this form you'll upload your résumé, check your camera and microphone, then start the interview." : "After this form you'll check your camera and microphone, then start the interview."} You can register only once.
              </p>
            </div>
            <RegistrationForm
              fields={data.form!.fields}
              dataUse={data.data_use}
              organizationName={data.organization_name}
              requiresAccessCode={data.requires_access_code || askCode}
              submitting={submitting}
              serverErrors={fieldErrors}
              formError={
                formError ? (
                  <Callout tone="danger" title="We couldn't register you" data-testid="open-link-error">
                    {formError}
                  </Callout>
                ) : null
              }
              onSubmit={(request) => void submit(request)}
            />
          </section>
        )}
      </div>
    );
  }

  return (
    <div data-testid="open-link-page" data-state={state}>
      {body}
    </div>
  );
}
