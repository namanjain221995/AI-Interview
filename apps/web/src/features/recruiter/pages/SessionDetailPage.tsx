import { useParams } from "react-router";

import { SessionDetailView } from "@/features/recruiter/review/session/SessionDetailView";

/** /app/sessions/:sessionId — the "why" behind every score, plus the live monitor for running interviews. */
export default function SessionDetailPage() {
  const { sessionId = "" } = useParams();
  return <SessionDetailView key={sessionId} sessionId={sessionId} />;
}
