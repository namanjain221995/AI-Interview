import type { ReactNode } from "react";

import { useInterviewState, type InterviewStore } from "@/features/realtime/store";
import { cn } from "@/lib/utils";

import { activityCue } from "./activityModel";

/**
 * The room layout while a reading or speaking exercise is up: the panel in the
 * middle, the interviewer small and frameless, the camera out of the text's way.
 * Phones and tablets: one slim row (interviewer, cue, camera) above the panel.
 * Wide screens: interviewer and cue on the left, the panel centred (48rem), the
 * camera bottom-right. One DOM per element; only the grid placement changes.
 */
export function ActivityStage({ presence, cue, camera, notices, children, className }: { presence: ReactNode; cue: ReactNode; camera: ReactNode; notices?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div
      data-testid="activity-stage"
      className={cn(
        "mx-auto grid h-full min-h-0 w-full max-w-[96rem] grid-cols-[auto_minmax(0,1fr)_auto] grid-rows-[auto_minmax(0,1fr)] gap-x-3 gap-y-3 px-3 pt-3 pb-2 sm:px-6 sm:pt-4 sm:pb-3",
        "xl:grid-cols-[minmax(0,1fr)_minmax(0,48rem)_minmax(0,1fr)] xl:grid-rows-[auto_auto_minmax(0,1fr)] xl:gap-x-10 xl:gap-y-4",
        className,
      )}
    >
      <div className="relative col-start-1 row-start-1 size-14 self-center sm:size-16 xl:aspect-square xl:size-auto xl:w-full xl:max-w-[13rem] xl:justify-self-center xl:self-start xl:pt-2">
        {presence}
      </div>
      <div className="col-start-2 row-start-1 min-w-0 self-center xl:col-start-1 xl:row-start-2 xl:self-start xl:justify-self-center">{cue}</div>
      <div className="col-start-3 row-start-1 w-[5.5rem] self-center sm:w-28 xl:col-start-3 xl:row-start-3 xl:w-full xl:max-w-[12rem] xl:self-end xl:justify-self-end">{camera}</div>
      <div className="col-span-3 row-start-2 flex min-h-0 flex-col gap-3 xl:col-span-1 xl:col-start-2 xl:row-span-3 xl:row-start-1">
        {notices}
        {children}
      </div>
    </div>
  );
}

/** Whose turn it is, and what kind of time it is (reading, preparing, listening). Announced politely. */
export function ActivityCue({ store, className }: { store: InterviewStore; className?: string }) {
  const activity = useInterviewState(store, (s) => s.activity);
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const name = useInterviewState(store, (s) => s.session?.interviewerName ?? "The interviewer");
  const cue = activityCue(activity, turn, name);
  return (
    <p data-testid="activity-cue" role="status" aria-live="polite" className={cn("flex items-center gap-2 text-sm leading-5 text-fg-muted", cue.tone === "listening" && "text-signal-fg", className)}>
      <span className="flex h-5 shrink-0 items-center" aria-hidden="true">
        {cue.tone === "speaking" ? (
          <span className="flex h-3.5 items-center gap-[3px]">
            {[0, 1, 2].map((i) => (
              <span key={i} className="trace-bar h-full w-[3px] rounded-full bg-accent" style={{ animationDelay: `${i * 140}ms` }} />
            ))}
          </span>
        ) : cue.tone === "listening" ? (
          <span className="relative flex size-2.5">
            <span className="absolute inset-0 animate-live-ring rounded-full bg-signal" />
            <span className="relative size-2.5 rounded-full bg-signal" />
          </span>
        ) : (
          <span className="size-1.5 rounded-full bg-fg-subtle" />
        )}
      </span>
      <span className="min-w-0">{cue.text}</span>
    </p>
  );
}
