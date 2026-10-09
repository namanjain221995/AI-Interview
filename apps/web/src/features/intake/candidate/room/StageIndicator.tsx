import { cn } from "@/lib/utils";

import type { IntakeView } from "./activityModel";

/**
 * "Part 4 of 7  Reading exercise" for the room's top bar. The same seven-part
 * vocabulary as the welcome page, so the candidate always knows where they are.
 */
export function StageIndicator({ intake, className }: { intake: IntakeView; className?: string }) {
  const total = intake.stages.length || 7;
  return (
    <p
      data-testid="intake-stage-indicator"
      data-stage={intake.stage}
      data-stage-number={intake.stageNumber}
      className={cn("flex min-w-0 items-baseline gap-1.5 text-xs leading-4", className)}
    >
      <span className="shrink-0 text-fg-muted tabular-nums">
        Part {intake.stageNumber} of {total}
        <span className="sr-only">:</span>
      </span>
      <span className="min-w-0 truncate font-medium text-fg">{intake.stageTitle}</span>
    </p>
  );
}

/**
 * Seven equal segments along the bottom edge of the top bar: past parts muted,
 * the current one in the accent colour, the rest on the track. Decorative for
 * assistive technology (the indicator text says the same).
 */
export function StageStrip({ intake, className }: { intake: IntakeView; className?: string }) {
  return (
    <ol aria-hidden="true" data-testid="intake-stage-strip" className={cn("pointer-events-none flex gap-0.5", className)}>
      {intake.stages.map((stage) => (
        <li
          key={stage.key}
          data-status={stage.status}
          className={cn(
            "h-[3px] flex-1 transition-colors duration-500 motion-reduce:transition-none",
            stage.status === "current" ? "bg-accent" : stage.status === "pending" ? "bg-track" : "bg-fg-muted/45",
          )}
        />
      ))}
    </ol>
  );
}
