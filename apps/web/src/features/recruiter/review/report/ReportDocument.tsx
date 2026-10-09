/**
 * The printable recruiter report. Pure presentation of RecruiterReportView:
 * every section the report API provides, in reading order, with print CSS
 * (see report-print.css). The recommendation is always shown as assistive,
 * next to the human-decision note and the human decision itself.
 */

import { CircleCheck, CircleDashed, CircleMinus, CirclePlus, CircleSlash, Hourglass, Scale, TriangleAlert, UserRound } from "lucide-react";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/brand/Logo";
import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { HUMAN_DECISION_NOTE, RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { scoreTone, TONE_BG } from "@/components/domain/scoreBands";
import { INSUFFICIENT_EVIDENCE_HEADLINE, NOT_ENOUGH_EVIDENCE } from "@/components/domain/evidenceCopy";
import { ScoreRing } from "@/components/domain/ScoreRing";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { confidenceLabel, formatClock, formatDate, formatDateTime, formatMinutes, formatOffsetMs, formatPercent, formatScore, humanize, pluralize, truncate } from "@/lib/format";
import {
  ANSWER_QUALITY_LABELS,
  CLAIM_STATUS_LABELS,
  CODE_LANGUAGE_LABELS,
  CODING_SESSION_STATUS_LABELS,
  INTEGRITY_EVENT_LABELS,
  INTEGRITY_REVIEW_STATUS_LABELS,
  INTEGRITY_SEVERITY_LABELS,
  labelFor,
  ROUND_TYPE_LABELS,
  SESSION_END_REASON_LABELS,
} from "@/lib/labels";
import type { AnswerQuality, ClaimStatus, Difficulty, IntegritySeverity } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { RecruiterReportView, ReportEvidenceView } from "../api/types";
import { DecisionBadge } from "../components/decisions";
import type { CompetencyIndex } from "../lib/competencies";
import { readableRationale } from "../lib/rationale";

const DIFFICULTIES: readonly string[] = ["easy", "medium", "hard"];

function Section({ id, title, children, description, pageBreak }: { id: string; title: string; children: ReactNode; description?: ReactNode; pageBreak?: boolean }) {
  return (
    <section aria-labelledby={id} className="grid gap-4 border-t border-line pt-7" data-print-flow data-print-page-break={pageBreak || undefined}>
      <div className="grid gap-1">
        <h2 id={id} className="text-[17px] leading-6 font-semibold tracking-[-0.01em] text-fg">
          {title}
        </h2>
        {description ? <p className="text-[13px] text-fg-muted">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Bullets({ items, className }: { items: string[]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={cn("grid list-disc gap-1 pl-5 text-[13.5px] leading-[1.45rem] text-fg", className)}>
      {items.map((item, i) => (
        <li key={`${item}-${i}`}>{item}</li>
      ))}
    </ul>
  );
}

function EvidenceLine({ item, nameOf }: { item: ReportEvidenceView; nameOf?: (key: string) => string }) {
  const Icon = item.signal === "positive" ? CirclePlus : item.signal === "negative" ? CircleMinus : CircleDashed;
  return (
    <li className="flex gap-2.5" data-print-avoid-break>
      <Icon
        className={cn("mt-0.5 size-4 shrink-0", item.signal === "positive" ? "text-success" : item.signal === "negative" ? "text-danger" : "text-fg-subtle")}
        role="img"
        aria-label={item.signal === "positive" ? "Supporting evidence" : item.signal === "negative" ? "Gap or concern" : "Neutral observation"}
      />
      <span className="grid gap-1 text-[13.5px] leading-[1.4rem]">
        <span className="text-fg">
          {item.evidence}
          {nameOf ? <span className="text-fg-subtle"> ({nameOf(item.competency_key)})</span> : null}
        </span>
        {item.quote ? <span className="text-[13px] text-fg-muted italic">“{item.quote}”</span> : null}
      </span>
    </li>
  );
}

function MiniBar({ value, max }: { value: number | null; max: 10 | 100 }) {
  if (value === null) return null;
  return (
    <span className="inline-block h-1.5 w-16 overflow-hidden rounded-full bg-track align-middle" aria-hidden="true">
      <span className={cn("block h-full rounded-full", TONE_BG[scoreTone(value, max)])} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
    </span>
  );
}

const CLAIM_ICON: Record<string, typeof CircleCheck> = {
  verified: CircleCheck,
  partially_verified: Scale,
  contradicted: TriangleAlert,
  in_progress: Hourglass,
  unverified: CircleDashed,
  not_applicable: CircleSlash,
};

export interface ReportDocumentProps {
  view: RecruiterReportView;
  index: CompetencyIndex;
  version?: number | null;
  generatedAt?: string | null;
  /** Shown above the summary when the narrative is not available (draft from session data). */
  draft?: boolean;
}

export function ReportDocument({ view, index, version, generatedAt, draft = false }: ReportDocumentProps) {
  const nameOf = index.nameOf;
  const verifiedClaims = view.claims.filter((c) => c.status === "verified" || c.status === "partially_verified");
  const openClaims = view.claims.filter((c) => !(c.status === "verified" || c.status === "partially_verified"));
  const questionLabel = (id: string | null) => {
    const q = id ? view.questions.find((x) => x.id === id) : undefined;
    return q ? `Q${q.sequence}` : null;
  };

  return (
    <article
      data-testid="recruiter-report"
      data-report-root
      data-print-flow
      className="mx-auto grid w-full max-w-[900px] gap-8 rounded-xl border border-line bg-surface px-6 py-8 shadow-[0_1px_3px_rgb(16_24_40/0.06)] sm:px-10 sm:py-10"
    >
      {/* ------------------------------------------------------------ header */}
      <header className="grid gap-5">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="grid min-w-0 gap-1.5">
            <p className="flex items-center gap-2 text-[13px] text-fg-muted">
              <BrandMark size={22} />
              Interview report{draft ? " (draft)" : ""}
            </p>
            <h1 className="text-[1.875rem] leading-9 font-semibold tracking-[-0.022em] text-fg">{view.candidate?.full_name ?? "Candidate"}</h1>
            <p className="text-[15px] text-fg">
              {view.job?.title ?? "Unknown role"}
              {view.round ? <span className="text-fg-muted">, {view.round.name}</span> : null}
            </p>
          </div>
          <div className="grid justify-items-center gap-1.5">
            <ScoreRing value={view.overallScore} max={100} size="lg" label="Overall score" ariaLabel="Overall score" />
            {view.overallScore === null && !draft ? (
              <p className="max-w-44 text-center text-xs font-medium text-warning-fg" data-testid="report-insufficient-evidence">
                {INSUFFICIENT_EVIDENCE_HEADLINE}
              </p>
            ) : null}
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg bg-surface-subtle px-5 py-4 text-[13px] ring-1 ring-line-subtle ring-inset sm:grid-cols-4">
          <div className="grid gap-0.5">
            <dt className="text-xs text-fg-muted">Interview date</dt>
            <dd className="font-medium text-fg">{view.startedAt ? formatDateTime(view.startedAt) : "—"}</dd>
          </div>
          <div className="grid gap-0.5">
            <dt className="text-xs text-fg-muted">Duration</dt>
            <dd className="font-medium text-fg">{formatMinutes(view.durationSeconds)}</dd>
          </div>
          <div className="grid gap-0.5">
            <dt className="text-xs text-fg-muted">Round type</dt>
            <dd className="font-medium text-fg">{view.round ? labelFor(ROUND_TYPE_LABELS, view.round.round_type) : "—"}</dd>
          </div>
          <div className="grid gap-0.5">
            <dt className="text-xs text-fg-muted">Ended because</dt>
            <dd className="font-medium text-fg">{view.endReason ? labelFor(SESSION_END_REASON_LABELS, view.endReason) : "—"}</dd>
          </div>
        </dl>
      </header>

      {/* ----------------------------------------------------------- summary */}
      <Section id="report-summary" title="Summary">
        {view.executiveSummary ? (
          <p className="text-[15px] leading-7 text-fg">{view.executiveSummary}</p>
        ) : (
          <p className="text-sm text-fg-muted">{draft ? "The written summary appears once the report has been generated." : "No written summary in this report."}</p>
        )}
        <div className="grid gap-4 sm:grid-cols-2" data-print-avoid-break>
          <div className="grid content-start gap-2.5 rounded-lg border border-line p-4">
            <p className="text-xs font-medium text-fg-muted">AI-assisted suggestion</p>
            <RecommendationBadge value={view.recommendation} />
            <span className="flex items-center gap-2 text-xs text-fg-muted">
              AI confidence <ConfidenceMeter value={view.confidence} />
            </span>
            {view.recommendationRationale ? <p className="text-[13px] leading-5 text-fg-muted">{readableRationale(view.recommendationRationale, nameOf)}</p> : null}
          </div>
          <div className="grid content-start gap-2.5 rounded-lg border border-line p-4">
            <p className="text-xs font-medium text-fg-muted">Human decision</p>
            {view.humanDecision ? (
              <>
                <DecisionBadge value={view.humanDecision.decision} />
                <p className="text-xs text-fg-muted">
                  {view.humanDecision.reviewer ?? "A reviewer"}
                  {view.humanDecision.at ? `, ${formatDateTime(view.humanDecision.at)}` : ""}
                  {view.humanDecision.overridesAi ? ". Differs from the AI suggestion." : ""}
                </p>
              </>
            ) : (
              <p className="flex items-center gap-1.5 text-sm text-fg">
                <CircleDashed className="size-4 text-fg-subtle" aria-hidden="true" />
                No decision recorded yet
              </p>
            )}
            <p className="flex items-center gap-1 text-xs text-fg-muted">
              <UserRound className="size-3.5" aria-hidden="true" />
              Recorded by a person, separately from the AI suggestion.
            </p>
          </div>
        </div>
      </Section>

      {/* -------------------------------------------------------- competency */}
      <Section id="report-scores" title="Competency scores" description="Competencies are scored out of 10 from the evidence; rubric categories and the overall score out of 100.">
        {view.competencies.length ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-xs text-fg-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Competency
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Score
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Confidence
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Evidence
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    Coverage
                  </th>
                </tr>
              </thead>
              <tbody>
                {view.competencies.map((c) => (
                  <tr key={c.key} className="border-b border-line-subtle">
                    <td className="py-2 pr-3">
                      <span className="flex items-center gap-2">
                        <span className="size-2 shrink-0 rounded-full" style={{ background: index.colorOf(c.key) }} aria-hidden="true" />
                        <span className="font-medium text-fg">{c.label}</span>
                        {typeof c.weight === "number" ? <span className="text-xs text-fg-subtle">×{c.weight}</span> : null}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-right whitespace-nowrap tabular-nums">
                      {c.score === null ? (
                        <span className="text-xs text-fg-subtle">{NOT_ENOUGH_EVIDENCE}</span>
                      ) : (
                        <>
                          <span className="mr-2">
                            <MiniBar value={c.score} max={10} />
                          </span>
                          <span className="font-semibold text-fg">{formatScore(c.score, 10)}</span>
                        </>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-fg-muted">{c.confidence === null ? "—" : confidenceLabel(c.confidence)}</td>
                    <td className="py-2 pr-3 text-right text-fg-muted tabular-nums">{c.evidenceCount ?? c.evidence.length}</td>
                    <td className="py-2 text-fg-muted">{c.sufficient === null ? "—" : c.sufficient ? "Enough evidence" : "Needs more evidence"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No competency scores recorded.</p>
        )}
        {view.rubric.length ? (
          <div className="grid gap-2.5 sm:grid-cols-2">
            {view.rubric.map((r) => (
              <div key={r.key} className="flex items-center justify-between gap-3 rounded-md border border-line-subtle px-3.5 py-2.5 text-[13px]" data-print-avoid-break>
                <span className="grid">
                  <span className="font-medium text-fg">{r.label}</span>
                  <span className="text-xs text-fg-subtle">{typeof r.weight === "number" ? `${Math.round(r.weight)}% of overall` : "Unweighted"}</span>
                </span>
                <span className="flex items-center gap-2 tabular-nums">
                  {r.score === null ? (
                    <span className="text-xs text-fg-subtle">{NOT_ENOUGH_EVIDENCE}</span>
                  ) : (
                    <>
                      <MiniBar value={r.score} max={100} />
                      <span className="font-semibold text-fg">{formatScore(r.score)}</span>
                    </>
                  )}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </Section>

      <Section id="report-evidence" title="Competency evidence" description="Why each score is what it is: findings, supporting evidence and gaps, strongest first.">
        <div className="grid gap-6" data-print-flow>
          {view.competencies.map((c) => {
            const shown = c.evidence.slice(0, 5);
            const hasContent = c.summary || c.strengths.length || c.weaknesses.length || c.unansweredAreas.length || shown.length;
            if (!hasContent) return null;
            return (
              <div key={c.key} className="grid gap-2.5" data-print-avoid-break>
                <h3 className="flex flex-wrap items-baseline gap-x-2 text-[15px] font-semibold text-fg">
                  {c.label}
                  <span className="text-[13px] font-normal text-fg-muted tabular-nums">
                    {c.score === null ? NOT_ENOUGH_EVIDENCE : `${formatScore(c.score, 10)}/10`}
                    {c.score !== null && c.confidence !== null ? `, ${confidenceLabel(c.confidence).toLowerCase()} confidence` : ""}
                  </span>
                </h3>
                {c.summary ? <p className="text-[13.5px] leading-[1.45rem] text-fg">{c.summary}</p> : null}
                {c.strengths.length || c.weaknesses.length ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {c.strengths.length ? (
                      <div className="grid content-start gap-1">
                        <p className="text-xs font-medium text-fg-muted">Strengths</p>
                        <Bullets items={c.strengths} />
                      </div>
                    ) : null}
                    {c.weaknesses.length ? (
                      <div className="grid content-start gap-1">
                        <p className="text-xs font-medium text-fg-muted">Gaps</p>
                        <Bullets items={c.weaknesses} />
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {shown.length ? (
                  <ul className="grid gap-2">
                    {shown.map((e) => (
                      <EvidenceLine key={e.id} item={e} />
                    ))}
                  </ul>
                ) : null}
                {c.unansweredAreas.length ? (
                  <p className="text-[13px] text-fg-muted">
                    <span className="font-medium text-fg">Not covered: </span>
                    {c.unansweredAreas.join(", ")}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      </Section>

      <Section id="report-questions" title="Question-by-question evaluation" pageBreak>
        {view.questions.length ? (
          <ol className="grid gap-5" data-print-flow>
            {view.questions.map((q) => {
              const parent = q.parentId ? view.questions.find((x) => x.id === q.parentId) : undefined;
              return (
                <li key={q.id} className={cn("grid gap-2", parent && "border-l-2 border-line pl-4")} data-print-avoid-break>
                  <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-fg-muted">
                    <span className="font-semibold text-fg tabular-nums">Q{q.sequence}</span>
                    {parent ? <span>Follow-up to Q{parent.sequence}</span> : null}
                    {q.difficulty && DIFFICULTIES.includes(q.difficulty) ? <DifficultyBadge value={q.difficulty as Difficulty} size="sm" /> : null}
                    {q.competencyKeys.map((k) => (
                      <span key={k}>{nameOf(k)}</span>
                    ))}
                    {q.quality ? (
                      <span className="ml-auto font-medium text-fg">
                        {labelFor(ANSWER_QUALITY_LABELS, q.quality as AnswerQuality)}
                        {q.confidence !== null ? <span className="font-normal text-fg-muted">, {formatPercent(q.confidence)} confidence</span> : null}
                      </span>
                    ) : null}
                  </div>
                  <p className="text-[14px] leading-6 font-medium text-fg">{q.text}</p>
                  {q.answer ? <p className="border-l-2 border-line-strong pl-3 text-[13px] leading-[1.4rem] text-fg-muted">{truncate(q.answer, 600)}</p> : <p className="text-[13px] text-fg-subtle">Not answered.</p>}
                  {q.isFallback ? <p className="text-xs text-warning-fg">Evaluated with a fallback; read the answer before relying on it.</p> : null}
                  {q.summary ? <p className="text-[13.5px] leading-[1.45rem] text-fg">{q.summary}</p> : null}
                  {q.dimensions.length ? (
                    <p className="text-xs text-fg-muted tabular-nums">
                      {q.dimensions.map((d) => `${humanize(d.key)} ${d.value.toFixed(1)}`).join(", ")}
                    </p>
                  ) : null}
                  {q.evidence.length ? (
                    <ul className="grid gap-1.5">
                      {q.evidence.slice(0, 4).map((e) => (
                        <EvidenceLine key={e.id} item={e} nameOf={nameOf} />
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="text-sm text-fg-muted">No questions recorded.</p>
        )}
      </Section>

      {view.excerpts.length ? (
        <Section id="report-excerpts" title="Transcript excerpts" description="Moments quoted as evidence.">
          <ul className="grid gap-3" data-print-flow>
            {view.excerpts.map((x) => (
              <li key={x.id} className="grid gap-1" data-print-avoid-break>
                <blockquote className="border-l-2 border-line-strong pl-3 text-[13.5px] leading-[1.45rem] text-fg italic">“{x.text}”</blockquote>
                <span className="pl-3.5 text-xs text-fg-muted tabular-nums">
                  {x.speaker === "candidate" ? "Candidate" : humanize(x.speaker)}
                  {x.startMs !== null ? ` at ${formatOffsetMs(x.startMs)}` : ""}
                  {questionLabel(x.questionId) ? `, ${questionLabel(x.questionId)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section id="report-coding" title="Coding" description={view.codingSummary ?? undefined}>
        {view.coding.length ? (
          <div className="grid gap-6" data-print-flow>
            {view.coding.map((c) => {
              const factor = c.assistanceFactor ?? 1;
              return (
                <div key={c.id} className="grid gap-3" data-print-avoid-break>
                  <h3 className="flex flex-wrap items-baseline gap-x-2 text-[15px] font-semibold text-fg">
                    {c.title}
                    <span className="text-[13px] font-normal text-fg-muted">
                      {c.language ? labelFor(CODE_LANGUAGE_LABELS, c.language as never) : ""}
                      {c.status ? `, ${labelFor(CODING_SESSION_STATUS_LABELS, c.status as never).toLowerCase()}` : ""}
                    </span>
                  </h3>
                  <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
                    <div>
                      <dt className="text-xs text-fg-muted">Tests passed</dt>
                      <dd className="font-medium text-fg tabular-nums">{c.testsPassed !== null ? `${c.testsPassed} of ${c.testsTotal ?? "—"}` : "—"}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-fg-muted">Score counted</dt>
                      <dd className="font-medium text-fg tabular-nums">
                        {c.scoreEstimate !== null ? `${(c.scoreEstimate * factor).toFixed(1)}/10` : "—"}
                        {c.scoreEstimate !== null && factor < 1 ? <span className="font-normal text-fg-muted"> ({c.scoreEstimate.toFixed(1)} × {factor.toFixed(2)})</span> : null}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-fg-muted">Complexity</dt>
                      {/* Labelled lines: the evaluator often writes sentences, which read badly when joined with "time, … space". */}
                      <dd className="grid grid-cols-1 gap-0.5 font-medium text-fg">
                        {c.timeComplexity ? (
                          <span>
                            <span className="font-normal text-fg-muted">Time </span>
                            {c.timeComplexity}
                          </span>
                        ) : null}
                        {c.spaceComplexity ? (
                          <span>
                            <span className="font-normal text-fg-muted">Space </span>
                            {c.spaceComplexity}
                          </span>
                        ) : null}
                        {!c.timeComplexity && !c.spaceComplexity ? "—" : null}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs text-fg-muted">Hints used</dt>
                      <dd className="font-medium text-fg">{c.hintsUsed}</dd>
                    </div>
                  </dl>
                  {c.correctnessSummary ? <p className="text-[13.5px] leading-[1.45rem] text-fg">{c.correctnessSummary}</p> : null}
                  {c.hints.length ? (
                    <ul className="grid gap-1 text-[13px]">
                      {c.hints.map((h) => (
                        <li key={h.level} className="text-fg-muted">
                          <span className="font-medium text-fg">Hint {h.level}</span>
                          {h.elapsedSeconds !== null ? ` at ${formatClock(h.elapsedSeconds)}` : ""}: {h.content}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {c.hintsImpact ? <p className="text-[13px] text-fg-muted">{c.hintsImpact}</p> : null}
                  {c.strengths.length || c.weaknesses.length ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {c.strengths.length ? (
                        <div className="grid content-start gap-1">
                          <p className="text-xs font-medium text-fg-muted">Strengths</p>
                          <Bullets items={c.strengths} />
                        </div>
                      ) : null}
                      {c.weaknesses.length ? (
                        <div className="grid content-start gap-1">
                          <p className="text-xs font-medium text-fg-muted">Areas to probe</p>
                          <Bullets items={c.weaknesses} />
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                  {c.finalCode ? <CodeBlock code={c.finalCode} language={c.language ?? undefined} title="Final code" showLineNumbers maxHeight="22rem" copyable={false} /> : null}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-sm text-fg-muted">This interview had no coding exercise.</p>
        )}
      </Section>

      <Section id="report-claims" title="Resume claims" description={view.claimsSummary ?? undefined}>
        {view.claims.length ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {[
              { title: "Supported by the interview", items: verifiedClaims },
              { title: "Not verified", items: openClaims },
            ].map((group) => (
              <div key={group.title} className="grid content-start gap-2">
                <p className="text-xs font-medium text-fg-muted">
                  {group.title} ({group.items.length})
                </p>
                {group.items.length ? (
                  <ul className="grid gap-2">
                    {group.items.map((claim) => {
                      const Icon = CLAIM_ICON[claim.status] ?? CircleDashed;
                      return (
                        <li key={claim.id} className="flex gap-2 text-[13.5px] leading-[1.4rem]" data-print-avoid-break>
                          <Icon className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden="true" />
                          <span className="grid gap-0.5">
                            <span className="text-fg">{claim.claim}</span>
                            <span className="text-xs text-fg-muted">
                              {labelFor(CLAIM_STATUS_LABELS, claim.status as ClaimStatus)}
                              {claim.note ? `: ${claim.note}` : ""}
                            </span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="text-[13px] text-fg-subtle">None</p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No resume claims were selected for this interview.</p>
        )}
      </Section>

      <Section id="report-contradictions" title="Statements to reconcile" description={view.contradictionsSummary ?? undefined}>
        {view.contradictions.length ? (
          <ul className="grid gap-3">
            {view.contradictions.map((c, i) => (
              <li key={i} className="grid gap-1 rounded-md border border-line-subtle px-4 py-3 text-[13.5px] sm:grid-cols-2 sm:gap-4" data-print-avoid-break>
                <span>
                  <span className="block text-xs text-fg-muted">Said</span>
                  {c.statement}
                </span>
                <span>
                  <span className="block text-xs text-fg-muted">Doesn't match ({c.severity === "high" ? "significant" : c.severity === "medium" ? "moderate" : "minor"})</span>
                  {c.conflicts_with}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-fg-muted">No contradictions were found.</p>
        )}
      </Section>

      <Section id="report-integrity" title="Integrity summary" description="Objective counts of what the browser, camera and server observed. Signals are observations, not determinations.">
        {view.integrity && view.integrity.total > 0 ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid content-start gap-2">
              <p className="text-xs font-medium text-fg-muted">By type</p>
              <ul className="grid gap-1 text-[13px]">
                {view.integrity.byType.map((t) => (
                  <li key={t.type} className="flex justify-between gap-3">
                    <span className="text-fg">{labelFor(INTEGRITY_EVENT_LABELS, t.type as never)}</span>
                    <span className="font-medium text-fg tabular-nums">{t.count}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="grid content-start gap-2">
              <p className="text-xs font-medium text-fg-muted">Severity and review</p>
              <ul className="grid gap-1 text-[13px]">
                {(["high", "medium", "low", "info"] as IntegritySeverity[])
                  .filter((s) => view.integrity!.bySeverity[s])
                  .map((s) => (
                    <li key={s} className="flex justify-between gap-3">
                      <span className="text-fg">{INTEGRITY_SEVERITY_LABELS[s]} severity</span>
                      <span className="font-medium text-fg tabular-nums">{view.integrity!.bySeverity[s]}</span>
                    </li>
                  ))}
                {Object.entries(view.integrity.reviewed)
                  .filter(([, n]) => n)
                  .map(([status, n]) => (
                    <li key={status} className="flex justify-between gap-3">
                      <span className="text-fg-muted">{labelFor(INTEGRITY_REVIEW_STATUS_LABELS, status as never)}</span>
                      <span className="text-fg-muted tabular-nums">{n}</span>
                    </li>
                  ))}
              </ul>
            </div>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No integrity observations were recorded.</p>
        )}
      </Section>

      <Section id="report-followup" title="Areas for human follow-up">
        {view.followUps.length || view.risks.length ? (
          <div className="grid gap-4 sm:grid-cols-2">
            {view.followUps.length ? (
              <div className="grid content-start gap-1.5">
                <p className="text-xs font-medium text-fg-muted">Suggested follow-ups</p>
                <Bullets items={view.followUps} />
              </div>
            ) : null}
            {view.risks.length ? (
              <div className="grid content-start gap-1.5">
                <p className="text-xs font-medium text-fg-muted">Open questions</p>
                <Bullets items={view.risks} />
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No follow-ups suggested.</p>
        )}
      </Section>

      <footer className="grid gap-1 border-t border-line pt-5 text-xs text-fg-muted">
        <p>
          {version ? `Report version ${version}` : "Report"}
          {generatedAt ? `, generated ${formatDate(generatedAt, { dateStyle: "long", timeStyle: "short" })}` : ""}. Scores and suggestions come from evidence collected in this interview only.
        </p>
        <p>{HUMAN_DECISION_NOTE} Candidate-facing reports never include this page's suggestion, integrity details or reviewer notes.</p>
        <p className="text-fg-subtle">{pluralize(view.questions.length, "question")} evaluated.</p>
      </footer>
    </article>
  );
}
