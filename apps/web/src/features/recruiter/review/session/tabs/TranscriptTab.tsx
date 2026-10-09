import { ChevronDown, ChevronUp, Keyboard, Search, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { Tooltip } from "@/components/ui/Tooltip";
import { useDebounce } from "@/lib/hooks";
import { formatOffsetMs, formatPercent, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useSessionTranscript } from "../../api/hooks";
import { AnswerAudio } from "../../components/media";
import { CompetencyTag, PanelSkeleton, QueryError } from "../../components/primitives";
import { questionLabel } from "../../lib/questions";
import { buildTranscriptTurns, highlightParts, interleaveQuestions, searchTurns, SPEAKER_LABELS, turnAt, type TranscriptTurn } from "../../lib/transcript";
import { useSessionContext } from "../context";

function Highlighted({ text, query, active }: { text: string; query: string; active: boolean }) {
  if (!query.trim()) return <>{text}</>;
  return (
    <>
      {highlightParts(text, query).map((part, i) =>
        part.match ? (
          <mark key={i} className={cn("rounded-[3px] px-0.5 text-fg", active ? "bg-warning/45" : "bg-warning/20")}>
            {part.text}
          </mark>
        ) : (
          <Fragment key={i}>{part.text}</Fragment>
        ),
      )}
    </>
  );
}

export function TranscriptTab() {
  const { sessionId, questions, inProgress, index, nav } = useSessionContext();
  const transcript = useSessionTranscript(sessionId, { live: inProgress });
  const [query, setQuery] = useState("");
  const debounced = useDebounce(query, 150);
  const [matchIndex, setMatchIndex] = useState(0);
  const [flashId, setFlashId] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const deepLinked = useRef(false);

  const turns = useMemo(() => buildTranscriptTurns(transcript.data ?? []), [transcript.data]);
  const blocks = useMemo(() => interleaveQuestions(turns, questions), [turns, questions]);
  const matches = useMemo(() => searchTurns(turns, debounced), [turns, debounced]);
  const questionById = useMemo(() => new Map(questions.map((q) => [q.id, q])), [questions]);

  const audioByAnswer = useMemo(() => {
    const map = new Map<string, { mediaId: string; typed: boolean }>();
    for (const q of questions) for (const a of q.answers) if (a.audio_media_id) map.set(a.id, { mediaId: a.audio_media_id, typed: a.typed });
    return map;
  }, [questions]);
  const typedAnswers = useMemo(() => new Set(questions.flatMap((q) => q.answers.filter((a) => a.typed).map((a) => a.id))), [questions]);

  const scrollToId = (id: string, flash = true) => {
    const element = document.getElementById(id);
    if (!element) return;
    element.scrollIntoView({ block: "center", behavior: "smooth" });
    if (flash) {
      setFlashId(id);
      window.setTimeout(() => setFlashId((current) => (current === id ? null : current)), 2_000);
    }
  };

  // Deep links: ?a=<answer> or ?t=<ms> (from evidence and competency drill-downs).
  useEffect(() => {
    if (deepLinked.current || turns.length === 0) return;
    let target: TranscriptTurn | null = null;
    if (nav.answer) target = turns.find((t) => t.answerId === nav.answer) ?? null;
    if (!target && nav.t !== null && Number.isFinite(nav.t)) target = turnAt(turns, nav.t);
    if (target) {
      deepLinked.current = true;
      const id = `turn-${target.id}`;
      requestAnimationFrame(() => scrollToId(id));
    }
  }, [turns, nav.answer, nav.t]);

  useEffect(() => setMatchIndex(0), [debounced]);
  useEffect(() => {
    const id = matches.turnIds[matchIndex];
    if (id) scrollToId(`turn-${id}`, false);
  }, [matchIndex, matches.turnIds]);

  const step = (delta: number) => {
    if (matches.turnIds.length === 0) return;
    setMatchIndex((i) => (i + delta + matches.turnIds.length) % matches.turnIds.length);
  };

  if (transcript.isPending) return <Card><PanelSkeleton rows={6} /></Card>;
  if (transcript.isError && !transcript.data) {
    return (
      <Card className="p-5">
        <QueryError error={transcript.error} title="Couldn't load the transcript" onRetry={() => void transcript.refetch()} />
      </Card>
    );
  }

  const activeTurnId = matches.turnIds[matchIndex] ?? null;
  const rootQuestions = questions.filter((q) => !q.parent_question_id);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <nav aria-label="Jump to question" className="hidden lg:block">
        <div className="sticky top-4 grid grid-cols-1 gap-1">
          <p className="px-2 pb-1 text-xs font-medium text-fg-muted">Questions</p>
          <ol className="grid grid-cols-1 max-h-[calc(100dvh-12rem)] gap-0.5 overflow-y-auto pr-1 scrollbar-thin">
            {questions.map((q) => (
              <li key={q.id}>
                <button
                  type="button"
                  onClick={() => scrollToId(`question-${q.id}`)}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors hover:bg-surface-hover",
                    q.parent_question_id && "pl-5",
                  )}
                >
                  <span className="shrink-0 font-semibold text-fg-muted tabular-nums">{questionLabel(q)}</span>
                  <span className="line-clamp-2 text-fg-muted">{q.question_focus ?? q.text}</span>
                </button>
              </li>
            ))}
            {questions.length === 0 ? <li className="px-2 text-xs text-fg-subtle">No questions yet</li> : null}
          </ol>
          {rootQuestions.length !== questions.length ? <p className="px-2 pt-2 text-xs text-fg-subtle">Indented items are follow-ups.</p> : null}
        </div>
      </nav>

      <Card className="min-w-0">
        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-t-lg border-b border-line-subtle bg-surface/95 px-4 py-3 backdrop-blur">
          <Input
            size="sm"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                step(e.shiftKey ? -1 : 1);
              }
            }}
            placeholder="Search the transcript"
            aria-label="Search the transcript"
            leading={<Search />}
            wrapperClassName="w-full sm:w-72"
          />
          {debounced.trim() ? (
            <span className="flex items-center gap-1 text-xs text-fg-muted tabular-nums" aria-live="polite">
              {matches.count === 0 ? "No matches" : `${matchIndex + 1} of ${matches.turnIds.length} ${matches.turnIds.length === 1 ? "turn" : "turns"} (${pluralize(matches.count, "match", "matches")})`}
              <IconButton size="xs" label="Previous match" icon={<ChevronUp />} onClick={() => step(-1)} disabled={matches.count === 0} />
              <IconButton size="xs" label="Next match" icon={<ChevronDown />} onClick={() => step(1)} disabled={matches.count === 0} />
              <IconButton size="xs" label="Clear search" icon={<X />} onClick={() => setQuery("")} />
            </span>
          ) : (
            <span className="text-xs text-fg-subtle">{pluralize(turns.length, "turn")}</span>
          )}
          {inProgress ? (
            <Badge tone="signal" size="sm" dot pulse className="ml-auto">
              Updating live
            </Badge>
          ) : null}
        </div>

        {blocks.length === 0 ? (
          <EmptyState size="sm" title="No transcript yet" description={inProgress ? "Speech appears here a few seconds after it is spoken." : "This session has no recorded speech."} />
        ) : (
          <ol ref={listRef} className="grid grid-cols-1 px-4 pb-4">
            {blocks.map((block) => {
              if (block.kind === "question") {
                const q = block.question;
                const parent = q.parent_question_id ? questionById.get(q.parent_question_id) : undefined;
                return (
                  <li
                    key={block.id}
                    id={block.id}
                    className={cn(
                      "mt-3 flex scroll-mt-24 items-start gap-3 border-t border-line-subtle pt-4 first:mt-0 first:border-t-0",
                      flashId === block.id && "rounded-md bg-accent-soft/60",
                    )}
                  >
                    <span className="mt-0.5 shrink-0 rounded-sm bg-neutral-soft px-1.5 py-0.5 text-xs font-semibold text-neutral-fg tabular-nums ring-1 ring-neutral-line ring-inset">
                      {questionLabel(q)}
                    </span>
                    <div className="grid grid-cols-1 min-w-0 flex-1 gap-1.5">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        {q.competency_keys.map((key) => (
                          <CompetencyTag key={key} name={index.nameOf(key)} color={index.colorOf(key)} size="sm" />
                        ))}
                        <DifficultyBadge value={q.difficulty} size="sm" />
                        {parent ? <span className="text-xs text-fg-subtle">Follow-up to {questionLabel(parent)}</span> : null}
                      </div>
                      {q.question_focus ? <p className="text-[13px] text-fg-muted">{q.question_focus}</p> : null}
                    </div>
                    <button
                      type="button"
                      onClick={() => nav.go({ tab: "questions", question: q.id })}
                      className="shrink-0 rounded-xs text-xs font-medium text-accent hover:underline"
                    >
                      Evaluation
                    </button>
                  </li>
                );
              }
              const turn = block.turn;
              const isCandidate = turn.speaker === "candidate";
              const audio = (turn.answerId ? audioByAnswer.get(turn.answerId)?.mediaId : null) ?? turn.audioMediaId;
              const typed = turn.answerId ? typedAnswers.has(turn.answerId) : false;
              const lowConfidence = isCandidate && !typed && turn.minConfidence !== null && turn.minConfidence < 0.6;
              const id = `turn-${turn.id}`;
              return (
                <li
                  key={block.id}
                  id={id}
                  data-testid="transcript-entry"
                  data-speaker={turn.speaker}
                  data-start-ms={turn.startMs ?? undefined}
                  className={cn(
                    "mt-2 grid scroll-mt-24 grid-cols-[3.25rem_minmax(0,1fr)] gap-3 rounded-md px-2 py-2.5 transition-colors duration-500",
                    isCandidate && "bg-surface-subtle",
                    flashId === id && "bg-accent-soft",
                    activeTurnId === turn.id && "ring-1 ring-warning-line",
                  )}
                >
                  <span className="pt-0.5 text-xs text-fg-subtle tabular-nums">{formatOffsetMs(turn.startMs)}</span>
                  <div className="grid grid-cols-1 min-w-0 gap-1">
                    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                      <span className={cn("text-[13px] font-semibold", isCandidate ? "text-fg" : "text-accent-fg")}>{SPEAKER_LABELS[turn.speaker]}</span>
                      {turn.interrupted ? (
                        <Tooltip content={isCandidate ? "This answer was cut short." : "The candidate started speaking before this finished (barge-in)."}>
                          <span tabIndex={0} className="rounded-xs">
                            <Badge size="sm" tone="warning">
                              Interrupted
                            </Badge>
                          </span>
                        </Tooltip>
                      ) : null}
                      {typed ? (
                        <Badge size="sm" tone="outline" icon={<Keyboard aria-hidden="true" />}>
                          Typed
                        </Badge>
                      ) : null}
                      {lowConfidence ? (
                        <Tooltip content={`Speech recognition confidence ${formatPercent(turn.minConfidence)}. Check the audio before relying on exact wording.`}>
                          <span tabIndex={0} className="rounded-xs">
                            <Badge size="sm" tone="neutral">
                              Low transcription confidence
                            </Badge>
                          </span>
                        </Tooltip>
                      ) : null}
                      {isCandidate && audio ? <AnswerAudio mediaId={audio} className="ml-auto" /> : null}
                    </div>
                    <p className="text-sm leading-6 whitespace-pre-wrap text-fg">
                      <Highlighted text={turn.text} query={debounced} active={activeTurnId === turn.id} />
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </Card>
    </div>
  );
}
