import { Info, Timer } from "lucide-react";

import { Input } from "@/components/ui/Input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { FieldShell, NumberInput, SectionBlock } from "@/features/recruiter/admin/components/Field";
import type { RoundConfigWithIntake } from "@/features/recruiter/admin/rounds/roundConfig";

import { useIntakeConfig } from "../../api";
import { ELIGIBILITY_NOTE, STAGE_SHORT_LABELS } from "../../labels";
import { FOLLOWUP_FIELDS, PASSAGE_A_KEYS, PASSAGE_B_KEYS, planBudgets, readIntakeRound, TOPIC_CARD_KEYS, writeIntakeRound, type IntakeRoundView } from "../../lib/roundIntake";

/** The seven stages laid out by their planned time budget for this duration. */
export function IntakeRoundBlueprint({ durationMinutes, className }: { durationMinutes: number; className?: string }) {
  const budgets = planBudgets(durationMinutes);
  const total = budgets.reduce((n, b) => n + b.seconds, 0) || 1;
  return (
    <div className={cn("grid grid-cols-1 gap-2", className)} data-testid="intake-round-blueprint">
      <p className="flex flex-wrap items-baseline justify-between gap-2 text-xs text-fg-muted">
        <span>
          <span className="text-sm font-semibold text-fg tabular-nums">{durationMinutes}</span> min across seven parts
        </span>
        <span className="inline-flex items-center gap-1">
          <Timer className="size-3" aria-hidden="true" />
          Fixed order, timed by the platform
        </span>
      </p>
      <ol className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full" aria-label="Planned time per stage">
        {budgets.map((b, i) => (
          <Tooltip key={b.key} content={`${i + 1}. ${b.title}: about ${formatClock(b.seconds)}`}>
            <li tabIndex={0} className="h-full bg-fg-subtle/70 first:rounded-l-full last:rounded-r-full hover:bg-fg-muted" style={{ width: `${(b.seconds / total) * 100}%` }}>
              <span className="sr-only">
                {b.title}, about {formatClock(b.seconds)}
              </span>
            </li>
          </Tooltip>
        ))}
      </ol>
      <ol className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-2xs text-fg-subtle sm:grid-cols-4 lg:grid-cols-7" aria-hidden="true">
        {budgets.map((b, i) => (
          <li key={b.key} className="truncate">
            {i + 1}. {STAGE_SHORT_LABELS[b.key]} <span className="tabular-nums">{formatClock(b.seconds)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export interface IntakeRoundPanelProps {
  value: RoundConfigWithIntake;
  onChange: (value: RoundConfigWithIntake) => void;
  errors: Record<string, string>;
  warnings: Record<string, string>;
  readOnly?: boolean;
}

type WaMode = "inherit" | "ask" | "off";

/** Round editor tab for intake rounds (design 4.1 `IntakeRoundConfig`). */
export function IntakeRoundPanel({ value, onChange, errors, warnings, readOnly }: IntakeRoundPanelProps) {
  const config = useIntakeConfig();
  const view = readIntakeRound(value.intake);
  const set = (patch: Partial<IntakeRoundView>) => onChange({ ...value, intake: writeIntakeRound(value.intake, { ...view, ...patch }) });
  const passages = config.data?.kit.passages ?? [];
  const cards = config.data?.kit.topic_cards ?? [];
  const passageLabel = (key: string) => {
    if (key === "auto") return "Chosen for the candidate's level";
    const p = passages.find((x) => x.key === key);
    return p ? `${key}: ${p.title} (${p.level})` : `Passage ${key}`;
  };
  const cardLabel = (key: string) => {
    if (key === "auto") return "Chosen for the candidate's level";
    const c = cards.find((x) => String(x.number) === key);
    return c ? `${key}: ${c.title}` : `Card ${key}`;
  };
  const waMode: WaMode = view.work_authorization === null ? "inherit" : view.work_authorization.enabled ? "ask" : "off";

  return (
    <fieldset disabled={readOnly} className="grid min-w-0 grid-cols-1 gap-8" data-testid="intake-round-panel">
      {errors.intake || warnings.intake ? <p className={cn("text-xs font-medium", errors.intake ? "text-danger-fg" : "text-warning-fg")}>{errors.intake ?? warnings.intake}</p> : null}
      <SectionBlock variant="plain" title="Stage plan" description="Seven stages in the kit's order; the strip above shows each one's share of the duration.">
        <p className="flex items-start gap-1.5 text-xs text-fg-muted">
          <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          Fixed timers from the kit: self-introduction about 90 s, read aloud up to 3 min, relay passage visible for 30 s, topic card 60 s to prepare and 2 min to speak. They don't change with the duration.
        </p>
      </SectionBlock>

      <SectionBlock variant="plain" title="Follow-up limits" description="The most follow-up questions the interviewer may add in each stage, on top of the fixed items.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FOLLOWUP_FIELDS.map((field) => (
            <FieldShell key={field.key} label={field.label} description={field.description} error={errors[`intake.followup_limits.${field.key}`]}>
              {(control) => (
                <NumberInput
                  {...control}
                  value={view.followup_limits[field.key]}
                  min={0}
                  max={field.max}
                  unit={`of ${field.max}`}
                  onChange={(v) => set({ followup_limits: { ...view.followup_limits, [field.key]: v ?? 0 } })}
                />
              )}
            </FieldShell>
          ))}
        </div>
      </SectionBlock>

      <SectionBlock variant="plain" title="Résumé claims" description="How many résumé claims the interviewer probes for ownership, mechanics and stress. Résumé checks are always on in intake rounds.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <FieldShell label="At least" error={errors["intake.claims.min_claims"]}>
            {(control) => <NumberInput {...control} value={view.claims.min_claims} min={1} max={3} unit="claims" onChange={(v) => set({ claims: { ...view.claims, min_claims: v ?? 1 } })} />}
          </FieldShell>
          <FieldShell label="At most" error={errors["intake.claims.max_claims"]}>
            {(control) => <NumberInput {...control} value={view.claims.max_claims} min={1} max={3} unit="claims" onChange={(v) => set({ claims: { ...view.claims, max_claims: v ?? 1 } })} />}
          </FieldShell>
        </div>
      </SectionBlock>

      <SectionBlock variant="plain" title="Reading and speaking material" description="By default the platform picks passages and the topic card for the level the standard questions show. Pin them only for a reason, such as comparing candidates on the same material.">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <FieldShell label="Drill A passage (read aloud)">
            {(control) => (
              <Select<string> id={control.id} value={view.passage_drill_a} onValueChange={(v) => set({ passage_drill_a: v })} options={PASSAGE_A_KEYS.map((k) => ({ value: k, label: passageLabel(k) }))} />
            )}
          </FieldShell>
          <FieldShell label="Drill B passage (read and relay)" error={errors["intake.passage_drill_b"]}>
            {(control) => (
              <Select<string> id={control.id} value={view.passage_drill_b} onValueChange={(v) => set({ passage_drill_b: v })} options={PASSAGE_B_KEYS.map((k) => ({ value: k, label: passageLabel(k) }))} />
            )}
          </FieldShell>
          <FieldShell label="Topic card">
            {(control) => <Select<string> id={control.id} value={view.topic_card} onValueChange={(v) => set({ topic_card: v })} options={TOPIC_CARD_KEYS.map((k) => ({ value: k, label: cardLabel(k) }))} />}
          </FieldShell>
          <FieldShell label="Scenario questions" description="Applied-judgement questions, dropped first when time runs short." error={errors["intake.scenarios"]}>
            {(control) => <NumberInput {...control} value={view.scenarios} min={0} max={3} unit="of 3" onChange={(v) => set({ scenarios: v ?? 0 })} />}
          </FieldShell>
          <FieldShell label="Candidate questions answered at the end" description="From the organisation's approved program information only." error={errors["intake.closing_questions_max"]}>
            {(control) => <NumberInput {...control} value={view.closing_questions_max} min={0} max={5} unit="of 5" onChange={(v) => set({ closing_questions_max: v ?? 0 })} />}
          </FieldShell>
        </div>
      </SectionBlock>

      <SectionBlock variant="plain" title="Work authorisation" description="Asked only on the candidate's form, never aloud.">
        <div className="grid grid-cols-1 gap-4">
          <RadioGroup
            value={waMode}
            onValueChange={(mode) =>
              set({
                work_authorization:
                  mode === "inherit" ? null : mode === "off" ? { enabled: false, country: null, ask_sponsorship: true, variant: "default" } : { enabled: true, country: view.work_authorization?.country ?? "", ask_sponsorship: view.work_authorization?.ask_sponsorship ?? true, variant: "default" },
              })
            }
            aria-label="Work authorisation for this round"
            className="gap-2.5"
          >
            <RadioGroupItem value="inherit" label="Use the organisation default" description="Set in the intake settings." />
            <RadioGroupItem value="ask" label="Ask in this round" />
            <RadioGroupItem value="off" label="Don't ask in this round" />
          </RadioGroup>
          {waMode === "ask" && view.work_authorization ? (
            <div className="grid grid-cols-1 gap-4 border-l-2 border-line pl-4 sm:grid-cols-2">
              <FieldShell label="Country" required error={errors["intake.work_authorization.country"]}>
                {(control) => (
                  <Input
                    {...control}
                    maxLength={80}
                    placeholder="e.g. the United States"
                    value={view.work_authorization?.country ?? ""}
                    onChange={(e) => set({ work_authorization: { ...view.work_authorization!, country: e.target.value } })}
                  />
                )}
              </FieldShell>
              <div className="grid grid-cols-1 content-start sm:pt-6">
                <Switch label="Also ask about sponsorship" checked={view.work_authorization.ask_sponsorship} onCheckedChange={(on) => set({ work_authorization: { ...view.work_authorization!, ask_sponsorship: on } })} />
              </div>
            </div>
          ) : null}
          <p className="flex items-start gap-1.5 text-xs text-fg-muted">
            <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
            {ELIGIBILITY_NOTE}.
          </p>
        </div>
      </SectionBlock>
    </fieldset>
  );
}
