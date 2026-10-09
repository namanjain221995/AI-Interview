import { VideoOff } from "lucide-react";
import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router";

import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";

import { useSessionDetail } from "@/features/recruiter/review/api/hooks";
import type { SessionDetailView } from "@/features/recruiter/review/api/types";
import { QueryError } from "@/features/recruiter/review/components/primitives";
import { SessionDataProvider, useSessionData, type SessionNav, type SessionTarget } from "@/features/recruiter/review/session/context";
import { IntegrityTab } from "@/features/recruiter/review/session/tabs/IntegrityTab";
import { RecordingTab } from "@/features/recruiter/review/session/tabs/RecordingTab";

import { OBSERVATIONS_NOTE } from "../../labels";

/**
 * The existing session tabs, embedded (design 11.1): they read the session context,
 * so this provides it from the session detail. Navigation requests from inside the
 * tabs open the full session page.
 */
function Provided({ session, kind }: { session: SessionDetailView; kind: "recording" | "integrity" }) {
  const data = useSessionData(session);
  const navigate = useNavigate();
  const go = useCallback(
    (target: SessionTarget) => {
      const params = new URLSearchParams({ tab: target.tab });
      if (target.question) params.set("q", target.question);
      if (target.competency) params.set("c", target.competency);
      if (typeof target.t === "number") params.set("t", String(Math.round(target.t)));
      if (target.answer) params.set("a", target.answer);
      navigate(`/app/sessions/${session.id}?${params.toString()}`);
    },
    [navigate, session.id],
  );
  const nav = useMemo<SessionNav>(() => ({ tab: kind, question: null, competency: null, t: null, answer: null, go }), [kind, go]);
  return <SessionDataProvider value={{ ...data, nav }}>{kind === "recording" ? <RecordingTab /> : <IntegrityTab />}</SessionDataProvider>;
}

export function EmbeddedSessionTab({ sessionId, kind }: { sessionId: string | null; kind: "recording" | "integrity" }) {
  const detail = useSessionDetail(sessionId ?? "");
  if (!sessionId) {
    return (
      <Card>
        <EmptyState
          icon={<VideoOff />}
          title={kind === "recording" ? "No recording yet" : "No integrity observations yet"}
          description={kind === "recording" ? "The recording starts with the interview, when the candidate agreed to be recorded." : `Observations appear once the interview starts. ${OBSERVATIONS_NOTE}`}
        />
      </Card>
    );
  }
  if (detail.isPending) return <Skeleton className="h-80 w-full" />;
  if (detail.isError || !detail.data) {
    return (
      <Card className="p-5">
        <QueryError error={detail.error} title="Couldn't load the session" onRetry={() => void detail.refetch()} />
      </Card>
    );
  }
  return <Provided session={detail.data} kind={kind} />;
}
