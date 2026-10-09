import { useParams } from "react-router";

import { RecruiterReportView } from "@/features/recruiter/review/report/RecruiterReportView";

/** /app/reports/:sessionId — the complete, printable recruiter report with regenerate, release, export and print. */
export default function RecruiterReportPage() {
  const { sessionId = "" } = useParams();
  return <RecruiterReportView key={sessionId} sessionId={sessionId} />;
}
