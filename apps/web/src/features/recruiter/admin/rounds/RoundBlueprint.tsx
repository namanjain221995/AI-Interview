import { Code2 } from "lucide-react";

import { DIFFICULTY_LABELS } from "@/lib/labels";
import type { Competency, RoundConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

import { DIFFICULTY_TINT } from "./DifficultyMixEditor";
import { DIFFICULTY_KEYS, toPercentages } from "./distribution";
import { CATEGORY_TINTS } from "./RubricEditor";
import { formatWeight, isRubricBalanced, rubricTotal } from "./rubric";

export interface RoundBlueprintProps {
  config: RoundConfig;
  catalog?: Competency[];
  className?: string;
  /** Hide the competency chips (dense lists). */
  compact?: boolean;
}

/**
 * A read-at-a-glance strip of the round: the time budget (with the coding
 * block placed on it), the difficulty mix and how scores roll up.
 */
export function RoundBlueprint({ config, catalog, className, compact }: RoundBlueprintProps) {
  const max = Math.max(config.max_duration_minutes, config.duration_minutes, 1);
  const planned = (config.duration_minutes / max) * 100;
  const coding = config.coding.enabled ? Math.min(config.coding.duration_minutes, config.duration_minutes) : 0;
  const codingStart = config.coding.enabled
    ? Math.min(config.coding.start_after_minutes ?? Math.max(0, config.duration_minutes - coding - Math.min(5, config.duration_minutes * 0.15)), Math.max(0, config.duration_minutes - coding))
    : 0;
  const mix = toPercentages(config.difficulty_distribution);
  const rubricSum = rubricTotal(config.rubric);
  const nameOf = (key: string) => catalog?.find((c) => c.key === key)?.name ?? config.competencies.find((c) => c.key === key)?.name ?? key;

  return (
    <div className={cn("grid gap-4", className)}>
      <div className="grid gap-4 sm:grid-cols-3">
        {/* Time */}
        <div className="grid content-start gap-1.5">
          <p className="flex items-baseline justify-between gap-2 text-xs text-fg-muted">
            <span>
              <span className="text-sm font-semibold text-fg tabular-nums">{config.duration_minutes}</span> min
              {config.max_duration_minutes > config.duration_minutes ? <span className="tabular-nums">, up to {config.max_duration_minutes}</span> : null}
            </span>
            {config.coding.enabled ? (
              <span className="inline-flex items-center gap-1 text-fg-muted">
                <Code2 className="size-3" aria-hidden="true" />
                <span className="tabular-nums">{coding}</span> min coding
              </span>
            ) : null}
          </p>
          <div className="relative h-2 w-full overflow-hidden rounded-full bg-track" aria-hidden="true">
            <div className="absolute inset-y-0 left-0 rounded-full bg-fg-subtle" style={{ width: `${planned}%` }} />
            {config.max_duration_minutes > config.duration_minutes ? (
              <div
                className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,var(--border-strong)_0_3px,transparent_3px_6px)]"
                style={{ left: `${planned}%`, width: `${100 - planned}%` }}
              />
            ) : null}
            {coding > 0 ? (
              <div className="absolute inset-y-0 bg-signal" style={{ left: `${(codingStart / max) * 100}%`, width: `${(coding / max) * 100}%` }} />
            ) : null}
          </div>
        </div>

        {/* Difficulty */}
        <div className="grid content-start gap-1.5">
          <p className="flex items-baseline gap-2 text-xs text-fg-muted tabular-nums">
            {DIFFICULTY_KEYS.map((key) => (
              <span key={key} className="inline-flex items-center gap-1">
                <span className={cn("size-1.5 rounded-full", DIFFICULTY_TINT[key])} aria-hidden="true" />
                <span className="sr-only">{DIFFICULTY_LABELS[key]}</span>
                <span className="font-medium text-fg">{mix[key]}%</span>
              </span>
            ))}
            <span className="ml-auto">starts {DIFFICULTY_LABELS[config.expected_difficulty].toLowerCase()}</span>
          </p>
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-track" aria-hidden="true">
            {DIFFICULTY_KEYS.map((key) => (
              <div key={key} className={cn("h-full", DIFFICULTY_TINT[key])} style={{ width: `${mix[key]}%` }} />
            ))}
          </div>
        </div>

        {/* Rubric */}
        <div className="grid content-start gap-1.5">
          <p className={cn("text-xs", isRubricBalanced(config.rubric) ? "text-fg-muted" : "font-medium text-danger-fg")}>
            {config.rubric.length === 0 ? (
              "Scored by competency weights"
            ) : (
              <>
                <span className="text-sm font-semibold text-fg tabular-nums">{config.rubric.length}</span> rubric {config.rubric.length === 1 ? "category" : "categories"}
                {isRubricBalanced(config.rubric) ? null : <span className="tabular-nums">, {formatWeight(rubricSum)}% of 100</span>}
              </>
            )}
          </p>
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-track" aria-hidden="true">
            {config.rubric.map((category, index) => (
              <div
                key={`${category.key}-${index}`}
                className={cn("h-full border-r border-surface last:border-r-0", CATEGORY_TINTS[index % CATEGORY_TINTS.length])}
                style={{ width: `${Math.max(0, category.weight) / Math.max(100, rubricSum) * 100}%` }}
                title={`${category.label}: ${formatWeight(category.weight)}%`}
              />
            ))}
          </div>
        </div>
      </div>

      {!compact && config.competencies.length > 0 ? (
        <div className="grid gap-1.5">
        <ul className="flex flex-wrap gap-1.5" aria-label="Competencies">
          {config.competencies.map((c, index) => (
            <li
              key={`${c.key}-${index}`}
              className={cn(
                "inline-flex h-6 items-center gap-1.5 rounded-sm border px-2 text-xs",
                c.required ? "border-line-strong bg-surface text-fg" : "border-dashed border-line-strong bg-transparent text-fg-muted",
              )}
              title={c.required ? "Required" : "Optional"}
            >
              {nameOf(c.key) || "Unnamed"}
              <span className="text-fg-subtle tabular-nums">×{formatWeight(c.weight)}</span>
            </li>
          ))}
        </ul>
        {config.competencies.some((c) => !c.required) ? <p className="text-2xs text-fg-subtle">Dashed: optional competency. ×: weight.</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Three tiny bars (time, difficulty mix, rubric) for dense lists such as the template table. */
export function RoundShapeMini({ config, className }: { config: RoundConfig; className?: string }) {
  const max = Math.max(config.max_duration_minutes, config.duration_minutes, 1);
  const coding = config.coding.enabled ? Math.min(config.coding.duration_minutes, config.duration_minutes) : 0;
  const mix = toPercentages(config.difficulty_distribution);
  const rubricSum = Math.max(100, rubricTotal(config.rubric));
  return (
    <div className={cn("grid w-[16rem] grid-cols-3 gap-3", className)}>
      <div className="grid gap-1" title={`${config.duration_minutes} minutes, up to ${config.max_duration_minutes}${coding ? `, ${coding} min coding` : ""}`}>
        <span className="truncate text-2xs text-fg-muted tabular-nums">
          {config.duration_minutes}
          {config.max_duration_minutes > config.duration_minutes ? `–${config.max_duration_minutes}` : ""} min
        </span>
        <div className="relative h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
          <div className="absolute inset-y-0 left-0 rounded-full bg-fg-subtle" style={{ width: `${(config.duration_minutes / max) * 100}%` }} />
          {coding ? <div className="absolute inset-y-0 bg-signal" style={{ left: `${((config.duration_minutes - coding) / max) * 100}%`, width: `${(coding / max) * 100}%` }} /> : null}
        </div>
      </div>
      <div className="grid gap-1" title={`Easy ${mix.easy}%, medium ${mix.medium}%, hard ${mix.hard}%`}>
        <span className="truncate text-2xs text-fg-muted tabular-nums">
          {mix.easy} / {mix.medium} / {mix.hard}
        </span>
        <div className="flex h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
          {DIFFICULTY_KEYS.map((key) => (
            <div key={key} className={cn("h-full", DIFFICULTY_TINT[key])} style={{ width: `${mix[key]}%` }} />
          ))}
        </div>
      </div>
      <div className="grid gap-1" title={config.rubric.map((c) => `${c.label} ${formatWeight(c.weight)}%`).join(", ") || "Scored by competency weights"}>
        <span className="truncate text-2xs text-fg-muted tabular-nums">{config.rubric.length ? `${config.rubric.length} categories` : "Weights"}</span>
        <div className="flex h-1.5 overflow-hidden rounded-full bg-track" aria-hidden="true">
          {config.rubric.map((category, index) => (
            <div key={`${category.key}-${index}`} className={cn("h-full", CATEGORY_TINTS[index % CATEGORY_TINTS.length])} style={{ width: `${(Math.max(0, category.weight) / rubricSum) * 100}%` }} />
          ))}
        </div>
      </div>
    </div>
  );
}
