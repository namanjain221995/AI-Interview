import { ArrowDownRight, ArrowUpRight, CircleStop, Mic, MonitorPlay, Pause, RadioTower, Sparkles, SquareCode, Volume2, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { IntegritySeverityBadge } from "@/components/domain/IntegritySeverityBadge";
import { ScoreBar } from "@/components/domain/ScoreBar";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { useInterval } from "@/lib/hooks";
import { formatClock, formatOffsetMs, formatTime } from "@/lib/format";
import type { CompetencyDigest, TurnState } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useSessionTranscript } from "../../api/hooks";
import { useLiveSession } from "../../api/live";
import type { IntegrityEventView, LiveConnectionState, LiveTranscriptLine } from "../../api/types";
import { InterviewTape } from "../../components/InterviewTape";
import { CompetencyTag, SegmentedControl } from "../../components/primitives";
import { eventTitle } from "../../lib/integrity";
import { projectedElapsed, projectedRemaining } from "../../lib/liveState";
import { questionLabel } from "../../lib/questions";
import { buildTranscriptTurns, SPEAKER_LABELS } from "../../lib/transcript";
import { useSessionContext } from "../context";

const TURN_COPY: Record<TurnState, { label: string; icon: ReactNode; tone: "signal" | "neutral" | "accent" | "warning" }> = {
  idle: { label: "Waiting to start", icon: <MonitorPlay />, tone: "neutral" },
  ai_speaking: { label: "Interviewer speaking", icon: <Volume2 />, tone: "accent" },
  listening: { label: "Waiting for the candidate", icon: <Mic />, tone: "neutral" },
  candidate_speaking: { label: "Candidate speaking", icon: <Mic />, tone: "signal" },
  processing: { label: "Interviewer thinking", icon: <Sparkles />, tone: "neutral" },
  coding: { label: "Coding exercise", icon: <SquareCode />, tone: "accent" },
  paused: { label: "Paused (connection lost)", icon: <Pause />, tone: "warning" },
  ended: { label: "Interview ended", icon: <CircleStop />, tone: "neutral" },
};

const CONNECTION_COPY: Record<LiveConnectionState, string> = {
  idle: "Not connected",
  connecting: "Connecting to the live feed",
  open: "Live feed connected",
  reconnecting: "Reconnecting; refreshing every few seconds meanwhile",
  closed: "Live feed closed",
  unsupported: "Refreshing every few seconds",
};

function SpeakingBars({ active }: { active: boolean }) {
  return (
    <span className="flex h-4 items-end gap-[2px]" aria-hidden="true">
      {[0.5, 1, 0.7, 0.9, 0.55].map((h, i) => (
        <span
          key={i}
          className={cn("w-[3px] rounded-full bg-current", active && "trace-bar")}
          style={{ height: `${Math.round(h * 16)}px`, animationDelay: `${i * 110}ms`, opacity: active ? 1 : 0.4 }}
        />
      ))}
    </span>
  );
}

interface MergedLine {
  id: string;
  speaker: LiveTranscriptLine["speaker"];
  text: string;
  startMs: number | null;
  partial: boolean;
  interrupted: boolean;
}

export function LiveTab() {
  const ctx = useSessionContext();
  const { sessionId, session, inProgress, questions, index, integrity, evidence, codingSpans, nav, scores } = ctx;
  const { state, connection } = useLiveSession(sessionId, { enabled: inProgress });
  const transcript = useSessionTranscript(sessionId, { live: inProgress });
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), inProgress ? 1_000 : null);
  const [follow, setFollow] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [competencySort, setCompetencySort] = useState<"plan" | "confidence">("plan");

  const status = state.status ?? session.status;
  const turnState: TurnState = state.turnState ?? session.state_digest?.turn_state ?? (status === "paused" ? "paused" : "idle");
  const ended = state.ended || !inProgress;
  const elapsed = projectedElapsed({ ...state, status }, now) ?? session.state_digest?.elapsed_s ?? session.elapsed_seconds;
  const remaining = projectedRemaining({ ...state, status }, now) ?? session.state_digest?.remaining_s ?? null;

  // REST transcript (polled) plus streamed lines it doesn't have yet, in time order. Streamed
  // conversation turns repeat stored ones (often split into segments), so match on text overlap.
  const lines = useMemo<MergedLine[]>(() => {
    const norm = (text: string) => text.toLowerCase().replace(/\s+/g, " ").trim();
    const turns = buildTranscriptTurns(transcript.data ?? []);
    const known = new Set((transcript.data ?? []).map((s) => s.id));
    const fromRest: MergedLine[] = turns.map((t) => ({ id: t.id, speaker: t.speaker, text: t.text, startMs: t.startMs, partial: false, interrupted: t.interrupted }));
    const restTexts = fromRest.map((l) => norm(l.text));
    const overlaps = (text: string) => {
      const probe = norm(text).slice(0, 80);
      return restTexts.some((r) => r.includes(probe) || probe.includes(r.slice(0, 80)));
    };
    const fromSse = state.transcript.filter((l) => !known.has(l.id) && !overlaps(l.text));
    const ordered = [...fromRest, ...fromSse]
      .map((line, order) => ({ line, order }))
      .sort((a, b) => (a.line.startMs ?? Number.MAX_SAFE_INTEGER) - (b.line.startMs ?? Number.MAX_SAFE_INTEGER) || a.order - b.order)
      .map(({ line }) => line);
    const partials = Object.values(state.partials).filter((p): p is LiveTranscriptLine => !!p);
    return [...ordered, ...partials];
  }, [transcript.data, state.transcript, state.partials]);

  useEffect(() => {
    if (!follow) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, follow]);

  // Current question: from the stream, else the stored digest, else the latest asked question.
  const storedCurrent = (session.state_digest as { current_question?: { id?: string | null; text?: string | null } | null } | null | undefined)?.current_question ?? null;
  const currentId = state.currentQuestion?.id ?? storedCurrent?.id ?? null;
  const latest = questions[questions.length - 1] ?? null;
  const currentQuestion = (currentId ? questions.find((q) => q.id === currentId) : null) ?? latest;
  const currentText = state.currentQuestion?.text ?? storedCurrent?.text ?? currentQuestion?.text ?? null;

  // Competency estimates: freshest source wins (stream digest, then polled digest, then scores).
  const competencies: CompetencyDigest[] = useMemo(() => {
    const source = state.competencies.length ? state.competencies : (session.state_digest?.competencies ?? []);
    if (source.length) return source;
    return (scores?.competencies ?? []).map((s) => ({ key: s.key, name: s.label, score_estimate: s.score, confidence: s.confidence, evidence_count: s.evidence_count, sufficient_evidence: s.sufficient_evidence }));
  }, [state.competencies, session.state_digest?.competencies, scores?.competencies]);
  const sortedCompetencies = useMemo(() => {
    const list = [...competencies];
    if (competencySort === "confidence") return list.sort((a, b) => a.confidence - b.confidence);
    return list.sort((a, b) => index.order.indexOf(a.key) - index.order.indexOf(b.key));
  }, [competencies, competencySort, index.order]);

  const integrityEvents = useMemo<IntegrityEventView[]>(() => {
    const byId = new Map<string, IntegrityEventView>();
    for (const e of integrity?.events ?? []) byId.set(e.id, e);
    for (const e of state.integrity) if (!byId.has(e.id)) byId.set(e.id, e);
    return [...byId.values()].sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));
  }, [integrity?.events, state.integrity]);

  const turn = TURN_COPY[turnState] ?? TURN_COPY.idle;
  const durationNow = Math.max(60, Math.round(elapsed ?? 0));

  return (
    <div className="grid grid-cols-1 gap-6">
      {ended ? (
        <Callout
          tone="neutral"
          title="This interview has ended"
          actions={
            <button type="button" className="text-sm font-medium text-accent hover:underline" onClick={() => nav.go({ tab: "overview" })}>
              Go to the overview
            </button>
          }
        >
          Final scores and the report appear once processing finishes, usually within a few minutes.
        </Callout>
      ) : null}

      <section aria-label="Live status" className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-[auto_auto_minmax(0,1fr)_auto] [&>*]:bg-surface">
        <div className="flex items-center gap-3 px-5 py-3.5">
          {ended ? (
            <Badge tone="neutral" dot>
              Ended
            </Badge>
          ) : status === "paused" ? (
            <Badge tone="warning" dot>
              Paused
            </Badge>
          ) : (
            <Badge tone="signal" dot pulse>
              Live
            </Badge>
          )}
          <span className="text-[1.375rem] leading-7 font-semibold tracking-[-0.02em] text-fg tabular-nums" aria-label="Elapsed time">
            {formatClock(elapsed)}
          </span>
        </div>
        <div className="flex items-center gap-2 px-5 py-3.5 text-[13px] text-fg-muted">
          {ended ? (
            "Interview over"
          ) : remaining !== null ? (
            <>
              <span className="tabular-nums">{formatClock(remaining)}</span> left
            </>
          ) : (
            "Time remaining unknown"
          )}
        </div>
        <div
          className={cn(
            "flex items-center gap-2.5 px-5 py-3.5 text-[13px] font-medium",
            turn.tone === "signal" ? "text-signal-fg" : turn.tone === "accent" ? "text-accent-fg" : turn.tone === "warning" ? "text-warning-fg" : "text-fg-muted",
          )}
          aria-live="polite"
        >
          <span className="[&_svg]:size-4" aria-hidden="true">
            {turn.icon}
          </span>
          {turn.label}
          {turnState === "candidate_speaking" || turnState === "ai_speaking" ? <SpeakingBars active /> : null}
        </div>
        <div className="flex items-center gap-2 px-5 py-3.5 text-xs text-fg-muted">
          {connection === "open" ? <RadioTower className="size-4 text-signal" aria-hidden="true" /> : connection === "reconnecting" ? <WifiOff className="size-4 text-warning" aria-hidden="true" /> : <RadioTower className="size-4 text-fg-subtle" aria-hidden="true" />}
          <span>{CONNECTION_COPY[connection]}</span>
        </div>
      </section>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,8fr)_minmax(0,5fr)]">
        <div className="grid grid-cols-1 min-w-0 gap-6">
          <Card>
            <CardHeader
              title="Current question"
              action={
                currentQuestion ? (
                  <span className="flex items-center gap-2 text-xs text-fg-muted">
                    <span className="font-semibold tabular-nums">{questionLabel(currentQuestion)}</span>
                    <DifficultyBadge value={currentQuestion.difficulty} size="sm" />
                  </span>
                ) : null
              }
            />
            <CardContent className="grid grid-cols-1 gap-2">
              <p className="text-[17px] leading-7 font-medium text-fg">{currentText ?? "The interviewer hasn't asked a question yet."}</p>
              {currentQuestion?.competency_keys.length ? (
                <span className="flex flex-wrap gap-x-3 gap-y-1">
                  {currentQuestion.competency_keys.map((k) => (
                    <CompetencyTag key={k} name={index.nameOf(k)} color={index.colorOf(k)} size="sm" />
                  ))}
                </span>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader
              title="Transcript"
              description="Streams in as people speak. Final text replaces the live draft."
              action={
                <button type="button" className="text-xs font-medium text-accent hover:underline" onClick={() => setFollow((v) => !v)} aria-pressed={follow}>
                  {follow ? "Following latest" : "Follow latest"}
                </button>
              }
            />
            <CardContent className="pt-3">
              <div
                ref={scrollRef}
                onScroll={(e) => {
                  const el = e.currentTarget;
                  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
                  if (!atBottom && follow) setFollow(false);
                  if (atBottom && !follow) setFollow(true);
                }}
                className="max-h-[26rem] overflow-y-auto pr-1 scrollbar-thin"
                aria-live="polite"
                aria-relevant="additions"
              >
                {lines.length === 0 ? (
                  <p className="py-10 text-center text-sm text-fg-muted">Waiting for speech…</p>
                ) : (
                  <ol className="grid grid-cols-1 gap-1">
                    {lines.slice(-120).map((line) => (
                      <li
                        key={`${line.id}-${line.partial ? "p" : "f"}`}
                        data-testid="transcript-entry"
                        className={cn("grid grid-cols-[3rem_minmax(0,1fr)] gap-3 rounded-md px-2 py-2", line.speaker === "candidate" && "bg-surface-subtle")}
                      >
                        <span className="pt-0.5 text-xs text-fg-subtle tabular-nums">{formatOffsetMs(line.startMs)}</span>
                        <span className="grid grid-cols-1 gap-0.5">
                          <span className={cn("text-xs font-semibold", line.speaker === "candidate" ? "text-fg" : "text-accent-fg")}>
                            {SPEAKER_LABELS[line.speaker]}
                            {line.interrupted ? <span className="ml-2 font-normal text-warning-fg">interrupted</span> : null}
                          </span>
                          <span className={cn("text-sm leading-6 text-fg", line.partial && "text-fg-muted italic")}>
                            {line.text}
                            {line.partial ? <span className="ml-0.5 inline-block h-3.5 w-[2px] translate-y-0.5 animate-pulse-soft bg-signal" aria-hidden="true" /> : null}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 min-w-0 gap-6">
          <Card>
            <CardHeader
              title="Competency estimates"
              description="Provisional. They settle as evidence accumulates."
              action={
                <SegmentedControl
                  label="Order"
                  value={competencySort}
                  onChange={setCompetencySort}
                  options={[
                    { value: "plan", label: "Plan" },
                    { value: "confidence", label: "Least certain" },
                  ]}
                />
              }
            />
            <CardContent className="grid grid-cols-1 gap-4">
              {sortedCompetencies.length === 0 ? (
                <p className="py-4 text-sm text-fg-muted">Estimates appear after the first evaluated answer.</p>
              ) : (
                sortedCompetencies.map((c) => {
                  const before = state.previousScores[c.key];
                  const delta = typeof before === "number" && c.score_estimate !== null ? c.score_estimate - before : null;
                  return (
                    <div key={c.key} className="grid grid-cols-1 gap-1">
                      <ScoreBar
                        label={
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="size-2 shrink-0 rounded-full" style={{ background: index.colorOf(c.key) }} aria-hidden="true" />
                            <span className="truncate">{c.name ?? index.nameOf(c.key)}</span>
                            {delta !== null && Math.abs(delta) >= 0.05 ? (
                              <span className={cn("inline-flex shrink-0 items-center text-[11px] font-medium tabular-nums", delta > 0 ? "text-success-fg" : "text-danger-fg")}>
                                {delta > 0 ? <ArrowUpRight className="size-3" aria-hidden="true" /> : <ArrowDownRight className="size-3" aria-hidden="true" />}
                                {Math.abs(delta).toFixed(1)}
                              </span>
                            ) : null}
                          </span>
                        }
                        value={c.score_estimate}
                        max={10}
                        confidence={c.confidence}
                        evidenceCount={c.evidence_count}
                        insufficient={!c.sufficient_evidence}
                      />
                    </div>
                  );
                })
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader title="Integrity observations" description="Signals for later review, not determinations." />
            <CardContent>
              {integrityEvents.length === 0 ? (
                <p className="py-2 text-sm text-fg-muted">Nothing recorded so far.</p>
              ) : (
                <ol className="grid grid-cols-1 gap-2">
                  {integrityEvents.slice(0, 8).map((event) => (
                    <li key={event.id} data-testid="integrity-event" className="flex items-start justify-between gap-3 rounded-md border border-line-subtle px-3 py-2">
                      <span className="grid grid-cols-1 gap-0.5">
                        <span className="text-[13px] font-medium text-fg">{eventTitle(event)}</span>
                        <span className="text-xs text-fg-muted tabular-nums">{typeof event.elapsed_seconds === "number" ? formatClock(event.elapsed_seconds) : formatTime(event.occurred_at)}</span>
                      </span>
                      <IntegritySeverityBadge severity={event.severity} size="sm" />
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

          {state.activity.length ? (
            <Card>
              <CardHeader title="Activity" />
              <CardContent>
                <ol className="grid grid-cols-1 gap-2">
                  {state.activity.slice(0, 8).map((a) => (
                    <li key={a.id} className="flex items-baseline justify-between gap-3 text-[13px]">
                      <span className="grid grid-cols-1 gap-0.5">
                        <span className="font-medium text-fg">{a.title}</span>
                        {a.detail ? <span className="text-xs text-fg-muted">{a.detail}</span> : null}
                      </span>
                      <span className="shrink-0 text-xs text-fg-subtle tabular-nums">{formatTime(a.at)}</span>
                    </li>
                  ))}
                </ol>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      {questions.length ? (
        <Card>
          <CardHeader title="Interview so far" description="Questions and evidence on one timeline." />
          <CardContent className="pt-4">
            <InterviewTape
              durationSeconds={durationNow}
              questions={questions}
              evidence={evidence}
              integrity={integrityEvents}
              coding={codingSpans}
              index={index}
              activeQuestionId={currentQuestion?.id ?? null}
              onSelectQuestion={(id) => nav.go({ tab: "questions", question: id })}
              live={!ended}
            />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
