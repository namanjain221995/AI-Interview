/**
 * Dashboard sections below the KPI strip. Lazy-loaded by DashboardView so the
 * greeting and KPIs paint first: live interviews, recent sessions, decisions
 * waiting, funnel, score distribution, AI suggestion mix and quick links.
 */

import { ArrowRight, BarChart3, BriefcaseBusiness, ClipboardCheck, MonitorPlay, Radio, ShieldCheck, Users } from "lucide-react";
import { Link } from "react-router";

import { DistributionBar } from "@/components/charts/DistributionBar";
import { HUMAN_DECISION_NOTE } from "@/components/domain/RecommendationBadge";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePermission } from "@/lib/auth";
import { formatMinutes, formatRelativeOrDate, pluralize } from "@/lib/format";
import { RECOMMENDATION_SHORT_LABELS } from "@/lib/labels";
import type { Recommendation, SessionSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useAnalyticsFunnel, useRecentSessionsSample, useSessionList } from "../api/hooks";
import type { AnalyticsOverviewView } from "../api/types";
import { Funnel, ScoreHistogram } from "../components/charts";
import { bucketsFromServer, CORE_FUNNEL, funnelSteps, histogram } from "../lib/analytics";
import { ScoreCell, SessionsTable } from "../sessions/SessionsTable";

const RECOMMENDATION_ORDER: Recommendation[] = ["strong_proceed", "proceed", "human_review", "borderline_evidence", "insufficient_evidence"];
const RECOMMENDATION_COLORS: Record<Recommendation, string> = {
  strong_proceed: "var(--success)",
  proceed: "var(--chart-1)",
  // Distinct from "not enough evidence" (grey): this one asks a person to look closely.
  human_review: "var(--chart-7)",
  borderline_evidence: "var(--warning)",
  insufficient_evidence: "var(--border-strong)",
};

function LiveNow({ sessions, loading, failed }: { sessions: SessionSummary[]; loading: boolean; failed: boolean }) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Live now
            {sessions.length ? (
              <Badge tone="signal" size="sm" dot pulse>
                {sessions.length}
              </Badge>
            ) : null}
          </span>
        }
        description="Interviews in progress. Watching is read-only; the candidate is not notified."
        action={
          sessions.length ? (
            <Button variant="ghost" size="sm" asChild>
              <Link to="/app/sessions?view=live">All live</Link>
            </Button>
          ) : null
        }
      />
      <CardContent>
        {loading ? (
          <div className="grid gap-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : failed ? (
          <p className="py-3 text-sm text-fg-muted">Live interviews aren't available right now.</p>
        ) : sessions.length === 0 ? (
          <p className="flex items-center gap-2 py-3 text-sm text-fg-muted">
            <Radio className="size-4 text-fg-subtle" aria-hidden="true" />
            No interviews are running right now.
          </p>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {sessions.slice(0, 4).map((s) => (
              <li key={s.id}>
                <Link
                  to={`/app/sessions/${s.id}?tab=live`}
                  className="flex items-center gap-3 rounded-md border border-signal-line/70 bg-signal-soft/40 px-3.5 py-3 transition-colors hover:border-signal-line hover:bg-signal-soft"
                >
                  <Avatar name={s.candidate?.full_name ?? "Candidate"} size="md" />
                  <span className="grid min-w-0 flex-1">
                    <span className="truncate text-sm font-medium text-fg">{s.candidate?.full_name ?? "Candidate"}</span>
                    <span className="truncate text-xs text-fg-muted">
                      {s.job?.title}
                      {s.round ? `, ${s.round.name}` : ""}
                    </span>
                  </span>
                  <span className="grid shrink-0 justify-items-end gap-0.5">
                    <span className={cn("text-xs font-medium", s.status === "paused" ? "text-warning-fg" : "text-signal-fg")}>{s.status === "paused" ? "Paused" : "Watch"}</span>
                    <span className="text-xs text-fg-muted tabular-nums">{formatMinutes(s.elapsed_seconds)} in</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function AwaitingDecision({ sessions, loading, failed }: { sessions: SessionSummary[]; loading: boolean; failed: boolean }) {
  return (
    <Card>
      <CardHeader
        title="Waiting for your decision"
        description="Completed interviews without a human decision, oldest first."
        action={
          <Button variant="ghost" size="sm" asChild>
            <Link to="/app/sessions?view=needs_review">View all</Link>
          </Button>
        }
      />
      <CardContent className="pt-3">
        {loading ? (
          <div className="grid gap-2">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        ) : failed ? (
          <p className="py-3 text-sm text-fg-muted">Decisions waiting for you aren't available right now.</p>
        ) : sessions.length === 0 ? (
          <p className="flex items-center gap-2 py-3 text-sm text-fg-muted">
            <ClipboardCheck className="size-4 text-fg-subtle" aria-hidden="true" />
            You're all caught up.
          </p>
        ) : (
          <ul className="grid divide-y divide-line-subtle">
            {sessions.slice(0, 5).map((s) => (
              <li key={s.id}>
                <Link to={`/app/sessions/${s.id}?tab=review`} className="-mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-surface-hover">
                  <Avatar name={s.candidate?.full_name ?? "Candidate"} size="sm" />
                  <span className="grid min-w-0 flex-1">
                    <span className="truncate text-[13px] font-medium text-fg">{s.candidate?.full_name ?? "Candidate"}</span>
                    <span className="truncate text-xs text-fg-muted">{s.job?.title}</span>
                  </span>
                  {s.recommendation ? <span className="hidden text-xs text-fg-muted sm:inline">{RECOMMENDATION_SHORT_LABELS[s.recommendation]}</span> : null}
                  <ScoreCell score={s.overall_score} compact insufficient={s.recommendation === "insufficient_evidence"} />
                  <span className="w-20 shrink-0 text-right text-xs whitespace-nowrap text-fg-subtle sm:w-28">{formatRelativeOrDate(s.ended_at ?? s.started_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function QuickLinks() {
  const canIntegrity = usePermission("integrity:review");
  const canJobs = usePermission("jobs:write");
  const links = [
    { to: "/app/sessions?view=needs_review", label: "Review finished interviews", icon: <ClipboardCheck />, show: true },
    { to: "/app/integrity", label: "Integrity review queue", icon: <ShieldCheck />, show: canIntegrity },
    { to: "/app/analytics", label: "Hiring analytics", icon: <BarChart3 />, show: true },
    { to: "/app/candidates", label: "Candidates", icon: <Users />, show: true },
    { to: "/app/jobs/new", label: "Create a job", icon: <BriefcaseBusiness />, show: canJobs },
  ].filter((l) => l.show);
  return (
    <Card>
      <CardHeader title="Quick links" />
      <CardContent className="pt-2">
        <ul className="grid gap-0.5">
          {links.map((l) => (
            <li key={l.to}>
              <Link to={l.to} className="-mx-2 flex items-center gap-2.5 rounded-md px-2 py-2 text-[13px] font-medium text-fg hover:bg-surface-hover [&_svg]:size-4 [&_svg]:text-fg-muted">
                {l.icon}
                {l.label}
                <ArrowRight className="ml-auto opacity-50" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export interface DashboardBodyProps {
  overview: AnalyticsOverviewView | undefined;
  overviewPending: boolean;
  unavailable: boolean;
}

export default function DashboardBody({ overview: o, overviewPending, unavailable }: DashboardBodyProps) {
  const funnel = useAnalyticsFunnel(30);
  const recent = useSessionList({ limit: 8, offset: 0 }, { refetchInterval: 30_000 });
  const live = useSessionList({ status: ["live", "paused"], limit: 6, offset: 0 }, { refetchInterval: 15_000 });
  const awaiting = useSessionList({ status: ["completed"], needs_review: true, limit: 20, offset: 0 });
  const sample = useRecentSessionsSample(30, 200);

  const liveSessions = (live.data?.items ?? []).filter((s) => s.status === "live" || s.status === "paused");
  const waiting = (awaiting.data?.items ?? []).filter((s) => s.status === "completed" && !s.review_decision).sort((a, b) => (a.ended_at ?? "").localeCompare(b.ended_at ?? ""));
  const buckets =
    bucketsFromServer(o?.score_distribution) ??
    (sample.data ? histogram(sample.data.items.filter((s) => s.status === "completed" && typeof s.overall_score === "number").map((s) => s.overall_score as number)) : null);
  const steps = funnel.data?.stages.length
    ? funnelSteps(funnel.data.stages, { only: CORE_FUNNEL })
    : o
      ? funnelSteps([
          { stage: "invited", count: o.interviews_invited },
          { stage: "completed", count: o.interviews_completed },
        ])
      : [];
  const distribution = RECOMMENDATION_ORDER.map((key) => ({
    key,
    label: RECOMMENDATION_SHORT_LABELS[key],
    value: o?.recommendation_distribution?.[key] ?? 0,
    color: RECOMMENDATION_COLORS[key],
  }));

  return (
      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 xl:col-span-2">
          <LiveNow sessions={liveSessions} loading={live.isPending} failed={live.isError} />
          <Card>
            <CardHeader
              title="Recent sessions"
              description="The latest interviews across every role."
              action={
                <Button variant="ghost" size="sm" asChild>
                  <Link to="/app/sessions">View all</Link>
                </Button>
              }
              className="pb-4"
            />
            <SessionsTable
              variant="compact"
              rows={recent.data?.items}
              loading={recent.isPending}
              fetching={recent.isFetching && !recent.isPending}
              error={recent.error}
              onRetry={() => void recent.refetch()}
              caption="Recent interview sessions"
              empty={
                <EmptyState
                  size="sm"
                  icon={<MonitorPlay />}
                  title="No interviews yet"
                  description="When a candidate starts an interview, it shows up here with its status and score."
                  actions={
                    <Button variant="outline" size="sm" asChild>
                      <Link to="/app/candidates">Go to candidates</Link>
                    </Button>
                  }
                />
              }
            />
          </Card>
          <AwaitingDecision sessions={waiting} loading={awaiting.isPending} failed={awaiting.isError} />
        </div>

        <div className="grid min-w-0 grid-cols-1 content-start gap-6">
          <Card>
            <CardHeader title="Funnel" description="Last 30 days, from invitation to a human decision." />
            <CardContent>
              {funnel.isPending && overviewPending ? <Skeleton className="h-36 w-full" /> : steps.length ? <Funnel steps={steps} /> : <p className="py-4 text-sm text-fg-muted">Not available yet</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader title="Overall scores" description="Completed interviews, last 30 days. Dashed lines mark the default guidance bands." />
            <CardContent>
              {buckets ? <ScoreHistogram buckets={buckets} note={o?.score_distribution ? undefined : sample.data ? `Based on the latest ${pluralize(sample.data.items.length, "session")}.` : undefined} /> : overviewPending || sample.isPending ? <Skeleton className="h-36 w-full" /> : <p className="py-4 text-sm text-fg-muted">Not available yet</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader title="AI suggestion mix" description={`Completed interviews, last ${o?.period_days ?? 30} days.`} />
            <CardContent className="grid gap-4">
              <DistributionBar segments={distribution} legendLayout="list" emptyMessage={unavailable ? "Not available yet" : "No completed interviews yet"} />
              <p className="border-t border-line-subtle pt-3 text-xs text-fg-muted">{HUMAN_DECISION_NOTE}</p>
            </CardContent>
          </Card>
          <QuickLinks />
        </div>
      </div>
  );
}
