import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, CircleCheck, FileText, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { FileDropzone, type DropzoneStatus } from "@/components/ui/FileDropzone";
import { Skeleton } from "@/components/ui/Skeleton";
import { errorMessage, isAbortError, isApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";

import { getResume, TERMINAL_RESUME_FAILURES, uploadResume, type ResumeStepView, type ResumeView } from "../api";
import { PreparationProgress } from "../components/PreparationProgress";
import { resumePollInterval, SummaryPanel } from "../components/ResumeSummary";
import { StepFooter, StepHeader } from "../components/StepLayout";
import { JourneyBoundary, JourneyError, useJourney, type JourneyContext } from "../hooks";

const ACCEPT = [".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain", "image/png", "image/jpeg"];
const MAX_BYTES = 10 * 1024 * 1024;
/** Résumé analysis on the shared model: ~15 s for a text file, plus ~20 s per scanned page. */
const USUAL_LABEL = "usually less than a minute";
const SLOW_AFTER_MS = 2 * 60_000;

export default function ResumePage() {
  useDocumentTitle("Your résumé");
  const { query, ctx } = useJourney("resume");
  return (
    <div>
      <StepHeader
        title="Share your résumé"
        lede="The interviewer uses it to ask about your real experience, so the conversation is about you rather than generic questions. Only the hiring team can see it."
      />
      <div className="mt-8">
        <JourneyBoundary query={query}>{ctx ? <Resume ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

function isProcessing(view: ResumeView | undefined): boolean {
  if (!view || view.status === "none") return false;
  if (TERMINAL_RESUME_FAILURES.has(view.status)) return false;
  return !(view.status === "ready" && view.profileReady);
}

function describeUploadError(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 413) return `This file is too large. The limit is ${formatBytes(MAX_BYTES)}.`;
    if (error.status === 415) return "This file type isn't supported. Upload a PDF, Word (.docx), text or image file.";
    return error.message || "The upload didn't go through. Try again.";
  }
  return errorMessage(error, "The upload didn't go through. Try again.");
}

function StepRow({ step }: { step: ResumeStepView }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <span
        className={cn(
          "flex size-6 shrink-0 items-center justify-center rounded-full",
          step.state === "done" && "bg-success-soft text-success-fg",
          step.state === "active" && "bg-accent-soft text-accent-fg",
          step.state === "failed" && "bg-danger-soft text-danger-fg",
          step.state === "pending" && "bg-surface-sunken text-fg-subtle",
        )}
        aria-hidden="true"
      >
        {step.state === "done" ? <Check className="size-3.5" strokeWidth={3} /> : null}
        {step.state === "active" ? <Loader2 className="size-3.5 animate-spin" /> : null}
        {step.state === "failed" ? <CircleAlert className="size-3.5" /> : null}
        {step.state === "pending" ? <span className="size-1.5 rounded-full bg-current" /> : null}
      </span>
      <span className={cn("text-sm", step.state === "pending" ? "text-fg-muted" : "font-medium text-fg")}>{step.label}</span>
      <span className="sr-only">{step.state === "done" ? "(done)" : step.state === "active" ? "(in progress)" : step.state === "failed" ? "(failed)" : "(waiting)"}</span>
    </li>
  );
}

function Resume({ ctx }: { ctx: JourneyContext }) {
  const queryClient = useQueryClient();
  const key = queryKeys.portal.resume(ctx.interviewId);
  const resume = useQuery({
    queryKey: key,
    queryFn: () => getResume(ctx.interviewId),
    meta: { errorToast: false },
    refetchInterval: (q) => resumePollInterval(q.state.data),
    refetchOnWindowFocus: false,
  });
  const [uploadStatus, setUploadStatus] = useState<DropzoneStatus>("idle");
  const [progress, setProgress] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = async (selected: File) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setFile(selected);
    setUploadError(null);
    setUploadStatus("uploading");
    setProgress(0);
    try {
      const view = await uploadResume(ctx.interviewId, selected, { signal: controller.signal, onProgress: (p) => setProgress(p.percent) });
      setUploadStatus("success");
      setReplacing(false);
      queryClient.setQueryData(key, { ...view, status: view.status === "none" ? "uploaded" : view.status });
      void queryClient.invalidateQueries({ queryKey: key });
    } catch (error) {
      if (isAbortError(error)) {
        setUploadStatus("idle");
        setFile(null);
        return;
      }
      setUploadStatus("error");
      setUploadError(describeUploadError(error));
    }
  };

  if (resume.isPending) {
    return (
      <div className="grid gap-4" aria-busy="true" aria-label="Loading">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-44 w-full" />
      </div>
    );
  }
  if (resume.isError && !(isApiError(resume.error) && resume.error.status === 404)) {
    return <JourneyError error={resume.error} onRetry={() => void resume.refetch()} />;
  }

  const view = resume.data ?? null;
  const optional = ctx.interview.requiresResume === false;
  const hasResume = Boolean(view && view.status !== "none");
  const failed = Boolean(view && TERMINAL_RESUME_FAILURES.has(view.status));
  const processing = isProcessing(view ?? undefined);
  const ready = Boolean(view && view.status === "ready" && view.profileReady);
  // Text extracted: the candidate can move on while the profile is analysed in the background.
  const canContinue = ready || Boolean(view?.extracted);
  const background = canContinue && !ready;
  const showDropzone = !hasResume || failed || replacing || uploadStatus === "uploading" || uploadStatus === "error";
  const showSummary = Boolean(view?.summary) && !failed && !(replacing && !failed);
  const partial = view?.summary?.partial ?? null;
  const activeStep = view?.steps.find((s) => s.state === "active" || s.state === "failed");
  const statusText = failed
    ? view?.error ?? "We couldn't read this file."
    : ready
      ? "Read and ready for your interview"
      : partial
        ? partial.pagesRead && partial.pagesTotal
          ? `Read page ${partial.pagesRead} of ${partial.pagesTotal} · scanning the rest`
          : "Read the first pages · scanning the rest"
        : background && view?.summary
          ? "Read · checking the details in the background"
          : background && activeStep
            ? `Read · ${activeStep.label} in the background`
            : activeStep
              ? `${activeStep.label}…`
              : view?.statusDetail ?? "Processing…";
  const continueEnabled = canContinue || optional;

  return (
    <div>
      <div className="grid gap-6">
        {showDropzone ? (
          <div>
            <FileDropzone
              inputProps={{ "data-testid": "resume-file-input", "aria-label": "Choose your résumé file" }}
              accept={ACCEPT}
              acceptLabel="PDF, Word (.docx), text, PNG or JPG"
              maxSizeBytes={MAX_BYTES}
              onFiles={(files) => files[0] && void start(files[0])}
              status={uploadStatus}
              progress={progress}
              file={file ? { name: file.name, size: file.size } : null}
              error={uploadError ?? undefined}
              onCancel={() => abortRef.current?.abort()}
              onRetry={file ? () => void start(file) : undefined}
              title="Drop your résumé here, or browse"
              hint={`PDF, Word (.docx), text or a photo of a printed résumé. Up to ${formatBytes(MAX_BYTES)}.`}
            />
            {replacing && !failed && uploadStatus !== "uploading" ? (
              <Button variant="ghost" size="sm" className="mt-2" onClick={() => setReplacing(false)}>
                Keep the current file
              </Button>
            ) : null}
          </div>
        ) : null}

        {hasResume && !(replacing && !failed) ? (
          <section aria-labelledby="resume-file-name" className="rounded-xl border border-line bg-surface p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <span
                  className={cn(
                    "flex size-10 shrink-0 items-center justify-center rounded-md [&_svg]:size-5",
                    failed ? "bg-danger-soft text-danger-fg" : ready ? "bg-success-soft text-success-fg" : "bg-neutral-soft text-fg-muted",
                  )}
                  aria-hidden="true"
                >
                  {failed ? <CircleAlert /> : ready ? <CircleCheck /> : <FileText />}
                </span>
                <div className="min-w-0">
                  <p id="resume-file-name" className="truncate text-sm font-medium text-fg">
                    {view?.filename ?? file?.name ?? "Your résumé"}
                  </p>
                  <p data-testid="resume-status" aria-live="polite" className={cn("mt-0.5 text-sm", failed ? "text-danger-fg" : ready ? "text-success-fg" : "text-fg-muted")}>
                    {statusText}
                  </p>
                </div>
              </div>
              {ready || failed || background ? (
                <Button variant="outline" size="sm" leftIcon={<RotateCcw />} onClick={() => setReplacing(true)} className="w-full sm:w-auto">
                  Upload a different file
                </Button>
              ) : null}
            </div>
            {background ? (
              <div className="mt-5 grid gap-5 border-t border-line pt-5">
                <PreparationProgress view={view} showLabel={false} showSteps usualLabel={USUAL_LABEL} slowAfterMs={SLOW_AFTER_MS} />
                <Callout tone="info" title="You can continue now">
                  We'll finish tailoring your interview in the background. The next steps don't depend on it; only the Begin button waits until it's ready.
                </Callout>
              </div>
            ) : processing || failed ? (
              <>
                <ol className="mt-4 border-t border-line pt-2">
                  {view?.steps.map((step) => (
                    <StepRow key={step.key} step={step} />
                  ))}
                </ol>
                {processing ? <p className="mt-3 text-[13px] text-fg-subtle">Reading a text file takes a second or two; a scanned page takes about 20 seconds.</p> : null}
              </>
            ) : null}
            {failed ? (
              <Callout tone="danger" className="mt-4" title="We couldn't use this file">
                {view?.error ?? "Try a different file, for example a PDF exported from your word processor."}
              </Callout>
            ) : null}
          </section>
        ) : null}

        {showSummary && view ? <SummaryPanel view={view} /> : null}
      </div>

      <StepFooter
        onBack={ctx.back}
        note={
          !canContinue && hasResume && !failed
            ? "You can continue as soon as we've read the file."
            : optional && !hasResume
              ? "A résumé is optional for this interview. Without one, the questions focus on the role rather than your past projects."
              : undefined
        }
      >
        <Button
          size="lg"
          variant={optional && !hasResume ? "outline" : "primary"}
          disabled={!continueEnabled}
          onClick={ctx.next}
          data-testid="resume-continue"
        >
          {optional && !hasResume ? "Continue without a résumé" : "Continue"}
        </Button>
      </StepFooter>
    </div>
  );
}
