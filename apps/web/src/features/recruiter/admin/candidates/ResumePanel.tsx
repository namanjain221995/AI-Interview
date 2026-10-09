import { Download, Eye, FileText, RotateCcw, ScanText, User } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { usePermission } from "@/lib/auth";
import { formatBytes, formatDateTime, formatRelativeOrDate } from "@/lib/format";
import type { CandidateResume } from "@/lib/types";

import { resumeFileUrl, useReprocessResume } from "../api/candidates";
import { describeFailure } from "../lib/serverErrors";
import { ResumePipeline } from "./ResumeUpload";

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

const TYPE_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "text/plain": "Text",
  "image/png": "PNG image",
  "image/jpeg": "JPEG image",
  "image/webp": "WebP image",
  "image/tiff": "TIFF image",
};

/** Open a resume through a short-lived signed URL (the only way files are served). */
async function openSigned(resume: CandidateResume, mode: "download" | "url"): Promise<string | null> {
  try {
    const url = await resumeFileUrl(resume);
    if (!url) {
      toast.error("This file isn't available", { description: "It may have passed its retention period." });
      return null;
    }
    if (mode === "download") {
      const link = document.createElement("a");
      link.href = url;
      link.rel = "noopener";
      link.download = resume.original_filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    }
    return url;
  } catch (error) {
    toast.error("Couldn't open the file", { description: describeFailure(error) });
    return null;
  }
}

export function ResumeCard({ resume, latest }: { resume: CandidateResume; latest?: boolean }) {
  const canWrite = usePermission("candidates:write");
  const reprocess = useReprocessResume();
  const [preview, setPreview] = useState<string | null>(null);
  const [opening, setOpening] = useState<"download" | "preview" | null>(null);
  const isImage = IMAGE_TYPES.has(resume.content_type);
  const summary = resume.extraction_summary;

  const run = async (mode: "download" | "preview") => {
    setOpening(mode);
    const url = await openSigned(resume, mode === "download" ? "download" : "url");
    setOpening(null);
    if (url && mode === "preview") setPreview(url);
  };

  return (
    <Card className={latest ? undefined : "bg-surface-subtle/40"}>
      <div className="grid gap-4 p-4">
        <div className="flex flex-wrap items-start gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-line bg-surface-subtle text-fg-muted" aria-hidden="true">
            <FileText className="size-5" />
          </span>
          <div className="grid min-w-0 flex-1 gap-0.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <p className="truncate text-sm font-semibold text-fg">{resume.original_filename}</p>
              {latest ? (
                <Badge size="sm" tone="neutral">
                  Latest
                </Badge>
              ) : null}
              <StatusBadge kind="resume" value={resume.status} size="sm" />
            </div>
            <p className="text-xs text-fg-muted">
              {[TYPE_LABEL[resume.content_type] ?? resume.content_type, formatBytes(resume.size_bytes), resume.page_count ? `${resume.page_count} ${resume.page_count === 1 ? "page" : "pages"}` : null]
                .filter(Boolean)
                .join(", ")}
              . Uploaded {formatRelativeOrDate(resume.created_at)}
            </p>
            <p className="flex items-center gap-1.5 text-xs text-fg-subtle">
              <User className="size-3" aria-hidden="true" />
              {resume.uploaded_by_candidate ? "Uploaded by the candidate" : "Uploaded by your team"}
              {resume.extraction_method ? (
                <>
                  <ScanText className="ml-2 size-3" aria-hidden="true" />
                  {resume.extraction_method === "text" ? "Text extracted" : resume.extraction_method === "ocr" ? "Read with OCR" : "Text and OCR"}
                </>
              ) : null}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            {isImage ? (
              <Button size="sm" variant="outline" leftIcon={<Eye />} loading={opening === "preview"} onClick={() => void run("preview")}>
                Preview
              </Button>
            ) : null}
            <Button size="sm" variant="outline" leftIcon={<Download />} loading={opening === "download"} onClick={() => void run("download")}>
              Download
            </Button>
            {canWrite && (resume.status === "failed" || resume.status === "ready") ? (
              <Button
                size="sm"
                variant="ghost"
                leftIcon={<RotateCcw />}
                loading={reprocess.isPending}
                onClick={() => reprocess.mutate(resume.id)}
                title="Run extraction and profiling again"
              >
                Reprocess
              </Button>
            ) : null}
          </div>
        </div>
        {resume.status !== "ready" ? <ResumePipeline resume={resume} /> : null}
        {summary && resume.status === "ready" ? (
          <dl className="grid gap-x-6 gap-y-2 rounded-md bg-surface-subtle px-3.5 py-3 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-fg-muted">Experience</dt>
              <dd className="font-medium text-fg tabular-nums">{typeof summary.total_experience_years === "number" ? `${summary.total_experience_years} years` : "Not stated"}</dd>
            </div>
            <div>
              <dt className="text-fg-muted">Roles found</dt>
              <dd className="font-medium text-fg tabular-nums">{summary.roles_count ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-fg-muted">Skills found</dt>
              <dd className="font-medium text-fg tabular-nums">{summary.skills_count ?? "—"}</dd>
            </div>
            {summary.warnings?.length ? (
              <div className="sm:col-span-3">
                <dt className="text-fg-muted">Notes from processing</dt>
                <dd className="text-warning-fg">{summary.warnings.join(" ")}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
        {(resume as CandidateResume & { warnings?: string[] }).warnings?.length ? (
          <ul className="grid gap-1 text-xs text-warning-fg">
            {(resume as CandidateResume & { warnings?: string[] }).warnings!.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
        {resume.processing_finished_at ? <p className="text-2xs text-fg-subtle">Processed {formatDateTime(resume.processing_finished_at)}</p> : null}
      </div>

      <Dialog open={preview !== null} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>{resume.original_filename}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            {preview ? (
              <img src={preview} alt={`Resume image: ${resume.original_filename}`} className="mx-auto max-h-[75vh] w-auto rounded-md border border-line" />
            ) : (
              <Spinner />
            )}
          </DialogBody>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export function ResumeList({ resumes, loading, onUpload }: { resumes: CandidateResume[]; loading: boolean; onUpload?: () => void }) {
  if (loading) return <Spinner />;
  if (resumes.length === 0) {
    return (
      <EmptyState
        bordered
        icon={<FileText />}
        title="No resume yet"
        description="Upload one to build the profile, match it against a job and give the interviewer something to ask about."
        actions={onUpload ? <Button size="sm" onClick={onUpload}>Upload resume</Button> : undefined}
      />
    );
  }
  return (
    <div className="grid gap-3">
      <p className="text-xs text-fg-muted">PDF and Word files download; images can be previewed here. Files are served through short-lived signed links.</p>
      {resumes.map((resume, index) => (
        <ResumeCard key={resume.id} resume={resume} latest={index === 0} />
      ))}
    </div>
  );
}
