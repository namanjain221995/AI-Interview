import { useMemo } from "react";
import { useSearchParams } from "react-router";

import { BarList } from "@/components/charts/BarList";
import { DistributionBar } from "@/components/charts/DistributionBar";
import { HUMAN_DECISION_NOTE } from "@/components/domain/RecommendationBadge";
import { scoreTone, TONE_BG } from "@/components/domain/scoreBands";
import { PageContainer } from "@/components/layout/StaffShell";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatCard, StatGroup } from "@/components/ui/StatCard";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Tooltip } from "@/components/ui/Tooltip";
import { usePermission } from "@/lib/auth";
import { formatCompact, formatLatency, formatMinutes, formatNumber, formatPercent, formatScore, humanize, pluralize } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { INTEGRITY_EVENT_LABELS, INTEGRITY_SEVERITY_LABELS, labelFor, REVIEW_DECISION_LABELS, SESSION_END_REASON_LABELS } from "@/lib/labels";
import type { IntegritySeverity, ReviewDecisionValue, SessionEndReason } from "@/lib/types";
import { cn } from "@/lib/utils";

import {
  useAnalyticsCompetencies,
  useAnalyticsCompletion,
  useAnalyticsFunnel,
  useAnalyticsHints,
  useAnalyticsIntegrity,
  useAnalyticsModelUsage,
  useAnalyticsOverview,
  useAnalyticsScores,
  useRecentSessionsSample,
  useJobOptions,
  type AnalyticsScope,
} from "../api/hooks";
import type { CompletionGroupDTO, ScoreDistributionGroupDTO } from "../api/types";
import { Funnel, ScoreHistogram, ScoreStripPlot } from "../components/charts";
import { QueryError, SegmentedControl } from "../components/primitives";
import { funnelSteps, groupSessions, histogram, integrityByType } from "../lib/analytics";

const PERIODS = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "12 months" },
] as const;
const ALL = "__all";

/** Plain names for model tasks (backend task keys). */
const TASK_LABELS: Record<string, string> = {
  stt: "Speech to text",
  tts: "Text to speech",
  ocr: "Text recognition (OCR)",
  embedding: "Embeddings",
  rerank: "Reranking",
  turn_decision: "Interviewer turns: full decision time",
  answer_assessment: "Quick answer check",
  answer_evaluation: "Answer evaluation",
  code_observation: "Coding comments",
  hint_generation: "Coding hints",
  coding_evaluation: "Coding evaluation",
  interview_planning: "Interview planning",
  opening: "Opening",
  report_generation: "Report writing",
  resume_extraction: "Resume reading",
  profile_analysis: "Profile analysis",
  job_match: "Job match",
  question_generation: "Question generation",
  competency_summary: "Competency summaries",
  memory_summary: "Conversation memory",
  frame_analysis: "Camera frame checks",
  screen_analysis: "Screen checks",
  routing_classification: "Routing",
  document_classification: "Document checks",
};

/** What a task's response time measures, where it is easy to misread as the candidate's wait. */
const TASK_HINTS: Record<string, string> = {
  turn_decision: "Until the whole decision is written. Candidates hear the reply much sooner: speech starts while the model is still writing.",
};

const DECISION_COLORS: Record<string, string> = {
  advance: "var(--success)",
  hold: "var(--warning)",
  needs_more_information: "var(--chart-1)",
  reject: "var(--danger)",
};

const SEVERITY_COLORS: Record<IntegritySeverity, string> = {
  high: "var(--danger)",
  medium: "var(--warning)",
  low: "var(--chart-muted)",
  info: "var(--border-strong)",
};

/** Small-multiple histogram per group (10-point buckets from /analytics/scores). */
function GroupHistograms({ groups }: { groups: ScoreDistributionGroupDTO[] }) {
  const rows = groups.filter((g) => g.count > 0);
  if (rows.length === 0) return <p className="py-6 text-center text-sm text-fg-muted">No scored interviews in this period.</p>;
  return (
    <div className="grid gap-3">
      {rows.map((g) => {
        const max = Math.max(1, ...g.buckets);
        return (
          <div key={g.key} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_3.5rem] items-center gap-4 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_4rem]">
            <span className="grid min-w-0">
              <span className="truncate text-[13px] font-medium text-fg">{g.label}</span>
              <span className="text-[11px] text-fg-subtle">{pluralize(g.count, "interview")}</span>
            </span>
            <span className="flex h-8 items-end gap-[3px]" role="img" aria-label={`${g.label}: median ${formatScore(g.median)}, range ${formatScore(g.min)} to ${formatScore(g.max)}`}>
              {g.buckets.map((count, i) => (
                <Tooltip key={i} content={`${i * 10}–${i === 9 ? 100 : i * 10 + 9}: ${pluralize(count, "interview")}`}>
                  <span tabIndex={-1} className="flex h-full flex-1 items-end">
                    <span className={cn("w-full rounded-t-[2px]", count ? TONE_BG[scoreTone(i * 10 + 5, 100)] : "bg-track")} style={{ height: count ? `${Math.max(10, (count / max) * 100)}%` : "2px", opacity: count ? 0.85 : 1 }} />
                  </span>
                </Tooltip>
              ))}
            </span>
            <span className="grid justify-items-end">
              <span className="text-sm font-semibold text-fg tabular-nums">{formatScore(g.median)}</span>
              <span className="text-[11px] text-fg-subtle">median</span>
            </span>
          </div>
        );
      })}
      <div className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_3.5rem] gap-4 text-[11px] text-fg-subtle sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_4rem]" aria-hidden="true">
        <span />
        <span className="flex justify-between tabular-nums">
          <span>0</span>
          <span>50</span>
          <span>100</span>
        </span>
        <span />
      </div>
    </div>
  );
}

function CompletionTable({ groups, caption }: { groups: CompletionGroupDTO[]; caption: string }) {
  if (groups.length === 0) return <p className="px-5 pb-5 text-sm text-fg-muted">No interviews in this period.</p>;
  return (
    <Table>
      <caption className="sr-only">{caption}</caption>
      <TableHeader>
        <tr>
          <TableHead>{caption.includes("round") ? "Round" : "Job"}</TableHead>
          <TableHead align="right">Started</TableHead>
          <TableHead align="right">Completed</TableHead>
          <TableHead align="right">Rate</TableHead>
          <TableHead align="right" className="max-sm:hidden">
            Typical length
          </TableHead>
        </tr>
      </TableHeader>
      <TableBody>
        {groups.map((g) => (
          <TableRow key={g.key}>
            <TableCell className="min-w-[8rem] py-2.5 font-medium">{g.label}</TableCell>
            <TableCell numeric className="py-2.5">
              {formatNumber(g.started)}
            </TableCell>
            <TableCell numeric className="py-2.5">
              {formatNumber(g.completed)}
            </TableCell>
            <TableCell numeric className="py-2.5">
              {formatPercent(g.completion_rate)}
            </TableCell>
            <TableCell numeric className="py-2.5 max-sm:hidden">
              <span className="block">{formatMinutes(g.median_duration_seconds)}</span>
              {typeof g.p90_duration_seconds === "number" ? (
                <span className="block whitespace-nowrap text-[11px] font-normal text-fg-subtle">9 in 10 under {formatMinutes(g.p90_duration_seconds)}</span>
              ) : null}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Section({ title, description, children, className, action }: { title: string; description?: string; children: React.ReactNode; className?: string; action?: React.ReactNode }) {
  return (
    <Card className={className}>
      <CardHeader title={title} description={description} action={action} />
      <CardContent className="pt-4">{children}</CardContent>
    </Card>
  );
}

function Loading({ height = "h-40" }: { height?: string }) {
  return <Skeleton className={cn("w-full", height)} />;
}

export function AnalyticsView() {
  useDocumentTitle("Analytics");
  const [params, setParams] = useSearchParams();
  const days = Number(params.get("days")) || 30;
  const jobId = params.get("job") ?? undefined;
  const scope: AnalyticsScope = { days, job_id: jobId };
  const canIntegrity = usePermission("integrity:review");

  const setParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const overview = useAnalyticsOverview(scope);
  const funnel = useAnalyticsFunnel(scope);
  const scores = useAnalyticsScores(scope);
  const competencies = useAnalyticsCompetencies(scope);
  const completion = useAnalyticsCompletion(scope);
  const integrity = useAnalyticsIntegrity(scope);
  const hints = useAnalyticsHints(scope);
  const models = useAnalyticsModelUsage(scope);
  const jobs = useJobOptions();
  // Fallback for score distributions when the breakdown endpoint is unavailable.
  const sample = useRecentSessionsSample(days, 200);

  const o = overview.data;
  const steps = funnel.data?.stages.length ? funnelSteps(funnel.data.stages) : [];
  const sampleScores = useMemo(
    () => (sample.data?.items ?? []).filter((s) => (!jobId || s.job_id === jobId) && s.status === "completed" && typeof s.overall_score === "number"),
    [sample.data, jobId],
  );
  const overallBuckets = scores.data?.overall.buckets.length
    ? scores.data.overall.buckets.map((count, i) => ({ min: i * 10, max: i === 9 ? 100 : i * 10 + 10, label: `${i * 10}–${i === 9 ? 100 : i * 10 + 10}`, count }))
    : sampleScores.length
      ? histogram(sampleScores.map((s) => s.overall_score as number))
      : null;
  const decisions = Object.entries(o?.review_decision_distribution ?? {}).map(([key, value]) => ({ key, label: labelFor(REVIEW_DECISION_LABELS, key as ReviewDecisionValue), value, color: DECISION_COLORS[key] }));
  // Competencies never assessed with enough evidence mostly average the neutral starting
  // estimate: list them after the ones with evidence, de-emphasised.
  const noEvidence = (c: { sufficient_rate?: number | null; average_score?: number | null }) =>
    c.average_score == null || c.sufficient_rate === 0;
  const competencyItems = [...(competencies.data?.items ?? [])].sort(
    (a, b) => Number(noEvidence(a)) - Number(noEvidence(b)) || (b.average_score ?? -1) - (a.average_score ?? -1),
  );
  const integrityTypes = integrityByType(integrity.data?.by_type).slice(0, 8);
  const hintDistribution = ["0", "1", "2", "3"].map((k, i) => ({ key: k, label: k === "0" ? "No hints" : pluralize(Number(k), "hint"), value: hints.data?.distribution?.[k] ?? 0, color: ["var(--chart-muted)", "var(--chart-1)", "var(--chart-4)", "var(--chart-2)"][i] }));
  const endReasons = Object.entries(completion.data?.end_reasons ?? {}).map(([key, value], i) => ({ key, label: labelFor(SESSION_END_REASON_LABELS, key as SessionEndReason), value, color: `var(--chart-${(i % 8) + 1})` }));

  return (
    <PageContainer>
      <PageHeader
        title="Analytics"
        description="How interviews are going across your roles: funnel, scores, completion, integrity observations and model performance."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select
              size="sm"
              className="w-56"
              aria-label="Filter by job"
              value={jobId ?? ALL}
              onValueChange={(v) => setParam("job", v === ALL ? undefined : v)}
              options={[{ value: ALL, label: "All jobs" }, ...(jobs.data ?? []).map((j) => ({ value: j.id, label: j.title }))]}
            />
            <SegmentedControl label="Period" value={String(days)} onChange={(v) => setParam("days", v === "30" ? undefined : v)} options={PERIODS} />
          </div>
        }
      />

      {overview.isError && !o ? (
        <QueryError error={overview.error} title="Couldn't load analytics" onRetry={() => void overview.refetch()} />
      ) : (
        <StatGroup className="xl:grid-cols-6 sm:grid-cols-3">
          <StatCard bare label="Interviews completed" value={formatNumber(o?.sessions_completed)} loading={overview.isPending} hint={`of ${formatNumber(o?.sessions_started ?? o?.interviews_invited)} started`} trend={o?.sessions_by_day?.map((d) => d.count)} trendLabel="Completed per day" />
          <StatCard bare label="Completion rate" value={formatPercent(o?.completion_rate)} loading={overview.isPending} hint="Completed of started" />
          <StatCard bare label="Average overall score" value={formatScore(o?.average_overall_score)} unit={typeof o?.average_overall_score === "number" ? "/ 100" : undefined} loading={overview.isPending} hint={typeof o?.average_confidence === "number" ? `${formatPercent(o.average_confidence)} average confidence` : undefined} />
          <StatCard bare label="Average length" value={formatMinutes(o?.average_duration_seconds)} loading={overview.isPending} hint="Interview clock, pauses excluded" />
          <StatCard
            bare
            label="Decisions differing from AI"
            value={formatPercent(o?.ai_override_rate)}
            loading={overview.isPending}
            hint={o?.awaiting_review ? `${o.awaiting_review} awaiting a decision` : "Of human decisions"}
            info="Share of human decisions that went against the AI-assisted suggestion. A healthy rate shows reviewers use their own judgement."
          />
          <StatCard bare label="Integrity observations" value={formatNumber(o?.integrity_events)} loading={overview.isPending} hint={`${formatNumber(o?.integrity_unreviewed)} awaiting review`} />
        </StatGroup>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <Section title="Funnel" description="Candidates at each stage in the period." className="lg:col-span-2">
          {funnel.isPending ? <Loading /> : funnel.isError ? <QueryError error={funnel.error} onRetry={() => void funnel.refetch()} /> : <Funnel steps={steps} />}
        </Section>
        <Section title="Overall scores" description="Completed interviews; dashed lines mark the default guidance bands." className="lg:col-span-3">
          {scores.isPending && sample.isPending ? (
            <Loading />
          ) : overallBuckets ? (
            <ScoreHistogram buckets={overallBuckets} height={150} note={scores.data ? undefined : `Based on ${pluralize(sampleScores.length, "recent interview")}.`} />
          ) : (
            <p className="py-6 text-center text-sm text-fg-muted">No scored interviews in this period.</p>
          )}
        </Section>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Section title="Scores by job" description="Distribution of overall scores per role.">
          {scores.isPending && sample.isPending ? <Loading /> : scores.data ? <GroupHistograms groups={scores.data.by_job} /> : <ScoreStripPlot groups={groupSessions(sampleScores, "job")} />}
        </Section>
        <Section title="Scores by round" description="Comparable round types across roles.">
          {scores.isPending && sample.isPending ? <Loading /> : scores.data ? <GroupHistograms groups={scores.data.by_round} /> : <ScoreStripPlot groups={groupSessions(sampleScores, "round")} />}
        </Section>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <Section title="Competency averages" description="Average score out of 10 and how often the evidence was sufficient." className="lg:col-span-3">
          {competencies.isPending ? (
            <Loading height="h-64" />
          ) : competencies.isError ? (
            <QueryError error={competencies.error} onRetry={() => void competencies.refetch()} />
          ) : (
            <BarList
              items={competencyItems.map((c) => ({
                key: c.competency_key,
                label: c.name ?? humanize(c.competency_key),
                value: c.average_score ?? 0,
                hint: noEvidence(c)
                  ? `${pluralize(c.sessions, "interview")}, not enough evidence yet to compare`
                  : `${pluralize(c.sessions, "interview")}${typeof c.sufficient_rate === "number" ? `, enough evidence in ${formatPercent(c.sufficient_rate)}` : ""}`,
                muted: noEvidence(c),
              }))}
              max={10}
              valueFormatter={(v) => formatScore(v, 10)}
              emptyMessage="No competency scores in this period."
              aria-label="Competency averages"
            />
          )}
        </Section>
        <div className="grid content-start gap-6 lg:col-span-2">
          <Section title="Human decisions" description={HUMAN_DECISION_NOTE}>
            {overview.isPending ? <Loading height="h-20" /> : <DistributionBar segments={decisions} legendLayout="list" emptyMessage="No decisions recorded yet." />}
          </Section>
          <Section title="Coding hints" description="How many hints candidates used per coding exercise.">
            {hints.isPending ? (
              <Loading height="h-20" />
            ) : hints.data ? (
              <div className="grid gap-4">
                <DistributionBar segments={hintDistribution} legendLayout="list" emptyMessage="No coding exercises in this period." />
                <p className="text-xs text-fg-muted">
                  {formatPercent(hints.data.hint_rate)} of {pluralize(hints.data.coding_sessions, "exercise")} used at least one hint; {typeof hints.data.average_hints === "number" ? hints.data.average_hints.toFixed(1) : "—"} on average. Hints lower coding scores slightly and openly; they never fail a candidate.
                </p>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">Not available.</p>
            )}
          </Section>
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Completion by round" description="Started versus completed, and how long interviews take." className="pb-4" />
          {completion.isPending ? <div className="p-5"><Loading /></div> : completion.data ? <CompletionTable groups={completion.data.by_round} caption="Completion by round" /> : <p className="px-5 pb-5 text-sm text-fg-muted">Not available.</p>}
        </Card>
        <Card>
          <CardHeader title="Completion by job" description="The same view per role, with why interviews ended." className="pb-4" />
          {completion.isPending ? <div className="p-5"><Loading /></div> : completion.data ? <CompletionTable groups={completion.data.by_job} caption="Completion by job" /> : <p className="px-5 pb-5 text-sm text-fg-muted">Not available.</p>}
          {endReasons.length ? (
            <div className="grid gap-2 border-t border-line-subtle px-5 py-4">
              <p className="text-xs font-medium text-fg-muted">Why interviews ended</p>
              <DistributionBar segments={endReasons} />
            </div>
          ) : null}
        </Card>
      </div>

      {canIntegrity || integrity.data ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-5">
          <Section title="Integrity observations" description="Observations, not determinations. Rates help tune monitoring policy." className="lg:col-span-3">
            {integrity.isPending ? (
              <Loading />
            ) : integrity.data ? (
              <div className="grid gap-5">
                <dl className="grid grid-cols-3 gap-4">
                  <div className="grid gap-0.5">
                    <dt className="text-xs text-fg-muted">Per interview</dt>
                    <dd className="text-lg font-semibold text-fg tabular-nums">{typeof integrity.data.events_per_session === "number" ? integrity.data.events_per_session.toFixed(2) : "—"}</dd>
                  </div>
                  <div className="grid gap-0.5">
                    <dt className="text-xs text-fg-muted">Interviews flagged for review</dt>
                    <dd className="text-lg font-semibold text-fg tabular-nums">{formatPercent(integrity.data.flag_rate)}</dd>
                  </div>
                  <div className="grid gap-0.5">
                    <dt className="text-xs text-fg-muted">Total observations</dt>
                    <dd className="text-lg font-semibold text-fg tabular-nums">{formatNumber(integrity.data.events_total)}</dd>
                  </div>
                </dl>
                <BarList items={integrityTypes.map((t) => ({ key: t.type, label: labelFor(INTEGRITY_EVENT_LABELS, t.type as never), value: t.count }))} color="var(--chart-muted)" emptyMessage="No observations in this period." aria-label="Observations by type" />
              </div>
            ) : (
              <p className="text-sm text-fg-muted">Not available.</p>
            )}
          </Section>
          <Section title="By severity" description="Higher severity means a policy asked for a person to look, not that something happened." className="lg:col-span-2">
            {integrity.data ? (
              <DistributionBar
                segments={(["high", "medium", "low", "info"] as IntegritySeverity[]).map((s) => ({ key: s, label: INTEGRITY_SEVERITY_LABELS[s], value: integrity.data!.by_severity?.[s] ?? 0, color: SEVERITY_COLORS[s] }))}
                legendLayout="list"
              />
            ) : integrity.isPending ? (
              <Loading height="h-24" />
            ) : (
              <p className="text-sm text-fg-muted">Not available.</p>
            )}
          </Section>
        </div>
      ) : null}

      <Card className="mt-6">
        <CardHeader
          title="Model usage and latency"
          description="Calls to the AI services behind interviews, with error rates and response times."
          action={
            models.data?.totals && typeof models.data.totals.calls === "number" ? (
              <span className="text-xs text-fg-muted tabular-nums">{formatCompact(models.data.totals.calls as number)} calls in total</span>
            ) : null
          }
          className="pb-4"
        />
        {models.isPending ? (
          <div className="p-5">
            <Loading />
          </div>
        ) : models.isError ? (
          <div className="p-5">
            <QueryError error={models.error} onRetry={() => void models.refetch()} />
          </div>
        ) : (models.data?.items.length ?? 0) === 0 ? (
          <p className="px-5 pb-5 text-sm text-fg-muted">No model calls in this period.</p>
        ) : (
          <Table>
            <caption className="sr-only">Model usage and latency by task</caption>
            <TableHeader>
              <tr>
                <TableHead>Task</TableHead>
                <TableHead className="max-sm:hidden">Service</TableHead>
                <TableHead align="right">Calls</TableHead>
                <TableHead align="right">Errors</TableHead>
                <TableHead align="right">Median</TableHead>
                <TableHead align="right">Slowest 5%</TableHead>
                <TableHead align="right" className="max-md:hidden">
                  First token
                </TableHead>
                <TableHead align="right" className="max-lg:hidden">
                  Tokens
                </TableHead>
              </tr>
            </TableHeader>
            <TableBody>
              {[...(models.data?.items ?? [])]
                .sort((a, b) => b.calls - a.calls)
                .map((m) => {
                  const rate = m.error_rate ?? (m.calls ? m.errors / m.calls : null);
                  return (
                    <TableRow key={`${m.task}-${m.endpoint}`}>
                      <TableCell className="py-2.5 font-medium">
                        <span className="block">{TASK_LABELS[m.task] ?? humanize(m.task)}</span>
                        {TASK_HINTS[m.task] ? <span className="block max-w-[22rem] text-[11px] font-normal text-fg-subtle">{TASK_HINTS[m.task]}</span> : null}
                      </TableCell>
                      <TableCell className="py-2.5 text-fg-muted max-sm:hidden">{m.endpoint}</TableCell>
                      <TableCell numeric className="py-2.5">
                        {formatNumber(m.calls)}
                      </TableCell>
                      <TableCell numeric className={cn("py-2.5", rate !== null && rate > 0.02 ? "text-warning-fg" : "text-fg-muted")}>
                        {rate === null ? "—" : formatPercent(rate, rate < 0.01 && rate > 0 ? 1 : 0)}
                      </TableCell>
                      <TableCell numeric className="py-2.5">
                        {formatLatency(m.p50_ms)}
                      </TableCell>
                      <TableCell numeric className="py-2.5">
                        {formatLatency(m.p95_ms)}
                      </TableCell>
                      <TableCell numeric className="py-2.5 text-fg-muted max-md:hidden">
                        {formatLatency(m.avg_ttft_ms)}
                      </TableCell>
                      <TableCell numeric className="py-2.5 text-fg-muted max-lg:hidden">
                        {m.prompt_tokens + m.completion_tokens ? formatCompact(m.prompt_tokens + m.completion_tokens) : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
            </TableBody>
          </Table>
        )}
      </Card>
    </PageContainer>
  );
}
