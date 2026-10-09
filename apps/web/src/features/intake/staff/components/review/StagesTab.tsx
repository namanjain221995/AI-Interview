import { ChevronDown, ChevronRight, CornerDownRight, Keyboard, MessageSquareText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatDuration, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { AnswerAudio } from "@/features/recruiter/review/components/media";

import { useIntakeTimeline } from "../../api";
import { ITEM_STATUS_LABELS, lookup, SKIP_REASON_LABELS, STAGE_LABELS, STAGE_STATUS_LABELS, STAGE_STATUS_TONES } from "../../labels";
import { evidenceByAnswer, formatClockMs, reviewHref, type AnswerEvidence } from "../../lib/review";
import type { IntakeDetail, TimelineExchange, TimelineItem } from "../../types";
import { IntakeLoadError } from "../IntakeStates";
import { EvidenceChip } from "./EvidenceChip";
import { RecordingMomentButton } from "./RecordingMoment";

const KIND_LABELS: Record<string, string> = {
  prompt: "Question",
  followup: "Follow-up",
  clarify: "Clarification",
  ladder: "Résumé question",
  mq: "Standard question",
  verification: "Language check",
  activity: "Activity",
  answer_question: "Answer to the candidate",
  redirect: "Redirect",
  transition: "Transition",
  closing: "Closing",
};

const NO_EXCHANGE_TEXT: Partial<Record<TimelineItem["status"], string>> = {
  prefilled: "Answered on the candidate's form; never asked aloud.",
  not_reached: "Not reached before time ran out.",
  declined: "The candidate preferred to skip it.",
  not_applicable: "Not part of this interview.",
  interrupted: "Interrupted by a lost connection.",
};

const ACTIVITY_ITEMS: Record<string, "reading" | "topic"> = { "reading.drill_a": "reading", "reading.drill_b": "reading", "topic.card": "topic" };

function ItemStatusBadge({ item }: { item: TimelineItem }) {
  const tone = item.status === "answered" || item.status === "prefilled" ? "success" : item.status === "not_reached" || item.status === "interrupted" ? "warning" : "outline";
  return (
    <Badge size="sm" tone={tone}>
      {ITEM_STATUS_LABELS[item.status]}
    </Badge>
  );
}

function Exchange({ exchange, intakeId, sessionId, evidence, focused }: { exchange: TimelineExchange; intakeId: string; sessionId: string | null; evidence: AnswerEvidence[]; focused: boolean }) {
  const answer = exchange.answer;
  return (
    <div
      id={`qa-${exchange.question_id}`}
      data-testid="intake-qa-item"
      data-question-id={exchange.question_id}
      className={cn("grid grid-cols-1 gap-2 scroll-mt-28 rounded-md transition-shadow", focused && "shadow-[0_0_0_2px_var(--accent-line)]")}
    >
      <div className="flex gap-2.5">
        <MessageSquareText className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
        <div className="grid grid-cols-1 min-w-0 gap-0.5">
          <p className="text-xs text-fg-muted">
            <span className="font-medium text-fg">Interviewer</span>
            {exchange.item_kind ? `, ${lookup(KIND_LABELS, exchange.item_kind).toLowerCase()}` : ""}
            {exchange.asked_ms !== null ? <span className="tabular-nums"> at {formatClockMs(exchange.asked_ms)}</span> : null}
            {exchange.template ? <span className="text-fg-subtle"> (fixed wording)</span> : null}
          </p>
          <p className="text-sm leading-6 text-fg">{exchange.text}</p>
        </div>
      </div>
      {answer ? (
        <div className="ml-6 grid grid-cols-1 gap-2 rounded-md bg-surface-subtle px-3.5 py-3 ring-1 ring-line-subtle ring-inset">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-fg-muted">
            <span className="font-semibold text-fg">Answer</span>
            {answer.started_ms !== null ? <span className="tabular-nums">{formatClockMs(answer.started_ms)}</span> : null}
            {answer.duration_ms ? <span className="tabular-nums">{formatDuration(answer.duration_ms / 1000)}</span> : null}
            {answer.typed ? (
              <Badge size="sm" tone="outline" icon={<Keyboard aria-hidden="true" />}>
                Typed
              </Badge>
            ) : null}
            <span className="ml-auto flex flex-wrap items-center gap-2">
              {answer.audio_media_id ? <AnswerAudio mediaId={answer.audio_media_id} /> : null}
              {sessionId && answer.started_ms !== null ? <RecordingMomentButton sessionId={sessionId} startMs={answer.started_ms} label={exchange.text} /> : null}
            </span>
          </div>
          <p className="text-sm leading-6 whitespace-pre-wrap text-fg">{answer.text || <span className="text-fg-subtle">No speech captured.</span>}</p>
          {evidence.length ? (
            <ul className="flex flex-wrap gap-1.5" aria-label="Evidence from this answer">
              {evidence.map((e, i) => (
                <li key={i} className="max-w-full min-w-0">
                  <EvidenceChip intakeId={intakeId} evidence={e.ref} label={e.label} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <p className="ml-6 text-xs text-fg-subtle">No answer recorded.</p>
      )}
    </div>
  );
}

export function StagesTab({ detail, focusQuestion, focusAnswer }: { detail: IntakeDetail; focusQuestion: string | null; focusAnswer: string | null }) {
  const live = Boolean(detail.session && ["live", "paused"].includes(detail.session.status));
  const timeline = useIntakeTimeline(detail.id, { live });
  const evidence = useMemo(() => evidenceByAnswer(detail.evaluation), [detail.evaluation]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Resolve a deep link to a question (answer ids map to their exchange).
  const focusId = useMemo(() => {
    if (focusQuestion) return focusQuestion;
    if (!focusAnswer || !timeline.data) return null;
    for (const stage of timeline.data.stages) for (const item of stage.items) for (const ex of item.exchanges) if (ex.answer?.answer_id === focusAnswer) return ex.question_id;
    return null;
  }, [focusQuestion, focusAnswer, timeline.data]);

  useEffect(() => {
    if (!focusId || !timeline.data) return;
    const element = document.getElementById(`qa-${focusId}`);
    element?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusId, timeline.data]);

  if (timeline.isPending) {
    return (
      <div className="grid grid-cols-1 gap-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (timeline.isError) return <IntakeLoadError error={timeline.error} title="Couldn't load the questions and answers" onRetry={() => void timeline.refetch()} />;
  const stages = timeline.data.stages;
  if (!stages.some((s) => s.items.some((i) => i.exchanges.length))) {
    return (
      <Card>
        <EmptyState icon={<MessageSquareText />} title="No questions yet" description={detail.session ? "Questions and answers appear here as the interview runs." : "The candidate hasn't started the interview."} />
      </Card>
    );
  }
  const allOpen = collapsed.size === 0;

  return (
    <div className="grid grid-cols-1 gap-4" data-testid="intake-stages-tab">
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => setCollapsed(allOpen ? new Set(stages.map((s) => s.key)) : new Set())}>
          {allOpen ? "Collapse all" : "Expand all"}
        </Button>
      </div>
      {stages.map((stage) => {
        const open = !collapsed.has(stage.key);
        const answered = stage.items.reduce((n, item) => n + item.exchanges.filter((e) => e.answer).length, 0);
        return (
          <Card key={stage.key} className="overflow-hidden">
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setCollapsed((current) => {
                const next = new Set(current);
                if (next.has(stage.key)) next.delete(stage.key);
                else next.add(stage.key);
                return next;
              })}
              className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-surface-hover/60"
            >
              <span className="text-fg-subtle" aria-hidden="true">
                {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
              </span>
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-neutral-soft text-xs font-semibold text-neutral-fg tabular-nums ring-1 ring-neutral-line ring-inset">{stage.number}</span>
              <span className="grid min-w-0 flex-1">
                <span className="truncate text-[15px] font-semibold text-fg">{stage.title || STAGE_LABELS[stage.key]}</span>
                <span className="text-xs text-fg-muted">{pluralize(answered, "answer")}</span>
              </span>
              <Badge size="sm" tone={STAGE_STATUS_TONES[stage.status]} dot>
                {STAGE_STATUS_LABELS[stage.status]}
              </Badge>
            </button>
            {open ? (
              <div className="grid grid-cols-1 divide-y divide-line-subtle border-t border-line-subtle">
                {stage.items.map((item) => {
                  const activityTab = ACTIVITY_ITEMS[item.item_key];
                  return (
                    <section key={item.item_key} aria-label={item.title} className="grid grid-cols-1 gap-3 px-5 py-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="min-w-0 text-sm font-semibold text-fg">{item.title}</h3>
                        <ItemStatusBadge item={item} />
                        {item.mandatory ? <span className="text-xs text-fg-subtle">Mandatory</span> : <span className="text-xs text-fg-subtle">Optional</span>}
                        {item.skip_reason && item.status !== "prefilled" ? <span className="text-xs text-fg-muted">{lookup(SKIP_REASON_LABELS, item.skip_reason)}</span> : null}
                        {activityTab ? (
                          <Link to={reviewHref(detail.id, activityTab)} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline">
                            <CornerDownRight className="size-3.5" aria-hidden="true" />
                            {activityTab === "reading" ? "Reading analysis" : "Topic card analysis"}
                          </Link>
                        ) : null}
                      </div>
                      {item.exchanges.length ? (
                        <div className="grid grid-cols-1 gap-4">
                          {item.exchanges.map((exchange) => (
                            <Exchange
                              key={exchange.question_id}
                              exchange={exchange}
                              intakeId={detail.id}
                              sessionId={detail.session?.id ?? null}
                              evidence={(exchange.answer && evidence.get(exchange.answer.answer_id)) || []}
                              focused={focusId === exchange.question_id}
                            />
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-fg-subtle">{NO_EXCHANGE_TEXT[item.status] ?? "Not asked."}</p>
                      )}
                    </section>
                  );
                })}
              </div>
            ) : null}
          </Card>
        );
      })}
    </div>
  );
}
