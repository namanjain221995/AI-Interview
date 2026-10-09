import { Lock, LockOpen } from "lucide-react";
import { useState } from "react";

import { Slider } from "@/components/ui/Slider";
import { DIFFICULTY_LABELS } from "@/lib/labels";
import type { DifficultyDistribution } from "@/lib/types";
import { cn } from "@/lib/utils";

import { NumberInput } from "../components/Field";
import { DIFFICULTY_KEYS, fromPercentages, rebalance, toPercentages, type DifficultyKey } from "./distribution";

/**
 * Bar colours: a quiet ink ramp (difficulty is not good or bad). More contrast with
 * the surface means harder, so the ramp runs light-to-dark ink in the light theme and
 * dark-to-light in the dark theme.
 */
export const DIFFICULTY_TINT: Record<DifficultyKey, string> = {
  easy: "bg-ink-300 dark:bg-ink-600",
  medium: "bg-ink-500 dark:bg-ink-400",
  hard: "bg-ink-800 dark:bg-ink-100",
};

const HELP: Record<DifficultyKey, string> = {
  easy: "Warm-ups and fundamentals",
  medium: "Typical day-to-day depth",
  hard: "Senior-level depth and edge cases",
};

export interface DifficultyMixEditorProps {
  value: DifficultyDistribution;
  onChange: (value: DifficultyDistribution) => void;
  error?: string;
  disabled?: boolean;
}

/**
 * Three linked sliders that always total 100%. Moving one redistributes the
 * remainder across the other two in proportion; a lock pins one share.
 */
export function DifficultyMixEditor({ value, onChange, error, disabled }: DifficultyMixEditorProps) {
  const percentages = toPercentages(value);
  const [locked, setLocked] = useState<DifficultyKey | null>(null);

  const set = (key: DifficultyKey, next: number) => {
    onChange(fromPercentages(rebalance(percentages, key, next, locked)));
  };

  return (
    <div className="grid gap-4" role="group" aria-label="Difficulty mix">
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-track" aria-hidden="true">
        {DIFFICULTY_KEYS.map((key) => (
          <div key={key} className={cn("h-full transition-[width] duration-200 ease-out", DIFFICULTY_TINT[key])} style={{ width: `${percentages[key]}%` }} />
        ))}
      </div>
      <div className="grid gap-3.5">
        {DIFFICULTY_KEYS.map((key) => {
          const isLocked = locked === key;
          return (
            <div key={key} className="grid grid-cols-[11rem_1fr_5.5rem_2rem] items-center gap-3 max-md:grid-cols-[5rem_1fr_4.75rem_2rem]">
              <div className="grid">
                <span className="flex items-center gap-2 text-sm font-medium text-fg">
                  <span className={cn("size-2 rounded-full", DIFFICULTY_TINT[key])} aria-hidden="true" />
                  {DIFFICULTY_LABELS[key]}
                </span>
                <span className="text-2xs text-fg-subtle max-md:hidden">{HELP[key]}</span>
              </div>
              <Slider
                value={[percentages[key]]}
                min={0}
                max={100}
                step={1}
                disabled={disabled || isLocked}
                thumbLabels={[`${DIFFICULTY_LABELS[key]} share`]}
                formatValue={(v) => `${v}%`}
                onValueChange={([next]) => set(key, next ?? 0)}
              />
              <NumberInput
                size="sm"
                value={percentages[key]}
                min={0}
                max={100}
                unit="%"
                disabled={disabled || isLocked}
                aria-label={`${DIFFICULTY_LABELS[key]} percentage`}
                onChange={(next) => set(key, next ?? 0)}
              />
              <button
                type="button"
                onClick={() => setLocked(isLocked ? null : key)}
                disabled={disabled}
                aria-pressed={isLocked}
                aria-label={isLocked ? `Unlock ${DIFFICULTY_LABELS[key]}` : `Lock ${DIFFICULTY_LABELS[key]}`}
                title={isLocked ? "Unlock this share" : "Keep this share fixed while adjusting the others"}
                className={cn(
                  "flex size-8 items-center justify-center rounded-control transition-colors",
                  isLocked ? "bg-accent-soft text-accent-fg" : "text-fg-subtle hover:bg-surface-hover hover:text-fg",
                )}
              >
                {isLocked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
              </button>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-fg-muted" aria-live="polite">
        Total <span className="font-medium text-fg tabular-nums">100%</span>. Adjusting one share rebalances the other two
        {locked ? `; ${DIFFICULTY_LABELS[locked].toLowerCase()} is locked` : ""}.
      </p>
      {error ? <p className="text-xs font-medium text-danger-fg">{error}</p> : null}
    </div>
  );
}
