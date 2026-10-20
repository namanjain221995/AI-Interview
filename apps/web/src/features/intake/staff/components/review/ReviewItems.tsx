import { CheckCircle2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { lookup, RESOLUTION_LABELS, REVIEW_ITEM_LABELS } from "../../labels";
import type { ReviewItem } from "../../types";
import { SeverityBadge } from "../IntakeBadges";
import { EvidenceChip } from "./EvidenceChip";

const SEVERITY_ORDER = { important: 0, attention: 1, info: 2 } as const;
const DETAILS_SHOWN = 4;

export function sortReviewItems(items: readonly ReviewItem[]): ReviewItem[] {
  return [...items].sort((a, b) => Number(Boolean(a.resolution)) - Number(Boolean(b.resolution)) || SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

/** Open review items of one kind and severity, shown and resolved together (an interview that ended early raises
 * one "Stage not completed" per stage and one "Mandatory item not covered" per question). */
export interface ReviewItemGroup {
  key: string;
  title: string;
  items: ReviewItem[];
}

export function reviewItemTitle(item: ReviewItem): string {
  return item.title || lookup(REVIEW_ITEM_LABELS, item.kind);
}

/** Sorted groups: open items of the same kind and severity share a group; resolved items stay one per group. */
export function groupReviewItems(items: readonly ReviewItem[]): ReviewItemGroup[] {
  const groups: ReviewItemGroup[] = [];
  const open = new Map<string, ReviewItemGroup>();
  for (const item of sortReviewItems(items)) {
    if (item.resolution) {
      groups.push({ key: item.id, title: reviewItemTitle(item), items: [item] });
      continue;
    }
    const key = `${item.kind}:${item.severity}`;
    const group = open.get(key);
    if (group) group.items.push(item);
    else {
      const created = { key: item.id, title: reviewItemTitle(item), items: [item] };
      open.set(key, created);
      groups.push(created);
    }
  }
  return groups;
}

/** The details of a group's items, the first few shown and the rest behind a toggle. */
export function GroupDetails({ group }: { group: ReviewItemGroup }) {
  const [all, setAll] = useState(false);
  const details = group.items.map((i) => i.detail).filter(Boolean);
  if (details.length === 0) return null;
  if (details.length === 1) return <p className="text-[13px] leading-5 text-fg-muted">{details[0]}</p>;
  const shown = all ? details : details.slice(0, DETAILS_SHOWN);
  return (
    <div className="grid grid-cols-1 gap-1">
      <ul className="grid list-disc grid-cols-1 gap-0.5 pl-5 text-[13px] leading-5 text-fg-muted">
        {shown.map((d, i) => (
          <li key={i}>{d}</li>
        ))}
      </ul>
      {details.length > DETAILS_SHOWN ? (
        <button type="button" onClick={() => setAll((v) => !v)} className="w-fit rounded-xs text-xs font-medium text-fg-muted hover:text-fg">
          {all ? "Show fewer" : `Show all ${details.length}`}
        </button>
      ) : null}
    </div>
  );
}

export function GroupTitle({ group }: { group: ReviewItemGroup }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SeverityBadge severity={group.items[0].severity} />
      <span className="min-w-0 text-sm font-medium text-fg">{group.title}</span>
      {group.items.length > 1 ? (
        <Badge size="sm" tone="outline">
          {group.items.length}
        </Badge>
      ) : null}
    </div>
  );
}

/** Review items with their evidence. They never decide anything; a person resolves them. */
export function ReviewItemList({ intakeId, items, compact = false }: { intakeId: string; items: readonly ReviewItem[]; compact?: boolean }) {
  if (items.length === 0) return <p className="text-sm text-fg-muted">No review items. Nothing in this interview needs a closer look.</p>;
  return (
    <ul className="grid grid-cols-1 divide-y divide-line-subtle">
      {groupReviewItems(items).map((group) => {
        const item = group.items[0];
        const evidence = group.items.flatMap((i) => i.evidence).slice(0, 4);
        return (
          <li key={group.key} data-testid="intake-review-item" data-kind={item.kind} className={cn("grid grid-cols-1 gap-1.5 py-3 first:pt-0 last:pb-0", item.resolution && "opacity-75")}>
            <GroupTitle group={group} />
            {!compact ? <GroupDetails group={group} /> : null}
            {evidence.length && !compact ? (
              <ul className="flex flex-wrap gap-1.5">
                {evidence.map((ref, i) => (
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
        );
      })}
    </ul>
  );
}
