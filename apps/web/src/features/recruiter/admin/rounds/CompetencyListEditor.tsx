import { Plus, Trash2 } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import { Combobox, MultiSelect, type ComboboxOption } from "@/components/ui/Combobox";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { COMPETENCY_CATEGORY_LABELS, DIFFICULTY_LABELS, labelFor } from "@/lib/labels";
import type { Competency, Difficulty, RoundCompetency } from "@/lib/types";
import { cn } from "@/lib/utils";

import { NumberInput } from "../components/Field";
import type { FieldErrors } from "../lib/serverErrors";

const ANY = "__any__";
const GRID = "lg:grid-cols-[minmax(12rem,1fr)_7rem_6.5rem_8rem_6.5rem_2rem]";

export interface CompetencyListEditorProps {
  value: RoundCompetency[];
  onChange: (value: RoundCompetency[]) => void;
  catalog: Competency[] | undefined;
  /** Round default, shown as the placeholder for per-competency overrides. */
  defaultMinEvidence: number;
  errors?: FieldErrors;
}

export function competencyOptionsFrom(catalog: readonly Competency[] | undefined): ComboboxOption[] {
  return (catalog ?? []).map((c) => ({
    value: c.key,
    label: c.name,
    description: labelFor(COMPETENCY_CATEGORY_LABELS, c.category),
    keywords: `${c.key} ${c.category}`,
  }));
}

/** Suggested focus areas for a competency come from its rubric dimensions. */
function focusOptions(catalog: readonly Competency[] | undefined, key: string): ComboboxOption[] {
  const dims = catalog?.find((c) => c.key === key)?.rubric?.dimensions ?? [];
  return dims.map((d) => ({ value: d, label: d.replace(/_/g, " ") }));
}

/** Competencies the round assesses, with weight, required flag and evidence targets. */
export function CompetencyListEditor({ value, onChange, catalog, defaultMinEvidence, errors }: CompetencyListEditorProps) {
  const options = useMemo(() => competencyOptionsFrom(catalog), [catalog]);
  const totalWeight = value.reduce((sum, c) => sum + (Number.isFinite(c.weight) ? c.weight : 0), 0);

  const update = (index: number, patch: Partial<RoundCompetency>) => onChange(value.map((c, i) => (i === index ? { ...c, ...patch } : c)));

  const add = () => {
    const used = new Set(value.map((c) => c.key));
    onChange([
      ...value,
      { key: options.find((o) => !used.has(o.value))?.value ?? "", name: null, weight: 1, required: true, min_evidence: null, target_difficulty: null, focus_areas: [] },
    ]);
  };

  return (
    <div className="grid gap-3">
      {value.length === 0 ? (
        <p className="rounded-md border border-dashed border-line-strong bg-surface-subtle/60 px-4 py-6 text-center text-sm text-fg-muted">
          No competencies yet. Add what this round should gather evidence on.
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border border-line">
          <div className={cn(GRID, "hidden border-b border-line bg-surface-subtle px-3 py-2 text-xs font-medium text-fg-muted lg:grid")} aria-hidden="true">
            <span>Competency</span>
            <span>Weight</span>
            <span title="Evidence items needed before it counts as covered">Min. evidence</span>
            <span>Start at</span>
            <span>Required</span>
            <span />
          </div>
          <ul className="divide-y divide-line-subtle">
            {value.map((competency, index) => {
              const share = totalWeight > 0 ? Math.round((competency.weight / totalWeight) * 100) : 0;
              const used = new Set(value.filter((_, i) => i !== index).map((c) => c.key));
              const rowOptions = options.map((o) => ({ ...o, disabled: used.has(o.value) }));
              if (competency.key && !options.some((o) => o.value === competency.key)) {
                rowOptions.unshift({ value: competency.key, label: competency.name || competency.key, description: "Not in the catalog", keywords: competency.key, disabled: false });
              }
              const name = competency.name || options.find((o) => o.value === competency.key)?.label || competency.key || `competency ${index + 1}`;
              const rowErrors = [
                errors?.[`competencies.${index}.key`],
                errors?.[`competencies.${index}.weight`],
                errors?.[`competencies.${index}.min_evidence`],
              ].filter(Boolean);
              return (
                <li key={index} className="grid gap-2 bg-surface px-3 py-3">
                  <div className={cn(GRID, "grid items-center gap-3 max-lg:grid-cols-2")}>
                    <label className="grid gap-1 max-lg:col-span-2">
                      <span className="text-2xs font-medium text-fg-muted lg:hidden">Competency</span>
                      <Combobox
                        aria-label={`Competency ${index + 1}`}
                        invalid={Boolean(errors?.[`competencies.${index}.key`])}
                        options={rowOptions}
                        value={competency.key || null}
                        clearable={false}
                        placeholder="Search competencies"
                        onChange={(key) => {
                          const match = catalog?.find((c) => c.key === key);
                          update(index, { key: key ?? "", name: match?.name ?? null });
                        }}
                      />
                    </label>
                    <label className="grid gap-1">
                      <span className="text-2xs font-medium text-fg-muted lg:hidden">Weight</span>
                      <span className="flex items-center gap-1.5">
                        <NumberInput
                          aria-label={`Weight of ${name}`}
                          aria-invalid={errors?.[`competencies.${index}.weight`] ? true : undefined}
                          size="sm"
                          className="w-16"
                          value={competency.weight}
                          min={0}
                          max={10}
                          step={0.1}
                          onChange={(weight) => update(index, { weight: weight ?? 0 })}
                        />
                        <span className="text-2xs text-fg-subtle tabular-nums" title="Share of the round's total weight">
                          {share}%
                        </span>
                      </span>
                    </label>
                    <label className="grid gap-1">
                      <span className="text-2xs font-medium text-fg-muted lg:hidden">Min. evidence</span>
                      <NumberInput
                        aria-label={`Minimum evidence for ${name}`}
                        aria-invalid={errors?.[`competencies.${index}.min_evidence`] ? true : undefined}
                        size="sm"
                        value={competency.min_evidence}
                        nullable
                        min={1}
                        max={20}
                        placeholder={`${defaultMinEvidence}`}
                        onChange={(min_evidence) => update(index, { min_evidence })}
                      />
                    </label>
                    <label className="grid gap-1">
                      <span className="text-2xs font-medium text-fg-muted lg:hidden">Start at</span>
                      <Select<string>
                        aria-label={`Starting difficulty for ${name}`}
                        size="sm"
                        value={competency.target_difficulty ?? ANY}
                        onValueChange={(v) => update(index, { target_difficulty: v === ANY ? null : (v as Difficulty) })}
                        options={[{ value: ANY, label: "Adaptive" }, ...(["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }))]}
                      />
                    </label>
                    <div className="flex items-center gap-2">
                      <Switch size="sm" checked={competency.required} onCheckedChange={(required) => update(index, { required })} aria-label={`${name} is required`} />
                      <span className="text-xs text-fg-muted">{competency.required ? "Required" : "Optional"}</span>
                    </div>
                    <Button variant="ghost" size="sm" className="size-8 justify-self-end px-0" aria-label={`Remove ${name}`} onClick={() => onChange(value.filter((_, i) => i !== index))}>
                      <Trash2 />
                    </Button>
                  </div>
                  <div className="flex items-center gap-2">
                  <span className="w-12 shrink-0 text-2xs font-medium text-fg-subtle" aria-hidden="true">
                    Probe
                  </span>
                  <MultiSelect
                    aria-label={`Focus areas for ${name}`}
                    creatable
                    value={competency.focus_areas}
                    options={focusOptions(catalog, competency.key)}
                    normalize={(raw) => raw.trim().toLowerCase().replace(/\s+/g, "_")}
                    placeholder="Focus areas (optional), e.g. availability, cost"
                    onChange={(focus_areas) => update(index, { focus_areas })}
                    className="min-h-8 border-dashed bg-surface-subtle/50 text-[13px] shadow-none"
                  />
                  </div>
                  {rowErrors.length ? <p className="text-xs font-medium text-danger-fg">{rowErrors.join(" ")}</p> : null}
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <div className={cn("flex items-center justify-between gap-3")}>
        <Button size="sm" variant="outline" leftIcon={<Plus />} onClick={add}>
          Add competency
        </Button>
        {value.length > 0 ? (
          <span className="text-xs text-fg-muted">
            {value.filter((c) => c.required).length} of {value.length} required
          </span>
        ) : null}
      </div>
    </div>
  );
}
