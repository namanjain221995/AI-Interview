import { Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Progress } from "@/components/ui/Progress";
import { cn } from "@/lib/utils";

import type { ResumeStepView, ResumeView } from "../api";

/** Résumé analysis + interview plan usually take about this long on the shared model. */
export const USUAL_PREPARATION_MINUTES = 3;
const LONGER_THAN_USUAL_MS = 4.5 * 60_000;

/** When the step now running started (server time), falling back to the upload time. */
export function activeSince(view: ResumeView | null | undefined): number | null {
  const active = view?.steps.find((s) => s.state === "active");
  const at = active?.at ?? view?.createdAt ?? null;
  const t = at ? Date.parse(at) : Number.NaN;
  return Number.isFinite(t) ? t : null;
}

/**
 * Milliseconds since `startMs`, refreshed every `everyMs`. Falls back to the time since
 * mount when the server timestamp is missing or the device clock is clearly off.
 */
export function useElapsed(startMs: number | null, everyMs = 15_000): number {
  const [mountedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  const fromServer = startMs !== null ? now - startMs : Number.NaN;
  return Number.isFinite(fromServer) && fromServer >= 0 && fromServer < 6 * 3_600_000 ? fromServer : now - mountedAt;
}

/** "Just started" / "About a minute so far" / "About 4 minutes so far": calm, not a ticking counter. */
export function elapsedPhrase(ms: number): string {
  const minutes = ms / 60_000;
  if (minutes < 1) return "Just started";
  if (minutes < 1.5) return "About a minute so far";
  return `About ${Math.round(minutes)} minutes so far`;
}

/** The step the analysis is on, in plain words. */
export function activeStepLabel(view: ResumeView | null | undefined): string | null {
  return view?.steps.find((s) => s.state === "active")?.label ?? null;
}

function StepMark({ step }: { step: ResumeStepView }) {
  return (
    <li className="flex items-center gap-1.5">
      {step.state === "done" ? (
        <Check className="size-3.5 text-success" strokeWidth={3} aria-hidden="true" />
      ) : step.state === "active" ? (
        <Loader2 className="size-3.5 animate-spin text-accent-fg" aria-hidden="true" />
      ) : (
        <span className="mx-[5px] size-1 rounded-full bg-fg-subtle" aria-hidden="true" />
      )}
      <span className={cn(step.state === "pending" ? "text-fg-subtle" : step.state === "active" ? "font-medium text-fg" : "text-fg-muted")}>{step.label}</span>
      <span className="sr-only">{step.state === "done" ? "(done)" : step.state === "active" ? "(in progress)" : "(next)"}</span>
    </li>
  );
}

/**
 * How far the interview preparation has got: what is happening now, a calm
 * indeterminate bar (the duration varies with load, so no fake percentages), how
 * long it has been running against the usual duration, and optionally the steps.
 */
export function PreparationProgress({
  view,
  fallbackLabel = "Getting your interview ready",
  showLabel = true,
  showSteps = false,
  usualLabel = `usually about ${USUAL_PREPARATION_MINUTES} minutes`,
  slowAfterMs = LONGER_THAN_USUAL_MS,
  className,
}: {
  view: ResumeView | null | undefined;
  /** Shown when there is no résumé step running (e.g. the plan is being prepared). */
  fallbackLabel?: string;
  /** Hide the "what is happening now" line when the surrounding UI already says it. */
  showLabel?: boolean;
  showSteps?: boolean;
  /** How long this usually takes, in words (e.g. "usually less than a minute"). */
  usualLabel?: string;
  /** After this long the calm "taking longer than usual" note replaces the estimate. */
  slowAfterMs?: number;
  className?: string;
}) {
  const elapsed = useElapsed(activeSince(view), slowAfterMs < 60_000 ? 5_000 : 15_000);
  const label = activeStepLabel(view) ?? fallbackLabel;
  const slow = elapsed >= slowAfterMs;
  const steps = view?.steps ?? [];
  return (
    <div className={cn("grid content-start gap-2.5", className)}>
      {showLabel ? (
        <p className="flex items-center gap-2 text-sm font-medium text-fg">
          <Loader2 className="size-4 shrink-0 animate-spin text-accent-fg" aria-hidden="true" />
          {label}
        </p>
      ) : null}
      <Progress value={null} tone="accent" size="xs" aria-label={label} />
      <p className="text-[13px] leading-5 text-fg-muted">
        {slow
          ? "Taking longer than usual. It keeps going in the background and nothing is lost."
          : `${elapsedPhrase(elapsed)} · ${usualLabel}`}
      </p>
      {showSteps && steps.length ? (
        <ol className="mt-1 flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] leading-5" aria-label="Preparation steps">
          {steps.map((step) => (
            <StepMark key={step.key} step={step} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}
