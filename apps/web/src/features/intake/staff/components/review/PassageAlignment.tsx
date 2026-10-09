import { useMemo } from "react";

import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/utils";

import { buildAlignment, describeExtra, describeMark, type AlignmentPart, type ExtraPart, type MarkedPart } from "../../lib/alignment";
import type { ReadingEvent, ReadingMetricsView } from "../../types";

/** Kit material (passages, topic cards) is set in the serif face: the candidate's text, not the UI. */
export const MATERIAL_TEXT = "font-serif text-[16px] leading-[1.95] text-fg";

function Mark({ part }: { part: MarkedPart }) {
  const description = describeMark(part);
  const className = {
    omission: "text-fg-muted line-through decoration-danger decoration-2",
    substitution: "underline decoration-warning decoration-wavy decoration-[1.5px] underline-offset-[5px]",
    asr_variant: "underline decoration-accent decoration-dotted decoration-2 underline-offset-[5px]",
    reordered: "underline decoration-accent decoration-2 underline-offset-[5px]",
  }[part.mark];
  return (
    <Tooltip content={description}>
      <span tabIndex={0} data-mark={part.mark} className={cn("rounded-[2px] focus-visible:outline-offset-1", className)}>
        {part.text}
        {part.mark === "substitution" && part.heard ? (
          <span className="ml-0.5 align-super font-sans text-[11px] font-medium text-warning-fg" aria-hidden="true">
            {part.heard}
          </span>
        ) : null}
        <span className="sr-only"> ({description})</span>
      </span>
    </Tooltip>
  );
}

function Extra({ part }: { part: ExtraPart }) {
  const description = describeExtra(part);
  return (
    <Tooltip content={description}>
      <span tabIndex={0} data-mark="extra" className="mx-0.5 inline-flex items-baseline gap-0.5 rounded-sm align-super font-sans text-[11px] leading-none font-medium text-fg-muted">
        <span aria-hidden="true" className="text-fg-subtle">
          ‸
        </span>
        {part.items.map((item) => item.word).join(" ")}
        <span className="sr-only"> ({description})</span>
      </span>
    </Tooltip>
  );
}

function Part({ part }: { part: AlignmentPart }) {
  if (part.type === "text") return <>{part.text}</>;
  if (part.type === "mark") return <Mark part={part} />;
  return <Extra part={part} />;
}

export function AlignmentLegend({ className }: { className?: string }) {
  return (
    <ul className={cn("flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-fg-muted", className)} aria-label="Legend">
      <li>
        <span className="text-fg line-through decoration-danger decoration-2">word</span> not read
      </li>
      <li>
        <span className="text-fg underline decoration-warning decoration-wavy underline-offset-4">word</span>
        <span className="align-super text-[10px] text-warning-fg">heard</span> read as another word
      </li>
      <li>
        <span className="text-fg underline decoration-accent decoration-dotted decoration-2 underline-offset-4">word</span> close recognition, counted as read
      </li>
      <li>
        <span className="text-fg-subtle">‸</span>
        <span className="text-fg">extra</span> word heard, position approximate
      </li>
    </ul>
  );
}

/**
 * The passage with what the reader did to it: omitted words struck through,
 * substitutions underlined with the heard word, extra words marked where they were
 * (approximately) heard. Without word events it falls back to sentence coverage.
 */
export function PassageAlignment({ paragraphs, events, metrics }: { paragraphs: readonly string[]; events: readonly ReadingEvent[] | null; metrics: ReadingMetricsView | null }) {
  const alignment = useMemo(() => (events ? buildAlignment(paragraphs, events) : null), [paragraphs, events]);

  if (!alignment) {
    // Sentence-level view from the metrics (the word events come with the report).
    const sentences = metrics?.sentences ?? [];
    if (!sentences.length) {
      return (
        <div data-testid="intake-alignment" data-level="none" className={cn("grid grid-cols-1 gap-4", MATERIAL_TEXT)} lang="en">
          {paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      );
    }
    return (
      <div data-testid="intake-alignment" data-level="sentence" className="grid grid-cols-1 gap-2">
        <p className={MATERIAL_TEXT} lang="en">
          {sentences.map((s) => (
            <span
              key={s.index}
              className={cn(s.status === "skipped" && "text-fg-muted line-through decoration-danger decoration-2", s.status === "partial" && "underline decoration-warning decoration-wavy underline-offset-[5px]")}
              title={s.status === "read" ? "Read" : s.status === "partial" ? `Partly read (${Math.round(s.coverage * 100)}% of its words)` : "Skipped"}
            >
              {s.text}{" "}
            </span>
          ))}
        </p>
        <p className="text-xs text-fg-subtle">Sentence-level view: word-by-word marks appear once the report has been generated.</p>
      </div>
    );
  }

  return (
    <div data-testid="intake-alignment" data-level="word" className={cn("grid grid-cols-1 gap-4", MATERIAL_TEXT)} lang="en">
      {alignment.paragraphs.map((paragraph) => (
        <p key={paragraph.index}>
          {paragraph.parts.map((part, i) => (
            <Part key={i} part={part} />
          ))}
        </p>
      ))}
    </div>
  );
}
