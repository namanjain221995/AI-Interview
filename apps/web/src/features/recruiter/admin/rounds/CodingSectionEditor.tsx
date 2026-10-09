import { useMemo } from "react";

import { MultiSelect, type ComboboxOption } from "@/components/ui/Combobox";
import { Select } from "@/components/ui/Select";
import { CODE_LANGUAGE_LABELS, DIFFICULTY_LABELS } from "@/lib/labels";
import type { CodingQuestion, CodingSection, Difficulty } from "@/lib/types";

import { FieldShell, NumberInput, ToggleRow } from "../components/Field";
import { LanguagePicker } from "../components/LanguagePicker";
import type { FieldErrors } from "../lib/serverErrors";
import { ALL_CODE_LANGUAGES } from "./roundConfig";

export interface CodingSectionEditorProps {
  value: CodingSection;
  onChange: (value: CodingSection) => void;
  problems: CodingQuestion[] | undefined;
  roundDuration: number;
  errors?: FieldErrors;
  warnings?: FieldErrors;
}

const DIFFICULTY_OPTIONS = (["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }));

/** The live coding exercise inside a round. */
export function CodingSectionEditor({ value, onChange, problems, roundDuration, errors, warnings }: CodingSectionEditorProps) {
  const set = <K extends keyof CodingSection>(key: K, next: CodingSection[K]) => onChange({ ...value, [key]: next });

  const problemOptions = useMemo<ComboboxOption[]>(
    () =>
      (problems ?? [])
        .filter((p) => p.status !== "retired")
        .map((p) => ({
          value: p.id,
          label: p.title,
          description: `${DIFFICULTY_LABELS[p.difficulty]}: ${(p.languages ?? []).map((l) => CODE_LANGUAGE_LABELS[l]).join(", ")}`,
          keywords: `${p.slug} ${p.difficulty} ${(p.competency_keys ?? []).join(" ")}`,
        })),
    [problems],
  );
  const pinned = (problems ?? []).filter((p) => value.question_ids.includes(p.id));
  const unsupported = pinned.filter((p) => !value.languages.some((l) => (p.languages ?? []).includes(l)));

  return (
    <div className="grid gap-6">
      <ToggleRow
        className="py-0"
        label="Include a coding exercise"
        description="The interviewer moves to a live editor partway through the round, watches progress and asks about the approach."
        checked={value.enabled}
        onCheckedChange={(enabled) => set("enabled", enabled)}
      />

      {value.enabled ? (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <FieldShell label="Coding time" error={errors?.["coding.duration_minutes"]} warning={warnings?.["coding.duration_minutes"]} description={`Of ${roundDuration} minutes in the round`}>
              {(control) => <NumberInput {...control} value={value.duration_minutes} min={5} max={120} unit="min" onChange={(v) => set("duration_minutes", v ?? 0)} />}
            </FieldShell>
            <FieldShell label="Difficulty">
              {(control) => (
                <Select<Difficulty> id={control.id} value={value.difficulty} options={DIFFICULTY_OPTIONS} onValueChange={(v) => set("difficulty", v)} />
              )}
            </FieldShell>
            <FieldShell label="Start after" description="Leave empty to let the interviewer choose" error={errors?.["coding.start_after_minutes"]}>
              {(control) => (
                <NumberInput {...control} value={value.start_after_minutes} nullable min={0} step={1} unit="min" placeholder="Flexible" onChange={(v) => set("start_after_minutes", v)} />
              )}
            </FieldShell>
          </div>

          <FieldShell label="Languages candidates can use" error={errors?.["coding.languages"]} warning={warnings?.["coding.languages"]}>
            {(control) => <LanguagePicker {...control} value={value.languages} options={ALL_CODE_LANGUAGES} onChange={(languages) => set("languages", languages)} />}
          </FieldShell>

          <div className="grid gap-4 md:grid-cols-[1fr_9rem]">
            <FieldShell
              label="Pinned problems"
              optional
              description="Leave empty to pick from the library by difficulty and language."
              warning={unsupported.length ? `${unsupported.map((p) => p.title).join(", ")} has no starter code in the selected languages.` : undefined}
            >
              {(control) => (
                <MultiSelect {...control} value={value.question_ids} options={problemOptions} placeholder="Search coding problems" onChange={(ids) => set("question_ids", ids)} />
              )}
            </FieldShell>
            <FieldShell label="Problems" error={errors?.["coding.max_problems"]}>
              {(control) => <NumberInput {...control} value={value.max_problems} min={1} max={5} onChange={(v) => set("max_problems", v ?? 1)} />}
            </FieldShell>
          </div>

          <div className="divide-y divide-line-subtle rounded-md border border-line px-4">
            <ToggleRow
              label="Allow AI-generated problems"
              description="When no pinned or library problem fits, a new one is generated and checked in the sandbox first."
              checked={value.allow_generated}
              onCheckedChange={(v) => set("allow_generated", v)}
            />
            <ToggleRow label="Offer hints" description="Candidates can ask for up to three hints. Hint use is shown to reviewers." checked={value.hints_enabled} onCheckedChange={(v) => set("hints_enabled", v)} />
            {value.hints_enabled ? (
              <div className="grid gap-4 py-3 sm:grid-cols-2">
                <FieldShell label="Hints available" error={errors?.["coding.max_hints"]}>
                  {(control) => <NumberInput {...control} value={value.max_hints} min={0} max={3} onChange={(v) => set("max_hints", v ?? 0)} />}
                </FieldShell>
                <div className="flex items-end pb-1">
                  <ToggleRow
                    className="w-full py-0"
                    label="Last hint may reveal the approach"
                    checked={value.hint3_may_reveal_solution}
                    onCheckedChange={(v) => set("hint3_may_reveal_solution", v)}
                  />
                </div>
              </div>
            ) : null}
            <ToggleRow
              label="Restrict pasting"
              description="Large pastes into the editor are recorded for reviewers (the integrity policy can block paste entirely)."
              checked={value.restrict_paste}
              onCheckedChange={(v) => set("restrict_paste", v)}
            />
            {value.restrict_paste ? (
              <div className="py-3">
                <FieldShell label="Large paste threshold" error={errors?.["coding.large_paste_threshold_chars"]} className="max-w-56">
                  {(control) => <NumberInput {...control} value={value.large_paste_threshold_chars} min={20} unit="chars" onChange={(v) => set("large_paste_threshold_chars", v ?? 200)} />}
                </FieldShell>
              </div>
            ) : null}
            <ToggleRow
              label="Interviewer comments while coding"
              description="The interviewer occasionally asks about changes, like a human pairing partner."
              checked={value.proactive_comments}
              onCheckedChange={(v) => set("proactive_comments", v)}
            />
            {value.proactive_comments ? (
              <div className="py-3">
                <FieldShell label="At most one comment every" error={errors?.["coding.min_seconds_between_comments"]} className="max-w-56">
                  {(control) => <NumberInput {...control} value={value.min_seconds_between_comments} min={20} unit="sec" onChange={(v) => set("min_seconds_between_comments", v ?? 75)} />}
                </FieldShell>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
