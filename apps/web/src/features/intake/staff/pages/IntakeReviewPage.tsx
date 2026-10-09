import { ClipboardX } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useDocumentTitle } from "@/lib/hooks";

import { useIntakeDetail, useIntakeEvaluation } from "../api";
import { IntakeLoadError } from "../components/IntakeStates";
import { DecisionTab } from "../components/review/DecisionTab";
import { EmbeddedSessionTab } from "../components/review/EmbeddedSessionTab";
import { OverviewTab } from "../components/review/OverviewTab";
import { ReadingTab } from "../components/review/ReadingTab";
import { ReviewHeader } from "../components/review/ReviewHeader";
import { ScorecardTab } from "../components/review/ScorecardTab";
import { StagesTab } from "../components/review/StagesTab";
import { TopicTab } from "../components/review/TopicTab";
import { readTab, REVIEW_TAB_LABELS, REVIEW_TABS, type ReviewTab } from "../lib/review";

function CountBadge({ count, label, tone = "warning" }: { count: number; label: string; tone?: "warning" | "neutral" }) {
  return (
    <span
      aria-label={label}
      className={
        tone === "warning"
          ? "flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-warning-soft px-1 text-2xs font-semibold text-warning-fg tabular-nums ring-1 ring-warning-line ring-inset"
          : "text-xs text-fg-subtle tabular-nums"
      }
    >
      {count}
    </span>
  );
}

/** /app/intake/:intakeId: everything a reviewer needs, one tab per question they ask. */
export default function IntakeReviewPage() {
  const { intakeId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = readTab(params.get("tab"));
  const version = Number(params.get("version")) || null;
  const detail = useIntakeDetail(intakeId);
  const pinned = useIntakeEvaluation(intakeId, version, { enabled: Boolean(version) });
  useDocumentTitle(detail.data ? `${detail.data.candidate.full_name}: intake review` : "Intake review");

  if (detail.isPending) {
    return (
      <PageContainer>
        <div className="grid grid-cols-1 gap-6" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-14 w-96 max-w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </PageContainer>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <PageContainer>
        <IntakeLoadError
          error={detail.error}
          title="Couldn't load this intake interview"
          onRetry={() => void detail.refetch()}
          notFound={
            <EmptyState
              bordered
              icon={<ClipboardX />}
              title="This intake interview doesn't exist or was removed"
              description="Check the link, or find it in the list."
              actions={
                <Button size="sm" variant="outline" asChild>
                  <Link to="/app/intake">Back to intake interviews</Link>
                </Button>
              }
            />
          }
        />
      </PageContainer>
    );
  }

  const data = detail.data;
  const evaluation = version ? (pinned.data ?? null) : data.evaluation;
  const openItems = data.review_items.filter((i) => !i.resolution).length;
  const awaitingDecision = Boolean(data.session && ["completing", "completed"].includes(data.session.status) && data.decisions.length === 0);
  const nowElapsed = data.session?.elapsed_seconds ?? null;

  const select = (next: string) => {
    const search = new URLSearchParams();
    search.set("tab", next);
    if (version) search.set("version", String(version));
    setParams(search, { preventScrollReset: true });
  };

  const badge = (key: ReviewTab) => {
    if (key === "overview" && openItems) return <CountBadge count={openItems} label={`${openItems} open review items`} />;
    if (key === "integrity" && data.integrity.unreviewed) return <CountBadge count={data.integrity.unreviewed} label={`${data.integrity.unreviewed} unreviewed observations`} tone="neutral" />;
    if (key === "decision" && awaitingDecision) return <span className="size-1.5 rounded-full bg-warning" aria-label="Awaiting a decision" />;
    return null;
  };

  return (
    <PageContainer>
      <div data-testid="intake-review-page" data-intake-id={data.id}>
        <ReviewHeader detail={data} evaluation={evaluation} />
        {version ? (
          <Callout tone="info" className="mb-5" title={`Showing evaluation version ${version}`}>
            Decisions and review items always refer to the latest evaluation. <Link to={`/app/intake/${data.id}?tab=${tab}`}>Show the latest</Link>
          </Callout>
        ) : null}
        <Tabs value={tab} onValueChange={select}>
          <TabsList aria-label="Intake review sections">
            {REVIEW_TABS.map((key) => (
              <TabsTrigger key={key} value={key} data-testid={`intake-tab-${key}`}>
                {REVIEW_TAB_LABELS[key]}
                {badge(key)}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="overview">
            <OverviewTab detail={data} nowElapsed={nowElapsed} />
          </TabsContent>
          <TabsContent value="stages">
            <StagesTab detail={data} focusQuestion={params.get("q")} focusAnswer={params.get("a")} />
          </TabsContent>
          <TabsContent value="reading">
            <ReadingTab detail={{ ...data, evaluation }} />
          </TabsContent>
          <TabsContent value="topic">
            <TopicTab detail={{ ...data, evaluation }} />
          </TabsContent>
          <TabsContent value="scorecard">
            <ScorecardTab detail={data} evaluation={evaluation} />
          </TabsContent>
          <TabsContent value="recording">
            <EmbeddedSessionTab sessionId={data.session?.id ?? null} kind="recording" />
          </TabsContent>
          <TabsContent value="integrity">
            <EmbeddedSessionTab sessionId={data.session?.id ?? null} kind="integrity" />
          </TabsContent>
          <TabsContent value="decision">
            <DecisionTab detail={data} evaluation={data.evaluation} />
          </TabsContent>
        </Tabs>
      </div>
    </PageContainer>
  );
}
