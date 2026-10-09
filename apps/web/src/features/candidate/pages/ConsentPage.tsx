import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Markdown } from "@/components/ui/Markdown";
import { Skeleton } from "@/components/ui/Skeleton";
import { errorMessage, isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";

import { consentBody, getDisclosure, postConsent, type DisclosureView } from "../api";
import { StepFooter, StepHeader } from "../components/StepLayout";
import { JourneyBoundary, JourneyError, useJourney, type JourneyContext } from "../hooks";
import { writeLocal } from "../journey";

export default function ConsentPage() {
  useDocumentTitle("Privacy and consent");
  const { query, ctx } = useJourney("consent");
  return (
    <div>
      {/* The disclosure's own intro is the lede (it is part of the text the policy hash covers). */}
      <StepHeader title="Your privacy and consent" />
      <div className="mt-2">
        <JourneyBoundary query={query}>{ctx ? <Consent ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

function Statement({ title, children }: { title: string; children: string | null }) {
  if (!children) return null;
  return (
    <div className="grid content-start gap-1">
      <dt className="text-sm font-medium text-fg">{title}</dt>
      <dd className="text-sm leading-6 text-fg-muted">{children}</dd>
    </div>
  );
}

function Consent({ ctx }: { ctx: JourneyContext }) {
  const queryClient = useQueryClient();
  const disclosure = useQuery({
    queryKey: queryKeys.portal.disclosure(ctx.interviewId),
    queryFn: () => getDisclosure(ctx.interviewId),
    meta: { errorToast: false },
  });
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const alreadyConsented = ctx.interview.stepsCompleted.includes("consent") || Boolean(disclosure.data?.consented);

  const accept = useMutation({
    mutationFn: (d: DisclosureView) => postConsent(ctx.interviewId, consentBody(d, checked)),
    meta: { silentError: true },
    onSuccess: async (_data, d) => {
      writeLocal(ctx.interviewId, "consent", consentBody(d, checked).items);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(ctx.interviewId) });
      ctx.next();
    },
    onError: async (caught) => {
      if (isApiError(caught) && (caught.status === 409 || caught.code.includes("hash") || caught.code.includes("policy"))) {
        setError("The interview policy changed while this page was open. Review the updated details below and accept again.");
        setChecked({});
        await disclosure.refetch();
        return;
      }
      setError(errorMessage(caught, "We couldn't save your consent. Try again."));
    },
  });

  const items = disclosure.data?.items ?? [];
  const required = useMemo(() => items.filter((i) => i.required), [items]);
  const allRequired = required.every((i) => checked[i.key]);
  const remaining = required.filter((i) => !checked[i.key]).length;

  if (disclosure.isPending) {
    return (
      <div className="grid gap-4" aria-busy="true" aria-label="Loading the consent details">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (disclosure.isError) return <JourneyError error={disclosure.error} onRetry={() => void disclosure.refetch()} />;
  const d = disclosure.data;

  return (
    <div>
      <p className="max-w-2xl text-[15px] leading-6 text-fg-muted">
        {d.intro || "Here is exactly what is recorded during your interview, why, and for how long. Please read each item and confirm."}
      </p>

      {alreadyConsented ? (
        <Callout tone="success" className="mt-6" title="You've already agreed to this policy">
          You can review it again here, or continue to the next step.
        </Callout>
      ) : null}

      <fieldset className="mt-8">
        <legend className="text-heading text-fg">What you're agreeing to</legend>
        <p className="mt-1 text-sm text-fg-muted">Each item needs your confirmation{required.length < items.length ? " unless it's marked optional" : ""}.</p>
        <ul className="mt-5 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
          {items.map((item) => (
            <li key={item.key} className="px-4 py-4 sm:px-5">
              <Checkbox
                id={`consent-${item.key}`}
                data-testid={`consent-item-${item.key}`}
                checked={Boolean(checked[item.key])}
                onCheckedChange={(value) => setChecked((prev) => ({ ...prev, [item.key]: value === true }))}
                label={
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    {item.title}
                    {!item.required ? <span className="text-xs font-normal text-fg-muted">Optional</span> : null}
                  </span>
                }
                description={
                  item.description ? (
                    // Legal text: larger than a form hint, with a comfortable measure.
                    <span className="block max-w-[68ch] pt-0.5 text-[13px] leading-5 sm:text-sm sm:leading-[1.375rem]">{item.description}</span>
                  ) : undefined
                }
              />
            </li>
          ))}
        </ul>
      </fieldset>

      {d.textMarkdown ? (
        // The exact text the policy hash covers, for anyone who wants every word.
        <details className="group mt-6 rounded-lg border border-line bg-surface">
          <summary className="cursor-pointer list-none px-5 py-3.5 text-sm font-medium text-fg hover:bg-surface-subtle">
            Read the full policy text
          </summary>
          <div className="border-t border-line px-5 py-4">
            <Markdown size="sm">{d.textMarkdown}</Markdown>
          </div>
        </details>
      ) : null}

      {d.purpose || d.retentionSummary || d.humanReviewStatement || d.limitationsStatement ? (
        <section className="mt-10" aria-labelledby="data-heading">
          <h2 id="data-heading" className="text-heading text-fg">
            How your information is used
          </h2>
          <dl className="mt-4 grid gap-5 sm:grid-cols-2">
            <Statement title="Purpose">{d.purpose}</Statement>
            <Statement title="How long it's kept">{d.retentionSummary}</Statement>
            <Statement title="Who decides">{d.humanReviewStatement}</Statement>
            <Statement title="Limits of monitoring">{d.limitationsStatement}</Statement>
          </dl>
        </section>
      ) : null}

      <div aria-live="polite" className="mt-6 empty:hidden">
        {error ? <Callout tone="danger">{error}</Callout> : null}
      </div>

      <StepFooter
        onBack={ctx.back}
        note={`If you'd rather not agree, you can close this page and contact ${ctx.interview.organizationName || "the hiring team"} to ask about alternatives. Policy version ${d.policyVersion || "current"}.`}
      >
        {!allRequired ? (
          <span className="text-[13px] text-fg-muted sm:mr-2" aria-live="polite">
            {remaining} {remaining === 1 ? "item" : "items"} left to confirm
          </span>
        ) : null}
        <Button
          size="lg"
          disabled={!allRequired || !d.policyHash}
          loading={accept.isPending}
          loadingText="Saving"
          onClick={() => {
            setError(null);
            accept.mutate(d);
          }}
          data-testid="consent-accept-button"
        >
          I agree and continue
        </Button>
      </StepFooter>
    </div>
  );
}
