import { DIFFICULTY_LABELS } from "@/lib/labels";
import type { Difficulty } from "@/lib/types";
import { cn } from "@/lib/utils";

const LEVEL: Record<Difficulty, number> = { easy: 1, medium: 2, hard: 3 };

export interface DifficultyBadgeProps {
  value: Difficulty;
  size?: "sm" | "md";
  /** Hide the word and keep only the glyph (the label stays accessible). */
  iconOnly?: boolean;
  className?: string;
}

/** Difficulty is not good or bad, so it stays neutral: a three-step glyph plus the word. */
export function DifficultyBadge({ value, size = "md", iconOnly = false, className }: DifficultyBadgeProps) {
  const level = LEVEL[value] ?? 2;
  const label = DIFFICULTY_LABELS[value] ?? value;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-sm font-medium text-fg-muted",
        !iconOnly && "bg-neutral-soft ring-1 ring-neutral-line ring-inset",
        !iconOnly && (size === "sm" ? "h-5 px-1.5 text-2xs" : "h-6 px-2 text-xs"),
        className,
      )}
      aria-label={`Difficulty: ${label}`}
      role="img"
    >
      <span className="flex items-end gap-px" aria-hidden="true">
        {[1, 2, 3].map((step) => (
          <span key={step} className={cn("w-[3px] rounded-[1px]", step <= level ? "bg-fg-muted" : "bg-line-strong")} style={{ height: `${3 + step * 2.5}px` }} />
        ))}
      </span>
      {iconOnly ? null : <span aria-hidden="true">{label}</span>}
    </span>
  );
}
