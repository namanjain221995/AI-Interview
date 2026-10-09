import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Clock, Code2 } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { errorMessage } from "@/lib/api";
import { formatList } from "@/lib/format";
import { DIFFICULTY_LABELS, CODE_LANGUAGE_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";
import type { Difficulty, RoundType } from "@/lib/types";
import { cn } from "@/lib/utils";

import { selectRound, type PortalRoundView } from "../api";
import { ResumeProgressChip } from "../components/ResumeProgressChip";
import { StepFooter, StepHeader } from "../components/StepLayout";
import { JourneyBoundary, useJourney, type JourneyContext } from "../hooks";
import { roundSelectionApplies, stepAfter } from "../journey";

export default function RoundSelectPage() {
  useDocumentTitle("Choose your interview");
  const { query, ctx } = useJourney("round");
  return (
    <div>
      <StepHeader
        title="Choose your interview"
        lede="Pick the round you'd like to take today. Each one focuses on different topics; choose the one that best shows your strengths."
        aside={ctx ? <ResumeProgressChip interviewId={ctx.interviewId} /> : null}
      />
      <div className="mt-8">
        <JourneyBoundary query={query}>{ctx ? <RoundSelect ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

function RoundCard({ round, selected, onSelect, tabIndex, onKeyDown }: { round: PortalRoundView; selected: boolean; onSelect: () => void; tabIndex: number; onKeyDown: (e: KeyboardEvent) => void }) {
  const typeLabel = ROUND_TYPE_LABELS[round.roundType as RoundType] ?? round.roundType;
  const difficulty = round.difficulty ? DIFFICULTY_LABELS[round.difficulty as Difficulty] ?? round.difficulty : null;
  return (
    <div
      role="radio"
      aria-checked={selected}
      tabIndex={tabIndex}
      data-testid={`round-option-${round.id}`}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      className={cn(
        "relative flex cursor-pointer flex-col rounded-lg border bg-surface p-5 text-left outline-none transition-[border-color,box-shadow] duration-150",
        "focus-visible:ring-3 focus-visible:ring-accent/25",
        selected ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-line hover:border-line-strong",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] text-fg-muted">{typeLabel}</p>
          <h3 className="mt-0.5 text-heading text-fg">{round.name}</h3>
        </div>
        <span
          className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border", selected ? "border-primary bg-primary text-primary-fg" : "border-line-emphasis")}
          aria-hidden="true"
        >
          {selected ? <Check className="size-3" strokeWidth={3} /> : null}
        </span>
      </div>
      {round.description || round.instructions ? <p className="mt-2 line-clamp-3 text-sm leading-6 text-fg-muted">{round.description ?? round.instructions}</p> : null}
      <dl className="mt-4 grid gap-2 text-sm">
        <div className="flex items-start gap-2 text-fg">
          <Clock className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          <dt className="sr-only">Length</dt>
          <dd>About {round.durationMinutes} minutes{difficulty ? `, ${difficulty.toLowerCase()} level` : ""}</dd>
        </div>
        <div className="flex items-start gap-2 text-fg">
          <Code2 className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          <dt className="sr-only">Coding</dt>
          <dd>
            {round.codingEnabled
              ? `Includes a coding exercise${round.codingLanguages.length ? ` (${formatList(round.codingLanguages.map((l) => CODE_LANGUAGE_LABELS[l] ?? l))})` : ""}`
              : "No coding exercise"}
          </dd>
        </div>
      </dl>
      {round.competencies.length ? (
        <div className="mt-4 border-t border-line pt-3">
          <p className="text-[13px] text-fg-muted">Topics</p>
          <p className="mt-1 text-sm text-fg">{formatList(round.competencies.slice(0, 6))}</p>
        </div>
      ) : null}
    </div>
  );
}

function RoundSelect({ ctx }: { ctx: JourneyContext }) {
  const { interview } = ctx;
  const queryClient = useQueryClient();
  const applies = roundSelectionApplies(interview);
  const [selected, setSelected] = useState<string | null>(interview.selectedRoundId);
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (!applies) ctx.goTo(stepAfter("round", interview), { replace: true });
  }, [applies, ctx, interview]);

  const save = useMutation({
    mutationFn: (roundId: string) => selectRound(ctx.interviewId, roundId),
    meta: { silentError: true },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(ctx.interviewId) });
      ctx.next();
    },
  });

  if (!applies) return null;
  const rounds = interview.rounds;
  const move = (from: number, delta: number) => {
    const next = rounds[(from + delta + rounds.length) % rounds.length]!;
    setSelected(next.id);
    refs.current[next.id]?.focus();
  };

  return (
    <div>
      <div role="radiogroup" aria-label="Interview rounds" className="grid gap-4 sm:grid-cols-2">
        {rounds.map((round, index) => (
          <div key={round.id} ref={(el) => void (refs.current[round.id] = el?.firstElementChild as HTMLDivElement | null)}>
            <RoundCard
              round={round}
              selected={selected === round.id}
              onSelect={() => setSelected(round.id)}
              tabIndex={selected === round.id || (!selected && index === 0) ? 0 : -1}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowRight") {
                  e.preventDefault();
                  move(index, 1);
                } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
                  e.preventDefault();
                  move(index, -1);
                } else if (e.key === " " || e.key === "Enter") {
                  e.preventDefault();
                  setSelected(round.id);
                }
              }}
            />
          </div>
        ))}
      </div>
      {save.isError ? (
        <Callout tone="danger" className="mt-6">
          {errorMessage(save.error, "We couldn't save your choice. Try again.")}
        </Callout>
      ) : null}
      <StepFooter onBack={ctx.back}>
        <Button size="lg" disabled={!selected} loading={save.isPending} onClick={() => selected && save.mutate(selected)} data-testid="round-continue">
          Continue
        </Button>
      </StepFooter>
    </div>
  );
}
