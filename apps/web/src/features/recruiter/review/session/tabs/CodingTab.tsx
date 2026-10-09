import { ChevronDown, ChevronRight, Lightbulb, Mic, MousePointerClick } from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { ScoreBar } from "@/components/domain/ScoreBar";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { EmptyState } from "@/components/ui/EmptyState";
import { Markdown } from "@/components/ui/Markdown";
import { Slider } from "@/components/ui/Slider";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { formatClock, formatDuration, formatLatency, formatNumber, humanize, pluralize, truncate } from "@/lib/format";
import { CODE_LANGUAGE_LABELS, CODING_SESSION_STATUS_LABELS, labelFor } from "@/lib/labels";
import type { CodeRunRecord, CodeSnapshotRecord, CodingSessionDetail, SnapshotTrigger } from "@/lib/types";
import { cn } from "@/lib/utils";

import { CodeDiff } from "../../components/CodeDiff";
import { assistanceFactor } from "../../lib/coding";
import { Fact, FactGrid, PanelSkeleton, QueryError, SegmentedControl } from "../../components/primitives";
import { useSessionContext } from "../context";

const TRIGGER_LABELS: Record<SnapshotTrigger, string> = {
  autosave: "Autosave",
  run: "Ran the code",
  submit: "Submitted",
  language_change: "Changed language",
  hint: "Took a hint",
};

const RUN_KIND_LABELS = { run: "Run", test: "Tests", submit: "Submission" } as const;

function stringify(value: unknown): string {
  if (value === undefined) return "—";
  try {
    return truncate(typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value), 160);
  } catch {
    return String(value);
  }
}

/** Extra fields the coding API adds to the evaluation and runs. */
interface EvaluationExtras {
  raw_score_estimate?: number | null;
  score_cap?: number | null;
  hints_used?: number;
  assistance_factor?: number | null;
  adjusted_score?: number | null;
  needs_human_review?: boolean;
}
type RunView = CodeRunRecord & { platform_fault?: boolean; system_run?: boolean };

const END_REASON_LABELS: Record<string, string> = {
  submitted: "Submitted by the candidate",
  time_limit: "Time limit reached",
  interviewer_moved_on: "Interviewer moved on",
  session_ended: "Interview ended",
};

function Evaluation({ coding }: { coding: CodingSessionDetail }) {
  const e = coding.evaluation as CodingSessionDetail["evaluation"] & EvaluationExtras;
  const factor = typeof e.assistance_factor === "number" ? e.assistance_factor : assistanceFactor(coding.hints_used);
  const estimate = typeof e.raw_score_estimate === "number" ? e.raw_score_estimate : typeof e.score_estimate === "number" ? e.score_estimate : null;
  const counted = typeof e.adjusted_score === "number" ? e.adjusted_score : estimate !== null ? estimate * factor : null;
  const capped = typeof e.score_cap === "number" && estimate !== null && estimate > e.score_cap;
  const dims: Array<[string, number | null | undefined]> = [
    ["Code quality", e.code_quality],
    ["Problem solving", e.problem_solving],
    ["Edge cases", e.edge_case_handling],
    ["Testing approach", e.testing_approach],
    ["Explaining the approach", e.communication],
  ];
  if (!estimate && !e.correctness_summary) {
    return <p className="text-sm text-fg-muted">{coding.status === "active" ? "The exercise is still in progress." : "The coding evaluation hasn't finished yet."}</p>;
  }
  return (
    <div className="grid min-w-0 grid-cols-1 gap-5">
      {e.needs_human_review ? (
        <Callout tone="warning" title="Flagged for a human look">
          The coding evaluation had low confidence or a platform problem. Read the code and runs before relying on this score.
        </Callout>
      ) : null}
      {estimate !== null && counted !== null ? (
        <div className="grid grid-cols-1 gap-2 rounded-md bg-surface-subtle p-4 ring-1 ring-line-subtle ring-inset">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-fg">
            <span className="text-[1.5rem] leading-7 font-semibold tracking-[-0.02em] tabular-nums">{counted.toFixed(1)}</span>
            <span className="text-fg-subtle">/10 counted</span>
          </div>
          <p className="text-[13px] text-fg-muted tabular-nums">
            Estimate {estimate.toFixed(1)}
            {capped ? `, capped at ${e.score_cap!.toFixed(1)} for the problem's difficulty` : ""} × assistance factor {factor.toFixed(2)}
            {coding.hints_used ? ` for ${pluralize(coding.hints_used, "hint")}` : " (no hints used)"}
          </p>
          <p className="text-xs text-fg-subtle">Each hint lowers the coding score by 7% of the estimate. Hints are recorded openly and never fail a candidate on their own.</p>
          {typeof e.confidence === "number" ? <ConfidenceMeter value={e.confidence} /> : null}
        </div>
      ) : null}
      {e.correctness_summary ? <p className="text-sm leading-6 text-fg">{e.correctness_summary}</p> : null}
      <FactGrid className="sm:grid-cols-3">
        <Fact label="Tests passed">{typeof e.tests_passed === "number" ? `${e.tests_passed} of ${e.tests_total ?? "—"}` : "—"}</Fact>
        <Fact label="Time complexity">{e.time_complexity ?? "—"}</Fact>
        <Fact label="Space complexity">{e.space_complexity ?? "—"}</Fact>
      </FactGrid>
      {e.complexity_assessment ? <p className="text-[13px] text-fg-muted">{e.complexity_assessment}</p> : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {dims
          .filter(([, v]) => typeof v === "number")
          .map(([label, v]) => (
            <ScoreBar key={label} label={label} value={v as number} max={10} />
          ))}
      </div>
      {e.hints_impact ? (
        <p className="text-[13px] text-fg-muted">
          <span className="font-medium text-fg">Effect of hints: </span>
          {e.hints_impact}
        </p>
      ) : null}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {e.strengths?.length ? (
          <div className="grid grid-cols-1 content-start gap-1">
            <p className="text-xs font-medium text-fg-muted">Strengths</p>
            <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
              {e.strengths.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {e.weaknesses?.length ? (
          <div className="grid grid-cols-1 content-start gap-1">
            <p className="text-xs font-medium text-fg-muted">Areas to probe</p>
            <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
              {e.weaknesses.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
      {e.process_observations?.length ? (
        <div className="grid grid-cols-1 content-start gap-1">
          <p className="text-xs font-medium text-fg-muted">How they worked</p>
          <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
            {e.process_observations.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function Replay({ snapshots, finalCode, language }: { snapshots: CodeSnapshotRecord[]; finalCode: string | null; language: string }) {
  const [position, setPosition] = useState(Math.max(0, snapshots.length - 1));
  const [mode, setMode] = useState<"changes" | "code">("changes");
  if (snapshots.length === 0) {
    return finalCode ? <CodeBlock code={finalCode} language={language} showLineNumbers maxHeight="28rem" /> : <p className="text-sm text-fg-muted">No code was saved.</p>;
  }
  const index = Math.min(position, snapshots.length - 1);
  const current = snapshots[index]!;
  const previous = index > 0 ? snapshots[index - 1]! : null;
  return (
    <div className="grid min-w-0 grid-cols-1 gap-3">
      <div className="grid grid-cols-1 gap-2 rounded-md border border-line-subtle bg-surface-subtle px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
          <span className="font-medium text-fg">
            Snapshot {index + 1} of {snapshots.length}
            <span className="ml-2 font-normal text-fg-muted">
              {TRIGGER_LABELS[current.trigger] ?? current.trigger}
              {typeof current.elapsed_seconds === "number" ? ` at ${formatClock(current.elapsed_seconds)}` : ""}
            </span>
          </span>
          <SegmentedControl
            label="Snapshot view"
            value={mode}
            onChange={setMode}
            options={[
              { value: "changes", label: "Changes" },
              { value: "code", label: "Full code" },
            ]}
          />
        </div>
        {snapshots.length > 1 ? (
          <Slider
            min={0}
            max={snapshots.length - 1}
            step={1}
            value={[index]}
            onValueChange={(v) => setPosition(v[0] ?? 0)}
            thumbLabels={["Snapshot"]}
            formatValue={(v) => `Snapshot ${v + 1} of ${snapshots.length}`}
          />
        ) : null}
        <ol className="flex flex-wrap gap-1" aria-label="Snapshot markers">
          {snapshots.map((s, i) => (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => setPosition(i)}
                aria-label={`Snapshot ${i + 1}: ${TRIGGER_LABELS[s.trigger] ?? s.trigger}`}
                aria-current={i === index ? "step" : undefined}
                className={cn(
                  "h-1.5 w-4 rounded-full transition-colors",
                  i === index ? "bg-primary" : s.trigger === "submit" ? "bg-success/60" : s.trigger === "hint" ? "bg-warning/70" : s.trigger === "run" ? "bg-accent/50" : "bg-line-strong",
                )}
              />
            </li>
          ))}
        </ol>
      </div>
      {mode === "changes" ? (
        <CodeDiff before={previous?.code ?? ""} after={current.code} beforeLabel={previous ? `Snapshot ${index}` : "Empty"} afterLabel={`Snapshot ${index + 1}`} />
      ) : (
        <CodeBlock code={current.code} language={current.language} showLineNumbers maxHeight="28rem" />
      )}
    </div>
  );
}

function RunsTable({ runs }: { runs: RunView[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (runs.length === 0) return <p className="px-5 pb-5 text-sm text-fg-muted">The code was never run.</p>;
  return (
    <Table>
      <TableHeader>
        <tr>
          <TableHead className="w-8">
            <span className="sr-only">Details</span>
          </TableHead>
          <TableHead>Time</TableHead>
          <TableHead>Kind</TableHead>
          <TableHead>Result</TableHead>
          <TableHead align="right">Tests</TableHead>
          <TableHead align="right" className="max-sm:hidden">
            Runtime
          </TableHead>
          <TableHead align="right" className="max-md:hidden">
            Memory
          </TableHead>
        </tr>
      </TableHeader>
      <TableBody>
        {runs.map((run) => {
          const expanded = open === run.id;
          const detailsId = `run-${run.id}`;
          return (
            <Fragment key={run.id}>
              <TableRow interactive onClick={() => setOpen(expanded ? null : run.id)}>
                <TableCell className="py-2.5">
                  <button type="button" aria-expanded={expanded} aria-controls={detailsId} aria-label={expanded ? "Hide run details" : "Show run details"} className="flex items-center text-fg-subtle">
                    {expanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                  </button>
                </TableCell>
                <TableCell className="py-2.5 tabular-nums">{formatClock(run.elapsed_seconds)}</TableCell>
                <TableCell className="py-2.5">
                  <span className="flex flex-wrap items-center gap-1.5">
                    {RUN_KIND_LABELS[run.kind] ?? run.kind}
                    {run.system_run ? (
                      <Badge size="sm" tone="outline">
                        Automatic
                      </Badge>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell className="py-2.5">
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusBadge kind="run" value={run.status} size="sm" />
                    {run.platform_fault ? (
                      <Badge size="sm" tone="warning">
                        Platform issue
                      </Badge>
                    ) : null}
                  </span>
                </TableCell>
                <TableCell numeric className="py-2.5">
                  {run.total_count ? `${run.passed_count}/${run.total_count}` : "—"}
                </TableCell>
                <TableCell numeric className="py-2.5 max-sm:hidden">
                  {formatLatency(run.runtime_ms)}
                </TableCell>
                <TableCell numeric className="py-2.5 max-md:hidden">
                  {run.memory_kb ? `${formatNumber(run.memory_kb / 1024, { maximumFractionDigits: 1 })} MB` : "—"}
                </TableCell>
              </TableRow>
              {expanded ? (
                <tr id={detailsId}>
                  <td colSpan={7} className="border-b border-line-subtle bg-surface-subtle px-5 py-4">
                    <div className="grid grid-cols-1 gap-3">
                      {run.platform_fault ? (
                        <p className="text-[13px] text-warning-fg">This run failed because of a problem on our side, not the candidate's code. It is excluded from the evaluation.</p>
                      ) : null}
                      {run.test_results.length ? (
                        <ul className="grid grid-cols-1 gap-1.5">
                          {run.test_results.map((t) => (
                            <li key={t.id} className="grid grid-cols-1 gap-1 rounded-md bg-surface px-3 py-2 text-[13px] ring-1 ring-line-subtle ring-inset sm:grid-cols-[8rem_5rem_minmax(0,1fr)] sm:items-start sm:gap-3">
                              <span className="flex items-center gap-2">
                                <span className={cn("size-2 rounded-full", t.passed ? "bg-success" : "bg-danger")} aria-hidden="true" />
                                <span className="font-medium text-fg">{t.passed ? "Passed" : "Failed"}</span>
                              </span>
                              <span className="text-xs text-fg-muted">{t.hidden ? "Hidden test" : "Visible test"}</span>
                              <span className="grid grid-cols-1 min-w-0 gap-0.5 font-mono text-xs text-fg-muted">
                                {t.args !== undefined ? <span className="truncate">input {stringify(t.args)}</span> : null}
                                {t.expected !== undefined ? <span className="truncate">expected {stringify(t.expected)}</span> : null}
                                {t.actual !== undefined && !t.passed ? <span className="truncate text-danger-fg">got {stringify(t.actual)}</span> : null}
                                {t.error ? <span className="truncate text-danger-fg">{truncate(t.error, 200)}</span> : null}
                                {t.args === undefined && t.expected === undefined && !t.error ? <span className="font-sans">{t.id}</span> : null}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {run.compile_output ? <CodeBlock code={run.compile_output} title="Compiler output" maxHeight="12rem" wrap /> : null}
                      {run.stderr ? <CodeBlock code={run.stderr} title="Error output" maxHeight="12rem" wrap /> : null}
                      {run.stdout ? <CodeBlock code={run.stdout} title="Program output" maxHeight="12rem" wrap /> : null}
                      {!run.test_results.length && !run.compile_output && !run.stderr && !run.stdout ? <p className="text-[13px] text-fg-muted">No output.</p> : null}
                    </div>
                  </td>
                </tr>
              ) : null}
            </Fragment>
          );
        })}
      </TableBody>
    </Table>
  );
}

function CodingSessionPanel({ coding }: { coding: CodingSessionDetail }) {
  const [promptOpen, setPromptOpen] = useState(false);
  const [codeView, setCodeView] = useState<"final" | "replay">("final");
  const language = labelFor(CODE_LANGUAGE_LABELS, coding.language);
  const usedSeconds = coding.ended_at && coding.started_at ? (Date.parse(coding.ended_at) - Date.parse(coding.started_at)) / 1000 : null;
  const finalCode = coding.final_code ?? coding.snapshots[coding.snapshots.length - 1]?.code ?? null;
  const hints = useMemo(() => [...coding.hints].sort((a, b) => a.level - b.level), [coding.hints]);
  const endReason = (coding as CodingSessionDetail & { end_reason?: string | null }).end_reason ?? null;

  return (
    <div className="grid grid-cols-1 gap-6">
      <Card>
        <CardHeader
          title={coding.question?.title ?? "Coding exercise"}
          description={
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {coding.question?.difficulty ? <DifficultyBadge value={coding.question.difficulty} size="sm" /> : null}
              <span>{language}</span>
              <span>
                {labelFor(CODING_SESSION_STATUS_LABELS, coding.status)}
                {endReason ? ` (${(END_REASON_LABELS[endReason] ?? humanize(endReason)).toLowerCase()})` : ""}
              </span>
              <span className="tabular-nums">
                {usedSeconds !== null ? `${formatDuration(usedSeconds)} of ${formatDuration(coding.time_limit_seconds)}` : `${formatDuration(coding.time_limit_seconds)} allowed`}
              </span>
            </span>
          }
          action={
            <span className="flex flex-wrap items-center gap-2">
              <Badge tone={coding.hints_used ? "warning" : "neutral"} size="sm" icon={<Lightbulb aria-hidden="true" />}>
                {coding.hints_used} of {coding.hints_available} hints
              </Badge>
              {coding.paste_event_count ? (
                <Badge tone="neutral" size="sm">
                  {pluralize(coding.paste_event_count, "paste")}
                </Badge>
              ) : null}
            </span>
          }
        />
        {/* grid-cols-1 + min-w-0: a wide code block in the problem must scroll inside its
            column, never push it under the evaluation column (text drawn over text). */}
        <CardContent className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="grid min-w-0 grid-cols-1 content-start gap-3">
            <h3 className="text-sm font-semibold text-fg">Problem</h3>
            {coding.question?.prompt_markdown ? (
              <div className="relative min-w-0">
                <div className={cn(!promptOpen && "max-h-56 overflow-hidden")}>
                  <Markdown size="sm">{coding.question.prompt_markdown}</Markdown>
                </div>
                {!promptOpen ? <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-surface to-transparent" /> : null}
                <button type="button" className="relative mt-2 text-xs font-medium text-accent hover:underline" onClick={() => setPromptOpen((v) => !v)}>
                  {promptOpen ? "Show less" : "Show the full problem"}
                </button>
              </div>
            ) : (
              <p className="text-sm text-fg-muted">The problem statement isn't available.</p>
            )}
          </div>
          <div className="grid min-w-0 grid-cols-1 content-start gap-3">
            <h3 className="text-sm font-semibold text-fg">Coding evaluation</h3>
            <Evaluation coding={coding} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader
          title="Code"
          description={codeView === "final" ? "What the candidate ended with." : "Step through saved snapshots and see what changed each time."}
          action={
            <SegmentedControl
              label="Code view"
              value={codeView}
              onChange={setCodeView}
              options={[
                { value: "final", label: "Final code" },
                { value: "replay", label: `Replay (${coding.snapshots.length})` },
              ]}
            />
          }
        />
        <CardContent className="pt-4">
          {codeView === "final" ? (
            finalCode ? (
              <CodeBlock code={finalCode} language={coding.language} showLineNumbers maxHeight="32rem" />
            ) : (
              <p className="text-sm text-fg-muted">No code was saved.</p>
            )
          ) : (
            <Replay snapshots={coding.snapshots} finalCode={finalCode} language={coding.language} />
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-5">
        <Card className="min-w-0 lg:col-span-3">
          <CardHeader title="Runs and test results" description={`${pluralize(coding.runs.length, "run")}. Open a run to see each test.`} className="pb-4" />
          <RunsTable runs={coding.runs as RunView[]} />
        </Card>
        <Card className="min-w-0 lg:col-span-2">
          <CardHeader title="Hints used" description="Shown to the candidate on request, in order." />
          <CardContent>
            {hints.length ? (
              <ol className="grid grid-cols-1 gap-3">
                {hints.map((hint) => (
                  <li key={hint.id} className="grid grid-cols-1 gap-1.5 rounded-md border border-line-subtle px-3.5 py-3">
                    <span className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                      <Badge tone="warning" size="sm">
                        Hint {hint.level}
                      </Badge>
                      <span className="tabular-nums">{formatClock(hint.elapsed_seconds)}</span>
                      <span className="inline-flex items-center gap-1">
                        {hint.requested_via === "voice" ? <Mic className="size-3.5" aria-hidden="true" /> : <MousePointerClick className="size-3.5" aria-hidden="true" />}
                        {hint.requested_via === "voice" ? "Asked by voice" : "Hint button"}
                      </span>
                    </span>
                    <p className="text-[13px] leading-5 text-fg">{hint.content}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-fg-muted">No hints were used.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export function CodingTab() {
  const { coding, codingQuery } = useSessionContext();
  if (codingQuery.isPending) return <Card><PanelSkeleton rows={5} /></Card>;
  if (codingQuery.isError && coding.length === 0) {
    return (
      <Card className="p-5">
        <QueryError error={codingQuery.error} title="Couldn't load the coding exercise" onRetry={() => void codingQuery.refetch()} />
      </Card>
    );
  }
  if (coding.length === 0) {
    return (
      <Card>
        <EmptyState title="No coding exercise in this interview" description="When a round includes coding, the problem, every code snapshot, test runs and hints show up here." />
      </Card>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-10">
      {coding.map((c) => (
        <CodingSessionPanel key={c.id} coding={c} />
      ))}
    </div>
  );
}
