import { Compass, Lock } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { isApiError } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import type { SessionDetailView as SessionDetailData } from "../api/types";

import { useSessionDetail } from "../api/hooks";
import { QueryError } from "../components/primitives";
import { SessionDataProvider, useSessionData, useSessionNav, type SessionTab } from "./context";
import { SessionHeader } from "./SessionHeader";
import { CodingTab } from "./tabs/CodingTab";
import { CompetenciesTab } from "./tabs/CompetenciesTab";
import { IntegrityTab } from "./tabs/IntegrityTab";
import { LiveTab } from "./tabs/LiveTab";
import { OverviewTab } from "./tabs/OverviewTab";
import { PlanTab } from "./tabs/PlanTab";
import { QuestionsTab } from "./tabs/QuestionsTab";
import { RecordingTab } from "./tabs/RecordingTab";
import { ReviewTab } from "./tabs/ReviewTab";
import { TimelineTab } from "./tabs/TimelineTab";
import { TranscriptTab } from "./tabs/TranscriptTab";

function Count({ value, tone = "neutral" }: { value: number; tone?: "neutral" | "warning" }) {
  if (!value) return null;
  return (
    <span
      className={
        tone === "warning"
          ? "rounded-full bg-warning-soft px-1.5 text-[11px] leading-[18px] font-semibold text-warning-fg tabular-nums ring-1 ring-warning-line ring-inset"
          : "rounded-full bg-neutral-soft px-1.5 text-[11px] leading-[18px] font-medium text-fg-muted tabular-nums"
      }
    >
      {value}
    </span>
  );
}

function LoadedSession({ session }: { session: SessionDetailData }) {
  const data = useSessionData(session);
  const nav = useSessionNav(data.watchable ? "live" : "overview");
  useDocumentTitle(session.candidate?.full_name ? `${session.candidate.full_name}: session` : "Session");

  const showLive = data.watchable || nav.tab === "live";
  const unreviewed = data.integrity?.events.filter((e) => e.review_status === "unreviewed").length ?? 0;
  const tabs: Array<{ value: SessionTab; label: string; count?: ReactNode }> = [
    ...(showLive ? [{ value: "live" as const, label: "Live" }] : []),
    { value: "overview", label: "Overview" },
    { value: "transcript", label: "Transcript" },
    { value: "recording", label: "Recording" },
    { value: "questions", label: "Questions & evidence", count: <Count value={data.questions.length} /> },
    { value: "competencies", label: "Competencies" },
    { value: "timeline", label: "Timeline" },
    { value: "coding", label: "Coding", count: <Count value={data.coding.length} /> },
    { value: "integrity", label: "Integrity", count: <Count value={unreviewed} tone="warning" /> },
    { value: "plan", label: "Plan & claims" },
    { value: "review", label: "Review" },
  ];
  const active = nav.tab === "live" && !showLive ? "overview" : nav.tab;

  return (
    <SessionDataProvider value={{ ...data, nav }}>
      <PageContainer>
        <SessionHeader />
        <Tabs value={active} onValueChange={(value) => nav.go({ tab: value as SessionTab }, { replace: true })}>
          <TabsList aria-label="Session sections" className="-mx-1 px-1">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value} data-testid={`session-tab-${tab.value}`}>
                {tab.value === "live" ? <span className="size-1.5 rounded-full bg-signal animate-pulse-soft" aria-hidden="true" /> : null}
                {tab.label}
                {tab.count}
              </TabsTrigger>
            ))}
          </TabsList>
          {showLive ? (
            <TabsContent value="live">
              <LiveTab />
            </TabsContent>
          ) : null}
          <TabsContent value="overview">
            <OverviewTab />
          </TabsContent>
          <TabsContent value="transcript">
            <TranscriptTab />
          </TabsContent>
          <TabsContent value="recording">
            <RecordingTab />
          </TabsContent>
          <TabsContent value="questions">
            <QuestionsTab />
          </TabsContent>
          <TabsContent value="competencies">
            <CompetenciesTab />
          </TabsContent>
          <TabsContent value="timeline">
            <TimelineTab />
          </TabsContent>
          <TabsContent value="coding">
            <CodingTab />
          </TabsContent>
          <TabsContent value="integrity">
            <IntegrityTab />
          </TabsContent>
          <TabsContent value="plan">
            <PlanTab />
          </TabsContent>
          <TabsContent value="review">
            <ReviewTab />
          </TabsContent>
        </Tabs>
      </PageContainer>
    </SessionDataProvider>
  );
}

function DetailSkeleton() {
  return (
    <PageContainer>
      <div className="grid gap-4 pb-6" aria-hidden="true">
        <Skeleton className="h-4 w-40" />
        <div className="flex items-center gap-3.5">
          <Skeleton className="size-14 rounded-full" />
          <div className="grid gap-2">
            <Skeleton className="h-7 w-56" />
            <Skeleton className="h-4 w-96 max-w-full" />
          </div>
        </div>
      </div>
      <Skeleton className="mb-6 h-10 w-full" />
      <div className="grid gap-6">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
      <p className="sr-only" role="status">
        Loading session
      </p>
    </PageContainer>
  );
}

export function SessionDetailView({ sessionId }: { sessionId: string }) {
  const detail = useSessionDetail(sessionId);
  useDocumentTitle("Session");

  if (detail.isPending) return <DetailSkeleton />;
  if (detail.isError) {
    const error = detail.error;
    return (
      <PageContainer>
        {isApiError(error) && error.status === 404 ? (
          <EmptyState
            bordered
            icon={<Compass />}
            title="This session doesn't exist"
            description="It may have been removed, or the link belongs to another organization."
            actions={
              <Button variant="outline" asChild>
                <Link to="/app/sessions">Go to sessions</Link>
              </Button>
            }
          />
        ) : isApiError(error) && error.status === 403 ? (
          <EmptyState bordered icon={<Lock />} title="You don't have access to this session" description="Ask an admin if you need it for your reviews." />
        ) : (
          <QueryError error={error} title="Couldn't load this session" onRetry={() => void detail.refetch()} />
        )}
      </PageContainer>
    );
  }
  return <LoadedSession session={detail.data} />;
}
