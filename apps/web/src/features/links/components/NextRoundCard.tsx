import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, CircleDashed, Hourglass, Loader2, Lock } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { continueToRound, resetPortalCache, useLinkProgress } from "../api";
import type { LinkProgress, RoundState } from "../types";

const CANDIDATE_ROUND_LABELS: Record<RoundState, { label: string; tone: BadgeTone }> = {
  locked: { label: "Not yet open", tone: "outline" },
  invited: { label: "Ready", tone: "accent" },
  in_progress: { label: "In progress", tone: "signal" },
  completed: { label: "Completed", tone: "success" },
  expired: { label: "Expired", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

const POLL_MS = 5_000;
const POLL_WINDOW_MS = 2 * 60_000;

export interface NextRoundCardProps {
  /** Poll while the just-finished round is being recorded (CompletePage). */
  poll?: boolean;
  className?: string;
}

function RoundIcon({ state }: { state: RoundState }) {
  if (state === "completed") return <Check className="size-3.5" />;
  if (state === "locked") return <Lock className="size-3" />;
  return <CircleDashed className="size-3.5" />;
}

/**
 * Round progress for interviews that came from an open link (OPEN_LINKS.md 7.3).
 * Renders nothing for ordinary invitations (`/portal/link/progress` answers 404).
 */
export function NextRoundCard({ poll = false, className }: NextRoundCardProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const pollUntil = useRef(Date.now() + POLL_WINDOW_MS);
  const progress = useLinkProgress({
    pollMs: poll ? (data) => (data && data.next_action === "continue_current" && Date.now() < pollUntil.current ? POLL_MS : false) : undefined,
  });
  const [continuing, setContinuing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const data: LinkProgress | undefined = progress.data;
  if (!data || progress.isError) return null;
  const multi = data.rounds.length > 1;
  if (!multi && (data.next_action === "continue_current" || data.next_action === "all_done")) return null;

  const current = data.rounds.find((r) => r.is_current) ?? null;
  const currentNumber = current ? data.rounds.indexOf(current) + 1 : null;
  const nextNumber = data.next_interview_id ? data.rounds.findIndex((r) => r.interview_id === data.next_interview_id) + 1 || null : null;
  const waiting = poll && data.next_action === "continue_current" && Date.now() < pollUntil.current;

  const goNext = async () => {
    if (!data.next_interview_id) return;
    setContinuing(true);
    setError(null);
    try {
      const result = await continueToRound(data.next_interview_id);
      resetPortalCache(queryClient);
      navigate(`/candidate/${result.interview_id}/${result.next_step || "welcome"}`);
    } catch (caught) {
      setError(errorMessage(caught, "We couldn't open your next round. Try again, or use your personal link."));
      setContinuing(false);
    }
  };

  return (
    <section
      data-testid="next-round-card"
      data-next-action={data.next_action}
      aria-labelledby="next-round-heading"
      className={cn("rounded-xl border border-line bg-surface p-5 sm:p-6", data.next_action === "start_next" && "border-accent-line", className)}
    >
      <h2 id="next-round-heading" className="text-heading text-fg">
        {data.next_action === "start_next" ? (nextNumber ? `Round ${nextNumber} is ready` : "Your next round is ready") : data.next_action === "all_done" ? "You've completed all rounds" : "Your rounds"}
      </h2>
      {data.link_name ? <p className="mt-0.5 text-sm text-fg-muted">{data.link_name}</p> : null}

      <ol className="mt-4 grid grid-cols-1 gap-2" aria-label="Rounds">
        {data.rounds.map((round, i) => {
          const status = CANDIDATE_ROUND_LABELS[round.state] ?? CANDIDATE_ROUND_LABELS.locked;
          return (
            <li key={`${round.sequence}-${round.name}`} className={cn("flex items-center gap-3 rounded-md px-3 py-2", round.is_current ? "bg-surface-subtle" : undefined)}>
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums",
                  round.state === "completed" ? "bg-success-soft text-success-fg" : "bg-neutral-soft text-neutral-fg",
                )}
                aria-hidden="true"
              >
                {round.state === "completed" || round.state === "locked" ? <RoundIcon state={round.state} /> : i + 1}
              </span>
              <span className="grid min-w-0 flex-1">
                <span className="truncate text-sm font-medium text-fg">{round.name}</span>
                <span className="text-xs text-fg-muted">About {round.duration_minutes} minutes</span>
              </span>
              <Badge size="sm" tone={status.tone}>
                {status.label}
              </Badge>
            </li>
          );
        })}
      </ol>

      <div className="mt-5 grid grid-cols-1 gap-3">
        {data.next_action === "start_next" ? (
          <>
            <p className="text-sm leading-6 text-fg-muted">
              You can start {data.next_round_name ?? "the next round"} now{data.deadline ? `, until ${formatDateTime(data.deadline)}` : ""}. If you'd rather take a break, come back later with your personal link.
            </p>
            <Button size="lg" rightIcon={<ArrowRight />} loading={continuing} loadingText="Opening" onClick={() => void goNext()} data-testid="next-round-continue" className="justify-self-start max-sm:w-full">
              {nextNumber ? `Continue to round ${nextNumber}` : "Continue to the next round"}
            </Button>
          </>
        ) : data.next_action === "wait_for_review" ? (
          <p className="flex gap-2 text-sm leading-6 text-fg-muted">
            <Hourglass className="mt-1 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
            The hiring team will review {currentNumber ? `round ${currentNumber}` : "your round"}. Come back with your personal link to check for the next round.
          </p>
        ) : data.next_action === "all_done" ? (
          <p className="text-sm leading-6 text-fg-muted">Thank you. The hiring team will be in touch about next steps.</p>
        ) : data.next_action === "closed" ? (
          <p className="text-sm leading-6 text-fg-muted">This registration is closed. Contact the hiring team if you have questions.</p>
        ) : waiting ? (
          <p className="flex items-center gap-2 text-sm text-fg-muted" role="status">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            Checking for your next round
          </p>
        ) : poll ? (
          <p className="text-sm leading-6 text-fg-muted">Your next round will appear here. You can also come back later with your personal link.</p>
        ) : null}
        {data.deadline && data.next_action === "continue_current" ? <p className="text-xs text-fg-muted">Start before {formatDateTime(data.deadline)}.</p> : null}
        {error ? <Callout tone="danger">{error}</Callout> : null}
      </div>
    </section>
  );
}
