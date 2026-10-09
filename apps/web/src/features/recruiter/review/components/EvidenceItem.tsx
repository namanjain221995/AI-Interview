import { MessageSquareQuote } from "lucide-react";

import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { EvidenceSignalIcon } from "@/components/domain/EvidenceSignalIcon";
import { Badge } from "@/components/ui/Badge";
import { formatOffsetMs, humanize } from "@/lib/format";
import { EVIDENCE_SOURCE_LABELS, labelFor } from "@/lib/labels";
import type { Difficulty, EvidenceSignal, EvidenceSource } from "@/lib/types";
import { cn } from "@/lib/utils";

import { CompetencyTag, StrengthMeter } from "./primitives";

/** The fields an evidence row needs (EvidenceRecord and report evidence both fit). */
export interface EvidenceLike {
  id: string;
  competency_key: string;
  signal: EvidenceSignal;
  dimension?: string | null;
  evidence: string;
  strength?: number | null;
  confidence?: number | null;
  difficulty?: Difficulty | null;
  source?: EvidenceSource;
  question_id?: string | null;
  answer_id?: string | null;
  quote?: string | null;
  transcript_ref?: { answer_id?: string; start_ms?: number; end_ms?: number; quote?: string };
}

export interface EvidenceItemProps {
  item: EvidenceLike;
  competency?: { name: string; color: string } | null;
  /** "Q3" label of the question the evidence came from. */
  questionLabel?: string | null;
  onOpenQuestion?: (questionId: string) => void;
  onOpenTranscript?: (target: { answerId: string | null; startMs: number | null }) => void;
  /** Hide the meta row (dense lists). */
  compact?: boolean;
  as?: "li" | "div";
  className?: string;
}

export function evidenceQuote(item: EvidenceLike): string | null {
  return item.quote ?? item.transcript_ref?.quote ?? null;
}

export function EvidenceItem({ item, competency, questionLabel, onOpenQuestion, onOpenTranscript, compact = false, as = "li", className }: EvidenceItemProps) {
  const Comp = as;
  const quote = evidenceQuote(item);
  const startMs = typeof item.transcript_ref?.start_ms === "number" ? item.transcript_ref.start_ms : null;
  const answerId = item.transcript_ref?.answer_id ?? item.answer_id ?? null;
  const canJump = !!onOpenTranscript && (startMs !== null || !!answerId);
  return (
    <Comp
      data-testid="evidence-item"
      data-signal={item.signal}
      className={cn("flex gap-3 rounded-md border border-line-subtle bg-surface px-3.5 py-3", className)}
    >
      <EvidenceSignalIcon signal={item.signal} className="mt-0.5 self-start" />
      <div className="grid grid-cols-1 min-w-0 flex-1 gap-2">
        <p className="text-sm leading-[1.4rem] text-fg">{item.evidence}</p>
        {quote ? (
          <blockquote className="flex gap-2 border-l-2 border-line-strong pl-3 text-[13px] leading-5 text-fg-muted">
            <span className="italic">“{quote}”</span>
          </blockquote>
        ) : null}
        {compact ? null : (
          <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5">
            {competency ? <CompetencyTag name={competency.name} color={competency.color} size="sm" /> : null}
            {item.dimension && item.dimension !== "general" ? <span className="text-xs text-fg-muted">{humanize(item.dimension)}</span> : null}
            <StrengthMeter value={item.strength} />
            {typeof item.confidence === "number" ? <ConfidenceMeter value={item.confidence} /> : null}
            {item.difficulty ? <DifficultyBadge value={item.difficulty} size="sm" /> : null}
            {item.source && item.source !== "answer" ? (
              <Badge size="sm" tone="outline">
                {labelFor(EVIDENCE_SOURCE_LABELS, item.source)}
              </Badge>
            ) : null}
            <span className="ml-auto flex items-center gap-3">
              {questionLabel && item.question_id && onOpenQuestion ? (
                <button
                  type="button"
                  onClick={() => onOpenQuestion(item.question_id!)}
                  className="rounded-xs text-xs font-medium text-accent hover:text-accent-hover hover:underline"
                >
                  From {questionLabel}
                </button>
              ) : null}
              {canJump ? (
                <button
                  type="button"
                  onClick={() => onOpenTranscript!({ answerId, startMs })}
                  className="inline-flex items-center gap-1 rounded-xs text-xs font-medium text-accent hover:text-accent-hover hover:underline"
                >
                  <MessageSquareQuote className="size-3.5" aria-hidden="true" />
                  {startMs !== null ? `Transcript ${formatOffsetMs(startMs)}` : "In transcript"}
                </button>
              ) : null}
            </span>
          </div>
        )}
      </div>
    </Comp>
  );
}
