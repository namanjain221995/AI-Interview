/**
 * The interview tape: the whole interview on one time axis. Question blocks
 * sit on the centre lane (tinted by their main competency), supporting
 * evidence ticks rise above it, gaps hang below, integrity observations sit
 * on their own lane and the coding exercise is hatched. Every block is a
 * button that opens that question, so the tape doubles as navigation.
 */

import { useMemo } from "react";

import { Tooltip } from "@/components/ui/Tooltip";
import { formatClock, pluralize, truncate } from "@/lib/format";
import { INTEGRITY_EVENT_LABELS, labelFor } from "@/lib/labels";
import type { EvidenceRecord, SessionQuestion } from "@/lib/types";
import { clamp, cn } from "@/lib/utils";

import type { IntegrityEventView } from "../api/types";
import type { CompetencyIndex } from "../lib/competencies";
import { evidenceWeight } from "../lib/evidence";
import { questionLabel, questionSpans } from "../lib/questions";
import { useElementWidth } from "./primitives";

export interface TapeCodingSpan {
  id: string;
  start: number;
  end: number;
  label: string;
}

export interface InterviewTapeProps {
  durationSeconds: number;
  questions: readonly SessionQuestion[];
  evidence: readonly EvidenceRecord[];
  integrity?: readonly IntegrityEventView[];
  coding?: readonly TapeCodingSpan[];
  index: CompetencyIndex;
  /** Dim evidence and questions of other competencies. */
  focusCompetency?: string | null;
  activeQuestionId?: string | null;
  onSelectQuestion?: (questionId: string) => void;
  /** Draws a "now" marker at the end (live sessions). */
  live?: boolean;
  compact?: boolean;
  className?: string;
}

const AXIS_STEPS = [1, 2, 5, 10, 15, 20, 30, 60];

function axisStep(minutes: number, width: number): number {
  const maxTicks = Math.max(2, Math.floor((width || 640) / 64));
  return AXIS_STEPS.find((step) => minutes / step <= maxTicks) ?? 60;
}

const SEVERITY_DIAMOND = {
  high: "bg-danger",
  medium: "bg-warning",
  low: "bg-fg-subtle",
  info: "bg-line-emphasis",
} as const;

export function InterviewTape({
  durationSeconds,
  questions,
  evidence,
  integrity = [],
  coding = [],
  index,
  focusCompetency = null,
  activeQuestionId = null,
  onSelectQuestion,
  live = false,
  compact = false,
  className,
}: InterviewTapeProps) {
  const [ref, width] = useElementWidth<HTMLDivElement>();

  const model = useMemo(() => {
    const latest = Math.max(
      0,
      ...questions.map((q) => q.elapsed_seconds ?? 0),
      ...evidence.map((e) => e.elapsed_seconds ?? 0),
      ...integrity.map((e) => e.elapsed_seconds ?? 0),
      ...coding.map((c) => c.end),
    );
    const duration = Math.max(60, durationSeconds || 0, latest);
    const spans = questionSpans(questions, duration);
    const spanById = new Map(spans.map((s) => [s.question.id, s]));
    const counts = new Map<string, { positive: number; negative: number }>();
    const ticks = evidence
      .filter((e) => e.signal !== "neutral")
      .map((e) => {
        const span = e.question_id ? spanById.get(e.question_id) : undefined;
        const at = typeof e.elapsed_seconds === "number" ? e.elapsed_seconds : span ? (span.start + span.end) / 2 : null;
        if (e.question_id) {
          const entry = counts.get(e.question_id) ?? { positive: 0, negative: 0 };
          if (e.signal === "positive") entry.positive += 1;
          else entry.negative += 1;
          counts.set(e.question_id, entry);
        }
        return at === null ? null : { id: e.id, at, signal: e.signal, weight: evidenceWeight(e), key: e.competency_key };
      })
      .filter((t): t is { id: string; at: number; signal: "positive" | "negative"; weight: number; key: string } => t !== null);
    return { duration, spans, ticks, counts };
  }, [durationSeconds, questions, evidence, integrity, coding]);

  const { duration, spans, ticks, counts } = model;
  const pct = (seconds: number) => `${clamp(seconds / duration, 0, 1) * 100}%`;
  const minutes = duration / 60;
  const step = axisStep(minutes, width);
  const axisTicks = Array.from({ length: Math.floor(minutes / step) + 1 }, (_, i) => i * step).filter((m) => m * 60 <= duration);
  const pxPerSecond = width > 0 ? width / duration : 0;
  const positives = ticks.filter((t) => t.signal === "positive").length;
  const negatives = ticks.length - positives;
  const tickHeight = (weight: number) => Math.round((compact ? 5 : 6) + weight * (compact ? 9 : 14));

  return (
    <div
      ref={ref}
      role="group"
      aria-label="Interview tape: questions over time with the evidence they produced"
      className={cn("relative select-none", className)}
    >
      <p className="sr-only">
        {pluralize(spans.length, "question")} over {Math.round(minutes)} minutes, {pluralize(positives, "supporting observation")}, {pluralize(negatives, "gap")}
        {integrity.length ? `, ${pluralize(integrity.length, "integrity observation")}` : ""}.
      </p>

      {/* Supporting evidence rises above the lane. */}
      <div className={cn("relative", compact ? "h-4" : "h-6")} aria-hidden="true">
        {ticks
          .filter((t) => t.signal === "positive")
          .map((t) => (
            <span
              key={t.id}
              className={cn("absolute bottom-0.5 w-[3px] -translate-x-1/2 rounded-full bg-success transition-opacity", focusCompetency && t.key !== focusCompetency && "opacity-15")}
              style={{ left: pct(t.at), height: tickHeight(t.weight) }}
            />
          ))}
      </div>

      {/* Question lane. */}
      <div className={cn("relative rounded-[7px] bg-surface-sunken/70 ring-1 ring-line-subtle ring-inset", compact ? "h-7" : "h-9")}>
        {spans.map(({ question, start, end }) => {
          const color = index.colorOf(question.competency_keys[0] ?? "");
          const widthPct = clamp((end - start) / duration, 0, 1) * 100;
          const px = widthPct * (width / 100);
          const c = counts.get(question.id) ?? { positive: 0, negative: 0 };
          const dimmed = !!focusCompetency && !question.competency_keys.includes(focusCompetency);
          const isFollowUp = !!question.parent_question_id;
          const active = activeQuestionId === question.id;
          const label = `${questionLabel(question)}${isFollowUp ? ", follow-up" : ""}`;
          return (
            <Tooltip
              key={question.id}
              content={
                <span className="grid grid-cols-1 gap-1 py-0.5">
                  <span className="font-semibold">
                    {label}
                    <span className="ml-2 font-normal opacity-75 tabular-nums">{formatClock(start)}</span>
                  </span>
                  <span className="font-normal opacity-90">{truncate(question.question_focus ?? question.text, 140)}</span>
                  <span className="font-normal opacity-75">
                    {question.competency_keys.map((k) => index.nameOf(k)).join(", ") || "General"}
                    {c.positive + c.negative > 0 ? `, ${c.positive} supporting, ${c.negative} ${c.negative === 1 ? "gap" : "gaps"}` : ""}
                  </span>
                </span>
              }
            >
              <button
                type="button"
                onClick={onSelectQuestion ? () => onSelectQuestion(question.id) : undefined}
                aria-label={`${label}: ${truncate(question.text, 90)}. ${c.positive} supporting, ${c.negative} gaps.`}
                aria-pressed={onSelectQuestion ? active : undefined}
                className={cn(
                  "absolute inset-y-[3px] flex min-w-[3px] items-center overflow-hidden rounded-[5px] border-t-2 px-1.5 text-left transition-[opacity,box-shadow] duration-150",
                  "hover:shadow-[0_0_0_1px_var(--border-emphasis)] focus-visible:outline-2 focus-visible:outline-offset-1",
                  dimmed && "opacity-35",
                  active && "shadow-[0_0_0_2px_var(--accent)]",
                  !onSelectQuestion && "cursor-default",
                )}
                style={{
                  left: `calc(${pct(start)} + 1px)`,
                  width: `calc(${widthPct}% - 2px)`,
                  borderTopColor: color,
                  background: `color-mix(in srgb, ${color} ${isFollowUp ? 8 : 15}%, var(--surface))`,
                }}
              >
                {px >= 30 ? (
                  <span className="truncate text-[11px] leading-none font-semibold text-fg-muted tabular-nums">{questionLabel(question)}</span>
                ) : null}
              </button>
            </Tooltip>
          );
        })}

        {coding.map((span) => {
          const widthPct = clamp((span.end - span.start) / duration, 0, 1) * 100;
          return (
            <div
              key={span.id}
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 rounded-[6px] ring-1 ring-fg-subtle/40 ring-inset"
              style={{
                left: pct(span.start),
                width: `${widthPct}%`,
                backgroundImage: "repeating-linear-gradient(135deg, color-mix(in srgb, var(--text) 12%, transparent) 0 1px, transparent 1px 6px)",
              }}
            >
              {widthPct * (width / 100) > 64 ? (
                <span className="absolute top-0.5 right-1.5 rounded-xs bg-surface/85 px-1 text-[10px] leading-4 font-medium text-fg-muted">{span.label}</span>
              ) : null}
            </div>
          );
        })}

        {live ? (
          <span aria-hidden="true" className="absolute -inset-y-1.5 right-0 w-0.5 rounded-full bg-signal">
            <span className="absolute -top-1 -left-[3px] size-2 rounded-full bg-signal animate-pulse-soft" />
          </span>
        ) : null}
      </div>

      {/* Gaps hang below the lane. */}
      <div className={cn("relative", compact ? "h-4" : "h-6")} aria-hidden="true">
        {ticks
          .filter((t) => t.signal === "negative")
          .map((t) => (
            <span
              key={t.id}
              className={cn("absolute top-0.5 w-[3px] -translate-x-1/2 rounded-full bg-danger transition-opacity", focusCompetency && t.key !== focusCompetency && "opacity-15")}
              style={{ left: pct(t.at), height: tickHeight(t.weight) }}
            />
          ))}
      </div>

      {integrity.length > 0 && !compact ? (
        <div className="relative h-4" aria-hidden="true">
          {integrity.map((event) =>
            typeof event.elapsed_seconds === "number" ? (
              <Tooltip key={event.id} content={`${labelFor(INTEGRITY_EVENT_LABELS, event.type)} at ${formatClock(event.elapsed_seconds)}. An observation, not a verdict.`}>
                <span
                  tabIndex={-1}
                  className={cn("absolute top-1 size-2 -translate-x-1/2 rotate-45 rounded-[1px]", SEVERITY_DIAMOND[event.severity] ?? SEVERITY_DIAMOND.info)}
                  style={{ left: pct(event.elapsed_seconds) }}
                />
              </Tooltip>
            ) : null,
          )}
        </div>
      ) : null}

      <div className="relative mt-1 h-4 text-[11px] text-fg-subtle tabular-nums" aria-hidden="true">
        {axisTicks.map((m) => {
          const atEnd = pxPerSecond > 0 && (duration - m * 60) * pxPerSecond < 24;
          return (
            <span key={m} className={cn("absolute top-0", m === 0 ? "translate-x-0" : atEnd ? "-translate-x-full" : "-translate-x-1/2")} style={{ left: pct(m * 60) }}>
              {m}m
            </span>
          );
        })}
      </div>
    </div>
  );
}

/** Legend for the tape (rendered by the parent card). */
export function TapeLegend({ showIntegrity = true, showCoding = true, className }: { showIntegrity?: boolean; showCoding?: boolean; className?: string }) {
  return (
    <ul className={cn("flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-fg-muted", className)} aria-label="Tape legend">
      <li className="flex items-center gap-1.5">
        <span className="h-3 w-[3px] rounded-full bg-success" aria-hidden="true" />
        Supporting evidence
      </li>
      <li className="flex items-center gap-1.5">
        <span className="h-3 w-[3px] rounded-full bg-danger" aria-hidden="true" />
        Gap or concern
      </li>
      {showCoding ? (
        <li className="flex items-center gap-1.5">
          <span
            className="h-3 w-4 rounded-[3px] ring-1 ring-fg-subtle/40 ring-inset"
            style={{ backgroundImage: "repeating-linear-gradient(135deg, color-mix(in srgb, var(--text) 14%, transparent) 0 1px, transparent 1px 5px)" }}
            aria-hidden="true"
          />
          Coding
        </li>
      ) : null}
      {showIntegrity ? (
        <li className="flex items-center gap-1.5">
          <span className="size-2 rotate-45 rounded-[1px] bg-fg-subtle" aria-hidden="true" />
          Integrity observation
        </li>
      ) : null}
      <li className="text-fg-subtle">Taller marks carry more weight</li>
    </ul>
  );
}
