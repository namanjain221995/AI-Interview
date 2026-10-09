/**
 * The round editor shared by job rounds and templates. Controlled: it renders
 * a complete RoundConfig and reports every change; validation is derived on
 * each render so issues update live as the recruiter types.
 */

import { Info } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { HUMAN_DECISION_NOTE } from "@/components/domain/RecommendationBadge";
import { Callout } from "@/components/ui/Callout";
import { MultiSelect, type ComboboxOption } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Slider } from "@/components/ui/Slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { DIFFICULTY_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { CodingQuestion, Competency, Difficulty, RoundConfig, RoundType } from "@/lib/types";
import { cn } from "@/lib/utils";

import { IntakeRoundBlueprint, IntakeRoundPanel } from "@/features/intake/staff/components/round/IntakeRoundPanel";

import { FieldShell, NumberInput, SectionBlock, ToggleRow } from "../components/Field";
import type { FieldErrors } from "../lib/serverErrors";
import { CodingSectionEditor } from "./CodingSectionEditor";
import { CompetencyListEditor, competencyOptionsFrom } from "./CompetencyListEditor";
import { DifficultyMixEditor } from "./DifficultyMixEditor";
import { RoundBlueprint } from "./RoundBlueprint";
import {
  applyRoundTypeChange,
  countBySection,
  INTAKE_DURATION,
  issuesToErrors,
  QUESTION_STYLES,
  ROUND_TYPES,
  sectionsFor,
  validateRoundConfig,
  type RoundConfigWithIntake,
  type RoundSection,
} from "./roundConfig";
import { RubricEditor } from "./RubricEditor";

const STYLE_OPTIONS: ComboboxOption[] = QUESTION_STYLES.map((s) => ({ value: s.value, label: s.label, description: s.description }));
const DIFFICULTY_OPTIONS = (["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }));
const ROUND_TYPE_OPTIONS = ROUND_TYPES.map((t) => ({ value: t, label: ROUND_TYPE_LABELS[t] }));

export interface RoundConfigEditorProps {
  value: RoundConfig;
  onChange: (value: RoundConfig) => void;
  catalog: Competency[] | undefined;
  problems: CodingQuestion[] | undefined;
  /** Errors returned by the API for this round, keyed by config path. */
  serverErrors?: FieldErrors;
  /** Show only after the first save attempt (otherwise errors appear as you type). */
  showErrors?: boolean;
  /** Extra content at the top of the Setup tab (e.g. template name fields). */
  setupExtra?: ReactNode;
  readOnly?: boolean;
  className?: string;
}

/** 0-1 fraction edited as a whole percentage. */
function PercentInput({ value, onChange, ...rest }: { value: number; onChange: (value: number) => void; id?: string; "aria-invalid"?: boolean; "aria-describedby"?: string }) {
  return <NumberInput {...rest} value={Math.round(value * 100)} min={0} max={100} unit="%" onChange={(v) => onChange(Math.max(0, Math.min(100, v ?? 0)) / 100)} />;
}

export function RoundConfigEditor({ value, onChange, catalog, problems, serverErrors, showErrors = true, setupExtra, readOnly, className }: RoundConfigEditorProps) {
  const [tab, setTab] = useState<RoundSection>("setup");
  const issues = useMemo(() => validateRoundConfig(value), [value]);
  const errors = useMemo<FieldErrors>(() => ({ ...(showErrors ? issuesToErrors(issues, "error") : {}), ...(serverErrors ?? {}) }), [issues, serverErrors, showErrors]);
  const warnings = useMemo(() => issuesToErrors(issues, "warning"), [issues]);
  const counts = countBySection(Object.keys(errors));

  const set = <K extends keyof RoundConfig>(key: K, next: RoundConfig[K]) => onChange({ ...value, [key]: next });
  // Intake rounds (docs/design/INTAKE_INTERVIEW.md 4.1): 35-50 minutes, no coding or difficulty mix, an Intake tab.
  const isIntake = value.round_type === "intake";
  const sections = sectionsFor(value.round_type);
  const activeTab: RoundSection = sections.some((section) => section.key === tab) ? tab : "setup";
  const roundCompetencyOptions: ComboboxOption[] = useMemo(() => {
    const names = competencyOptionsFrom(catalog);
    return value.competencies
      .filter((c) => c.key)
      .map((c) => ({ value: c.key, label: names.find((o) => o.value === c.key)?.label ?? c.name ?? c.key }));
  }, [catalog, value.competencies]);

  return (
    <div className={cn("grid min-w-0 gap-5", className)}>
      {isIntake ? (
        <IntakeRoundBlueprint
          durationMinutes={value.duration_minutes >= INTAKE_DURATION.min && value.duration_minutes <= INTAKE_DURATION.max ? value.duration_minutes : INTAKE_DURATION.default}
          className="rounded-lg border border-line bg-surface-subtle/60 p-4"
        />
      ) : (
        <RoundBlueprint config={value} catalog={catalog} compact className="rounded-lg border border-line bg-surface-subtle/60 p-4" />
      )}

      <Tabs value={activeTab} onValueChange={(v) => setTab(v as RoundSection)}>
        <TabsList aria-label="Round settings">
          {sections.map((section) => (
            <TabsTrigger key={section.key} value={section.key}>
              {section.label}
              {counts[section.key] > 0 ? (
                <span className="flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-danger px-1 text-2xs font-semibold text-danger-contrast tabular-nums" aria-label={`${counts[section.key]} to fix`}>
                  {counts[section.key]}
                </span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* ---------------------------------------------------------- Setup */}
        <TabsContent value="setup">
          <fieldset disabled={readOnly} className="grid min-w-0 gap-8">
          <SectionBlock variant="plain" title="Basics">
            <div className="grid gap-4">
              {setupExtra}
              <div className="grid gap-4 sm:grid-cols-2">
                <FieldShell label="Round name" required error={errors.name}>
                  {(control) => <Input {...control} value={value.name} maxLength={200} placeholder="e.g. Technical deep dive" onChange={(e) => set("name", e.target.value)} />}
                </FieldShell>
                <FieldShell label="Round type" description="Shapes the interviewer's opening and segment plan.">
                  {(control) => (
                    <Select<RoundType> id={control.id} value={value.round_type} options={ROUND_TYPE_OPTIONS} onValueChange={(v) => onChange(applyRoundTypeChange(value as RoundConfigWithIntake, v))} />
                  )}
                </FieldShell>
              </div>
              <div className={cn("grid gap-4", isIntake ? "sm:grid-cols-2" : "sm:grid-cols-3")}>
                <FieldShell label="Planned duration" error={errors.duration_minutes} description={isIntake ? `Intake rounds last ${INTAKE_DURATION.min}-${INTAKE_DURATION.max} minutes.` : undefined}>
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.duration_minutes}
                      min={isIntake ? INTAKE_DURATION.min : 5}
                      max={isIntake ? INTAKE_DURATION.max : 240}
                      unit="min"
                      onChange={(v) => set("duration_minutes", v ?? 0)}
                    />
                  )}
                </FieldShell>
                <FieldShell label="Hard stop" error={errors.max_duration_minutes} warning={warnings.max_duration_minutes} description="The interview always ends by here.">
                  {(control) => <NumberInput {...control} value={value.max_duration_minutes} min={5} max={300} unit="min" onChange={(v) => set("max_duration_minutes", v ?? 0)} />}
                </FieldShell>
                {!isIntake ? (
                  <FieldShell label="Starting difficulty">
                    {(control) => (
                      <Select<Difficulty> id={control.id} value={value.expected_difficulty} options={DIFFICULTY_OPTIONS} onValueChange={(v) => set("expected_difficulty", v)} />
                    )}
                  </FieldShell>
                ) : null}
              </div>
            </div>
          </SectionBlock>

          {!isIntake ? (
            <SectionBlock
              variant="plain"
              title="Difficulty mix"
              description="Target share of questions at each level. With adaptive difficulty on, the interviewer moves up or down from here based on the answers."
            >
              <DifficultyMixEditor value={value.difficulty_distribution} onChange={(d) => set("difficulty_distribution", d)} error={errors.difficulty_distribution} disabled={readOnly} />
            </SectionBlock>
          ) : null}

          <SectionBlock
            variant="plain"
            title="Competencies"
            description="What this round gathers evidence on. Weights set how much each one counts; required ones must have enough evidence before the interviewer wraps up."
          >
            {warnings.competencies ? <p className="mb-3 text-xs font-medium text-warning-fg">{warnings.competencies}</p> : null}
            <CompetencyListEditor
              value={value.competencies}
              onChange={(competencies) => set("competencies", competencies)}
              catalog={catalog}
              defaultMinEvidence={value.min_evidence_per_competency}
              errors={errors}
            />
          </SectionBlock>
          </fieldset>
        </TabsContent>

        {/* -------------------------------------------------------- Scoring */}
        <TabsContent value="scoring">
          <fieldset disabled={readOnly} className="grid min-w-0 gap-8">
          <SectionBlock
            variant="plain"
            title="Rubric"
            description="Weighted categories for the overall score. Each category rolls up competency scores and answer dimensions. Weights must total 100%."
          >
            <RubricEditor value={value.rubric} onChange={(rubric) => set("rubric", rubric)} competencyOptions={roundCompetencyOptions} errors={errors} warnings={warnings} />
          </SectionBlock>

          <SectionBlock variant="plain" title="Evidence" description="How much evidence the interviewer collects before a competency counts as covered.">
            <div className="grid gap-4 sm:grid-cols-2">
              <FieldShell label="Evidence items per competency" error={errors.min_evidence_per_competency} description="Each competency can override this.">
                {(control) => <NumberInput {...control} value={value.min_evidence_per_competency} min={1} max={20} onChange={(v) => set("min_evidence_per_competency", v ?? 1)} />}
              </FieldShell>
              <FieldShell label="Confidence to call it covered" error={errors.confidence_threshold}>
                {(control) => <PercentInput {...control} value={value.confidence_threshold} onChange={(v) => set("confidence_threshold", v)} />}
              </FieldShell>
            </div>
          </SectionBlock>

          <SectionBlock variant="plain" title="Passing guidance" description="Score bands the report uses to describe the evidence. They suggest, never decide.">
            <div className="grid gap-4">
              <Callout tone="neutral" icon={<Info />}>
                {HUMAN_DECISION_NOTE}
              </Callout>
              {errors.passing_guidance ? <p className="text-xs font-medium text-danger-fg">{errors.passing_guidance}</p> : null}
              <div className="grid gap-4 sm:grid-cols-3">
                <FieldShell label="Strong evidence from" error={errors["passing_guidance.strong_proceed_min_score"]}>
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.passing_guidance.strong_proceed_min_score}
                      min={0}
                      max={100}
                      unit="/ 100"
                      onChange={(v) => set("passing_guidance", { ...value.passing_guidance, strong_proceed_min_score: v ?? 0 })}
                    />
                  )}
                </FieldShell>
                <FieldShell label="Supports proceeding from" error={errors["passing_guidance.proceed_min_score"]}>
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.passing_guidance.proceed_min_score}
                      min={0}
                      max={100}
                      unit="/ 100"
                      onChange={(v) => set("passing_guidance", { ...value.passing_guidance, proceed_min_score: v ?? 0 })}
                    />
                  )}
                </FieldShell>
                <FieldShell label="Borderline from" error={errors["passing_guidance.borderline_min_score"]}>
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.passing_guidance.borderline_min_score}
                      min={0}
                      max={100}
                      unit="/ 100"
                      onChange={(v) => set("passing_guidance", { ...value.passing_guidance, borderline_min_score: v ?? 0 })}
                    />
                  )}
                </FieldShell>
              </div>
              <ThresholdScale guidance={value.passing_guidance} />
              <div className="grid gap-4 sm:grid-cols-2">
                <FieldShell label="Minimum confidence" description="Below this the report says there isn't enough evidence." error={errors["passing_guidance.min_confidence"]}>
                  {(control) => (
                    <PercentInput {...control} value={value.passing_guidance.min_confidence} onChange={(v) => set("passing_guidance", { ...value.passing_guidance, min_confidence: v })} />
                  )}
                </FieldShell>
                <FieldShell label="Required competencies covered" description="Share that must have enough evidence." error={errors["passing_guidance.min_required_coverage"]}>
                  {(control) => (
                    <PercentInput
                      {...control}
                      value={value.passing_guidance.min_required_coverage}
                      onChange={(v) => set("passing_guidance", { ...value.passing_guidance, min_required_coverage: v })}
                    />
                  )}
                </FieldShell>
              </div>
              <FieldShell label="Note for reviewers" description="Shown with every recommendation.">
                {(control) => (
                  <Textarea
                    {...control}
                    rows={2}
                    autoResize
                    value={value.passing_guidance.reviewer_notes}
                    onChange={(e) => set("passing_guidance", { ...value.passing_guidance, reviewer_notes: e.target.value })}
                  />
                )}
              </FieldShell>
            </div>
          </SectionBlock>
          </fieldset>
        </TabsContent>

        {/* ----------------------------------------------------------- Flow */}
        <TabsContent value="flow">
          <fieldset disabled={readOnly} className="grid min-w-0 gap-8">
          <SectionBlock variant="plain" title="Question strategy" description="How the interviewer chooses and phrases questions.">
            <div className="grid gap-5">
              <FieldShell label="Question styles, in order of preference" warning={warnings["question_strategy.style"]}>
                {(control) => (
                  <MultiSelect
                    {...control}
                    value={value.question_strategy.style}
                    options={STYLE_OPTIONS}
                    placeholder="Add a style"
                    onChange={(style) => set("question_strategy", { ...value.question_strategy, style })}
                  />
                )}
              </FieldShell>
              <div className="grid gap-5 sm:grid-cols-2">
                <FieldShell label="Follow-ups on one topic" error={errors["question_strategy.max_followup_depth"]} description="While the answer is still unclear.">
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.question_strategy.max_followup_depth}
                      min={0}
                      max={8}
                      onChange={(v) => set("question_strategy", { ...value.question_strategy, max_followup_depth: v ?? 0 })}
                    />
                  )}
                </FieldShell>
                <FieldShell
                  label="Questions from the curated bank"
                  aside={<span className="text-xs font-medium text-fg tabular-nums">{Math.round(value.question_strategy.prefer_curated_ratio * 100)}%</span>}
                  description="The rest are written live from the resume and earlier answers."
                >
                  {() => (
                    <Slider
                      className="pt-2"
                      value={[Math.round(value.question_strategy.prefer_curated_ratio * 100)]}
                      min={0}
                      max={100}
                      step={5}
                      thumbLabels={["Curated question share"]}
                      disabled={readOnly}
                      formatValue={(v) => `${v}%`}
                      onValueChange={([v]) => set("question_strategy", { ...value.question_strategy, prefer_curated_ratio: (v ?? 0) / 100 })}
                    />
                  )}
                </FieldShell>
              </div>
              <div className="divide-y divide-line-subtle rounded-md border border-line px-4">
                <ToggleRow
                  label="Adapt difficulty to the answers"
                  description="Harder after strong answers, simpler after weak ones, while keeping the target mix."
                  checked={value.question_strategy.adaptive_difficulty}
                  onCheckedChange={(v) => set("question_strategy", { ...value.question_strategy, adaptive_difficulty: v })}
                />
                <ToggleRow
                  label="Allow newly written questions"
                  description="Lets the interviewer write questions beyond the bank, grounded in the job and resume."
                  checked={value.question_strategy.allow_generated_questions}
                  onCheckedChange={(v) => set("question_strategy", { ...value.question_strategy, allow_generated_questions: v })}
                />
                <ToggleRow
                  label="Invite the candidate's questions at the end"
                  checked={value.question_strategy.allow_candidate_questions_at_end}
                  onCheckedChange={(v) => set("question_strategy", { ...value.question_strategy, allow_candidate_questions_at_end: v })}
                />
              </div>
            </div>
          </SectionBlock>

          <SectionBlock variant="plain" title="When to stop" description="The interview also ends at the hard stop, whatever happens.">
            <div className="grid gap-5">
              <ToggleRow
                className="py-0"
                label="Wrap up once evidence is sufficient"
                description="Ends early when every required competency is covered and the minimums below are met."
                checked={value.stopping.stop_on_sufficient_evidence}
                onCheckedChange={(v) => set("stopping", { ...value.stopping, stop_on_sufficient_evidence: v })}
              />
              <div className="grid gap-4 sm:grid-cols-3">
                <FieldShell label="Run at least" error={errors["stopping.min_duration_minutes"]} warning={warnings["stopping.min_duration_minutes"]}>
                  {(control) => (
                    <NumberInput {...control} value={value.stopping.min_duration_minutes} min={0} unit="min" onChange={(v) => set("stopping", { ...value.stopping, min_duration_minutes: v ?? 0 })} />
                  )}
                </FieldShell>
                <FieldShell label="Ask at least" error={errors["stopping.min_questions"]}>
                  {(control) => (
                    <NumberInput {...control} value={value.stopping.min_questions} min={0} max={100} unit="questions" onChange={(v) => set("stopping", { ...value.stopping, min_questions: v ?? 0 })} />
                  )}
                </FieldShell>
                <FieldShell label="Ask at most" error={errors["stopping.max_questions"]}>
                  {(control) => (
                    <NumberInput
                      {...control}
                      value={value.stopping.max_questions}
                      nullable
                      min={1}
                      max={200}
                      placeholder="No limit"
                      unit="questions"
                      onChange={(v) => set("stopping", { ...value.stopping, max_questions: v })}
                    />
                  )}
                </FieldShell>
              </div>
            </div>
          </SectionBlock>

          <SectionBlock variant="plain" title="Resume claims" description="Check the most important statements on the resume against what the candidate says.">
            <div className="grid gap-5">
              <ToggleRow
                className="py-0"
                label="Verify resume claims"
                checked={value.resume_validation.enabled}
                onCheckedChange={(v) => set("resume_validation", { ...value.resume_validation, enabled: v })}
              />
              {value.resume_validation.enabled ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <FieldShell label="Claims to check" error={errors["resume_validation.max_claims"]}>
                    {(control) => (
                      <NumberInput {...control} value={value.resume_validation.max_claims} min={0} max={20} onChange={(v) => set("resume_validation", { ...value.resume_validation, max_claims: v ?? 0 })} />
                    )}
                  </FieldShell>
                  <FieldShell label="Only claims of priority" error={errors["resume_validation.min_priority"]} description="1 is minor, 5 is central to the job.">
                    {(control) => (
                      <Select<string>
                        id={control.id}
                        value={String(value.resume_validation.min_priority)}
                        onValueChange={(v) => set("resume_validation", { ...value.resume_validation, min_priority: Number(v) })}
                        options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 5 ? "5 only" : `${n} and above` }))}
                      />
                    )}
                  </FieldShell>
                </div>
              ) : null}
            </div>
          </SectionBlock>
          </fieldset>
        </TabsContent>

        {/* --------------------------------------------------------- Coding */}
        {!isIntake ? (
          <TabsContent value="coding">
            <fieldset disabled={readOnly} className="min-w-0">
            <CodingSectionEditor value={value.coding} onChange={(coding) => set("coding", coding)} problems={problems} roundDuration={value.duration_minutes} errors={errors} warnings={warnings} />
            </fieldset>
          </TabsContent>
        ) : (
          <TabsContent value="intake">
            <IntakeRoundPanel value={value as RoundConfigWithIntake} onChange={onChange} errors={errors} warnings={warnings} readOnly={readOnly} />
          </TabsContent>
        )}

        {/* ------------------------------------------------------ Candidate */}
        <TabsContent value="candidate">
          <fieldset disabled={readOnly} className="grid min-w-0 gap-8">
          <SectionBlock variant="plain" title="For the candidate">
            <div className="grid gap-5">
              <FieldShell label="Instructions before the round starts" error={errors.instructions} description="Plain language. Shown on the instructions step and read out by the interviewer.">
                {(control) => <Textarea {...control} rows={4} autoResize maxRows={12} maxLength={4000} showCount value={value.instructions} onChange={(e) => set("instructions", e.target.value)} />}
              </FieldShell>
              <ToggleRow
                className="py-0"
                label="Candidates can pick this round"
                description={isIntake ? "Intake rounds are assigned by invitation; candidates can't pick them." : "When a job lets candidates choose, only rounds marked here are offered."}
                checked={value.candidate_selectable}
                disabled={isIntake && !value.candidate_selectable}
                onCheckedChange={(v) => set("candidate_selectable", v)}
              />
              {errors.candidate_selectable ? <p className="text-xs font-medium text-danger-fg">{errors.candidate_selectable}</p> : null}
            </div>
          </SectionBlock>
          <SectionBlock variant="plain" title="For the interviewer" description="Private guidance for the AI interviewer. Never shown to candidates.">
            <FieldShell label="Interviewer notes" optional>
              {(control) => (
                <Textarea
                  {...control}
                  rows={4}
                  autoResize
                  maxRows={12}
                  value={value.interviewer_notes ?? ""}
                  placeholder="e.g. Spend time on incident response; the team is on call weekly."
                  onChange={(e) => set("interviewer_notes", e.target.value)}
                />
              )}
            </FieldShell>
          </SectionBlock>
          </fieldset>
        </TabsContent>
      </Tabs>
    </div>
  );
}

/** Visual of the three score bands on a 0-100 line. */
function ThresholdScale({ guidance }: { guidance: RoundConfig["passing_guidance"] }) {
  const b = Math.max(0, Math.min(100, guidance.borderline_min_score));
  const p = Math.max(b, Math.min(100, guidance.proceed_min_score));
  const s = Math.max(p, Math.min(100, guidance.strong_proceed_min_score));
  const bands = [
    { label: "Not enough", from: 0, to: b, tone: "bg-track" },
    { label: "Borderline", from: b, to: p, tone: "bg-warning/40" },
    { label: "Supports proceeding", from: p, to: s, tone: "bg-chart-1/45" },
    { label: "Strong", from: s, to: 100, tone: "bg-success/50" },
  ];
  return (
    <div aria-hidden="true" className="grid gap-1">
      <div className="flex h-2 w-full overflow-hidden rounded-full">
        {bands.map((band) => (
          <div key={band.label} className={cn("h-full", band.tone)} style={{ width: `${band.to - band.from}%` }} />
        ))}
      </div>
      <div className="relative h-4 text-2xs text-fg-subtle tabular-nums">
        {[0, b, p, s, 100].map((mark, index) => (
          <span key={index} className="absolute -translate-x-1/2" style={{ left: `${mark}%` }}>
            {mark}
          </span>
        ))}
      </div>
    </div>
  );
}
