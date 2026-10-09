import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Combobox } from "@/components/ui/Combobox";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { Progress } from "@/components/ui/Progress";
import { isAbortError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { JOB_STATUS_LABELS, labelFor, RESUME_STATUS_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import type { CandidateResume, ResumeStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import { RESUME_STEPS, RESUME_TERMINAL, uploadResume, useResumeStatus } from "../api/candidates";
import { useJobs } from "../api/jobs";
import { describeFailure } from "../lib/serverErrors";

export const RESUME_ACCEPT = [".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", "application/pdf", "image/*", "text/plain"];
export const RESUME_MAX_BYTES = 10 * 1024 * 1024;

const STEP_LABEL: Record<ResumeStatus, string> = {
  uploaded: "Uploaded",
  validating: "Security and format checks",
  extracting: "Reading the document",
  ocr: "Scanning pages",
  analyzing: "Analyzing experience and skills",
  ready: "Ready",
  failed: "Failed",
  rejected: "Rejected",
};

export type StepState = "pending" | "active" | "done" | "failed" | "skipped";
export interface PipelineStep {
  key: string;
  label: string;
  state: StepState;
}

/**
 * Steps from `progress.steps` (`{key, label, state, at}`) when the API sends
 * them, otherwise derived from the status. Skipped steps (e.g. OCR for a text
 * PDF) are left out.
 */
export function pipelineSteps(resume: Pick<CandidateResume, "status" | "progress" | "extraction_method">): PipelineStep[] {
  const raw = (resume.progress as { steps?: unknown } | undefined)?.steps;
  if (Array.isArray(raw) && raw.length) {
    return raw
      .filter((s): s is Record<string, unknown> => Boolean(s) && typeof s === "object" && typeof (s as Record<string, unknown>).key === "string")
      .map((s) => ({
        key: String(s.key),
        label: typeof s.label === "string" && s.label ? s.label : (STEP_LABEL[s.key as ResumeStatus] ?? String(s.key)),
        state: (["pending", "active", "done", "failed", "skipped"].includes(String(s.state)) ? s.state : "pending") as StepState,
      }))
      .filter((s) => s.state !== "skipped" && !(s.key === "ocr" && s.state === "pending" && resume.status === "ready"));
  }
  const failed = resume.status === "failed" || resume.status === "rejected";
  const stage = (resume.progress?.stage as ResumeStatus | undefined) ?? resume.status;
  const keys = RESUME_STEPS.filter((s) => s !== "ocr" || stage === "ocr" || resume.extraction_method === "ocr" || resume.extraction_method === "mixed");
  const currentIndex = failed ? keys.indexOf(stage) : Math.max(0, keys.indexOf(stage === "ready" || resume.status === "ready" ? "ready" : stage));
  return keys.map((key, index) => ({
    key,
    label: STEP_LABEL[key],
    state: resume.status === "ready" || index < currentIndex ? "done" : index === currentIndex ? (failed ? "failed" : "active") : "pending",
  }));
}

/** Where a resume is in the pipeline, as a row of steps with the live message. */
export function ResumePipeline({ resume, className }: { resume: Pick<CandidateResume, "status" | "progress" | "extraction_method" | "error" | "status_detail">; className?: string }) {
  const failed = resume.status === "failed" || resume.status === "rejected";
  const steps = pipelineSteps(resume);
  const pages = resume.progress?.pages_total ? `${resume.progress.pages_done ?? 0} of ${resume.progress.pages_total} pages` : null;
  return (
    <div className={cn("grid gap-3", className)} data-testid="resume-pipeline" aria-live="polite">
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2" aria-label="Resume processing">
        {steps.map((step, index) => {
          const done = step.state === "done";
          const current = step.state === "active";
          const broken = step.state === "failed";
          return (
            <li key={step.key} className="flex items-center gap-1" aria-current={current ? "step" : undefined}>
              <span
                className={cn(
                  "inline-flex h-6 items-center gap-1.5 rounded-full px-2 text-xs font-medium",
                  done && "bg-success-soft text-success-fg",
                  current && "bg-accent-soft text-accent-fg",
                  broken && "bg-danger-soft text-danger-fg",
                  step.state === "pending" && "text-fg-subtle",
                )}
              >
                {done ? <Check className="size-3" aria-hidden="true" /> : current ? <Loader2 className="size-3 animate-spin" aria-hidden="true" /> : broken ? <AlertCircle className="size-3" aria-hidden="true" /> : null}
                {step.label}
              </span>
              {index < steps.length - 1 ? <span className="h-px w-3 bg-line-strong" aria-hidden="true" /> : null}
            </li>
          );
        })}
      </ol>
      {!failed && resume.status !== "ready" ? (
        <Progress value={typeof resume.progress?.percent === "number" ? resume.progress.percent : null} size="xs" aria-label="Processing progress" />
      ) : null}
      {resume.progress?.message || pages ? (
        <p className="text-xs text-fg-muted">{[resume.progress?.message, pages].filter(Boolean).join(", ")}</p>
      ) : null}
      {failed ? (
        <Callout tone="danger" icon={<AlertCircle />} title={resume.status === "rejected" ? "This file was rejected" : "Processing failed"}>
          {resume.error ?? resume.status_detail ?? "Try uploading the file again, or a different format."}
        </Callout>
      ) : null}
    </div>
  );
}

export interface ResumeUploaderProps {
  candidateId: string;
  /** Called once the resume finishes processing (ready, failed or rejected). */
  onFinished?: (resume: CandidateResume) => void;
  /** Ask which job to analyse against (default true). */
  askJob?: boolean;
  defaultJobId?: string | null;
  compact?: boolean;
}

/** Upload a resume, then follow it through validation, extraction and profiling. */
export function ResumeUploader({ candidateId, onFinished, askJob = true, defaultJobId = null, compact }: ResumeUploaderProps) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [percent, setPercent] = useState<number | null>(null);
  const [status, setStatus] = useState<"idle" | "uploading" | "success" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(defaultJobId);
  const abort = useRef<AbortController | null>(null);
  const jobs = useJobs({ limit: 100, offset: 0 }, { enabled: askJob });
  const tracked = useResumeStatus(resumeId);
  const finishedFor = useRef<string | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  useEffect(() => {
    const resume = tracked.data;
    if (!resume || !RESUME_TERMINAL.has(resume.status) || finishedFor.current === resume.id) return;
    finishedFor.current = resume.id;
    void queryClient.invalidateQueries({ queryKey: queryKeys.candidates.detail(candidateId) });
    void queryClient.invalidateQueries({ queryKey: ["candidates", "list"] });
    onFinished?.(resume);
  }, [tracked.data, candidateId, onFinished, queryClient]);

  const start = async (picked: File) => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setFile(picked);
    setStatus("uploading");
    setError(null);
    setPercent(0);
    setResumeId(null);
    finishedFor.current = null;
    try {
      const resume = await uploadResume(candidateId, picked, { jobId, onProgress: setPercent, signal: controller.signal });
      setStatus("success");
      setResumeId(resume.id);
      queryClient.setQueryData(queryKeys.resumes.detail(resume.id), resume);
      void queryClient.invalidateQueries({ queryKey: queryKeys.candidates.resumes(candidateId) });
    } catch (e) {
      if (isAbortError(e)) {
        setStatus("idle");
        setFile(null);
        return;
      }
      setStatus("error");
      setError(describeFailure(e, "The upload failed. Try again."));
    }
  };

  const jobOptions = (jobs.data?.items ?? []).map((j) => ({ value: j.id, label: j.title, description: j.status === "open" ? undefined : labelFor(JOB_STATUS_LABELS, j.status) }));

  return (
    <div className="grid gap-4">
      {askJob ? (
        <div className="grid gap-1.5">
          <span className="text-[13px] font-medium text-fg">
            Match against a job <span className="text-xs font-normal text-fg-subtle">Optional</span>
          </span>
          <Combobox options={jobOptions} value={jobId} onChange={setJobId} placeholder="No job: analyse generally" aria-label="Job to match against" disabled={status === "uploading"} />
          <p className="text-xs text-fg-muted">The profile then includes requirement-by-requirement matching and claims worth verifying for that role.</p>
        </div>
      ) : null}
      <FileDropzone
        accept={RESUME_ACCEPT}
        acceptLabel="PDF, Word (.docx), text or an image"
        maxSizeBytes={RESUME_MAX_BYTES}
        onFiles={(files) => files[0] && void start(files[0])}
        status={status}
        progress={percent}
        file={file ? { name: file.name, size: file.size } : null}
        error={error}
        onCancel={() => abort.current?.abort()}
        onRetry={() => file && void start(file)}
        title={compact ? "Drop a resume or browse" : "Drop a resume here or browse"}
        hint={`PDF, DOCX, TXT, PNG, JPG or TIFF up to ${formatBytes(RESUME_MAX_BYTES)}. Scanned documents are read with OCR.`}
      />
      {resumeId && tracked.data ? (
        <div className="rounded-md border border-line bg-surface-subtle/60 p-3.5">
          <p className="mb-2.5 text-[13px] font-medium text-fg">{tracked.data.status === "ready" ? "Profile ready" : RESUME_STATUS_LABELS[tracked.data.status]}</p>
          <ResumePipeline resume={tracked.data} />
          {tracked.data.status === "failed" ? (
            <Button className="mt-3" size="sm" variant="outline" onClick={() => file && void start(file)}>
              Upload again
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
