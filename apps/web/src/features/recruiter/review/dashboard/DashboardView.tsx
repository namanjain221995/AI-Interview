/**
 * Recruiter dashboard: greeting, actions and the KPI strip render first; the
 * sections below load as a separate chunk (DashboardBody).
 */

import { ArrowRight, Plus, UserPlus } from "lucide-react";
import { lazy, Suspense, type ReactNode } from "react";
import { Link } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard, StatGroup } from "@/components/ui/StatCard";
import { useAuthStore, usePermission } from "@/lib/auth";
import { formatNumber, formatScore, greeting } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";

import { useAnalyticsOverview, useSessionList } from "../api/hooks";

const DashboardBody = lazy(() => import("./DashboardBody"));

function BodySkeleton() {
  return (
    <div className="mt-6 grid gap-6 xl:grid-cols-3" aria-hidden="true">
      <div className="grid content-start gap-6 xl:col-span-2">
        <Skeleton className="h-36 w-full rounded-lg" />
        <Skeleton className="h-96 w-full rounded-lg" />
      </div>
      <div className="grid content-start gap-6">
        <Skeleton className="h-56 w-full rounded-lg" />
        <Skeleton className="h-56 w-full rounded-lg" />
      </div>
    </div>
  );
}

function KpiLink({ to, children, label }: { to: string; children: ReactNode; label: string }) {
  return (
    <Link to={to} aria-label={label} className="group relative block transition-colors hover:bg-surface-subtle focus-visible:outline-2 focus-visible:-outline-offset-2">
      {children}
      <ArrowRight className="absolute top-5 right-5 size-4 text-fg-subtle opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" />
    </Link>
  );
}

export function DashboardView() {
  useDocumentTitle("Dashboard");
  const user = useAuthStore((s) => s.user);
  const canWriteJobs = usePermission("jobs:write");
  const canWriteCandidates = usePermission("candidates:write");
  const canIntegrity = usePermission("integrity:review");
  // Profiles without a name fall back to the email; greet without it then.
  const firstName = user?.full_name && !user.full_name.includes("@") ? (user.full_name.split(/\s+/)[0] ?? "") : "";

  const overview = useAnalyticsOverview(30);
  const live = useSessionList({ status: ["live", "paused"], limit: 6, offset: 0 }, { refetchInterval: 15_000 });

  const o = overview.data;
  const unavailable = overview.isError;
  const statHint = (text: string) => (unavailable ? "Not available yet" : text);
  const liveSessions = (live.data?.items ?? []).filter((s) => s.status === "live" || s.status === "paused");
  const liveCount = live.data ? liveSessions.length : (o?.sessions_live ?? null);
  const completedWeek = o?.completed_this_week ?? null;

  return (
    <PageContainer>
      <PageHeader
        title={firstName ? `${greeting()}, ${firstName}` : greeting()}
        description="Interviews, decisions and integrity observations across your open roles."
        actions={
          <>
            {canWriteCandidates ? (
              <Button variant="outline" leftIcon={<UserPlus />} asChild>
                <Link to="/app/candidates?new=1">Add candidate</Link>
              </Button>
            ) : null}
            {canWriteJobs ? (
              <Button leftIcon={<Plus />} asChild>
                <Link to="/app/jobs/new">Create job</Link>
              </Button>
            ) : null}
          </>
        }
      />

      <StatGroup className="xl:grid-cols-5">
        <KpiLink to="/app/sessions?view=live" label="Live interviews: open the live view">
          <StatCard
            bare
            label="Live now"
            value={liveCount === null ? (live.isPending ? "…" : "—") : formatNumber(liveCount)}
            loading={live.isPending && overview.isPending}
            hint={liveCount ? "Read-only monitoring available" : statHint("No interviews running")}
          />
        </KpiLink>
        <StatCard
          bare
          label="Completed this week"
          value={completedWeek === null ? "—" : formatNumber(completedWeek)}
          loading={overview.isPending}
          trend={o?.sessions_by_day?.slice(-14).map((d) => d.count)}
          trendLabel="Completed interviews per day, last two weeks"
          hint={statHint("Last 7 days")}
        />
        <StatCard
          bare
          label="Average overall score"
          value={unavailable ? "—" : formatScore(o?.average_overall_score)}
          unit={!unavailable && typeof o?.average_overall_score === "number" ? "/ 100" : undefined}
          loading={overview.isPending}
          hint={statHint(`Last ${o?.period_days ?? 30} days`)}
          info="Mean overall score of interviews completed in the period. Scores support, never replace, a reviewer's judgement."
        />
        <KpiLink to="/app/sessions?view=needs_review" label="Interviews waiting for a decision: open the list">
          <StatCard bare label="Waiting for a decision" value={unavailable ? "—" : formatNumber(o?.awaiting_review)} loading={overview.isPending} hint={statHint("Human reviews pending")} />
        </KpiLink>
        {canIntegrity ? (
          <KpiLink to="/app/integrity" label="Integrity observations to review: open the queue">
            <StatCard bare label="Integrity observations to review" value={unavailable ? "—" : formatNumber(o?.integrity_unreviewed)} loading={overview.isPending} hint={statHint("Observations, not verdicts")} />
          </KpiLink>
        ) : (
          <StatCard bare label="Integrity observations to review" value={unavailable ? "—" : formatNumber(o?.integrity_unreviewed)} loading={overview.isPending} hint={statHint("Observations, not verdicts")} />
        )}
      </StatGroup>

      <Suspense fallback={<BodySkeleton />}>
        <DashboardBody overview={o} overviewPending={overview.isPending} unavailable={unavailable} />
      </Suspense>
    </PageContainer>
  );
}
