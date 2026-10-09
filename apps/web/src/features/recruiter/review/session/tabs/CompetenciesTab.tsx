import { CircleCheck, CircleDashed, MessageSquareQuote } from "lucide-react";
import { useMemo } from "react";

import { CompetencyRadar } from "@/components/charts/CompetencyRadar";
import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { ScoreBar } from "@/components/domain/ScoreBar";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatOffsetMs, formatScore, pluralize, truncate } from "@/lib/format";
import { ANSWER_QUALITY_LABELS, labelFor } from "@/lib/labels";
import { cn } from "@/lib/utils";

import { EvidenceItem } from "../../components/EvidenceItem";
import { InterviewTape } from "../../components/InterviewTape";
import { BalanceBar, PanelSkeleton, SignalCounts } from "../../components/primitives";
import { evidenceWeight, explainCompetency, explanationSentence, type CompetencyExplanation } from "../../lib/evidence";
import { primaryAnswer, questionLabel } from "../../lib/questions";
import { useSessionContext } from "../context";

function useExplanations(): CompetencyExplanation[] {
  const { index, scores, session, evidence, questions } = useSessionContext();
  return useMemo(() => {
    const byKey = new Map((scores?.competencies ?? []).map((s) => [s.key, s]));
    const digest = new Map((session.state_digest?.competencies ?? []).map((d) => [d.key, d]));
    return index.order.map((key) =>
      explainCompetency({
        key,
        label: index.nameOf(key),
        score: byKey.get(key) ?? null,
        digest: digest.get(key) ?? null,
        evidence,
        questions,
        weight: index.byKey.get(key)?.weight ?? null,
        required: index.byKey.get(key)?.required ?? null,
      }),
    );
  }, [index, scores?.competencies, session.state_digest?.competencies, evidence, questions]);
}

function CompetencyListCard({ item, selected, onSelect }: { item: CompetencyExplanation; selected: boolean; onSelect: () => void }) {
  const { index } = useSessionContext();
  return (
    <button
      type="button"
      data-testid="competency-card"
      data-competency-key={item.key}
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "grid w-full gap-2 rounded-lg border bg-surface px-4 py-3.5 text-left transition-[border-color,box-shadow] duration-150",
        selected ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-line hover:border-line-strong",
      )}
    >
      <ScoreBar
        label={
          <span className="flex min-w-0 items-center gap-2">
            <span className="size-2 shrink-0 rounded-full" style={{ background: index.colorOf(item.key) }} aria-hidden="true" />
            <span className="truncate">{item.label}</span>
          </span>
        }
        value={item.score}
        max={10}
        insufficient={item.sufficient === false}
      />
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <ConfidenceMeter value={item.confidence} />
        <SignalCounts positive={item.positives.length} negative={item.negatives.length} />
        {item.required ? <span className="text-xs text-fg-subtle">Required</span> : null}
      </span>
    </button>
  );
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(1)}`;
}

/** The scoring engine's own arithmetic for this competency (when the scores API includes it). */
function ScoreMath({ item }: { item: CompetencyExplanation }) {
  const { questionById } = useSessionContext();
  const calc = item.calculation;
  if (!calc || typeof calc.score !== "number") return null;
  const observations = item.observations ?? [];
  return (
    <section className="grid grid-cols-1 gap-3 rounded-md bg-surface-subtle p-4 ring-1 ring-line-subtle ring-inset" aria-labelledby={`math-${item.key}`}>
      <h3 id={`math-${item.key}`} className="text-sm font-semibold text-fg">
        How the score was calculated
      </h3>
      <dl className="grid grid-cols-1 gap-1.5 text-[13px]">
        {typeof calc.bayesian_mean === "number" ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-fg-muted">
              Weighted average of {observations.length ? pluralize(observations.length, "evaluated answer") : "evaluated answers"}, starting from a neutral 5.0
            </dt>
            <dd className="font-medium text-fg tabular-nums">{calc.bayesian_mean.toFixed(1)}</dd>
          </div>
        ) : null}
        {typeof calc.evidence_balance === "number" ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-fg-muted">Evidence balance (supporting minus gaps)</dt>
            <dd className="font-medium text-fg tabular-nums">{signed(calc.evidence_balance)}</dd>
          </div>
        ) : null}
        <div className="flex items-baseline justify-between gap-3 border-t border-line-subtle pt-1.5">
          <dt className="font-medium text-fg">Score</dt>
          <dd className="font-semibold text-fg tabular-nums">{calc.score.toFixed(1)} /10</dd>
        </div>
        {typeof calc.confidence === "number" ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-fg-muted">
              Confidence
              {typeof calc.difficulty_levels === "number" ? `, ${pluralize(calc.difficulty_levels, "difficulty level")} seen` : ""}
              {typeof calc.consistency_penalty === "number" && calc.consistency_penalty > 0.005 ? `, lowered ${(calc.consistency_penalty * 100).toFixed(0)} points for inconsistent answers` : ""}
            </dt>
            <dd className="font-medium text-fg tabular-nums">{Math.round(calc.confidence * 100)}%</dd>
          </div>
        ) : null}
      </dl>
      {observations.length ? (
        <ul className="grid grid-cols-1 gap-1 border-t border-line-subtle pt-2.5 text-xs text-fg-muted">
          {observations.map((o, i) => {
            const q = o.question_id ? questionById.get(o.question_id) : undefined;
            const assisted = typeof o.assistance_factor === "number" && o.assistance_factor < 1;
            return (
              <li key={`${o.answer_id ?? o.coding_session_id ?? i}`} className="flex items-baseline justify-between gap-3">
                <span>
                  {q ? questionLabel(q) : o.coding_session_id ? "Coding" : "Answer"}
                  {o.difficulty ? `, ${o.difficulty}` : ""}
                  {assisted ? `, hints ×${o.assistance_factor!.toFixed(2)}` : ""}
                </span>
                <span className="tabular-nums">
                  {typeof o.effective_score === "number" ? o.effective_score.toFixed(1) : "—"}
                  {typeof o.weight === "number" ? <span className="text-fg-subtle"> (weight {o.weight.toFixed(2)})</span> : null}
                </span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

function DrillDown({ item }: { item: CompetencyExplanation }) {
  const { index, questions, evidence, durationSeconds, codingSpans, nav, questionById } = useSessionContext();
  const positiveWeight = item.positives.reduce((sum, e) => sum + evidenceWeight(e), 0);
  const negativeWeight = item.negatives.reduce((sum, e) => sum + evidenceWeight(e), 0);
  const openQuestion = (id: string) => nav.go({ tab: "questions", question: id });
  const openTranscript = ({ answerId, startMs }: { answerId: string | null; startMs: number | null }) => nav.go({ tab: "transcript", t: startMs, answer: answerId });
  const minEvidence = index.byKey.get(item.key)?.minEvidence;

  return (
    <Card className="lg:sticky lg:top-4">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="size-2.5 rounded-full" style={{ background: index.colorOf(item.key) }} aria-hidden="true" />
            {item.label}
          </span>
        }
        description={explanationSentence(item)}
        action={
          <span className="text-right">
            <span className="block text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-fg tabular-nums">
              {formatScore(item.score, 10)}
              <span className="text-sm font-medium tracking-normal text-fg-subtle"> /10</span>
            </span>
          </span>
        }
      />
      <CardContent className="grid grid-cols-1 gap-5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <ConfidenceMeter value={item.confidence} />
          {item.sufficient === true ? (
            <span className="inline-flex items-center gap-1 text-xs text-success-fg">
              <CircleCheck className="size-3.5" aria-hidden="true" />
              Enough evidence
            </span>
          ) : item.sufficient === false ? (
            <span className="inline-flex items-center gap-1 text-xs text-warning-fg">
              <CircleDashed className="size-3.5" aria-hidden="true" />
              Needs more evidence{minEvidence ? ` (aim: ${minEvidence} items)` : ""}
            </span>
          ) : null}
          <span className="text-xs text-fg-muted">{pluralize(item.evidenceCount, "scored item")}</span>
          {(["easy", "medium", "hard"] as const).map((d) =>
            item.difficultyMix[d] ? (
              <span key={d} className="inline-flex items-center gap-1 text-xs text-fg-muted">
                <DifficultyBadge value={d} size="sm" iconOnly />
                {item.difficultyMix[d]} {d}
              </span>
            ) : null,
          )}
        </div>

        <div className="grid grid-cols-1 gap-1.5">
          <div className="flex justify-between text-xs text-fg-muted">
            <span>Supporting weight</span>
            <span>Gap weight</span>
          </div>
          <BalanceBar positive={positiveWeight} negative={negativeWeight} />
        </div>

        <ScoreMath item={item} />

        <div className="grid grid-cols-1 gap-2">
          <p className="text-xs font-medium text-fg-muted">Where it came up</p>
          <InterviewTape
            compact
            durationSeconds={durationSeconds}
            questions={questions}
            evidence={evidence}
            coding={codingSpans}
            index={index}
            focusCompetency={item.key}
            onSelectQuestion={openQuestion}
          />
        </div>

        <section className="grid grid-cols-1 gap-2" aria-labelledby={`pos-${item.key}`}>
          <h3 id={`pos-${item.key}`} className="text-sm font-semibold text-fg">
            Supporting evidence <span className="font-normal text-fg-muted">({item.positives.length})</span>
          </h3>
          {item.positives.length ? (
            <ul className="grid grid-cols-1 gap-2">
              {item.positives.map((e) => {
                const q = e.question_id ? questionById.get(e.question_id) : undefined;
                return <EvidenceItem key={e.id} item={e} questionLabel={q ? questionLabel(q) : null} onOpenQuestion={openQuestion} onOpenTranscript={openTranscript} />;
              })}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">No supporting evidence yet.</p>
          )}
        </section>

        <section className="grid grid-cols-1 gap-2" aria-labelledby={`neg-${item.key}`}>
          <h3 id={`neg-${item.key}`} className="text-sm font-semibold text-fg">
            Gaps and concerns <span className="font-normal text-fg-muted">({item.negatives.length})</span>
          </h3>
          {item.negatives.length ? (
            <ul className="grid grid-cols-1 gap-2">
              {item.negatives.map((e) => {
                const q = e.question_id ? questionById.get(e.question_id) : undefined;
                return <EvidenceItem key={e.id} item={e} questionLabel={q ? questionLabel(q) : null} onOpenQuestion={openQuestion} onOpenTranscript={openTranscript} />;
              })}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">No gaps recorded.</p>
          )}
        </section>

        {item.neutrals.length ? (
          <section className="grid grid-cols-1 gap-2" aria-labelledby={`neu-${item.key}`}>
            <h3 id={`neu-${item.key}`} className="text-sm font-semibold text-fg">
              Neutral observations <span className="font-normal text-fg-muted">({item.neutrals.length}, not scored)</span>
            </h3>
            <ul className="grid grid-cols-1 gap-2">
              {item.neutrals.map((e) => (
                <EvidenceItem key={e.id} item={e} compact />
              ))}
            </ul>
          </section>
        ) : null}

        <section className="grid grid-cols-1 gap-2" aria-labelledby={`open-${item.key}`}>
          <h3 id={`open-${item.key}`} className="text-sm font-semibold text-fg">
            Not covered yet
          </h3>
          {item.unansweredAreas.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {item.unansweredAreas.map((area) => (
                // min-w-0 + max-w-full: a long topic truncates inside the card instead of widening it.
                <li key={area} className="min-w-0 max-w-full">
                  <Badge tone="outline" title={area}>
                    {area}
                  </Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">Every expected topic was addressed by at least one answer.</p>
          )}
        </section>

        <section className="grid grid-cols-1 gap-2" aria-labelledby={`qs-${item.key}`}>
          <h3 id={`qs-${item.key}`} className="text-sm font-semibold text-fg">
            Questions behind this score <span className="font-normal text-fg-muted">({item.supportingQuestions.length})</span>
          </h3>
          {item.supportingQuestions.length ? (
            <ul className="grid grid-cols-1 divide-y divide-line-subtle rounded-md border border-line-subtle">
              {item.supportingQuestions.map((q) => {
                const quality = primaryAnswer(q)?.evaluation?.overall_quality;
                return (
                  <li key={q.id}>
                    <button type="button" onClick={() => openQuestion(q.id)} className="flex w-full items-start gap-3 px-3 py-2.5 text-left hover:bg-surface-hover">
                      <span className="mt-px shrink-0 text-xs font-semibold text-fg-muted tabular-nums">{questionLabel(q)}</span>
                      <span className="min-w-0 flex-1 text-[13px] leading-5 text-fg">{truncate(q.question_focus ?? q.text, 140)}</span>
                      <span className="flex shrink-0 items-center gap-2">
                        <DifficultyBadge value={q.difficulty} size="sm" iconOnly />
                        {quality ? <span className="text-xs text-fg-muted">{labelFor(ANSWER_QUALITY_LABELS, quality)}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">No questions have targeted this competency yet.</p>
          )}
        </section>

        <section className="grid grid-cols-1 gap-2" aria-labelledby={`tr-${item.key}`}>
          <h3 id={`tr-${item.key}`} className="text-sm font-semibold text-fg">
            In the transcript
          </h3>
          {item.transcriptRefs.length ? (
            <ul className="grid grid-cols-1 gap-1.5">
              {item.transcriptRefs.slice(0, 8).map((ref, i) => (
                <li key={`${ref.answerId}-${ref.startMs}-${i}`}>
                  <button
                    type="button"
                    onClick={() => openTranscript({ answerId: ref.answerId, startMs: ref.startMs })}
                    className="flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-hover"
                  >
                    <MessageSquareQuote className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
                    <span className="min-w-0 flex-1 text-[13px] leading-5 text-fg-muted italic">{ref.quote ? `“${truncate(ref.quote, 200)}”` : "Answer segment"}</span>
                    <span className="shrink-0 text-xs text-accent tabular-nums">
                      {ref.startMs !== null ? formatOffsetMs(ref.startMs) : ref.questionId && questionById.get(ref.questionId) ? questionLabel(questionById.get(ref.questionId)!) : "Open"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">No quoted moments yet.</p>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

export function CompetenciesTab() {
  const { nav, scoresQuery, evidenceQuery } = useSessionContext();
  const explanations = useExplanations();
  const selectedKey = nav.competency && explanations.some((e) => e.key === nav.competency) ? nav.competency : (explanations[0]?.key ?? null);
  const selected = explanations.find((e) => e.key === selectedKey) ?? null;
  const radar = explanations.map((e) => ({ key: e.key, label: e.label, score: e.score }));

  if ((scoresQuery.isPending || evidenceQuery.isPending) && explanations.length === 0) return <Card><PanelSkeleton rows={5} /></Card>;
  if (explanations.length === 0) {
    return (
      <Card>
        <EmptyState title="No competencies to explain yet" description="Scores and the evidence behind them appear here once answers are evaluated." />
      </Card>
    );
  }

  const scored = explanations.filter((e) => typeof e.score === "number");
  const strongest = [...scored].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 2);
  const weakest = [...scored].sort((a, b) => (a.score ?? 0) - (b.score ?? 0)).slice(0, 2);
  const needEvidence = explanations.filter((e) => e.sufficient === false);
  const openAreas = explanations.reduce((sum, e) => sum + e.unansweredAreas.length, 0);
  const select = (key: string) => nav.go({ tab: "competencies", competency: key }, { replace: true });

  return (
    <div className="grid grid-cols-1 gap-6">
      <Card>
        <CardContent className="grid grid-cols-1 items-center gap-6 py-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
          {radar.length >= 3 ? (
            <CompetencyRadar data={radar} title="Competency profile" description="Scores out of 10" height={280} />
          ) : (
            <div className="grid grid-cols-1 gap-1">
              <p className="text-sm font-semibold text-fg">Competency profile</p>
              <p className="text-[13px] text-fg-muted">A profile chart appears once three or more competencies are scored.</p>
            </div>
          )}
          <dl className="grid grid-cols-1 content-center gap-4 text-[13px] sm:grid-cols-2">
            <div className="grid grid-cols-1 content-start gap-1.5">
              <dt className="text-xs font-medium text-fg-muted">Strongest</dt>
              {strongest.map((e) => (
                <dd key={e.key}>
                  <button type="button" onClick={() => select(e.key)} className="flex w-full items-baseline justify-between gap-3 rounded-xs text-left hover:underline">
                    <span className="truncate text-fg">{e.label}</span>
                    <span className="font-semibold text-fg tabular-nums">{formatScore(e.score, 10)}</span>
                  </button>
                </dd>
              ))}
            </div>
            <div className="grid grid-cols-1 content-start gap-1.5">
              <dt className="text-xs font-medium text-fg-muted">Weakest</dt>
              {weakest.map((e) => (
                <dd key={e.key}>
                  <button type="button" onClick={() => select(e.key)} className="flex w-full items-baseline justify-between gap-3 rounded-xs text-left hover:underline">
                    <span className="truncate text-fg">{e.label}</span>
                    <span className="font-semibold text-fg tabular-nums">{formatScore(e.score, 10)}</span>
                  </button>
                </dd>
              ))}
            </div>
            <div className="grid grid-cols-1 content-start gap-1.5 sm:col-span-2">
              <dt className="text-xs font-medium text-fg-muted">Needs more evidence</dt>
              <dd className="flex flex-wrap gap-1.5">
                {needEvidence.length ? (
                  needEvidence.map((e) => (
                    <button key={e.key} type="button" onClick={() => select(e.key)} className="rounded-sm">
                      <Badge tone="warning" size="sm">
                        {e.label}
                      </Badge>
                    </button>
                  ))
                ) : (
                  <span className="text-fg-muted">Every competency has enough evidence.</span>
                )}
              </dd>
            </div>
            <div className="grid grid-cols-1 content-start gap-1 sm:col-span-2">
              <dt className="text-xs font-medium text-fg-muted">Topics not covered</dt>
              <dd className="text-fg">{openAreas ? `${pluralize(openAreas, "topic")} across ${pluralize(explanations.filter((e) => e.unansweredAreas.length).length, "competency", "competencies")}` : "None"}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <div className="grid grid-cols-1 gap-2">
          <div className="grid grid-cols-1 gap-2" role="group" aria-label="Competencies">
            {explanations.map((item) => (
              <CompetencyListCard key={item.key} item={item} selected={item.key === selectedKey} onSelect={() => select(item.key)} />
            ))}
          </div>
          <p className="px-1 pt-2 text-xs text-fg-subtle">
            Scores combine every evaluated answer with a neutral starting point, so a single answer never decides a score. Confidence grows with the amount and consistency of evidence.
          </p>
        </div>
        {selected ? <DrillDown item={selected} /> : null}
      </div>
    </div>
  );
}
