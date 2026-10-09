import { CircleDashed, CircleMinus, CirclePlus } from "lucide-react";
import { Link } from "react-router";

import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/utils";

import { SUPPORT_LABELS } from "../../labels";
import { reviewHref, type ReviewTab } from "../../lib/review";
import type { EvidenceRef } from "../../types";

const ICON = { positive: CirclePlus, negative: CircleMinus, context: CircleDashed } as const;
const TONE = { positive: "text-success", negative: "text-danger", context: "text-fg-subtle" } as const;

function targetTab(ref: EvidenceRef): ReviewTab {
  if (ref.source === "activity") return ref.stage === "topic" ? "topic" : "reading";
  if (ref.source === "onboarding" || ref.source === "resume") return "overview";
  return "stages";
}

/** A cited piece of evidence: opens the exact answer it quotes (or the activity / form it came from). */
export function EvidenceChip({ intakeId, evidence, label, className }: { intakeId: string; evidence: EvidenceRef; label?: string; className?: string }) {
  const Icon = ICON[evidence.supports];
  const where = evidence.source === "onboarding" ? "the onboarding form" : evidence.source === "resume" ? "the résumé" : evidence.source === "activity" ? "an activity capture" : "an answer";
  const text = label ?? (evidence.quote ? `“${evidence.quote}”` : `From ${where}`);
  const tip = (
    <span className="grid max-w-xs gap-1">
      <span className="font-medium">
        {SUPPORT_LABELS[evidence.supports]}, from {where}
      </span>
      {evidence.quote ? <span>“{evidence.quote}”</span> : null}
    </span>
  );
  return (
    <Tooltip content={tip}>
      <Link
        to={reviewHref(intakeId, targetTab(evidence), { question: evidence.question_id, answer: evidence.answer_id })}
        data-testid="intake-evidence-ref"
        data-supports={evidence.supports}
        className={cn(
          "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-sm border border-line bg-surface px-2 py-1 text-left text-xs text-fg-muted transition-colors hover:border-line-strong hover:text-fg",
          className,
        )}
      >
        <Icon className={cn("size-3.5 shrink-0", TONE[evidence.supports])} role="img" aria-label={SUPPORT_LABELS[evidence.supports]} />
        <span className="min-w-0 truncate">{text}</span>
      </Link>
    </Tooltip>
  );
}
