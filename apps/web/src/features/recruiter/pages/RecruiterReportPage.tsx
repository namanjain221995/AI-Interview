import { useParams, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Skeleton } from "@/components/ui/Skeleton";

import { IntakeReportScreen } from "@/features/intake/staff/report/IntakeReportScreen";
import { useSessionDetail, useSessionReport } from "@/features/recruiter/review/api/hooks";
import { RecruiterReportView } from "@/features/recruiter/review/report/RecruiterReportView";

/** True for intake reports (`content.kind === "intake"`, docs/design/INTAKE_INTERVIEW.md 9.1). */
function isIntakeContent(content: unknown): boolean {
  return Boolean(content) && typeof content === "object" && (content as { kind?: unknown }).kind === "intake";
}

/**
 * Chooses the report layout: intake sessions (round type, or an intake report document)
 * get the intake report; every other round keeps the recruiter report.
 */
function ReportDispatch({ sessionId }: { sessionId: string }) {
  const [params] = useSearchParams();
  const version = Number(params.get("version")) || null;
  const detail = useSessionDetail(sessionId);
  const report = useSessionReport(sessionId, "recruiter", { version });
  if (detail.isPending) {
    return (
      <PageContainer>
        <Skeleton className="mb-6 h-10 w-full" />
        <Skeleton className="mx-auto h-[70vh] w-full max-w-[900px]" />
      </PageContainer>
    );
  }
  const intake = detail.data?.round?.round_type === "intake" || isIntakeContent(report.data?.content);
  return intake ? <IntakeReportScreen sessionId={sessionId} /> : <RecruiterReportView sessionId={sessionId} />;
}

/** /app/reports/:sessionId — the complete, printable recruiter report with regenerate, release, export and print. */
export default function RecruiterReportPage() {
  const { sessionId = "" } = useParams();
  return <ReportDispatch key={sessionId} sessionId={sessionId} />;
}
