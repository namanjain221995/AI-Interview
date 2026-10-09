import { CheckCircle2 } from "lucide-react";

import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { lookup, RESOLUTION_LABELS, REVIEW_ITEM_LABELS } from "../../labels";
import type { ReviewItem } from "../../types";
import { SeverityBadge } from "../IntakeBadges";
import { EvidenceChip } from "./EvidenceChip";

const SEVERITY_ORDER = { important: 0, attention: 1, info: 2 } as const;

export function sortReviewItems(items: readonly ReviewItem[]): ReviewItem[] {
  return [...items].sort((a, b) => Number(Boolean(a.resolution)) - Number(Boolean(b.resolution)) || SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

/** Review items with their evidence. They never decide anything; a person resolves them. */
export function ReviewItemList({ intakeId, items, compact = false }: { intakeId: string; items: readonly ReviewItem[]; compact?: boolean }) {
  if (items.length === 0) return <p className="text-sm text-fg-muted">No review items. Nothing in this interview needs a closer look.</p>;
  return (
    <ul className="grid grid-cols-1 divide-y divide-line-subtle">
      {sortReviewItems(items).map((item) => (
        <li key={item.id} data-testid="intake-review-item" data-kind={item.kind} className={cn("grid grid-cols-1 gap-1.5 py-3 first:pt-0 last:pb-0", item.resolution && "opacity-75")}>
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={item.severity} />
            <span className="min-w-0 text-sm font-medium text-fg">{item.title || lookup(REVIEW_ITEM_LABELS, item.kind)}</span>
          </div>
          {item.detail && !compact ? <p className="text-[13px] leading-5 text-fg-muted">{item.detail}</p> : null}
          {item.evidence.length && !compact ? (
            <ul className="flex flex-wrap gap-1.5">
              {item.evidence.slice(0, 4).map((ref, i) => (
                <li key={i} className="max-w-full min-w-0">
                  <EvidenceChip intakeId={intakeId} evidence={ref} />
                </li>
              ))}
            </ul>
          ) : null}
          {item.resolution ? (
            <p className="flex items-center gap-1.5 text-xs text-fg-muted">
              <CheckCircle2 className="size-3.5 text-success" aria-hidden="true" />
              {RESOLUTION_LABELS[item.resolution.resolution]}
              {item.resolution.reviewer ? ` by ${item.resolution.reviewer.full_name}` : ""}
              {item.resolution.at ? `, ${formatDateTime(item.resolution.at)}` : ""}
              {item.resolution.note ? `: ${item.resolution.note}` : ""}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
