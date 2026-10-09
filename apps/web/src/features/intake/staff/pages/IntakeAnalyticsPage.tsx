import { BarChart3 } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useSearchParams } from "react-router";

import { BarList } from "@/components/charts/BarList";
import { ChartFrame } from "@/components/charts/ChartFrame";
import { PageContainer } from "@/components/layout/StaffShell";
import { Card, CardContent } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatMinutes, formatNumber, formatPercent } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { Funnel } from "@/features/recruiter/review/components/charts";

import { isIntakeDisabled, useIntakeAnalytics, useIntakeConfig } from "../api";
import { IntakeDisabledState, IntakeLoadError } from "../components/IntakeStates";
import { useReviewerOptions } from "../components/reviewers";
import { TARGET_ROLE_LABELS } from "../labels";
import { averageText, bandRows, competencyRows, decisionRows, funnelSteps, overrideText, roleLabel, stageRows, summarize, trainingRows, weaknessRows, type CountRow } from "../lib/analytics";
import { formatScore5, formatTotal } from "../lib/decimals";
import { TARGET_ROLES, type TargetRole } from "../types";

const ALL = "__all";
const PERIODS = [7, 30, 90, 365] as const;

function Kpi({ label, value, unit, hint }: { label: string; value: ReactNode; unit?: string; hint?: ReactNode }) {
  return (
    <div className="grid grid-cols-1 min-w-0 content-start gap-1 border-r border-b border-line-subtle px-4 py-3">
      <dt className="truncate text-xs text-fg-muted">{label}</dt>
      <dd className="flex items-baseline gap-1 text-xl leading-7 font-semibold text-fg">
        {value}
        {unit ? <span className="text-xs font-medium text-fg-subtle">{unit}</span> : null}
      </dd>
      {hint ? <dd className="truncate text-xs text-fg-subtle">{hint}</dd> : null}
    </div>
  );
}

function BarCard({ title, description, rows, format = (v: number) => formatNumber(v), max, columnLabel = "Count", empty = "No data in this period.", className }: { title: string; description?: string; rows: CountRow[]; format?: (v: number) => string; max?: number; columnLabel?: string; empty?: string; className?: string }) {
  const hasData = rows.some((r) => r.value > 0);
  return (
    <Card className={className}>
      <CardContent className="py-5">
        <ChartFrame
          title={title}
          description={description}
          summary={rows.length ? `${rows.map((r) => `${r.label}: ${format(r.value)}`).join(", ")}.` : empty}
          table={{ caption: title, columns: [{ key: "label", label: "Item" }, { key: "value", label: columnLabel, numeric: true }], rows: rows.map((r) => ({ label: r.label, value: format(r.value) })) }}
        >
          {hasData ? <BarList items={rows.map((r) => ({ key: r.key, label: r.label, value: r.value, hint: r.hint }))} valueFormatter={format} max={max} aria-label={title} /> : <p className="py-6 text-center text-sm text-fg-muted">{empty}</p>}
        </ChartFrame>
      </CardContent>
    </Card>
  );
}

/** /app/intake/analytics: how intake interviews go, from invitation to decision. */
export default function IntakeAnalyticsPage() {
  useDocumentTitle("Intake analytics");
  const [params, setParams] = useSearchParams();
  const days = Number(params.get("days")) || 30;
  const jobId = params.get("job") ?? undefined;
  const role = (params.get("role") as TargetRole | null) ?? undefined;
  const reviewerId = params.get("reviewer") ?? undefined;
  const query = useMemo(() => ({ days: PERIODS.includes(days as (typeof PERIODS)[number]) ? days : 30, job_id: jobId, target_role: role, reviewer_id: reviewerId }), [days, jobId, role, reviewerId]);
  const analytics = useIntakeAnalytics(query);
  const config = useIntakeConfig();
  const data = analytics.data;
  const reviewers = useReviewerOptions((data?.reviewers ?? []).map((r) => ({ id: r.id, full_name: r.full_name })));

  const setParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const header = <PageHeader breadcrumbs={[{ label: "Intake interviews", to: "/app/intake" }, { label: "Analytics" }]} title="Intake analytics" description="From invitation to decision. Bands are AI recommendations; outcomes are human decisions." />;

  if (isIntakeDisabled(analytics.error) || isIntakeDisabled(config.error)) {
    return (
      <PageContainer>
        {header}
        <div data-testid="intake-analytics-page">
          <IntakeDisabledState />
        </div>
      </PageContainer>
    );
  }

  const completionRate = data && data.funnel.started ? data.funnel.completed / data.funnel.started : null;

  return (
    <PageContainer>
      {header}
      <div data-testid="intake-analytics-page" className="grid grid-cols-1 gap-5">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            size="sm"
            className="w-full sm:w-40"
            aria-label="Period"
            data-testid="intake-analytics-period"
            value={String(query.days)}
            onValueChange={(v) => setParam("days", v === "30" ? undefined : v)}
            options={PERIODS.map((p) => ({ value: String(p), label: p === 365 ? "Last 12 months" : `Last ${p} days` }))}
          />
          <Select
            size="sm"
            className="w-full sm:w-56"
            aria-label="Job"
            value={jobId ?? ALL}
            onValueChange={(v) => setParam("job", v === ALL ? undefined : v)}
            options={[{ value: ALL, label: "All jobs" }, ...(config.data?.jobs ?? []).map((j) => ({ value: j.job_id, label: j.title }))]}
          />
          <Select
            size="sm"
            className="w-full sm:w-48"
            aria-label="Target role"
            value={role ?? ALL}
            onValueChange={(v) => setParam("role", v === ALL ? undefined : v)}
            options={[{ value: ALL, label: "Any target role" }, ...TARGET_ROLES.map((r) => ({ value: r, label: TARGET_ROLE_LABELS[r] }))]}
          />
          <Select
            size="sm"
            className="w-full sm:w-48"
            aria-label="Reviewer"
            value={reviewerId ?? ALL}
            onValueChange={(v) => setParam("reviewer", v === ALL ? undefined : v)}
            options={[{ value: ALL, label: "Any reviewer" }, ...reviewers]}
          />
        </div>

        {analytics.isError && !data ? (
          <IntakeLoadError error={analytics.error} title="Couldn't load intake analytics" onRetry={() => void analytics.refetch()} />
        ) : !data ? (
          <div className="grid grid-cols-1 gap-5" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-24 w-full" />
            <div className="grid gap-5 lg:grid-cols-2">
              <Skeleton className="h-72" />
              <Skeleton className="h-72" />
            </div>
          </div>
        ) : data.funnel.invitations === 0 ? (
          <Card>
            <EmptyState icon={<BarChart3 />} title="No intake interviews in this period" description="Try a longer period or clear the filters." />
          </Card>
        ) : (
          <div className={cn("grid grid-cols-1 gap-5 transition-opacity", analytics.isFetching && "opacity-70")}>
            <div className="overflow-hidden rounded-lg border border-line bg-surface">
              <dl className="-mr-px -mb-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="intake-analytics-kpis">
                <Kpi label="Invited" value={formatNumber(data.funnel.invitations)} />
                <Kpi label="Completed" value={formatNumber(data.funnel.completed)} hint={completionRate !== null ? `${formatPercent(completionRate)} of those who started` : undefined} />
                <Kpi label="Incomplete" value={formatNumber(data.funnel.incomplete)} />
                <Kpi label="Average score" value={data.average_weighted_total ? formatTotal(data.average_weighted_total) : "—"} unit={data.average_weighted_total ? "/ 5" : undefined} hint="AI measure" />
                <Kpi label="Average duration" value={data.average_duration_s ? formatMinutes(data.average_duration_s) : "—"} />
                <Kpi label="Decided" value={formatNumber(data.funnel.decided)} hint={data.overrides.rate !== null ? `${formatPercent(data.overrides.rate)} differ from the AI` : undefined} />
              </dl>
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <Card>
                <CardContent className="py-5">
                  <Funnel steps={funnelSteps(data.funnel)} title="Funnel" description="Each step as a share of the one before." />
                </CardContent>
              </Card>
              <BarCard title="Stage completion" description="Share of interviews that finished each stage." rows={stageRows(data.stage_completion)} format={(v) => formatPercent(v)} max={1} columnLabel="Completed" />
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <BarCard title="AI recommendations" description="Band of each ready evaluation. Advisory only." rows={bandRows(data.bands)} />
              <Card>
                <CardContent className="grid grid-cols-1 gap-3 py-5">
                  <ChartFrame
                    title="Human decisions"
                    description={overrideText(data.overrides, data.funnel.decided)}
                    summary={summarize(decisionRows(data.decisions))}
                    table={{ caption: "Human decisions", columns: [{ key: "label", label: "Outcome" }, { key: "value", label: "Decisions", numeric: true }], rows: decisionRows(data.decisions).map((r) => ({ label: r.label, value: formatNumber(r.value) })) }}
                  >
                    {decisionRows(data.decisions).some((r) => r.value) ? <BarList items={decisionRows(data.decisions)} aria-label="Human decisions" /> : <p className="py-6 text-center text-sm text-fg-muted">No decisions in this period.</p>}
                  </ChartFrame>
                </CardContent>
              </Card>
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <BarCard
                title="Competency averages"
                description="Mean AI score per competency, 1 to 5."
                rows={competencyRows(data.competency_averages)}
                format={(v) => (v ? `${formatScore5(v)} / 5` : "—")}
                max={5}
                columnLabel="Average"
              />
              <BarCard title="Common technical weaknesses" description="How often each pattern was flagged for review." rows={weaknessRows(data.technical_weaknesses)} empty="None flagged in this period." />
            </div>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <BarCard title="Training needs" description="Modules recommended in training plans." rows={trainingRows(data.training_needs)} empty="No training plans in this period." />
              <Card>
                <CardContent className="py-5">
                  <ChartFrame
                    title="By target role"
                    tableToggle={false}
                    summary={data.by_target_role.map((r) => `${roleLabel(r.target_role, r.label)}: ${r.count}`).join(", ") || "No data."}
                    table={{ caption: "By target role", columns: [{ key: "role", label: "Target role" }, { key: "count", label: "Interviews", numeric: true }, { key: "avg", label: "Average score", numeric: true }], rows: data.by_target_role.map((r) => ({ role: roleLabel(r.target_role, r.label), count: formatNumber(r.count), avg: averageText(r.average_weighted_total) })) }}
                  >
                    {data.by_target_role.length ? (
                      <table className="w-full border-collapse text-sm">
                        <thead>
                          <tr className="text-xs text-fg-muted">
                            <th scope="col" className="border-b border-line py-2 text-left font-medium">Target role</th>
                            <th scope="col" className="border-b border-line py-2 text-right font-medium">Interviews</th>
                            <th scope="col" className="border-b border-line py-2 text-right font-medium">Average score</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.by_target_role.map((r) => (
                            <tr key={r.target_role || "none"}>
                              <td className="border-b border-line-subtle py-2 text-fg">{roleLabel(r.target_role, r.label)}</td>
                              <td className="border-b border-line-subtle py-2 text-right text-fg tabular-nums">{formatNumber(r.count)}</td>
                              <td className="border-b border-line-subtle py-2 text-right text-fg-muted tabular-nums">{averageText(r.average_weighted_total)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <p className="py-6 text-center text-sm text-fg-muted">No data in this period.</p>
                    )}
                  </ChartFrame>
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
