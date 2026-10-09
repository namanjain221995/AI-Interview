import { ArrowRight, CircleDashed, PenLine } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { scoreTone, TONE_BG } from "@/components/domain/scoreBands";
import { ScoreRing } from "@/components/domain/ScoreRing";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { usePermission } from "@/lib/auth";
import { formatDateTime, formatMinutes, formatPercent, formatScore, pluralize } from "@/lib/format";
import { labelFor, ROUND_TYPE_LABELS, SESSION_END_REASON_LABELS } from "@/lib/labels";
import type { Difficulty } from "@/lib/types";
import { cn } from "@/lib/utils";

import { isRecord, stringOrNull } from "../../api/normalize";
import { DecisionBadge } from "../../components/decisions";
import { EvidenceItem } from "../../components/EvidenceItem";
import { InterviewTape, TapeLegend } from "../../components/InterviewTape";
import { Fact, FactGrid, QueryError, SignalCounts } from "../../components/primitives";
import { groupEvidenceByCompetency, topEvidence } from "../../lib/evidence";
import { questionLabel } from "../../lib/questions";
import { readableRationale } from "../../lib/rationale";
import { useSessionContext } from "../context";

function VerdictStrip() {
  const { overallScore, confidence, recommendation, rationale: apiRationale, scores, scoresQuery, reviews, reviewsQuery, inProgress, nav, evidence, questions, index } = useSessionContext();
  const canReview = usePermission("reviews:write");
  const latest = reviews[0] ?? null;
  const details = scores?.overall?.details;
  const rationale = apiRationale ?? (isRecord(details) ? (stringOrNull(details.rationale) ?? stringOrNull(details.recommendation_rationale)) : null);
  const counted = evidence.filter((e) => e.signal !== "neutral").length;
  const answered = questions.filter((q) => q.answers.length > 0).length;

  return (
    <section aria-label="Scores and decision" className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-line bg-line shadow-[0_1px_2px_rgb(16_24_40/0.04)] lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.25fr)_minmax(0,1fr)] [&>*]:bg-surface">
      <div className="flex items-center gap-5 p-5">
        <div data-testid="session-overall-score" data-score={overallScore ?? ""}>
          {scoresQuery.isPending && overallScore === null ? (
            <Skeleton className="size-[88px] rounded-full" />
          ) : (
            <ScoreRing value={overallScore} max={100} size="md" ariaLabel="Overall score" />
          )}
        </div>
        <div className="grid grid-cols-1 min-w-0 gap-1.5">
          <p className="text-[13px] font-medium text-fg-muted">Overall score</p>
          <ConfidenceMeter value={confidence} />
          <p className="text-xs text-fg-subtle">
            {inProgress
              ? "Updates as answers are evaluated."
              : counted
                ? `From ${pluralize(counted, "evidence item")} across ${pluralize(answered, "answered question")}.`
                : "No scored evidence yet."}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 content-center gap-2.5 p-5">
        <p className="text-[13px] font-medium text-fg-muted">AI-assisted suggestion</p>
        <div data-testid="session-recommendation" data-recommendation={recommendation ?? ""}>
          <RecommendationBadge value={recommendation} />
        </div>
        {rationale ? <p className="text-[13px] leading-5 text-fg-muted">{readableRationale(rationale, (key) => index.nameOf(key))}</p> : null}
        {typeof confidence === "number" ? (
          <p className="text-xs text-fg-subtle">Suggestion confidence {formatPercent(confidence)}. It weighs evidence coverage, not just the score.</p>
        ) : null}
      </div>

      <div className="grid grid-cols-1 content-center gap-2.5 p-5">
        <p className="text-[13px] font-medium text-fg-muted">Human decision</p>
        {reviewsQuery.isPending ? (
          <Skeleton className="h-6 w-32" />
        ) : reviewsQuery.isError ? (
          <QueryError error={reviewsQuery.error} title="Couldn't load decisions" onRetry={() => void reviewsQuery.refetch()} />
        ) : latest ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <DecisionBadge value={latest.decision} />
              {latest.overrides_ai ? (
                <Badge tone="outline" size="sm">
                  Differs from the AI suggestion
                </Badge>
              ) : null}
            </div>
            <p className="text-xs text-fg-muted">
              {latest.reviewer?.full_name ?? "A reviewer"}, {formatDateTime(latest.created_at)}
            </p>
            <Button variant="link" size="sm" className="w-fit" onClick={() => nav.go({ tab: "review" })}>
              {reviews.length > 1 ? `See all ${reviews.length} decisions` : "See decision notes"}
            </Button>
          </>
        ) : (
          <>
            <p className="flex items-center gap-1.5 text-sm text-fg">
              <CircleDashed className="size-4 text-fg-subtle" aria-hidden="true" />
              {inProgress ? "Available after the interview" : "No decision recorded yet"}
            </p>
            {canReview && !inProgress ? (
              <Button size="sm" className="w-fit" leftIcon={<PenLine />} onClick={() => nav.go({ tab: "review" })}>
                Record decision
              </Button>
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}

function CompetencySummary() {
  const { index, scores, session, evidence, nav, scoresQuery } = useSessionContext();
  const groups = groupEvidenceByCompetency(evidence, index.order);
  const byKey = new Map((scores?.competencies ?? []).map((s) => [s.key, s]));
  const digest = new Map((session.state_digest?.competencies ?? []).map((d) => [d.key, d]));

  return (
    <Card className="lg:col-span-3">
      <CardHeader
        title="Competencies"
        description="Scores out of 10. Select one to see the evidence behind it."
        action={
          <Button variant="ghost" size="sm" rightIcon={<ArrowRight />} onClick={() => nav.go({ tab: "competencies" })}>
            Why these scores
          </Button>
        }
      />
      <CardContent className="pt-3">
        {scoresQuery.isError && !scores ? <QueryError error={scoresQuery.error} onRetry={() => void scoresQuery.refetch()} /> : null}
        {index.order.length === 0 ? <p className="py-6 text-center text-sm text-fg-muted">No competencies scored yet.</p> : null}
        <ul className="grid grid-cols-1">
          {index.order.map((key) => {
            const record = byKey.get(key);
            const live = digest.get(key);
            const group = groups.find((g) => g.key === key);
            const meta = index.byKey.get(key);
            const score = record?.score ?? live?.score_estimate ?? null;
            const confidence = record?.confidence ?? live?.confidence ?? null;
            const insufficient = (record?.sufficient_evidence ?? live?.sufficient_evidence) === false;
            const positives = record?.positive_count ?? group?.positive.length ?? 0;
            const negatives = record?.negative_count ?? group?.negative.length ?? 0;
            // Nothing observed yet: the score is only the neutral starting estimate, so don't draw it as one.
            const unassessed = positives + negatives === 0 && (confidence === null || confidence < 0.05);
            const tone = scoreTone(score, 10);
            return (
              <li key={key} className="border-b border-line-subtle last:border-b-0">
                <button
                  type="button"
                  data-competency-key={key}
                  onClick={() => nav.go({ tab: "competencies", competency: key })}
                  aria-label={`${index.nameOf(key)}: ${score === null || unassessed ? "not assessed yet, no evidence" : `${formatScore(score, 10)} out of 10`}. See the evidence.`}
                  className="-mx-2 grid w-[calc(100%+1rem)] grid-cols-[minmax(0,11rem)_minmax(0,1fr)_2.75rem] items-center gap-x-4 rounded-md px-2 py-2.5 text-left transition-colors hover:bg-surface-hover focus-visible:outline-2 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_2.75rem_7.5rem_3.5rem]"
                >
                  <span className="grid grid-cols-1 min-w-0">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="size-2 shrink-0 rounded-full" style={{ background: meta?.color }} aria-hidden="true" />
                      <span className="truncate text-sm font-medium text-fg">{index.nameOf(key)}</span>
                    </span>
                    <span className="truncate pl-4 text-xs text-fg-subtle">
                      {insufficient ? <span className="text-warning-fg">Needs more evidence</span> : meta?.required === false ? "Optional" : "Required"}
                    </span>
                  </span>
                  <span className={cn("relative h-1.5 rounded-full", unassessed ? "border border-dashed border-line-strong" : "bg-track")} aria-hidden="true">
                    {score !== null && !unassessed ? (
                      <span className={cn("absolute inset-y-0 left-0 rounded-full", TONE_BG[tone], insufficient && "opacity-50")} style={{ width: `${Math.min(100, Math.max(2, score * 10))}%` }} />
                    ) : null}
                  </span>
                  <span className={cn("text-right text-sm font-semibold tabular-nums", unassessed ? "text-fg-subtle" : "text-fg")}>{unassessed ? "—" : formatScore(score, 10)}</span>
                  <span className="max-sm:hidden">
                    <ConfidenceMeter value={confidence} suffix="" />
                  </span>
                  <span className="text-right max-sm:hidden">
                    <SignalCounts positive={positives} negative={negatives} />
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

function RubricCard() {
  const { scores, session } = useSessionContext();
  const rubric: Array<{ key: string; label: string | null; weight: number | null; score: number; confidence: number }> = scores?.rubric?.length ? scores.rubric : (session.rubric ?? []);
  if (rubric.length === 0) return null;
  return (
    <Card>
      <CardHeader title="Rubric" description="Category scores out of 100, weighted into the overall score." />
      <CardContent className="grid grid-cols-1 gap-3.5">
        {rubric.map((category) => (
          <div key={category.key} className="grid grid-cols-1 gap-1.5">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[13px] font-medium text-fg">{category.label ?? category.key}</span>
              <span className="flex shrink-0 items-baseline gap-2">
                {typeof category.weight === "number" ? <span className="text-xs text-fg-subtle tabular-nums">{Math.round(category.weight)}%</span> : null}
                <span className="text-sm font-semibold text-fg tabular-nums">{formatScore(category.score)}</span>
              </span>
            </div>
            <span className="relative h-1.5 rounded-full bg-track" aria-hidden="true">
              <span className={cn("absolute inset-y-0 left-0 rounded-full", TONE_BG[scoreTone(category.score, 100)])} style={{ width: `${Math.min(100, Math.max(1, category.score))}%` }} />
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SessionFacts() {
  const { session, questions, coding, integrity, durationSeconds, plannedSeconds, sessionId, nav, inProgress } = useSessionContext();
  const followUps = questions.filter((q) => q.parent_question_id).length;
  const difficulty: Record<Difficulty, number> = { easy: 0, medium: 0, hard: 0 };
  for (const q of questions) if (q.difficulty in difficulty) difficulty[q.difficulty] += 1;
  const unreviewed = integrity?.events.filter((e) => e.review_status === "unreviewed").length ?? 0;
  const firstCoding = coding[0];
  const finalRun = firstCoding?.runs.find((r) => r.id === firstCoding.final_run_id) ?? firstCoding?.runs[firstCoding.runs.length - 1];

  return (
    <Card>
      <CardHeader title="Session facts" />
      <CardContent>
        <FactGrid>
          <Fact label="Round">{session.round ? labelFor(ROUND_TYPE_LABELS, session.round.round_type) : "—"}</Fact>
          <Fact label="Length" hint={plannedSeconds ? `${formatMinutes(plannedSeconds)} planned` : undefined}>
            {formatMinutes(durationSeconds)}
            {inProgress ? " so far" : ""}
          </Fact>
          <Fact label="Ended because">{session.end_reason ? labelFor(SESSION_END_REASON_LABELS, session.end_reason) : inProgress ? "Still running" : "—"}</Fact>
          <Fact label="Questions" hint={followUps ? `${pluralize(followUps, "follow-up")} included` : undefined}>
            {questions.length}
          </Fact>
          <Fact label="Difficulty mix" className="col-span-2">
            <span className="flex flex-wrap items-center gap-2">
              {(["easy", "medium", "hard"] as const).map((d) => (
                <span key={d} className="inline-flex items-center gap-1.5">
                  <DifficultyBadge value={d} size="sm" />
                  <span className="text-[13px] tabular-nums">{difficulty[d]}</span>
                </span>
              ))}
            </span>
          </Fact>
          <Fact label="Coding">
            {firstCoding ? (
              <button type="button" className="rounded-xs text-left hover:underline" onClick={() => nav.go({ tab: "coding" })}>
                {finalRun ? `${finalRun.passed_count} of ${finalRun.total_count} tests` : (firstCoding.question?.title ?? "Started")}
                {firstCoding.hints_used ? `, ${pluralize(firstCoding.hints_used, "hint")}` : ""}
              </button>
            ) : (
              <span className="font-normal text-fg-muted">Not part of this interview</span>
            )}
          </Fact>
          <Fact label="Reconnects">{session.reconnect_count}</Fact>
          <Fact label="Integrity observations" hint={unreviewed ? `${unreviewed} not reviewed` : undefined}>
            <button type="button" className="rounded-xs hover:underline" onClick={() => nav.go({ tab: "integrity" })}>
              {integrity ? integrity.events.length : (session.integrity_flags ?? "—")}
            </button>
          </Fact>
          <Fact label="Report">
            {session.report_status ? (
              <Link to={`/app/reports/${sessionId}`} className="rounded-xs">
                <StatusBadge kind="report" value={session.report_status} size="sm" />
              </Link>
            ) : inProgress ? (
              <span className="font-normal text-fg-muted">After the interview</span>
            ) : (
              <Link to={`/app/reports/${sessionId}`} className="font-normal text-accent hover:underline">
                Open report
              </Link>
            )}
          </Fact>
        </FactGrid>
      </CardContent>
    </Card>
  );
}

function EvidenceHighlights() {
  const { evidence, index, questionById, nav, evidenceQuery } = useSessionContext();
  const strongest = topEvidence(evidence, "positive", 3);
  const gaps = topEvidence(evidence, "negative", 3);
  const openQuestion = (id: string) => nav.go({ tab: "questions", question: id });
  const openTranscript = ({ answerId, startMs }: { answerId: string | null; startMs: number | null }) => nav.go({ tab: "transcript", t: startMs, answer: answerId });

  const list = (items: typeof strongest, emptyText: string) =>
    evidenceQuery.isPending && evidence.length === 0 ? (
      <div className="grid grid-cols-1 gap-2">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    ) : items.length === 0 ? (
      <p className="py-4 text-sm text-fg-muted">{emptyText}</p>
    ) : (
      <ul className="grid grid-cols-1 gap-2">
        {items.map((item) => {
          const q = item.question_id ? questionById.get(item.question_id) : undefined;
          return (
            <EvidenceItem
              key={item.id}
              item={item}
              competency={{ name: index.nameOf(item.competency_key), color: index.colorOf(item.competency_key) }}
              questionLabel={q ? questionLabel(q) : null}
              onOpenQuestion={openQuestion}
              onOpenTranscript={openTranscript}
            />
          );
        })}
      </ul>
    );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Strongest evidence" description="The observations that carry the most weight, strongest first." />
        <CardContent>{list(strongest, "No supporting evidence recorded yet.")}</CardContent>
      </Card>
      <Card>
        <CardHeader title="Main gaps" description="Where answers fell short of what the round looks for." />
        <CardContent>{list(gaps, "No gaps recorded.")}</CardContent>
      </Card>
    </div>
  );
}

function NotCovered() {
  const { scores, index, nav } = useSessionContext();
  const areas = (scores?.competencies ?? []).filter((s) => s.unanswered_areas.length > 0);
  if (areas.length === 0) return null;
  return (
    <Card>
      <CardHeader title="Not covered yet" description="Topics the round expected that no answer addressed. Good starting points for a follow-up conversation." />
      <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {areas.map((s) => (
          <div key={s.key} className="grid grid-cols-1 content-start gap-2">
            <button type="button" className="w-fit rounded-xs text-left text-sm font-medium text-fg hover:underline" onClick={() => nav.go({ tab: "competencies", competency: s.key })}>
              {index.nameOf(s.key)}
            </button>
            <ul className="flex flex-wrap gap-1.5">
              {s.unanswered_areas.map((area) => (
                <li key={area} className="min-w-0 max-w-full">
                  <Badge tone="outline" size="sm" title={area}>
                    {area}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function OverviewTab() {
  const { durationSeconds, questions, evidence, integrity, codingSpans, index, nav, live, questionsQuery } = useSessionContext();
  const [focus, setFocus] = useState<string>("all");
  const focusOptions = [{ value: "all", label: "All competencies" }, ...index.order.map((key) => ({ value: key, label: index.nameOf(key) }))];

  return (
    <div className="grid grid-cols-1 gap-6">
      <VerdictStrip />

      <Card>
        <CardHeader
          title="Interview tape"
          description="Every question on one timeline with the evidence it produced. Select a question to read it."
          action={index.order.length > 1 ? <Select size="sm" options={focusOptions} value={focus} onValueChange={setFocus} aria-label="Highlight a competency" className="w-52 max-sm:hidden" /> : null}
        />
        <CardContent className="grid grid-cols-1 gap-3 pt-4">
          {questionsQuery.isError && questions.length === 0 ? (
            <QueryError error={questionsQuery.error} onRetry={() => void questionsQuery.refetch()} />
          ) : questionsQuery.isPending && questions.length === 0 ? (
            <Skeleton className="h-28 w-full" />
          ) : questions.length === 0 ? (
            <EmptyState size="sm" title="No questions yet" description="Questions appear here as the interviewer asks them." />
          ) : (
            <>
              <InterviewTape
                durationSeconds={durationSeconds}
                questions={questions}
                evidence={evidence}
                integrity={integrity?.events}
                coding={codingSpans}
                index={index}
                focusCompetency={focus === "all" ? null : focus}
                onSelectQuestion={(id) => nav.go({ tab: "questions", question: id })}
                live={live}
              />
              <TapeLegend showIntegrity={(integrity?.events.length ?? 0) > 0} showCoding={codingSpans.length > 0} />
            </>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-5">
        <CompetencySummary />
        <div className="grid min-w-0 grid-cols-1 gap-6 lg:col-span-2">
          <SessionFacts />
          <RubricCard />
        </div>
      </div>

      <EvidenceHighlights />
      <NotCovered />
    </div>
  );
}
