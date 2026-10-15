import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  Bot,
  Camera,
  Clock,
  FileText,
  Info,
  Mic,
  MonitorUp,
  ScanEye,
  ScanFace,
  ShieldCheck,
  Video,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Markdown } from "@/components/ui/Markdown";
import { Skeleton } from "@/components/ui/Skeleton";
import { Switch } from "@/components/ui/Switch";
import { errorMessage, isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";

import { consentBody, getDisclosure, postConsent, type DisclosureItemView, type DisclosureView } from "../api";
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

const ITEM_ICONS: Record<string, LucideIcon> = {
  microphone: Mic,
  camera: Camera,
  transcript: FileText,
  integrity_monitoring: ShieldCheck,
  camera_frames: ScanEye,
  face_check: ScanFace,
  screen_share: MonitorUp,
  audio_recording: AudioLines,
  video_recording: Video,
  ai_assessment: Bot,
  retention: Clock,
};

/** One consent item: icon, title and one-line summary; the full text (what the policy hash covers) one click away. */
function ItemRow({ item, control }: { item: DisclosureItemView; control?: ReactNode }) {
  const Icon = ITEM_ICONS[item.key] ?? Info;
  const summary = item.summary || item.description;
  const details = item.summary && item.description && item.description !== item.summary ? item.description : null;
  return (
    <li className="flex gap-3 px-4 py-4 sm:gap-4 sm:px-5" data-testid={`consent-item-${item.key}`}>
      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full bg-surface-subtle text-fg-muted" aria-hidden="true">
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm leading-5 font-medium text-fg">
            {item.title}
            {!item.required ? <span className="ml-2 text-xs font-normal text-fg-muted">Optional</span> : null}
          </p>
          {control}
        </div>
        <p className="mt-1 max-w-[68ch] text-[13px] leading-5 text-fg-muted sm:text-sm sm:leading-[1.375rem]">{summary}</p>
        {details ? (
          <details className="group mt-1.5">
            <summary className="cursor-pointer list-none text-[13px] font-medium text-primary hover:underline sm:text-sm">
              <span className="group-open:hidden">Details</span>
              <span className="hidden group-open:inline">Hide details</span>
            </summary>
            {/* Legal text: larger than a form hint, with a comfortable measure. */}
            <p className="mt-1.5 max-w-[68ch] text-[13px] leading-5 text-fg-muted sm:text-sm sm:leading-[1.375rem]">{details}</p>
          </details>
        ) : null}
      </div>
    </li>
  );
}

function Consent({ ctx }: { ctx: JourneyContext }) {
  const queryClient = useQueryClient();
  const disclosure = useQuery({
    queryKey: queryKeys.portal.disclosure(ctx.interviewId),
    queryFn: () => getDisclosure(ctx.interviewId),
    meta: { errorToast: false },
  });
  // One confirmation covers every required item; each optional item has its own switch, off by default (an
  // optional purpose is never bundled with the required ones).
  const [agreed, setAgreed] = useState(false);
  const [optional, setOptional] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const alreadyConsented = ctx.interview.stepsCompleted.includes("consent") || Boolean(disclosure.data?.consented);

  const checkedFor = (d: DisclosureView): Record<string, boolean> =>
    Object.fromEntries(d.items.map((i) => [i.key, i.required ? agreed : i.enabled && Boolean(optional[i.key])]));

  const accept = useMutation({
    mutationFn: (d: DisclosureView) => postConsent(ctx.interviewId, consentBody(d, checkedFor(d))),
    meta: { silentError: true },
    onSuccess: async (_data, d) => {
      writeLocal(ctx.interviewId, "consent", consentBody(d, checkedFor(d)).items);
      await queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(ctx.interviewId) });
      ctx.next();
    },
    onError: async (caught) => {
      if (isApiError(caught) && (caught.status === 409 || caught.code.includes("hash") || caught.code.includes("policy"))) {
        setError("The interview policy changed while this page was open. Review the updated details below and accept again.");
        setAgreed(false);
        setOptional({});
        await disclosure.refetch();
        return;
      }
      setError(errorMessage(caught, "We couldn't save your consent. Try again."));
    },
  });

  const items = disclosure.data?.items ?? [];
  const required = useMemo(() => items.filter((i) => i.required), [items]);
  // Items not used by this interview never reach the page (normalizeDisclosure keeps enabled items only).
  const choices = useMemo(() => items.filter((i) => !i.required), [items]);
  const ready = required.length === 0 || agreed;

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

      <section className="mt-8" aria-labelledby="consent-items-heading">
        <h2 id="consent-items-heading" className="text-heading text-fg">
          What this interview uses
        </h2>
        <p className="mt-1 text-sm text-fg-muted">Each item is summarised in one line; open Details for the full text.</p>
        {required.length ? (
          <ul className="mt-5 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface" aria-label="Required items">
            {required.map((item) => (
              <ItemRow key={item.key} item={item} />
            ))}
          </ul>
        ) : null}
        {choices.length ? (
          <>
            <h3 className="mt-6 text-sm font-medium text-fg">Your choice</h3>
            <p className="mt-0.5 text-[13px] text-fg-muted">Off unless you turn it on; the interview works either way.</p>
            <ul className="mt-3 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface" aria-label="Optional items">
              {choices.map((item) => (
                <ItemRow
                  key={item.key}
                  item={item}
                  control={
                    <Switch
                      size="sm"
                      aria-label={`Allow: ${item.title}`}
                      data-testid={`consent-optional-${item.key}`}
                      checked={Boolean(optional[item.key])}
                      onCheckedChange={(value) => setOptional((prev) => ({ ...prev, [item.key]: value }))}
                    />
                  }
                />
              ))}
            </ul>
          </>
        ) : null}
      </section>

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

      {required.length ? (
        <div className="mt-8 rounded-lg border border-line bg-surface px-4 py-4 sm:px-5">
          <Checkbox
            id="consent-agree-all"
            data-testid="consent-agree-all"
            checked={agreed}
            onCheckedChange={(value) => setAgreed(value === true)}
            label={`I've read the ${required.length === 1 ? "item" : `${required.length} items`} above and agree to them`}
            description={choices.length ? "Optional items stay as you set them." : undefined}
          />
        </div>
      ) : null}

      <div aria-live="polite" className="mt-6 empty:hidden">
        {error ? <Callout tone="danger">{error}</Callout> : null}
      </div>

      <StepFooter
        onBack={ctx.back}
        note={`If you'd rather not agree, you can close this page and contact ${ctx.interview.organizationName || "the hiring team"} to ask about alternatives. Policy version ${d.policyVersion || "current"}.`}
      >
        {!ready ? (
          <span className="text-[13px] text-fg-muted sm:mr-2" aria-live="polite">
            Tick the box above to continue
          </span>
        ) : null}
        <Button
          size="lg"
          disabled={!ready || !d.policyHash}
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
