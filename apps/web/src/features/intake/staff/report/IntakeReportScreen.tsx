import { ArrowLeft, FileDown, Printer, RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { confirm } from "@/components/ui/AlertDialog";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toaster";
import { api, errorMessage, isApiError } from "@/lib/api";
import { usePermission } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import type { MediaUrl } from "@/lib/types";

import { isInProgress, useRegenerateReport, useSessionDetail, useSessionReport } from "@/features/recruiter/review/api/hooks";
import "@/features/recruiter/review/report/report-print.css";

import { normalizeIntakeReport } from "../api";
import { IntakeReportView } from "./IntakeReportView";

/**
 * /app/reports/:sessionId for intake sessions: the intake report with its toolbar
 * (back to the review, versions, regenerate, HTML file, print). Candidates never get
 * an intake report, so there is no release control.
 */
export function IntakeReportScreen({ sessionId }: { sessionId: string }) {
  const [params, setParams] = useSearchParams();
  const requestedVersion = Number(params.get("version")) || null;
  const canRegenerate = usePermission("reviews:write");
  const detail = useSessionDetail(sessionId);
  /** The version on screen when "Regenerate" was pressed; polling stops once a newer one is ready. */
  const [awaitingAfter, setAwaitingAfter] = useState<number | null>(null);
  const awaiting = awaitingAfter !== null;
  const report = useSessionReport(sessionId, "recruiter", { version: requestedVersion, pollMs: awaiting ? 4_000 : false });
  const regenerate = useRegenerateReport(sessionId);
  const [downloading, setDownloading] = useState(false);
  const name = detail.data?.candidate?.full_name;
  useDocumentTitle(name ? `${name}: intake report` : "Intake report");

  const data = report.data;
  const content = normalizeIntakeReport(data?.content);
  const notReady = isApiError(report.error) && report.error.status === 404;
  const generating = awaiting || data?.status === "pending" || data?.status === "generating";
  const versions = data?.versions ?? [];
  const reviewHref = content?.intake_id ? `/app/intake/${content.intake_id}` : `/app/intake/session/${sessionId}`;

  useEffect(() => {
    if (awaitingAfter !== null && data && data.version > awaitingAfter && data.status === "ready") {
      setAwaitingAfter(null);
      toast.success(`Report version ${data.version} is ready`);
    }
  }, [awaitingAfter, data]);
  useEffect(() => {
    if (awaitingAfter === null) return;
    // Give up polling after a few minutes; the new version shows on the next visit.
    const timer = setTimeout(() => setAwaitingAfter(null), 4 * 60_000);
    return () => clearTimeout(timer);
  }, [awaitingAfter]);

  const startRegenerate = async () => {
    const ok = await confirm({
      title: "Regenerate this report?",
      description: "A new version is built from the latest evaluation and decisions. Earlier versions stay available.",
      confirmLabel: "Regenerate report",
    });
    if (!ok) return;
    regenerate.mutate(undefined, {
      onSuccess: () => {
        if (requestedVersion) setParams({}, { replace: true });
        setAwaitingAfter(data?.version ?? 0);
      },
    });
  };

  const download = async () => {
    if (!data?.artifact_media_id) return;
    setDownloading(true);
    try {
      const signed = await api.get<MediaUrl>(`/media/${data.artifact_media_id}/url`);
      const link = document.createElement("a");
      link.href = signed.url;
      link.rel = "noopener";
      link.download = `intake-report-${(name ?? "candidate").toLowerCase().replace(/[^a-z0-9]+/g, "-")}-v${data.version}.html`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      toast.error("Couldn't download the report file", { description: errorMessage(error) });
    } finally {
      setDownloading(false);
    }
  };

  return (
    <PageContainer width="full" className="max-w-[1100px]">
      <div data-print-hide className="sticky top-0 z-20 -mx-4 mb-6 border-b border-line-subtle bg-surface/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft />} asChild>
              <Link to={reviewHref}>Intake review</Link>
            </Button>
            <div className="grid min-w-0">
              <span className="truncate text-sm font-semibold text-fg">{name ?? "Candidate"}, intake report</span>
              <span className="truncate text-xs text-fg-muted">{data ? `Version ${data.version}${data.generated_at ? `, generated ${formatDateTime(data.generated_at)}` : ""}. Not shared with the candidate.` : "Not generated yet"}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {versions.length > 1 ? (
              <Select
                size="sm"
                className="w-40"
                aria-label="Report version"
                value={String(requestedVersion ?? data?.version ?? "")}
                onValueChange={(v) => setParams(Number(v) === Math.max(...versions.map((x) => x.version)) ? {} : { version: v }, { replace: true })}
                options={[...versions].sort((x, y) => y.version - x.version).map((v, i) => ({ value: String(v.version), label: `Version ${v.version}${i === 0 ? " (latest)" : ""}` }))}
              />
            ) : null}
            {canRegenerate ? (
              <Button variant="outline" leftIcon={generating ? <Spinner size="sm" label={null} /> : <RefreshCcw />} onClick={() => void startRegenerate()} disabled={generating || isInProgress(detail.data?.status) || regenerate.isPending}>
                {generating ? "Generating…" : "Regenerate"}
              </Button>
            ) : null}
            {data?.artifact_media_id ? (
              <Button variant="outline" leftIcon={<FileDown />} onClick={() => void download()} loading={downloading}>
                HTML file
              </Button>
            ) : null}
            <Button leftIcon={<Printer />} onClick={() => window.print()} disabled={!content}>
              Print or save PDF
            </Button>
          </div>
        </div>
      </div>

      <div data-print-hide className="mx-auto mb-6 grid max-w-[900px] gap-3">
        {notReady ? (
          <Callout tone="info" title="The intake report isn't ready yet">
            It's generated after the interview is evaluated, usually a few minutes after it ends. The intake review already shows what's available. <Link to={reviewHref}>Open the intake review</Link>
          </Callout>
        ) : report.isError ? (
          <Callout tone="danger" title="Couldn't load the report">
            {errorMessage(report.error)}
          </Callout>
        ) : data?.status === "failed" ? (
          <Callout tone="danger" title="The last report generation failed">
            {data.error ?? "Try regenerating it."}
          </Callout>
        ) : generating ? (
          <Callout tone="info" icon={<Spinner size="sm" label={null} />} title="Generating a new version">
            You can keep reading the current version; it refreshes here when the new one is ready.
          </Callout>
        ) : data && !content ? (
          <Callout tone="neutral" title="This report has another format">
            It isn't an intake report. <Link to={`/app/sessions/${sessionId}`}>Open the session</Link>
          </Callout>
        ) : null}
      </div>

      {report.isPending && !notReady ? <Skeleton className="mx-auto h-[70vh] w-full max-w-[900px]" /> : content ? <IntakeReportView content={content} version={data?.version} generatedAt={data?.generated_at} /> : null}
    </PageContainer>
  );
}
