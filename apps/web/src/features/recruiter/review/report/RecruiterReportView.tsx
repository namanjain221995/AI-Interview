import { ArrowLeft, FileDown, FileJson, Printer, RefreshCcw, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { toast } from "@/components/ui/Toaster";
import { Tooltip } from "@/components/ui/Tooltip";
import { api, errorMessage, isApiError } from "@/lib/api";
import { usePermission } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { CANDIDATE_REPORT_MODE_LABELS, labelFor } from "@/lib/labels";
import type { CandidateReportMode, MediaUrl } from "@/lib/types";

import {
  isInProgress,
  useCompetencyCatalog,
  useRegenerateReport,
  useReleaseCandidateReport,
  useSessionCoding,
  useSessionDetail,
  useSessionEvidence,
  useSessionIntegrity,
  useSessionQuestions,
  useSessionReport,
  useSessionReviews,
  useSessionScores,
} from "../api/hooks";
import { QueryError } from "../components/primitives";
import { buildCompetencyIndex } from "../lib/competencies";
import { collectEvidence } from "../lib/evidence";
import { buildReportView, downloadJson, reportFileName } from "../lib/report";
import { ReportDocument } from "./ReportDocument";
import "./report-print.css";

const REGENERATE_TIMEOUT_MS = 4 * 60_000;

export function RecruiterReportView({ sessionId }: { sessionId: string }) {
  const [params, setParams] = useSearchParams();
  const requestedVersion = Number(params.get("version")) || null;
  const canRegenerate = usePermission("reviews:write");
  const canRelease = usePermission("reports:release");

  const detail = useSessionDetail(sessionId);
  const [awaitingVersion, setAwaitingVersion] = useState<number | null>(null);
  const report = useSessionReport(sessionId, "recruiter", { version: requestedVersion, pollMs: awaitingVersion !== null ? 4_000 : false });
  const scores = useSessionScores(sessionId);
  const questions = useSessionQuestions(sessionId);
  const evidence = useSessionEvidence(sessionId);
  const coding = useSessionCoding(sessionId);
  const integrity = useSessionIntegrity(sessionId);
  const reviews = useSessionReviews(sessionId);
  const catalog = useCompetencyCatalog();
  const regenerate = useRegenerateReport(sessionId);
  const release = useReleaseCandidateReport(sessionId);
  const regenerateStarted = useRef<number>(0);

  const session = detail.data;
  useDocumentTitle(session?.candidate?.full_name ? `${session.candidate.full_name}: report` : "Interview report");

  const notReady = isApiError(report.error) && report.error.status === 404;
  const reportData = report.data;
  const allEvidence = useMemo(() => collectEvidence(evidence.data, questions.data), [evidence.data, questions.data]);
  const index = useMemo(
    () =>
      buildCompetencyIndex({
        roundConfig: session?.round_config,
        scores: scores.data?.competencies,
        digest: session?.state_digest?.competencies,
        evidence: allEvidence,
        catalog: catalog.data,
      }),
    [session?.round_config, session?.state_digest?.competencies, scores.data?.competencies, allEvidence, catalog.data],
  );
  const view = useMemo(
    () =>
      buildReportView({
        report: reportData ?? null,
        session: session ?? null,
        scores: scores.data ?? null,
        questions: questions.data,
        evidence: allEvidence,
        coding: coding.data,
        integrity: integrity.data ?? null,
        reviews: reviews.data,
        index,
      }),
    [reportData, session, scores.data, questions.data, allEvidence, coding.data, integrity.data, reviews.data, index],
  );

  // After "Regenerate", poll until a newer version is ready (or give up after a few minutes).
  useEffect(() => {
    if (awaitingVersion === null || !reportData) return;
    if (reportData.version > awaitingVersion && reportData.status === "ready") {
      setAwaitingVersion(null);
      toast.success(`Report version ${reportData.version} is ready`);
    } else if (Date.now() - regenerateStarted.current > REGENERATE_TIMEOUT_MS) {
      setAwaitingVersion(null);
      toast.message("Still generating. The new version will appear here when it's ready.");
    }
  }, [reportData, awaitingVersion]);

  const mode = (reportData?.candidate_report_mode ?? session?.candidate_report_mode ?? null) as CandidateReportMode | null;
  const candidateReport = session?.reports?.candidate ?? null;
  const released = candidateReport?.released_to_candidate ?? false;
  const inProgress = isInProgress(session?.status);
  const versions = reportData?.versions ?? [];
  const generating = awaitingVersion !== null || reportData?.status === "pending" || reportData?.status === "generating";

  const startRegenerate = async () => {
    const ok = await confirm({
      title: "Regenerate this report?",
      description: "A new version is built from the latest scores and evidence, with a fresh written summary. Earlier versions stay available.",
      confirmLabel: "Regenerate report",
    });
    if (!ok) return;
    regenerateStarted.current = Date.now();
    regenerate.mutate(undefined, {
      onSuccess: () => {
        if (requestedVersion) setParams({}, { replace: true });
        setAwaitingVersion(reportData?.version ?? 0);
      },
    });
  };

  const startRelease = async () => {
    const never = mode === "never";
    const ok = await confirm({
      title: never ? "Release despite the policy?" : "Release the candidate's report?",
      description: never
        ? "This interview's policy says candidate reports are not shared. Releasing overrides that for this candidate only, and the override is recorded. The candidate report never includes the AI suggestion, integrity details or reviewer notes."
        : "The candidate will be able to read their feedback report in the candidate portal. It never includes the AI suggestion, integrity details or reviewer notes.",
      confirmLabel: never ? "Override and release" : "Release report",
      tone: never ? "danger" : "default",
    });
    if (!ok) return;
    release.mutate({ acknowledgePolicyOverride: never });
  };

  const artifactId = reportData?.artifact_media_id ?? null;
  const [downloading, setDownloading] = useState(false);
  const downloadArtifact = async () => {
    if (!artifactId) return;
    setDownloading(true);
    try {
      const signed = await api.get<MediaUrl>(`/media/${artifactId}/url`);
      const link = document.createElement("a");
      link.href = signed.url;
      link.rel = "noopener";
      link.download = reportFileName(view, "html");
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      toast.error("Couldn't download the report file", { description: errorMessage(error) });
    } finally {
      setDownloading(false);
    }
  };

  const exportJson = () => {
    downloadJson(
      {
        exported_at: new Date().toISOString(),
        session_id: sessionId,
        report: reportData ?? null,
        view,
        note: "AI-assisted report. A person makes the hiring decision.",
      },
      reportFileName(view),
    );
  };

  if (detail.isPending) {
    return (
      <PageContainer>
        <Skeleton className="mb-6 h-10 w-full" />
        <Skeleton className="mx-auto h-[70vh] w-full max-w-[900px]" />
      </PageContainer>
    );
  }
  if (detail.isError || !session) {
    return (
      <PageContainer>
        <QueryError error={detail.error} title="Couldn't load this interview" onRetry={() => void detail.refetch()} />
      </PageContainer>
    );
  }

  const releaseControl = (() => {
    if (!canRelease) return null;
    if (mode === "immediate") {
      return (
        <Badge tone={released ? "success" : "neutral"} dot>
          {released ? "Candidate report shared automatically" : "Shared automatically when ready"}
        </Badge>
      );
    }
    if (released) {
      return (
        <Badge tone="success" dot>
          Candidate report released
        </Badge>
      );
    }
    const unavailable = !candidateReport || candidateReport.status !== "ready";
    // In "after review" mode the API requires a recorded human decision first.
    const needsDecision = mode === "after_review" && (session.review_count ?? reviews.data?.length ?? 0) === 0;
    const reason = unavailable ? "The candidate report isn't ready yet." : needsDecision ? "Record a decision on the session first; this job releases reports after review." : null;
    const button = (
      <Button variant="outline" leftIcon={<Send />} onClick={() => void startRelease()} loading={release.isPending} disabled={!!reason || inProgress}>
        Release candidate report
      </Button>
    );
    return reason ? (
      <Tooltip content={reason}>
        <span tabIndex={0}>{button}</span>
      </Tooltip>
    ) : (
      button
    );
  })();

  return (
    <PageContainer width="full" className="max-w-[1100px]">
      <div data-print-hide className="sticky top-0 z-20 -mx-4 mb-6 border-b border-line-subtle bg-surface/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="sm" leftIcon={<ArrowLeft />} asChild>
              <Link to={`/app/sessions/${sessionId}`}>Session</Link>
            </Button>
            <div className="grid min-w-0">
              <span className="truncate text-sm font-semibold text-fg">{session.candidate?.full_name ?? "Candidate"}, interview report</span>
              <span className="truncate text-xs text-fg-muted">
                {reportData ? `Version ${reportData.version}${reportData.generated_at ? `, generated ${formatDateTime(reportData.generated_at)}` : ""}` : "Not generated yet"}
                {mode ? `. Candidate report: ${labelFor(CANDIDATE_REPORT_MODE_LABELS, mode).toLowerCase()}` : ""}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {versions.length > 1 ? (
              <Select
                size="sm"
                className="w-40"
                aria-label="Report version"
                value={String(requestedVersion ?? reportData?.version ?? "")}
                onValueChange={(v) => setParams(Number(v) === Math.max(...versions.map((x) => x.version)) ? {} : { version: v }, { replace: true })}
                options={[...versions]
                  .sort((a, b) => b.version - a.version)
                  .map((v, i) => ({ value: String(v.version), label: `Version ${v.version}${i === 0 ? " (latest)" : ""}`, description: v.generated_at ? formatDateTime(v.generated_at) : v.status }))}
              />
            ) : null}
            {canRegenerate ? (
              <Button variant="outline" leftIcon={generating ? <Spinner size="sm" label={null} /> : <RefreshCcw />} onClick={() => void startRegenerate()} disabled={generating || inProgress || regenerate.isPending}>
                {generating ? "Generating…" : "Regenerate"}
              </Button>
            ) : null}
            {releaseControl}
            {artifactId ? (
              <Button variant="outline" leftIcon={<FileDown />} onClick={() => void downloadArtifact()} loading={downloading}>
                HTML file
              </Button>
            ) : null}
            <Button variant="outline" leftIcon={<FileJson />} onClick={exportJson}>
              Export JSON
            </Button>
            <Button leftIcon={<Printer />} onClick={() => window.print()}>
              Print or save PDF
            </Button>
          </div>
        </div>
      </div>

      <div data-print-hide className="mx-auto mb-6 grid max-w-[900px] gap-3">
        {session.status === "completing" ? (
          <Callout tone="info" icon={<Spinner size="sm" label={null} />} title="Final scores and the report are being prepared">
            The interview has ended. Below is a draft from the evidence evaluated so far; it updates when processing finishes.
          </Callout>
        ) : inProgress ? (
          <Callout tone="signal" title="This interview is still running">
            The report is generated after the interview ends. What you see below is a live draft from the evidence so far.
          </Callout>
        ) : notReady ? (
          <Callout
            tone="info"
            title="No report generated yet"
            actions={
              canRegenerate ? (
                <Button size="sm" onClick={() => void startRegenerate()} loading={regenerate.isPending} disabled={generating}>
                  Generate report
                </Button>
              ) : undefined
            }
          >
            Reports are built automatically once scoring finishes. This draft uses the current scores and evidence; the written summary comes with the generated report.
          </Callout>
        ) : report.isError ? (
          <QueryError error={report.error} title="Couldn't load the report" onRetry={() => void report.refetch()} />
        ) : reportData?.status === "failed" ? (
          <Callout tone="danger" title="The last report generation failed">
            {reportData.error ?? "Try regenerating it."}
          </Callout>
        ) : generating ? (
          <Callout tone="info" icon={<Spinner size="sm" label={null} />} title="Generating a new version">
            You can keep reading the current version; it refreshes here when the new one is ready.
          </Callout>
        ) : null}
        {(reportData?.content as Record<string, unknown> | undefined)?.narrative_disagrees ? (
          <Callout tone="neutral" title="The written summary leans differently">
            The narrative model suggested a different outcome than the evidence-based suggestion. The suggestion shown follows the evidence; read both before deciding.
          </Callout>
        ) : null}
      </div>

      {report.isPending && !notReady ? (
        <Skeleton className="mx-auto h-[70vh] w-full max-w-[900px]" />
      ) : view.questions.length === 0 && !reportData && !scores.data ? (
        <EmptyState bordered title="Nothing to report yet" description="Scores, evidence and the report appear as the interview is evaluated." />
      ) : (
        <ReportDocument view={view} index={index} version={reportData?.version} generatedAt={reportData?.generated_at} draft={!reportData || inProgress} />
      )}
    </PageContainer>
  );
}
