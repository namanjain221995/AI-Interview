import { ArrowDown, ArrowUp, Plus, Scale, Trash2, Wand2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { MultiSelect, type ComboboxOption } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import type { RubricCategory } from "@/lib/types";
import { cn } from "@/lib/utils";

import { FieldShell, NumberInput } from "../components/Field";
import type { FieldErrors } from "../lib/serverErrors";
import { RUBRIC_DIMENSIONS } from "./roundConfig";
import { distributeEvenly, formatWeight, isRubricBalanced, keyForLabel, newRubricCategory, normalizeRubricWeights, rubricBalanceMessage, rubricTotal } from "./rubric";

export const CATEGORY_TINTS = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-chart-5", "bg-chart-6", "bg-chart-7", "bg-chart-8"];

const DIMENSION_OPTIONS: ComboboxOption[] = RUBRIC_DIMENSIONS.map((d) => ({ value: d.value, label: d.label }));

export interface RubricEditorProps {
  value: RubricCategory[];
  onChange: (value: RubricCategory[]) => void;
  /** Competencies of the round (keys and display names). */
  competencyOptions: ComboboxOption[];
  errors?: FieldErrors;
  warnings?: FieldErrors;
}

/** Live total meter: one segment per category, an outline where 100 is. */
export function RubricMeter({ rubric }: { rubric: RubricCategory[] }) {
  const total = rubricTotal(rubric);
  const balanced = isRubricBalanced(rubric);
  const scale = Math.max(100, total);
  return (
    <div className="grid gap-1.5">
      <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-track" aria-hidden="true">
        <div className="flex h-full" style={{ width: `${(Math.min(total, scale) / scale) * 100}%` }}>
          {rubric.map((category, index) => (
            <div
              key={`${category.key}-${index}`}
              className={cn("h-full border-r border-surface last:border-r-0", CATEGORY_TINTS[index % CATEGORY_TINTS.length])}
              style={{ width: `${total > 0 ? (Math.max(0, category.weight) / total) * 100 : 0}%` }}
            />
          ))}
        </div>
        {total > 100 ? <div className="absolute inset-y-0 border-l-2 border-danger" style={{ left: `${(100 / scale) * 100}%` }} /> : null}
      </div>
      <p className={cn("text-xs tabular-nums", balanced ? "text-fg-muted" : "font-medium text-danger-fg")} aria-live="polite" data-testid="rubric-total">
        {rubric.length === 0
          ? "No categories: competency weights are used directly."
          : balanced
            ? `Total ${formatWeight(total)}%`
            : rubricBalanceMessage(rubric)}
      </p>
    </div>
  );
}

/** Weighted scoring categories with live "must total 100" validation. */
export function RubricEditor({ value, onChange, competencyOptions, errors, warnings }: RubricEditorProps) {
  const update = (index: number, patch: Partial<RubricCategory>) => onChange(value.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= value.length) return;
    const next = [...value];
    [next[index], next[target]] = [next[target]!, next[index]!];
    onChange(next);
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-56 flex-1">
          <RubricMeter rubric={value} />
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" leftIcon={<Scale />} disabled={value.length < 1} onClick={() => onChange(normalizeRubricWeights(value))}>
            Scale to 100
          </Button>
          <Button size="sm" variant="outline" leftIcon={<Wand2 />} disabled={value.length < 1} onClick={() => onChange(distributeEvenly(value))}>
            Split evenly
          </Button>
        </div>
      </div>

      {value.length > 0 ? (
        <ol className="grid gap-3">
          {value.map((category, index) => (
            <li key={index} className="rounded-md border border-line bg-surface-subtle/50 p-3.5">
              <div className="grid gap-3 sm:grid-cols-[1fr_8.5rem_auto]">
                <FieldShell label="Category" error={errors?.[`rubric.${index}.label`] ?? errors?.[`rubric.${index}.key`]}>
                  {(control) => (
                    <div className="flex items-center gap-2">
                      <span className={cn("size-2.5 shrink-0 rounded-full", CATEGORY_TINTS[index % CATEGORY_TINTS.length])} aria-hidden="true" />
                      <Input
                        {...control}
                        value={category.label}
                        placeholder="e.g. Technical knowledge"
                        onChange={(event) => {
                          const label = event.target.value;
                          update(index, { label, key: keyForLabel(label, value, index) });
                        }}
                      />
                    </div>
                  )}
                </FieldShell>
                <FieldShell label="Weight" error={errors?.[`rubric.${index}.weight`]}>
                  {(control) => (
                    <NumberInput {...control} value={category.weight} min={0} max={100} step={1} unit="%" onChange={(weight) => update(index, { weight: weight ?? 0 })} />
                  )}
                </FieldShell>
                <div className="flex items-end gap-0.5 pb-0.5">
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Move ${category.label || "category"} up`} disabled={index === 0} onClick={() => move(index, -1)}>
                    <ArrowUp />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="size-8 px-0"
                    aria-label={`Move ${category.label || "category"} down`}
                    disabled={index === value.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    <ArrowDown />
                  </Button>
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Remove ${category.label || "category"}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <FieldShell
                  label="Scores from competencies"
                  optional
                  warning={warnings?.[`rubric.${index}.competency_keys`]}
                  error={errors?.[`rubric.${index}.competency_keys`]}
                >
                  {(control) => (
                    <MultiSelect
                      {...control}
                      value={category.competency_keys}
                      options={competencyOptions}
                      placeholder={competencyOptions.length ? "Pick competencies" : "Add competencies to the round first"}
                      onChange={(competency_keys) => update(index, { competency_keys })}
                    />
                  )}
                </FieldShell>
                <FieldShell label="And answer dimensions" optional>
                  {(control) => (
                    <MultiSelect {...control} value={category.dimensions} options={DIMENSION_OPTIONS} placeholder="e.g. Depth, Clarity" onChange={(dimensions) => update(index, { dimensions })} />
                  )}
                </FieldShell>
              </div>
              <FieldShell label="What good looks like" optional className="mt-3">
                {(control) => (
                  <Textarea
                    {...control}
                    rows={1}
                    autoResize
                    maxRows={6}
                    className="min-h-9 py-1.5"
                    value={category.description ?? ""}
                    placeholder="Shown to reviewers next to the score"
                    onChange={(event) => update(index, { description: event.target.value })}
                  />
                )}
              </FieldShell>
            </li>
          ))}
        </ol>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="outline" leftIcon={<Plus />} onClick={() => onChange([...value, newRubricCategory(value)])}>
          Add category
        </Button>
        {errors?.rubric && isRubricBalanced(value) ? <p className="text-xs font-medium text-danger-fg">{errors.rubric}</p> : null}
      </div>
    </div>
  );
}
