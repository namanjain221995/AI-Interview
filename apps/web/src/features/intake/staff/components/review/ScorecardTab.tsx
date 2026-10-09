import { ChevronDown, ChevronRight, CircleDashed, Scale, TriangleAlert, UserRound } from "lucide-react";
import { useState } from "react";

import { INSUFFICIENT_EVIDENCE_HEADLINE, NOT_ENOUGH_EVIDENCE } from "@/components/domain/evidenceCopy";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { AI_RECOMMENDATION_NOTE, BAND_EDGES, BAND_GUIDANCE, BAND_LABELS, competencyLabel } from "../../labels";
import { decimalToNumber, formatHundred, formatScore5, formatTotal, formatWeightPercent, shareOf } from "../../lib/decimals";
import type { CompetencyScore, IntakeDetail, IntakeEvaluation } from "../../types";
import { BandBadge } from "../IntakeBadges";
import { EvidenceChip } from "./EvidenceChip";

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

/** Five notches, filled up to the score (fractions fill part of a notch: 3.4 = three and 40%). */
export function ScoreNotches({ score, label }: { score: string | null; label: string }) {
  const value = decimalToNumber(score);
  if (value === null) {
    return (
      <span className="flex gap-1" role="img" aria-label={`${label}: insufficient evidence`}>
        {[1, 2, 3, 4, 5].map((n) => (
          <span key={n} className="h-2 w-5 rounded-[3px] border border-dashed border-line-strong" />
        ))}
      </span>
    );
  }
  return (
    <span className="flex gap-1" role="img" aria-label={`${label}: ${formatScore5(score)} out of 5`}>
      {[1, 2, 3, 4, 5].map((n) => {
        const fill = Math.max(0, Math.min(1, value - (n - 1)));
        return (
          <span key={n} className="relative h-2 w-5 overflow-hidden rounded-[3px] bg-track">
            <span className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${fill * 100}%` }} />
          </span>
        );
      })}
    </span>
  );
}

/**
 * The weighted total as it is built: each scored competency's contribution
 * (score x weight, from the server) laid end to end on the 1-5 scale, with the
 * band edges marked. The total itself always comes from the server.
 */
function SumLine({ evaluation }: { evaluation: IntakeEvaluation }) {
  const scored = evaluation.competencies.filter((c) => c.status === "scored" && c.weighted);
  const total = evaluation.weighted_total ?? null;
  const summary = scored.map((c) => `${c.label || competencyLabel(c.key)} ${formatTotal(c.weighted)}`).join(", ");
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="relative">
        <div
          className="relative h-3 w-full overflow-hidden rounded-full bg-track"
          role="img"
          aria-label={`Weighted total ${total ? formatTotal(total) : "not available"} out of 5, built from ${summary || "no scored competencies"}.`}
        >
          <div className="absolute inset-0 flex gap-[2px]">
            {scored.map((c) => (
              <Tooltip key={c.key} content={`${c.label || competencyLabel(c.key)}: ${formatScore5(c.score)} × ${formatWeightPercent(c.weight)} = ${formatTotal(c.weighted)}`}>
                <span className="h-full min-w-[3px] bg-primary/85 first:rounded-l-full hover:bg-primary" style={{ width: `${shareOf(c.weighted) * 100}%` }} tabIndex={-1} />
              </Tooltip>
            ))}
          </div>
        </div>
        {/* Band edges as ticks under the bar, so they never read as segment gaps. */}
        {BAND_EDGES.slice(1).map((edge) => (
          <span key={edge.band} className="absolute top-full mt-0.5 h-1.5 w-px bg-fg-subtle" style={{ left: `${(edge.from / 5) * 100}%` }} aria-hidden="true" />
        ))}
      </div>
      <div className="relative mt-1 h-8 text-2xs text-fg-subtle" aria-hidden="true">
        {[0, 2.5, 3.3, 4.2, 5].map((mark) => (
          <span key={mark} className={cn("absolute top-0 tabular-nums", mark === 0 ? "left-0" : mark === 5 ? "right-0" : "-translate-x-1/2")} style={mark === 0 || mark === 5 ? undefined : { left: `${(mark / 5) * 100}%` }}>
            {mark.toFixed(mark % 1 ? 2 : 0)}
          </span>
        ))}
        {BAND_EDGES.map((edge, i) => {
          const from = i === 0 ? 0 : edge.from;
          const to = BAND_EDGES[i + 1]?.from ?? 5;
          return (
            <span key={edge.band} className="absolute bottom-0 truncate text-center" style={{ left: `${(from / 5) * 100}%`, width: `${((to - from) / 5) * 100}%` }}>
              {BAND_LABELS[edge.band]}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function CompetencyRow({ competency, intakeId }: { competency: CompetencyScore; intakeId: string }) {
  const [open, setOpen] = useState(false);
  const label = competency.label || competencyLabel(competency.key);
  const scored = competency.status === "scored";
  const subA = (competency.sub_scores.drill_a as { score?: unknown } | undefined)?.score;
  const subB = (competency.sub_scores.drill_b as { score?: unknown } | undefined)?.score;
  return (
    <li data-testid={`intake-competency-${competency.key}`} data-score={competency.score ?? ""} data-status={competency.status} className="grid grid-cols-1 gap-3 py-4 first:pt-0 last:pb-0">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,1fr)_7.5rem_6rem_5.5rem]">
        <div className="grid grid-cols-1 min-w-0 gap-0.5">
          <h3 className="truncate text-sm font-semibold text-fg">{label}</h3>
          <p className="text-xs text-fg-muted">Weight {formatWeightPercent(competency.weight)}</p>
        </div>
        <span className="justify-self-end sm:justify-self-start">
          <ScoreNotches score={scored ? competency.score : null} label={label} />
        </span>
        <p className="text-sm sm:text-right">
          {scored ? (
            <>
              <span className="text-[17px] font-semibold text-fg tabular-nums">{formatScore5(competency.score)}</span>
              <span className="text-xs text-fg-subtle"> / 5</span>
            </>
          ) : (
            <span className="inline-flex items-center gap-1 text-[13px] font-medium text-fg-muted">
              <CircleDashed className="size-3.5" aria-hidden="true" />
              {NOT_ENOUGH_EVIDENCE}
            </span>
          )}
        </p>
        <p className="text-right text-sm text-fg-muted tabular-nums" aria-label={scored ? `Contributes ${formatTotal(competency.weighted)} to the total` : "Contributes nothing to the total"}>
          {scored ? `+${formatTotal(competency.weighted)}` : "—"}
        </p>
      </div>

      {competency.reasoning ? <p className="text-[13px] leading-5 text-fg">{competency.reasoning}</p> : null}
      {competency.key === "technical_reading" && (subA !== undefined || subB !== undefined) ? (
        <p className="text-xs text-fg-muted tabular-nums">
          Drill A delivery {String(subA ?? "—")} / 5, Drill B relay {String(subB ?? "—")} / 5, combined as 0.4 × Drill A + 0.6 × Drill B.
        </p>
      ) : null}
      {competency.evidence.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={`Evidence for ${label}`}>
          {competency.evidence.slice(0, 6).map((ref, i) => (
            <li key={i} className="max-w-full min-w-0">
              <EvidenceChip intakeId={intakeId} evidence={ref} />
            </li>
          ))}
        </ul>
      ) : null}
      {!scored && competency.missing_evidence.length ? (
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg">Missing:</span> {competency.missing_evidence.join("; ")}
        </p>
      ) : null}

      <div>
        <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="inline-flex items-center gap-1 rounded-xs text-xs font-medium text-fg-muted hover:text-fg">
          {open ? <ChevronDown className="size-3.5" aria-hidden="true" /> : <ChevronRight className="size-3.5" aria-hidden="true" />}
          Rubric anchors, strengths and improvements
        </button>
        {open ? (
          <div className="mt-2 grid grid-cols-1 gap-3 rounded-md bg-surface-subtle px-3.5 py-3 ring-1 ring-line-subtle ring-inset sm:grid-cols-2">
            <dl className="grid grid-cols-1 content-start gap-1.5 sm:col-span-2">
              {(["1", "3", "5"] as const).map((level) =>
                competency.anchors[level] ? (
                  <div key={level} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2 text-[13px]">
                    <dt className="font-semibold text-fg tabular-nums">{level}</dt>
                    <dd className="text-fg-muted">{competency.anchors[level]}</dd>
                  </div>
                ) : null,
              )}
            </dl>
            {competency.strengths.length ? (
              <div className="grid grid-cols-1 content-start gap-1">
                <span className="text-xs font-medium text-fg">Strengths</span>
                <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg-muted">
                  {competency.strengths.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {competency.improvements.length ? (
              <div className="grid grid-cols-1 content-start gap-1">
                <span className="text-xs font-medium text-fg">To improve</span>
                <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg-muted">
                  {competency.improvements.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {scored && competency.missing_evidence.length ? (
              <p className="text-xs text-fg-muted sm:col-span-2">
                <span className="font-medium text-fg">Not covered:</span> {competency.missing_evidence.join("; ")}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    </li>
  );
}

function LatestNotice({ detail, evaluation }: { detail: IntakeDetail; evaluation: IntakeEvaluation | null }) {
  const latest = detail.evaluation_ref;
  if (!latest || !evaluation || latest.id === evaluation.id) return null;
  if (latest.status === "pending" || latest.status === "running") {
    return (
      <Callout tone="info" icon={<Spinner size="sm" label={null} />} title="A new evaluation is running">
        This scorecard is version {evaluation.version}. The new version replaces it here when it's ready.
      </Callout>
    );
  }
  if (latest.status === "failed") {
    return (
      <Callout tone="warning" title="The latest re-evaluation didn't finish">
        This scorecard is version {evaluation.version}, the newest one that completed.
      </Callout>
    );
  }
  return null;
}

export function ScorecardTab({ detail, evaluation }: { detail: IntakeDetail; evaluation: IntakeEvaluation | null }) {
  if (!evaluation || evaluation.status !== "ready") {
    const latest = detail.evaluation_ref?.status ?? evaluation?.status;
    const running = latest === "pending" || latest === "running";
    const failed = latest === "failed" || evaluation?.status === "failed";
    return (
      <Card data-testid="intake-scorecard">
        <EmptyState
          icon={<Scale />}
          title={running ? "The evaluation is running" : failed ? "The evaluation didn't finish" : "No evaluation yet"}
          description={
            running
              ? "Seven evaluators read the interview against the kit's rubric. This takes a few minutes; the page refreshes by itself."
              : failed
                ? `Run it again with Re-evaluate. You can still record a decision from the interview itself.${evaluation?.error ? ` (${evaluation.error})` : ""}`
                : detail.session
                  ? "The scorecard appears after the interview ends and is evaluated."
                  : "The candidate hasn't taken the interview yet."
          }
        />
      </Card>
    );
  }

  const insufficient = evaluation.band === "insufficient_evidence" || !evaluation.weighted_total;
  // Nothing scored at all: there is no number to show, not even a partial one (never a made-up score).
  const noEvidence = insufficient && !evaluation.competencies.some((c) => c.status === "scored");
  const ordered = [...evaluation.competencies].sort((a, b) => (decimalToNumber(b.weight) ?? 0) - (decimalToNumber(a.weight) ?? 0));

  return (
    <div className="grid grid-cols-1 gap-6" data-testid="intake-scorecard">
      <LatestNotice detail={detail} evaluation={evaluation} />
      <Card>
        <CardContent className="grid grid-cols-1 gap-5 py-5">
          <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
            <div className="grid grid-cols-1 gap-1" data-testid="intake-weighted-total" data-total={evaluation.weighted_total ?? ""}>
              <span className="text-xs text-fg-muted">{noEvidence ? "Weighted total" : insufficient ? (evaluation.partial_label ?? "Partial total") : "Weighted total"}</span>
              {noEvidence ? (
                <span className="text-lg font-semibold text-warning-fg" data-testid="intake-no-evidence">
                  {INSUFFICIENT_EVIDENCE_HEADLINE}
                </span>
              ) : (
                <span className="flex items-baseline gap-1.5">
                  <span className="text-[2.5rem] leading-none font-semibold tracking-[-0.03em] text-fg">
                    {insufficient ? formatTotal(evaluation.partial_total) : formatTotal(evaluation.weighted_total)}
                  </span>
                  <span className="text-base font-medium text-fg-subtle">/ 5</span>
                </span>
              )}
              {noEvidence ? (
                <span className="text-xs text-fg-muted">No competency could be scored, so there is no total and no band. Every competency below reads &ldquo;{NOT_ENOUGH_EVIDENCE}&rdquo;.</span>
              ) : insufficient ? (
                <span className="text-xs text-fg-muted">Re-normalised over the scored competencies only. No band is recommended without full evidence.</span>
              ) : evaluation.normalized_100 ? (
                <span className="text-xs text-fg-muted" data-testid="intake-normalized-100">
                  {formatHundred(evaluation.normalized_100)} {lowerFirst(evaluation.normalized_label || "Out of 100 (conversion)")}
                </span>
              ) : null}
            </div>
            <div className="grid max-w-md grid-cols-1 justify-items-start gap-1.5">
              <BandBadge band={evaluation.band} label={evaluation.band_label} size="md" testId="intake-band" />
              <p className="flex items-center gap-1.5 text-xs font-medium text-fg">
                <UserRound className="size-3.5 shrink-0" aria-hidden="true" />
                {AI_RECOMMENDATION_NOTE}
              </p>
              {evaluation.band ? <p className="text-xs text-fg-muted">{BAND_GUIDANCE[evaluation.band]}</p> : null}
            </div>
          </div>
          {!insufficient ? <SumLine evaluation={evaluation} /> : null}
          <p className="text-xs text-fg-subtle">
            Evaluation version {evaluation.version}
            {evaluation.finished_at ? `, ${formatDateTime(evaluation.finished_at)}` : ""}. Scores 1-5 per competency from the kit's rubric; the total is the weighted sum.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader title="Competencies" description="Each score cites the answers it rests on. Open a citation to read the full answer." />
        <CardContent className="pt-4">
          <ol className="grid grid-cols-1 divide-y divide-line-subtle">
            {ordered.map((competency) => (
              <CompetencyRow key={competency.key} competency={competency} intakeId={detail.id} />
            ))}
          </ol>
        </CardContent>
      </Card>

      {evaluation.validator_issues.length ? (
        <details className="group rounded-lg border border-line bg-surface px-5 py-3.5" data-testid="intake-validator-issues">
          <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-fg select-none">
            <TriangleAlert className="size-4 text-fg-subtle" aria-hidden="true" />
            {evaluation.validator_issues.length} automatic {evaluation.validator_issues.length === 1 ? "check" : "checks"} adjusted the evaluators' output
          </summary>
          <ul className="mt-3 grid grid-cols-1 gap-1.5 text-[13px] text-fg-muted">
            {evaluation.validator_issues.map((issue, i) => (
              <li key={i}>
                <span className="font-medium text-fg">{issue.code.replace(/_/g, " ")}</span>
                {issue.competency ? ` (${competencyLabel(issue.competency)})` : ""}: {issue.detail}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
