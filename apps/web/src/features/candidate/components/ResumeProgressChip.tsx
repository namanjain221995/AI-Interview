import { CircleAlert, CircleCheck, Loader2 } from "lucide-react";
import { Link } from "react-router";

import { cn } from "@/lib/utils";

import { TERMINAL_RESUME_FAILURES, type ResumeView } from "../api";
import { resumeInProgress, useResumeStatus } from "../readiness";

/** The step the résumé analysis is on, in plain words. */
export function resumeActivity(view: ResumeView): string {
  const active = view.steps.find((s) => s.state === "active");
  return active?.label ?? "Tailoring your interview";
}

/**
 * Small live indicator for the steps after the résumé: the analysis keeps
 * running in the background and only the interview start waits for it.
 */
export function ResumeProgressChip({ interviewId, className }: { interviewId: string; className?: string }) {
  const resume = useResumeStatus(interviewId);
  const view = resume.data;
  if (!view || view.status === "none") return null;
  const failed = TERMINAL_RESUME_FAILURES.has(view.status);
  const working = resumeInProgress(view);
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1.5 text-[13px]",
        failed ? "border-danger-line bg-danger-soft text-danger-fg" : working ? "border-line bg-surface text-fg-muted" : "border-success-line bg-success-soft text-success-fg",
        className,
      )}
    >
      {failed ? (
        <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
      ) : working ? (
        <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden="true" />
      ) : (
        <CircleCheck className="size-3.5 shrink-0" aria-hidden="true" />
      )}
      <span className="truncate">
        {failed ? (
          <>
            We couldn't read your résumé.{" "}
            <Link to={`/candidate/${interviewId}/resume`} className="font-medium underline underline-offset-2">
              Upload it again
            </Link>
          </>
        ) : working ? (
          <>
            Preparing your interview in the background
            <span className="sr-only">: {resumeActivity(view)}</span>
          </>
        ) : (
          "Your interview is ready"
        )}
      </span>
    </div>
  );
}
