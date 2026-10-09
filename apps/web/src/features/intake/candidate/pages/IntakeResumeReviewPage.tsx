import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, FileText, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Progress } from "@/components/ui/Progress";
import { Skeleton } from "@/components/ui/Skeleton";
import { elapsedPhrase } from "@/features/candidate/components/PreparationProgress";
import { StepFooter, StepHeader } from "@/features/candidate/components/StepLayout";
import { JourneyBoundary, JourneyError, useJourney, type JourneyContext } from "@/features/candidate/hooks";
import { errorMessage, isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";

import { getPortalIntake, getResumeReview, intakeKeys, intakeUnavailable, saveResumeReview } from "../api";
import { ResumeReviewEditor } from "../components/ResumeReviewEditor";
import { countChanges } from "../resumeReview";
import type { ResumeReviewDoc, ResumeReviewView, ReviewSectionState } from "../types";

/** Until the first read is on screen check often (about a second for a text file), then calmly until it is checked. */
export const REVIEW_FIRST_READ_POLL_MS = 600;
export const REVIEW_REFINE_POLL_MS = 1500;

export function reviewPollInterval(view: ResumeReviewView | undefined): number | false {
  if (!view) return REVIEW_FIRST_READ_POLL_MS;
  if (view.confirmed || view.skipped) return false;
  if (!view.document) return REVIEW_FIRST_READ_POLL_MS;
  return view.profileStage === "full" ? false : REVIEW_REFINE_POLL_MS;
}

function sameDoc(a: ResumeReviewDoc | null, b: ResumeReviewDoc | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** /candidate/:interviewId/resume-review: confirm or correct what was read from the résumé (§11.2). */
export default function IntakeResumeReviewPage() {
  useDocumentTitle("Check your résumé");
  const { query, ctx } = useJourney("resume-review");
  return (
    <div>
      <StepHeader title="Check what we read from your résumé" lede="We use these details to ask about your experience. Fix anything we read incorrectly." />
      <div className="mt-8">
        <JourneyBoundary query={query}>{ctx ? <Review ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

function describeSaveError(error: unknown): string {
  if (!isApiError(error)) return errorMessage(error, "Check your connection and try again.");
  if (error.code === "resume_not_ready") return "We're still reading your résumé. Try again in a moment.";
  if (error.code === "skip_not_allowed") return "You can continue without reviewing once we've been reading for three minutes.";
  if (error.code === "onboarding_locked") return "Your interview has already started, so the résumé details can't change now.";
  if (error.status === 429) return "That was a lot of saves in a short time. Wait a minute, then try again.";
  return error.message || "Check your connection and try again.";
}

function useSecondTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function Review({ ctx }: { ctx: JourneyContext }) {
  const queryClient = useQueryClient();
  const id = ctx.interviewId;
  const intake = useQuery({ queryKey: intakeKeys.portal(id), queryFn: () => getPortalIntake(id), meta: { errorToast: false }, refetchOnWindowFocus: false });
  const noResume = intake.data ? !intake.data.resumeUploaded : false;
  const review = useQuery({
    queryKey: intakeKeys.resumeReview(id),
    queryFn: () => getResumeReview(id),
    enabled: !noResume,
    meta: { errorToast: false },
    refetchOnWindowFocus: false,
    refetchInterval: (q) => (q.state.error ? false : reviewPollInterval(q.state.data)),
  });
  // The candidate's edits; null until the first one (until then the newest server read is shown).
  const [draft, setDraft] = useState<ResumeReviewDoc | null>(null);
  const [baseline, setBaseline] = useState<ResumeReviewDoc | null>(null);
  const [itemOpen, setItemOpen] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // 422 unknown_ref: the résumé changed under the edits (e.g. a new upload); offer the latest version.
  const [staleDoc, setStaleDoc] = useState(false);
  const [mountedAt] = useState(() => Date.now());

  const view = review.data;
  const serverDoc = view?.document ?? null;
  const current = draft ?? serverDoc;
  const waiting = !serverDoc;
  // The earliest moment skipping was promised (each poll restates it; never push it later).
  const skipAtRef = useRef<number | null>(null);
  if (view && view.skipAvailableInS !== null) {
    const promised = review.dataUpdatedAt + view.skipAvailableInS * 1000;
    skipAtRef.current = skipAtRef.current === null ? promised : Math.min(skipAtRef.current, promised);
  }
  const skipAt = skipAtRef.current;
  const now = useSecondTicker(waiting && Boolean(view));
  const canSkip = Boolean(view && !view.confirmed && (view.canSkip || (skipAt !== null && now >= skipAt)));
  const newerAvailable = Boolean(draft && baseline && serverDoc && !sameDoc(serverDoc, baseline));
  const changes = countChanges(baseline ?? serverDoc, current);
  const states = useMemo(() => new Map<string, ReviewSectionState>((view?.sections ?? []).map((s) => [s.key, s.state])), [view?.sections]);
  const stage = view?.profileStage ?? "none";

  const done = async (next: ResumeReviewView) => {
    queryClient.setQueryData(intakeKeys.resumeReview(id), next);
    await queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(id), exact: true });
    ctx.next();
  };
  const onError = (error: unknown) => {
    if (isApiError(error) && error.code === "consent_required") {
      ctx.goTo("consent", { replace: true });
      return;
    }
    setStaleDoc(isApiError(error) && error.code === "unknown_ref");
    setSaveError(describeSaveError(error));
  };
  const confirm = useMutation({
    mutationFn: (document: ResumeReviewDoc) => saveResumeReview(id, { action: "confirm", document }),
    meta: { silentError: true },
    onMutate: () => setSaveError(null),
    onSuccess: done,
    onError,
  });
  const skip = useMutation({
    mutationFn: () => saveResumeReview(id, { action: "skip", document: null }),
    meta: { silentError: true },
    onMutate: () => setSaveError(null),
    onSuccess: done,
    onError,
  });

  if (intake.isPending || (!noResume && review.isPending)) {
    return (
      <div className="grid gap-4" aria-busy="true" aria-label="Loading your résumé">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  // No résumé to check: required -> back to the upload; optional -> nothing to do here.
  if (noResume) {
    const required = intake.data?.resumeRequired ?? true;
    return (
      <div data-testid="intake-resume-review" data-stage="none">
        {required ? (
          <Callout tone="warning" title="Upload your résumé first">
            This interview asks about your experience, so it needs your résumé before we can check what we read.
          </Callout>
        ) : (
          <p className="text-[15px] leading-7 text-fg-muted">You didn't add a résumé, so there's nothing to check. The interviewer will ask about your experience instead.</p>
        )}
        <StepFooter onBack={ctx.back}>
          {required ? (
            <Button size="lg" onClick={() => ctx.goTo("resume")} leftIcon={<FileText />}>
              Upload your résumé
            </Button>
          ) : (
            <Button size="lg" onClick={ctx.next} data-testid="resume-review-confirm">
              Continue
            </Button>
          )}
        </StepFooter>
      </div>
    );
  }

  if (review.isError) {
    if (!intakeUnavailable(review.error)) return <JourneyError error={review.error} onRetry={() => void review.refetch()} />;
    return (
      <div data-testid="intake-resume-review" data-stage="none">
        <p className="text-[15px] leading-7 text-fg-muted">This check isn't available right now. The interviewer will use what we read from your file.</p>
        <StepFooter onBack={ctx.back}>
          <Button size="lg" onClick={ctx.next} data-testid="resume-review-confirm">
            Continue
          </Button>
        </StepFooter>
      </div>
    );
  }

  const busy = confirm.isPending || skip.isPending;

  return (
    <div data-testid="intake-resume-review" data-stage={stage}>
      {waiting ? (
        <section role="status" aria-live="polite" aria-labelledby="review-waiting" className="grid grid-cols-1 gap-3 rounded-xl border border-line bg-surface p-5 sm:p-6">
          <p id="review-waiting" className="flex items-center gap-2 text-[15px] font-medium text-fg">
            <Loader2 className="size-4 shrink-0 animate-spin text-accent-fg" aria-hidden="true" />
            Reading your résumé
          </p>
          <Progress value={null} tone="accent" size="xs" aria-label="Reading your résumé" />
          <p className="text-[13px] leading-5 text-fg-muted">
            {elapsedPhrase(now - mountedAt)}. A text file takes a second or two, a scanned page about 20 seconds. The details appear here as soon as they're read.
          </p>
        </section>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 pb-6">
            <p className="max-w-xl text-sm leading-6 text-fg-muted">
              {stage === "full"
                ? "Edit, add or remove anything that isn't right. Nothing here is scored."
                : "This is our first read of your file; we're still checking the details. You can start correcting it now. Nothing here is scored."}
            </p>
            {stage === "full" ? (
              <Badge tone="success" icon={<CircleCheck />} data-testid="resume-review-stage">
                Checked
              </Badge>
            ) : (
              <Badge tone="neutral" data-testid="resume-review-stage">
                First read
              </Badge>
            )}
          </div>

          {newerAvailable ? (
            <Callout
              tone="info"
              className="mb-6"
              title="We've finished checking your résumé"
              actions={
                <Button
                  size="sm"
                  variant="outline"
                  leftIcon={<RefreshCw />}
                  onClick={() => {
                    setDraft(null);
                    setBaseline(null);
                  }}
                >
                  Show the checked version
                </Button>
              }
            >
              It may add details you haven't seen yet. Showing it replaces the changes you've made so far.
            </Callout>
          ) : null}

          {view?.confirmed && !draft ? (
            <Callout tone="success" className="mb-6" title="You've already confirmed these details">
              You can still correct them before your interview starts.
            </Callout>
          ) : null}

          {current ? (
            <ResumeReviewEditor
              doc={current}
              onChange={(next) => {
                if (!draft) setBaseline(serverDoc);
                setDraft(next);
              }}
              sectionStates={states}
              final={stage === "full"}
              disabled={busy}
              onEditingChange={setItemOpen}
            />
          ) : null}
        </>
      )}

      {saveError ? (
        <Callout
          tone="danger"
          className="mt-6"
          title="That didn't save"
          data-testid="resume-review-error"
          actions={
            staleDoc ? (
              <Button
                size="sm"
                variant="outline"
                leftIcon={<RefreshCw />}
                onClick={() => {
                  setDraft(null);
                  setBaseline(null);
                  setSaveError(null);
                  setStaleDoc(false);
                  void review.refetch();
                }}
              >
                Show the latest version
              </Button>
            ) : undefined
          }
        >
          {saveError}
        </Callout>
      ) : null}

      <StepFooter
        onBack={ctx.back}
        note={
          itemOpen
            ? "Save or cancel the item you're editing first."
            : !waiting && changes > 0
              ? `${changes === 1 ? "1 correction" : `${changes} corrections`} will be saved with your résumé. The hiring team sees what you changed.`
              : waiting && canSkip
                ? "Taking a while? You can continue without reviewing; the interviewer will use what we read from your file."
                : undefined
        }
      >
        {canSkip && waiting ? (
          <Button size="lg" variant="outline" onClick={() => skip.mutate()} loading={skip.isPending} loadingText="Continuing" disabled={busy} data-testid="resume-review-skip">
            Continue without reviewing
          </Button>
        ) : null}
        {!waiting && current ? (
          <Button size="lg" onClick={() => confirm.mutate(current)} loading={confirm.isPending} loadingText="Saving" disabled={busy || itemOpen} data-testid="resume-review-confirm">
            Looks right, continue
          </Button>
        ) : null}
      </StepFooter>
    </div>
  );
}
