/**
 * Integrity event presentation and review controls, shared by the session
 * Integrity tab and the organisation-wide review queue. Wording describes the
 * signal, never the person.
 */

import { Info } from "lucide-react";
import { useState } from "react";

import { IntegritySeverityBadge } from "@/components/domain/IntegritySeverityBadge";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Textarea } from "@/components/ui/Textarea";
import { toast } from "@/components/ui/Toaster";
import { formatClock, formatDateTime, formatPercent } from "@/lib/format";
import { INTEGRITY_ACTION_LABELS, INTEGRITY_SOURCE_LABELS, labelFor } from "@/lib/labels";
import type { IntegrityReviewStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useReviewIntegrityEvent } from "../api/hooks";
import type { IntegrityEventView } from "../api/types";
import { describeDetails, eventTitle, OBSERVATIONS_BANNER, OBSERVATIONS_DETAIL, REVIEW_OPTIONS } from "../lib/integrity";
import { EvidenceFrame } from "./media";

export function ObservationsBanner({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <Callout tone="neutral" icon={<Info />} title={OBSERVATIONS_BANNER} className={className}>
      {compact ? "Review each signal in context before drawing any conclusion." : OBSERVATIONS_DETAIL}
    </Callout>
  );
}

export function ReviewControls({ event, onDone, className }: { event: IntegrityEventView; onDone?: () => void; className?: string }) {
  const [choice, setChoice] = useState<Exclude<IntegrityReviewStatus, "unreviewed"> | null>(null);
  const [note, setNote] = useState("");
  const review = useReviewIntegrityEvent();

  const save = () => {
    if (!choice) return;
    review.mutate(
      { id: event.id, review_status: choice, review_note: note.trim() || null },
      {
        onSuccess: () => {
          toast.success(`Review saved: ${REVIEW_OPTIONS.find((o) => o.value === choice)?.label.toLowerCase()}`);
          setChoice(null);
          setNote("");
          onDone?.();
        },
      },
    );
  };

  return (
    <div className={cn("grid gap-2.5", className)}>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={`Review: ${eventTitle(event)}`}>
        {REVIEW_OPTIONS.map((option) => (
          <Button
            key={option.value}
            size="xs"
            variant={choice === option.value ? "primary" : "outline"}
            aria-pressed={choice === option.value}
            title={option.description}
            onClick={() => setChoice(choice === option.value ? null : option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>
      {choice ? (
        <div className="grid gap-2">
          <p className="text-xs text-fg-muted">{REVIEW_OPTIONS.find((o) => o.value === choice)?.description}</p>
          <Textarea
            rows={2}
            autoResize
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Note for other reviewers (optional): what you checked and why"
            aria-label="Review note"
          />
          <div className="flex items-center gap-2">
            <Button size="xs" onClick={save} loading={review.isPending}>
              Save review
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setChoice(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export interface IntegrityEventRowProps {
  event: IntegrityEventView;
  canReview: boolean;
  /** Show candidate / job (queue). */
  showContext?: boolean;
  className?: string;
}

/** One event with its facts, evidence frame and (when permitted) review controls. */
export function IntegrityEventRow({ event, canReview, showContext = false, className }: IntegrityEventRowProps) {
  const details = describeDetails(event);
  const [changing, setChanging] = useState(false);
  const reviewed = event.review_status !== "unreviewed";
  return (
    <li
      data-testid="integrity-event"
      data-event-id={event.id}
      data-review-status={event.review_status}
      className={cn("grid gap-3 py-4 sm:grid-cols-[4.5rem_minmax(0,1fr)] sm:gap-4", className)}
    >
      <div className="flex items-center gap-2 sm:grid sm:content-start sm:gap-1">
        <span className="text-[13px] font-medium text-fg tabular-nums">{formatClock(event.elapsed_seconds)}</span>
        <span className="text-xs text-fg-subtle">{labelFor(INTEGRITY_SOURCE_LABELS, event.source)}</span>
      </div>
      <div className="flex min-w-0 gap-4">
        <div className="grid min-w-0 flex-1 gap-2">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="text-sm font-semibold text-fg">{eventTitle(event)}</span>
            <IntegritySeverityBadge severity={event.severity} size="sm" />
            <StatusBadge kind="integrity_review" value={event.review_status} size="sm" />
          </div>
          {showContext && (event.candidate || event.job) ? (
            <p className="text-xs text-fg-muted">
              {event.candidate?.full_name}
              {event.candidate && event.job ? ", " : ""}
              {event.job?.title}
            </p>
          ) : null}
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            {details.map((d) => (
              <div key={d.label} className="flex gap-1">
                <dt className="text-fg-muted">{d.label}</dt>
                <dd className="font-medium text-fg">{d.value}</dd>
              </div>
            ))}
            {event.action && event.action !== "log_only" ? (
              <div className="flex gap-1">
                <dt className="text-fg-muted">Response</dt>
                <dd className="font-medium text-fg">{labelFor(INTEGRITY_ACTION_LABELS, event.action)}</dd>
              </div>
            ) : null}
          </dl>
          {event.review_status !== "unreviewed" ? (
            <p className="text-xs text-fg-muted">
              Reviewed{event.reviewed_by?.full_name ? ` by ${event.reviewed_by.full_name}` : ""}
              {event.reviewed_at ? `, ${formatDateTime(event.reviewed_at)}` : ""}
              {event.review_note ? <span className="mt-0.5 block text-fg">“{event.review_note}”</span> : null}
            </p>
          ) : null}
          {canReview && (!reviewed || changing) ? <ReviewControls event={event} className="mt-1" onDone={() => setChanging(false)} /> : null}
          {canReview && reviewed && !changing ? (
            <button type="button" className="w-fit rounded-xs text-xs font-medium text-accent hover:underline" onClick={() => setChanging(true)}>
              Change review
            </button>
          ) : null}
        </div>
        <EvidenceFrame event={event} />
      </div>
    </li>
  );
}

export function confidenceText(event: IntegrityEventView): string {
  return typeof event.confidence === "number" ? formatPercent(event.confidence) : "Not applicable";
}
