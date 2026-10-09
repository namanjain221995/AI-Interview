import { ExternalLink, FileText, Flag, MonitorPlay, Video } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/Sheet";
import { Skeleton } from "@/components/ui/Skeleton";
import { Textarea } from "@/components/ui/Textarea";
import { formatDateTime, formatRelativeOrDate } from "@/lib/format";

import { LoadError } from "@/features/recruiter/admin/components/Controls";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";

import { useFlagReview, useRegistration } from "../api";
import { formatAnswer } from "../formSchema";
import { FLAG_LABELS, labelOr, REGISTRATION_STATUS_LABELS, REGISTRATION_STATUS_TONES, ROUND_STATE_LABELS, ROUND_STATE_TONES, STATUS_REASON_LABELS } from "../status";
import type { Registration, RegistrationDetail, RegistrationRound } from "../types";
import { retakeableRounds } from "./RetakeDialog";

export type RegistrationAction = "advance" | "retake" | "cancel" | "unblock" | "reissue";

/** Which actions make sense for a registration right now (the server re-checks). */
export function availableActions(reg: Registration): RegistrationAction[] {
  if (reg.status === "blocked") return ["unblock"];
  const out: RegistrationAction[] = [];
  if (reg.status === "awaiting_review" && reg.current_round_index < reg.rounds.length - 1) out.push("advance");
  if (retakeableRounds(reg).length && reg.status !== "cancelled") out.push("retake");
  out.push("reissue");
  if (!["completed", "cancelled"].includes(reg.status)) out.push("cancel");
  return out;
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="grid grid-cols-1 gap-3 border-t border-line-subtle pt-5 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function RoundAttempt({ round, index }: { round: RegistrationRound; index: number }) {
  return (
    <li className="grid grid-cols-1 gap-2 rounded-md border border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-fg-subtle tabular-nums">{index + 1}</span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg">{round.name}</span>
        {round.attempt > 1 ? (
          <Badge tone="outline" size="sm">
            Attempt {round.attempt}
          </Badge>
        ) : null}
        <Badge tone={ROUND_STATE_TONES[round.state]} size="sm">
          {ROUND_STATE_LABELS[round.state]}
        </Badge>
      </div>
      {round.started_at || round.completed_at || round.expires_at || round.end_reason ? (
        <p className="text-xs text-fg-muted">
          {round.completed_at ? `Finished ${formatDateTime(round.completed_at)}` : round.started_at ? `Started ${formatDateTime(round.started_at)}` : round.expires_at ? `Must start by ${formatDateTime(round.expires_at)}` : null}
          {round.end_reason && round.end_reason !== "completed" ? `, ended: ${round.end_reason.replace(/_/g, " ")}` : null}
        </p>
      ) : null}
      {round.session_id ? (
        <div className="flex flex-wrap gap-1.5">
          <Button asChild variant="outline" size="xs" leftIcon={<MonitorPlay />}>
            <Link to={`/app/sessions/${round.session_id}`}>Session</Link>
          </Button>
          {round.state === "completed" ? (
            <>
              <Button asChild variant="outline" size="xs" leftIcon={<FileText />}>
                <Link to={`/app/reports/${round.session_id}`}>Report</Link>
              </Button>
              <Button asChild variant="outline" size="xs" leftIcon={<Video />}>
                <Link to={`/app/sessions/${round.session_id}?tab=recording`}>Recording</Link>
              </Button>
            </>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function FlagReview({ linkId, detail, canReview }: { linkId: string; detail: RegistrationDetail; canReview: boolean }) {
  const review = useFlagReview(linkId);
  const [note, setNote] = useState("");
  useEffect(() => setNote(""), [detail.id]);
  if (!detail.flags.length) return <p className="text-sm text-fg-muted">No signals. Exact repeats (same email or mobile number) can't register at all.</p>;
  const decide = async (decision: "cleared" | "confirmed") => {
    try {
      await review.mutateAsync({ registrationId: detail.id, decision, ...(note.trim() ? { note: note.trim() } : {}) });
      toast.success(decision === "cleared" ? "Marked as not a duplicate" : "Marked as a likely duplicate", {
        description: decision === "confirmed" ? "Nothing changes automatically. Cancel the registration if you decide to." : undefined,
      });
    } catch (error) {
      toast.error("Couldn't save the review", { description: describeFailure(error) });
    }
  };
  return (
    <div className="grid grid-cols-1 gap-3">
      <ul className="grid grid-cols-1 gap-2">
        {detail.flags.map((flag, i) => (
          <li key={`${flag.code}-${i}`} className="flex gap-2.5 rounded-md bg-surface-subtle px-3 py-2.5 text-sm">
            <Flag className={flag.severity === "high" ? "mt-0.5 size-4 shrink-0 text-warning" : "mt-0.5 size-4 shrink-0 text-fg-subtle"} aria-hidden="true" />
            <span className="grid min-w-0 gap-0.5">
              <span className="font-medium text-fg">
                {labelOr(FLAG_LABELS, flag.code)}
                <span className="font-normal text-fg-muted">{flag.severity === "high" ? ", worth a look" : ", low priority"}</span>
              </span>
              <span className="text-fg-muted">{flag.message}</span>
              <span className="text-xs text-fg-subtle">{formatRelativeOrDate(flag.at)}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-fg-muted">Signals are observations for a person to review. They never stop the candidate or change their rounds.</p>
      {detail.flag_review === "cleared" || detail.flag_review === "confirmed" ? (
        <p className="text-sm text-fg">
          {detail.flag_review === "cleared" ? "Reviewed: not a duplicate" : "Reviewed: likely the same person"}
          {detail.flag_reviewed_at ? <span className="text-fg-muted">, {formatDateTime(detail.flag_reviewed_at)}</span> : null}
          {detail.flag_review_note ? <span className="mt-1 block text-[13px] text-fg-muted">“{detail.flag_review_note}”</span> : null}
        </p>
      ) : null}
      {canReview ? (
        <div className="grid grid-cols-1 gap-2">
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={2} placeholder="Note (optional), e.g. Siblings sharing a laptop" aria-label="Review note" />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => void decide("cleared")} loading={review.isPending && review.variables?.decision === "cleared"} data-testid="registration-flag-clear">
              Not a duplicate
            </Button>
            <Button size="sm" variant="outline" onClick={() => void decide("confirmed")} loading={review.isPending && review.variables?.decision === "confirmed"} data-testid="registration-flag-confirm">
              Likely the same person
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export interface RegistrationDetailSheetProps {
  linkId: string;
  registrationId: string | null;
  onOpenChange: (open: boolean) => void;
  canManage: boolean;
  canReviewFlags: boolean;
  onAction: (action: RegistrationAction, registration: Registration) => void;
}

/** Everything about one registration: answers, rounds and attempts, signals, personal link, timeline. */
export function RegistrationDetailSheet({ linkId, registrationId, onOpenChange, canManage, canReviewFlags, onAction }: RegistrationDetailSheetProps) {
  const query = useRegistration(linkId, registrationId);
  const detail = query.data;
  const actions = detail && canManage ? availableActions(detail) : [];
  const timeline: Array<{ at: string; label: string }> = detail
    ? [
        { at: detail.created_at, label: "Registered" },
        ...(detail.privacy_ack_at ? [{ at: detail.privacy_ack_at, label: "Read the data-use notice" }] : []),
        ...detail.attempts.flatMap((a) => [
          ...(a.started_at ? [{ at: a.started_at, label: `Started ${a.name}${a.attempt > 1 ? ` (attempt ${a.attempt})` : ""}` }] : []),
          ...(a.completed_at ? [{ at: a.completed_at, label: `Finished ${a.name}${a.attempt > 1 ? ` (attempt ${a.attempt})` : ""}` }] : []),
        ]),
        ...(detail.personal_link_issued_at ? [{ at: detail.personal_link_issued_at, label: "Personal link issued" }] : []),
        ...(detail.flag_reviewed_at ? [{ at: detail.flag_reviewed_at, label: "Signals reviewed" }] : []),
      ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
    : [];
  const extraAttempts = detail ? detail.attempts.filter((a) => a.attempt > 1 || detail.attempts.filter((b) => b.sequence === a.sequence).length > 1) : [];

  return (
    <Sheet open={Boolean(registrationId)} onOpenChange={onOpenChange}>
      <SheetContent side="right" size="lg" aria-describedby={undefined} data-testid="registration-detail">
        {query.isPending ? (
          <div className="grid gap-4 p-5" aria-busy="true" aria-label="Loading">
            <SheetTitle className="sr-only">Registration</SheetTitle>
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-64" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : query.error ? (
          <div className="p-5 pr-14">
            <SheetTitle className="sr-only">Registration</SheetTitle>
            <LoadError error={query.error} onRetry={() => void query.refetch()} title="Couldn't load this registration" />
          </div>
        ) : detail ? (
          <>
            <SheetHeader>
              <SheetTitle>{detail.full_name}</SheetTitle>
              <SheetDescription>
                {detail.email}
                {detail.phone ? `, ${detail.phone}` : ""}
              </SheetDescription>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <Badge tone={REGISTRATION_STATUS_TONES[detail.status]} dot>
                  {REGISTRATION_STATUS_LABELS[detail.status]}
                </Badge>
                {detail.status_reason ? <span className="text-xs text-fg-muted">{labelOr(STATUS_REASON_LABELS, detail.status_reason)}</span> : null}
              </div>
            </SheetHeader>
            <SheetBody className="grid content-start gap-5">
              {detail.candidate ? (
                <Button asChild variant="outline" size="sm" className="w-fit" rightIcon={<ExternalLink />}>
                  <Link to={`/app/candidates/${detail.candidate.id}`}>Candidate profile, résumé and interviews</Link>
                </Button>
              ) : (
                <Callout tone="neutral">Blocked registrations have no candidate record until you allow them.</Callout>
              )}

              <Section title="Rounds">
                <ol className="grid grid-cols-1 gap-2">
                  {detail.rounds.map((round, i) => (
                    <RoundAttempt key={`${round.sequence}-${round.attempt}`} round={round} index={i} />
                  ))}
                </ol>
                {extraAttempts.length > 0 ? (
                  <details className="text-sm">
                    <summary className="cursor-pointer text-fg-muted hover:text-fg">All attempts ({detail.attempts.length})</summary>
                    <ol className="mt-2 grid grid-cols-1 gap-2">
                      {detail.attempts.map((round) => (
                        <RoundAttempt key={`a-${round.sequence}-${round.attempt}`} round={round} index={round.sequence} />
                      ))}
                    </ol>
                  </details>
                ) : null}
              </Section>

              <Section title="Signals">
                <FlagReview linkId={linkId} detail={detail} canReview={canReviewFlags} />
              </Section>

              <Section title="Form answers">
                {detail.answers.length ? (
                  <dl className="grid grid-cols-1 divide-y divide-line-subtle">
                    {detail.answers.map((answer) => (
                      <div key={answer.key} className="grid grid-cols-1 gap-0.5 py-2 text-sm sm:grid-cols-[minmax(8rem,40%)_1fr] sm:gap-3">
                        <dt className="text-fg-muted">{answer.label}</dt>
                        <dd className="min-w-0 break-words text-fg">{formatAnswer(answer.value)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="text-sm text-fg-muted">No answers stored.</p>
                )}
                <p className="text-xs text-fg-muted">Answers are visible to your team only and never used by the AI.</p>
              </Section>

              <Section title="Personal link">
                <p className="text-sm text-fg-muted">
                  {detail.personal_link_issued_at ? `Issued ${formatDateTime(detail.personal_link_issued_at)}. ` : null}
                  The candidate uses it to come back on any device. If they lost it, make a new one and send it to them; the old one stops working.
                </p>
              </Section>

              <Section title="Timeline">
                <ol className="grid grid-cols-1 gap-2">
                  {timeline.map((item, i) => (
                    <li key={`${item.label}-${i}`} className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="text-fg">{item.label}</span>
                      <span className="shrink-0 text-xs text-fg-muted tabular-nums">{formatDateTime(item.at)}</span>
                    </li>
                  ))}
                </ol>
              </Section>
            </SheetBody>
            {actions.length ? (
              <SheetFooter className="flex-wrap justify-start">
                {actions.includes("advance") ? (
                  <Button size="sm" onClick={() => onAction("advance", detail)} data-testid="registration-advance">
                    Advance to next round
                  </Button>
                ) : null}
                {actions.includes("unblock") ? (
                  <Button size="sm" onClick={() => onAction("unblock", detail)} data-testid="registration-unblock">
                    Allow registration
                  </Button>
                ) : null}
                {actions.includes("retake") ? (
                  <Button size="sm" variant="outline" onClick={() => onAction("retake", detail)} data-testid="registration-retake">
                    Allow another attempt
                  </Button>
                ) : null}
                {actions.includes("reissue") ? (
                  <Button size="sm" variant="outline" onClick={() => onAction("reissue", detail)} data-testid="registration-reissue-link">
                    New personal link
                  </Button>
                ) : null}
                {actions.includes("cancel") ? (
                  <Button size="sm" variant="ghost" className="text-danger-fg" onClick={() => onAction("cancel", detail)} data-testid="registration-cancel">
                    Cancel registration
                  </Button>
                ) : null}
              </SheetFooter>
            ) : null}
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
