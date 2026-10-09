import { ChevronDown, ChevronRight, CornerDownRight, Keyboard, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { formatClock, formatDuration, pluralize } from "@/lib/format";
import { ANSWER_QUALITY_LABELS, labelFor, QUESTION_KIND_LABELS, TURN_ACTION_LABELS } from "@/lib/labels";
import type { EvidenceRecord, SessionAnswer, SessionQuestion } from "@/lib/types";
import { cn } from "@/lib/utils";

import { EvidenceItem } from "../../components/EvidenceItem";
import { AnswerAudio } from "../../components/media";
import { CompetencyTag, PanelSkeleton, QueryError, SignalCounts } from "../../components/primitives";
import { evidenceByQuestion } from "../../lib/evidence";
import { buildQuestionThreads, evaluationDimensions, followUpCount, primaryAnswer, QUALITY_TONE, questionLabel, type QuestionThread } from "../../lib/questions";
import { useSessionContext } from "../context";

function DimensionGrid({ dims }: { dims: ReturnType<typeof evaluationDimensions> }) {
  if (dims.length === 0) return null;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2 xl:grid-cols-3">
      {dims.map((d) => (
        <div key={d.key} className="grid grid-cols-1 gap-1">
          <div className="flex items-baseline justify-between gap-2 text-xs">
            <dt className="text-fg-muted">{d.label}</dt>
            <dd className="font-semibold text-fg tabular-nums">
              {d.value.toFixed(1)}
              <span className="font-normal text-fg-subtle">/10</span>
            </dd>
          </div>
          <div className="h-1 rounded-full bg-track" aria-hidden="true">
            <div className="h-full rounded-full bg-fg-muted/70" style={{ width: `${Math.max(0, Math.min(100, d.value * 10))}%` }} />
          </div>
        </div>
      ))}
    </dl>
  );
}

function AnswerBlock({ answer, defaultOpen }: { answer: SessionAnswer; defaultOpen: boolean }) {
  const long = answer.transcript_text.length > 520;
  const [open, setOpen] = useState(defaultOpen || !long);
  return (
    <div className="grid grid-cols-1 gap-2 rounded-md bg-surface-subtle px-3.5 py-3 ring-1 ring-line-subtle ring-inset">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
        <span className="font-semibold text-fg">Answer</span>
        {answer.duration_ms ? <span className="tabular-nums">{formatDuration(answer.duration_ms / 1000)}</span> : null}
        <span className="tabular-nums">{pluralize(answer.word_count, "word")}</span>
        {answer.typed ? (
          <Badge size="sm" tone="outline" icon={<Keyboard aria-hidden="true" />}>
            Typed
          </Badge>
        ) : null}
        {answer.interrupted_ai ? (
          <Badge size="sm" tone="neutral">
            Spoke over the interviewer
          </Badge>
        ) : null}
        {answer.audio_media_id ? <AnswerAudio mediaId={answer.audio_media_id} className="ml-auto" /> : null}
      </div>
      <p className={cn("text-sm leading-6 whitespace-pre-wrap text-fg", !open && "line-clamp-4")}>{answer.transcript_text || <span className="text-fg-subtle">No speech captured.</span>}</p>
      {long ? (
        <button type="button" className="w-fit rounded-xs text-xs font-medium text-accent hover:underline" onClick={() => setOpen((v) => !v)}>
          {open ? "Show less" : "Show the full answer"}
        </button>
      ) : null}
    </div>
  );
}

interface CardProps {
  thread: QuestionThread;
  evidenceIndex: Map<string, EvidenceRecord[]>;
  expanded: Set<string>;
  toggle: (id: string) => void;
  matches: (q: SessionQuestion) => boolean;
  focusId: string | null;
  flashId: string | null;
}

function QuestionCard({ thread, evidenceIndex, expanded, toggle, matches, focusId, flashId }: CardProps) {
  const { index, questionById, nav } = useSessionContext();
  const q = thread.question;
  const isOpen = expanded.has(q.id);
  const primary = primaryAnswer(q);
  const answers = [...q.answers].sort((a, b) => a.sequence - b.sequence);
  const evaluation = primary?.evaluation ?? null;
  const dims = evaluationDimensions(evaluation);
  const evidence = evidenceIndex.get(q.id) ?? [];
  const positives = evidence.filter((e) => e.signal === "positive").length;
  const negatives = evidence.filter((e) => e.signal === "negative").length;
  const parent = q.parent_question_id ? questionById.get(q.parent_question_id) : undefined;
  const doc = evaluation?.evaluation;
  const dimmed = !matches(q);

  return (
    <div className={cn(thread.depth > 0 && "border-l-2 border-line pl-4 sm:pl-5")}>
      <article
        id={`q-${q.id}`}
        data-question-id={q.id}
        aria-labelledby={`q-${q.id}-title`}
        className={cn(
          "scroll-mt-24 rounded-lg border border-line bg-surface transition-[box-shadow,opacity] duration-300",
          focusId === q.id && "shadow-[0_0_0_2px_var(--accent-line)]",
          flashId === q.id && "shadow-[0_0_0_3px_var(--accent)]",
          dimmed && "opacity-60",
        )}
      >
        <button
          type="button"
          onClick={() => toggle(q.id)}
          aria-expanded={isOpen}
          aria-controls={`q-${q.id}-body`}
          className="flex w-full items-start gap-3 rounded-lg px-4 py-3.5 text-left hover:bg-surface-hover/60"
        >
          <span className="mt-0.5 text-fg-subtle" aria-hidden="true">
            {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          </span>
          <span className="grid grid-cols-1 min-w-0 flex-1 gap-1.5">
            <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="rounded-sm bg-neutral-soft px-1.5 text-xs leading-5 font-semibold text-neutral-fg tabular-nums ring-1 ring-neutral-line ring-inset">{questionLabel(q)}</span>
              {parent ? (
                <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
                  <CornerDownRight className="size-3.5" aria-hidden="true" />
                  Follow-up to {questionLabel(parent)}
                </span>
              ) : (
                <span className="text-xs text-fg-muted">{labelFor(QUESTION_KIND_LABELS, q.kind)}</span>
              )}
              <DifficultyBadge value={q.difficulty} size="sm" />
              {q.competency_keys.map((key) => (
                <CompetencyTag key={key} name={index.nameOf(key)} color={index.colorOf(key)} size="sm" />
              ))}
              <span className="ml-auto text-xs text-fg-subtle tabular-nums">{formatClock(q.elapsed_seconds)}</span>
            </span>
            <span id={`q-${q.id}-title`} className="text-[15px] leading-6 font-medium text-fg">
              {q.text}
            </span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {evaluation ? (
                <Badge size="sm" tone={QUALITY_TONE[evaluation.overall_quality] ?? "neutral"}>
                  {labelFor(ANSWER_QUALITY_LABELS, evaluation.overall_quality)}
                </Badge>
              ) : primary ? (
                <span className="text-xs text-fg-subtle">Evaluation pending</span>
              ) : (
                <span className="text-xs text-fg-subtle">Not answered</span>
              )}
              {evidence.length ? <SignalCounts positive={positives} negative={negatives} /> : null}
              {q.interrupted ? (
                <Badge size="sm" tone="warning">
                  Interrupted
                </Badge>
              ) : null}
              {q.action !== "ask_question" && !(q.action === "ask_followup" && parent) ? <span className="text-xs text-fg-muted">{labelFor(TURN_ACTION_LABELS, q.action)}</span> : null}
              {thread.children.length ? <span className="text-xs text-fg-subtle">{pluralize(followUpCount(thread), "follow-up")}</span> : null}
            </span>
          </span>
        </button>

        {isOpen ? (
          <div id={`q-${q.id}-body`} className="grid grid-cols-1 gap-4 border-t border-line-subtle px-4 pt-4 pb-4 sm:pl-11">
            {q.reason || q.expected_evidence.length ? (
              <div className="grid grid-cols-1 gap-1.5 text-[13px]">
                {q.reason ? (
                  <p className="text-fg-muted">
                    <span className="font-medium text-fg">Why it was asked: </span>
                    {q.reason}
                  </p>
                ) : null}
                {q.expected_evidence.length ? (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-fg-muted">Looking for</span>
                    {q.expected_evidence.map((item) => (
                      <Badge key={item} size="sm" tone="outline">
                        {item}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            {answers.length ? (
              answers.map((a) => <AnswerBlock key={a.id} answer={a} defaultOpen={focusId === q.id} />)
            ) : (
              <p className="text-sm text-fg-muted">The candidate did not answer this question.</p>
            )}

            {evaluation ? (
              <div className="grid grid-cols-1 gap-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h4 className="text-sm font-semibold text-fg">Evaluation</h4>
                  <ConfidenceMeter value={evaluation.confidence} />
                </div>
                {evaluation.is_fallback ? (
                  <Callout tone="warning" icon={<TriangleAlert />} title="Fallback evaluation">
                    The detailed evaluation couldn't be produced, so a neutral placeholder was recorded with low confidence. Read the answer yourself before relying on it.
                  </Callout>
                ) : null}
                {doc?.summary ? <p className="text-sm leading-6 text-fg">{doc.summary}</p> : null}
                <DimensionGrid dims={dims} />
                {doc?.missing_details?.length || doc?.unsupported_claims?.length || doc?.measurable_outcomes?.length ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    {doc?.measurable_outcomes?.length ? (
                      <div className="grid grid-cols-1 content-start gap-1">
                        <p className="text-xs font-medium text-fg-muted">Measurable outcomes</p>
                        <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
                          {doc.measurable_outcomes.map((m) => (
                            <li key={m}>{m}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {doc?.missing_details?.length ? (
                      <div className="grid grid-cols-1 content-start gap-1">
                        <p className="text-xs font-medium text-fg-muted">Missing details</p>
                        <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
                          {doc.missing_details.map((m) => (
                            <li key={m}>{m}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {doc?.unsupported_claims?.length ? (
                      <div className="grid grid-cols-1 content-start gap-1">
                        <p className="text-xs font-medium text-fg-muted">Claims not backed up</p>
                        <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg">
                          {doc.unsupported_claims.map((m) => (
                            <li key={m}>{m}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}

            {evidence.length ? (
              <div className="grid grid-cols-1 gap-2">
                <h4 className="text-sm font-semibold text-fg">Evidence from this question</h4>
                <ul className="grid grid-cols-1 gap-2">
                  {evidence.map((item) => (
                    <EvidenceItem
                      key={item.id}
                      item={item}
                      competency={{ name: index.nameOf(item.competency_key), color: index.colorOf(item.competency_key) }}
                      onOpenTranscript={({ answerId, startMs }) => nav.go({ tab: "transcript", t: startMs, answer: answerId })}
                    />
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </article>

      {thread.children.length ? (
        <div className="mt-2 grid grid-cols-1 gap-2">
          {thread.children.map((child) => (
            <QuestionCard key={child.question.id} thread={child} evidenceIndex={evidenceIndex} expanded={expanded} toggle={toggle} matches={matches} focusId={focusId} flashId={flashId} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function threadMatches(thread: QuestionThread, matches: (q: SessionQuestion) => boolean): boolean {
  return matches(thread.question) || thread.children.some((c) => threadMatches(c, matches));
}

export function QuestionsTab() {
  const { questions, questionsQuery, evidence, index, nav } = useSessionContext();
  const threads = useMemo(() => buildQuestionThreads(questions), [questions]);
  const evidenceIndex = useMemo(() => evidenceByQuestion(evidence), [evidence]);
  const [competency, setCompetency] = useState("all");
  const [difficulty, setDifficulty] = useState("all");
  const [gapsOnly, setGapsOnly] = useState(false);
  // Short interviews open fully; longer ones open the first few questions (plus a deep-linked one).
  // Initialised once the questions have loaded (the tab can mount before they arrive).
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const initialised = useRef(false);
  useEffect(() => {
    if (initialised.current || questions.length === 0) return;
    initialised.current = true;
    const initial = questions.length <= 6 ? questions.map((q) => q.id) : questions.slice(0, 3).map((q) => q.id);
    if (nav.question) initial.push(nav.question);
    setExpanded((prev) => new Set([...prev, ...initial]));
  }, [questions, nav.question]);
  const [flashId, setFlashId] = useState<string | null>(null);
  const scrolled = useRef<string | null>(null);

  useEffect(() => {
    const target = nav.question;
    if (!target || scrolled.current === target || !questions.some((q) => q.id === target)) return;
    scrolled.current = target;
    setExpanded((prev) => new Set(prev).add(target));
    requestAnimationFrame(() => {
      document.getElementById(`q-${target}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
      setFlashId(target);
      window.setTimeout(() => setFlashId(null), 1_600);
    });
  }, [nav.question, questions]);

  const matches = (q: SessionQuestion) =>
    (competency === "all" || q.competency_keys.includes(competency)) &&
    (difficulty === "all" || q.difficulty === difficulty) &&
    (!gapsOnly || (evidenceIndex.get(q.id) ?? []).some((e) => e.signal === "negative"));
  const visible = threads.filter((t) => threadMatches(t, matches));
  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (questionsQuery.isPending && questions.length === 0) return <Card><PanelSkeleton rows={5} /></Card>;
  if (questionsQuery.isError && questions.length === 0) {
    return (
      <Card className="p-5">
        <QueryError error={questionsQuery.error} title="Couldn't load the questions" onRetry={() => void questionsQuery.refetch()} />
      </Card>
    );
  }
  if (questions.length === 0) {
    return (
      <Card>
        <EmptyState title="No questions yet" description="Each question, the answer, its evaluation and the evidence it produced will appear here." />
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          size="sm"
          className="w-52"
          aria-label="Filter by competency"
          value={competency}
          onValueChange={setCompetency}
          options={[{ value: "all", label: "All competencies" }, ...index.order.map((key) => ({ value: key, label: index.nameOf(key) }))]}
        />
        <Select
          size="sm"
          className="w-40"
          aria-label="Filter by difficulty"
          value={difficulty}
          onValueChange={setDifficulty}
          options={[
            { value: "all", label: "Any difficulty" },
            { value: "easy", label: "Easy" },
            { value: "medium", label: "Medium" },
            { value: "hard", label: "Hard" },
          ]}
        />
        <Switch size="sm" checked={gapsOnly} onCheckedChange={setGapsOnly} label="Only questions with gaps" />
        <span className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={() => setExpanded(new Set(questions.map((q) => q.id)))}>
            Expand all
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setExpanded(new Set())}>
            Collapse all
          </Button>
        </span>
      </div>
      {visible.length === 0 ? (
        <Card>
          <EmptyState size="sm" title="No questions match these filters" description="Clear a filter to see more questions." />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {visible.map((thread) => (
            <QuestionCard
              key={thread.question.id}
              thread={thread}
              evidenceIndex={evidenceIndex}
              expanded={expanded}
              toggle={toggle}
              matches={matches}
              focusId={nav.question}
              flashId={flashId}
            />
          ))}
        </div>
      )}
    </div>
  );
}
