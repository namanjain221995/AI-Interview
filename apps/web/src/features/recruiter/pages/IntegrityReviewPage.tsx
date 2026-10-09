import { IntegrityQueueView } from "@/features/recruiter/review/integrity/IntegrityQueueView";

/** /app/integrity — organization-wide integrity observations awaiting human review (bulk review, evidence viewer). */
export default function IntegrityReviewPage() {
  return <IntegrityQueueView />;
}
