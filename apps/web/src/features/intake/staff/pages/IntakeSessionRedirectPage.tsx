import { useQuery } from "@tanstack/react-query";
import { ClipboardX } from "lucide-react";
import { Link, Navigate, useParams, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";

import { findIntakeForSession, intakeKeys } from "../api";
import { IntakeLoadError } from "../components/IntakeStates";
import { readTab } from "../lib/review";

/**
 * /app/intake/session/:sessionId: resolves a session to its intake interview and
 * opens the review page (used by the session header and the printable report).
 */
export default function IntakeSessionRedirectPage() {
  const { sessionId = "" } = useParams();
  const [params] = useSearchParams();
  const intake = useQuery({
    queryKey: intakeKeys.bySession(sessionId),
    queryFn: ({ signal }) => findIntakeForSession(sessionId, signal),
    enabled: Boolean(sessionId),
    retry: false,
    staleTime: 5 * 60_000,
  });

  if (intake.data) {
    const tab = readTab(params.get("tab"));
    return <Navigate to={`/app/intake/${intake.data.id}${tab !== "overview" ? `?tab=${tab}` : ""}`} replace />;
  }
  return (
    <PageContainer>
      {intake.error ? (
        <IntakeLoadError error={intake.error} title="Couldn't open the intake review" onRetry={() => void intake.refetch()} />
      ) : intake.isSuccess && !intake.data ? (
        <EmptyState
          bordered
          icon={<ClipboardX />}
          title="No intake record for this session"
          description="This session isn't linked to an intake interview, or the intake was removed."
          actions={
            <Button size="sm" variant="outline" asChild>
              <Link to={`/app/sessions/${sessionId}`}>Back to the session</Link>
            </Button>
          }
        />
      ) : (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-fg-muted" aria-live="polite">
          <Spinner size="sm" label={null} />
          Opening the intake review…
        </div>
      )}
    </PageContainer>
  );
}
