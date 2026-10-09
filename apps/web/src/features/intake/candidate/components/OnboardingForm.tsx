import { LockKeyhole } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { StepFooter } from "@/features/candidate/components/StepLayout";
import { cn } from "@/lib/utils";

import {
  asksOpenToAiMl,
  AVAILABILITY_OPTIONS,
  COMFORT_OPTIONS,
  COMMITMENT_OPTIONS,
  ELIGIBILITY_OPTIONS,
  LANGUAGE_OPTIONS,
  LIMITS,
  ONBOARDING_FIELDS,
  OPEN_OPTIONS,
  SCRIPTING_OPTIONS,
  TARGET_ROLE_OPTIONS,
  toOnboardingBody,
  validateOnboarding,
  type OnboardingDraft,
  type OnboardingField,
} from "../onboarding";
import type { EligibilityView, IntakeOnboardingIn, TargetRole } from "../types";
import { ChoiceGroup, Field, RatingScale } from "./FormParts";

export interface OnboardingFormProps {
  initial: OnboardingDraft;
  eligibility: EligibilityView;
  /** The invitation's address (shown, never edited here). */
  email: string | null;
  /** A session started: the answers can no longer change. */
  locked: boolean;
  submitting: boolean;
  /** Errors the server returned, by form field. */
  serverErrors: Partial<Record<OnboardingField, string>>;
  formError: string | null;
  onSubmit: (body: IntakeOnboardingIn) => void;
  onBack?: (() => void) | null;
  /** Shown instead of the submit button when the form is locked. */
  lockedAction?: ReactNode;
  /** The hiring team set the role for this interview: shown read-only (the server stores the preset). */
  targetRolePreset?: TargetRole | null;
}

function Section({ id, title, lede, children, className }: { id: string; title: string; lede?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section aria-labelledby={id} className={cn("grid grid-cols-1 gap-6 border-t border-line pt-8 first:border-t-0 first:pt-0", className)}>
      <div className="grid gap-1">
        <h2 id={id} className="text-heading text-fg">
          {title}
        </h2>
        {lede ? <p className="max-w-xl text-sm leading-6 text-fg-muted">{lede}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * The intake details form: who you are, the role you're aiming for, your
 * technical background, training and availability, and (only when the
 * organisation asks) work eligibility. Errors appear after the first attempt to
 * continue and clear as each answer is fixed.
 */
export function OnboardingForm({ initial, eligibility, email, locked, submitting, serverErrors, formError, onSubmit, onBack, lockedAction, targetRolePreset = null }: OnboardingFormProps) {
  const [draft, setDraft] = useState<OnboardingDraft>(initial);
  const [attempted, setAttempted] = useState(false);
  // A server error stays on its field until that answer changes (or the next save answers).
  const [cleared, setCleared] = useState<ReadonlySet<string>>(() => new Set());
  useEffect(() => setCleared(new Set()), [serverErrors]);
  const formRef = useRef<HTMLFormElement>(null);

  const clientErrors = useMemo(() => (attempted ? validateOnboarding(draft, eligibility) : {}), [attempted, draft, eligibility]);
  const errors: Partial<Record<OnboardingField, string>> = { ...(submitting ? {} : serverErrorsFor(serverErrors, cleared)), ...clientErrors };
  const errorCount = ONBOARDING_FIELDS.filter((f) => errors[f]).length;

  const set = <K extends OnboardingField>(field: K, value: OnboardingDraft[K]) => {
    setDraft((d) => ({ ...d, [field]: value }));
    if (serverErrors[field]) setCleared((c) => new Set(c).add(field));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (locked || submitting) return;
    setAttempted(true);
    const found = validateOnboarding(draft, eligibility);
    const first = ONBOARDING_FIELDS.find((f) => found[f]);
    if (first) {
      focusField(formRef.current, first);
      return;
    }
    onSubmit(toOnboardingBody(draft, eligibility));
  };

  const disabled = locked || submitting;
  const country = eligibility.country ?? "this country";

  return (
    <form ref={formRef} noValidate onSubmit={submit} data-testid="intake-details-form" className="grid grid-cols-1 gap-8">
      {locked ? (
        <Callout tone="neutral" icon={<LockKeyhole />} title="These details are locked">
          Your interview has started, so your answers can no longer change. The hiring team sees them as you submitted them.
        </Callout>
      ) : null}

      <Section id="details-you" title="About you">
        <Field name="full_name" label="Full name" error={errors.full_name}>
          {(ids) => (
            <Input
              id={ids.id}
              data-testid="intake-field-full_name"
              size="lg"
              autoComplete="name"
              value={draft.full_name}
              maxLength={LIMITS.full_name}
              onChange={(e) => set("full_name", e.target.value)}
              aria-invalid={ids.invalid || undefined}
              aria-describedby={ids.describedBy}
              disabled={disabled}
              className="max-w-md"
            />
          )}
        </Field>
        {email ? (
          <p className="-mt-2 text-[13px] leading-5 text-fg-muted">
            We'll use <span className="font-medium break-all text-fg">{email}</span>, the address your invitation was sent to.
          </p>
        ) : null}
      </Section>

      <Section id="details-role" title="The role you're aiming for">
        {targetRolePreset ? (
          <div className="grid gap-1" data-field="target_role">
            <p data-testid="intake-field-target_role" data-preset={targetRolePreset} className="text-[15px] leading-6 font-medium text-fg">
              {TARGET_ROLE_OPTIONS.find((o) => o.value === targetRolePreset)?.label ?? targetRolePreset}
            </p>
            <p className="text-[13px] leading-5 text-fg-muted">The hiring team set this for your interview.</p>
          </div>
        ) : (
          <Field name="target_role" label="Which role are you aiming for?" error={errors.target_role}>
            {(ids) => <ChoiceGroup ids={ids} name="target_role" value={draft.target_role} options={TARGET_ROLE_OPTIONS} onChange={(v) => set("target_role", v)} disabled={disabled} layout="grid" />}
          </Field>
        )}
        {draft.target_role === "other" ? (
          <Field name="target_role_other" label="Which role?" error={errors.target_role_other}>
            {(ids) => (
              <Input
                id={ids.id}
                data-testid="intake-field-target_role_other"
                value={draft.target_role_other}
                maxLength={LIMITS.target_role_other}
                onChange={(e) => set("target_role_other", e.target.value)}
                aria-invalid={ids.invalid || undefined}
                aria-describedby={ids.describedBy}
                disabled={disabled}
                className="max-w-md"
              />
            )}
          </Field>
        ) : null}
        {asksOpenToAiMl(draft) ? (
          <Field
            name="open_to_ai_ml"
            label="Would you also consider an AI/ML role?"
            description="Most of the roles this program prepares people for are in AI/ML."
            error={errors.open_to_ai_ml}
          >
            {(ids) => <ChoiceGroup ids={ids} name="open_to_ai_ml" value={draft.open_to_ai_ml} options={OPEN_OPTIONS} onChange={(v) => set("open_to_ai_ml", v)} disabled={disabled} />}
          </Field>
        ) : null}
      </Section>

      <Section id="details-technical" title="Your technical background">
        <Field
          name="technical_background"
          label="Tell us about your technical background"
          description="Your studies, your work and anything you've built. A few sentences is enough; if you're new to tech, say so."
          error={errors.technical_background}
        >
          {(ids) => (
            <Textarea
              id={ids.id}
              data-testid="intake-field-technical_background"
              value={draft.technical_background}
              maxLength={LIMITS.technical_background}
              showCount
              autoResize
              rows={4}
              maxRows={10}
              onChange={(e) => set("technical_background", e.target.value)}
              aria-invalid={ids.invalid || undefined}
              aria-describedby={ids.describedBy}
              disabled={disabled}
            />
          )}
        </Field>
        <Field name="primary_language" label="Which programming language do you know best?" error={errors.primary_language}>
          {(ids) => <ChoiceGroup ids={ids} name="primary_language" value={draft.primary_language} options={LANGUAGE_OPTIONS} onChange={(v) => set("primary_language", v)} disabled={disabled} />}
        </Field>
        {draft.primary_language === "other" ? (
          <Field name="primary_language_other" label="Which language?" error={errors.primary_language_other}>
            {(ids) => (
              <Input
                id={ids.id}
                data-testid="intake-field-primary_language_other"
                value={draft.primary_language_other}
                maxLength={LIMITS.primary_language_other}
                onChange={(e) => set("primary_language_other", e.target.value)}
                aria-invalid={ids.invalid || undefined}
                aria-describedby={ids.describedBy}
                disabled={disabled}
                className="max-w-xs"
              />
            )}
          </Field>
        ) : null}
        <Field
          name="self_rating"
          label="How would you rate yourself in that language?"
          description="The interviewer asks one short question about it, so rate yourself as you really are."
          error={errors.self_rating}
        >
          {(ids) => <RatingScale ids={ids} value={draft.self_rating} onChange={(v) => set("self_rating", v)} disabled={disabled} />}
        </Field>
        <Field name="scripting" label="Have you written scripts, for example to automate a task or process data?" error={errors.scripting}>
          {(ids) => <ChoiceGroup ids={ids} name="scripting" value={draft.scripting} options={SCRIPTING_OPTIONS} onChange={(v) => set("scripting", v)} disabled={disabled} />}
        </Field>
        {draft.scripting && draft.scripting !== "none" ? (
          <Field name="scripting_details" label="What kind of scripts?" optional error={errors.scripting_details}>
            {(ids) => (
              <Textarea
                id={ids.id}
                data-testid="intake-field-scripting_details"
                value={draft.scripting_details}
                maxLength={LIMITS.scripting_details}
                autoResize
                rows={2}
                maxRows={6}
                onChange={(e) => set("scripting_details", e.target.value)}
                aria-invalid={ids.invalid || undefined}
                aria-describedby={ids.describedBy}
                disabled={disabled}
                placeholder="For example: a Python script that cleans weekly sales exports"
              />
            )}
          </Field>
        ) : null}
        <Field name="data_ml_libraries" label="Data or machine-learning libraries you've used" optional error={errors.data_ml_libraries}>
          {(ids) => (
            <Input
              id={ids.id}
              data-testid="intake-field-data_ml_libraries"
              value={draft.data_ml_libraries}
              maxLength={LIMITS.data_ml_libraries}
              onChange={(e) => set("data_ml_libraries", e.target.value)}
              aria-invalid={ids.invalid || undefined}
              aria-describedby={ids.describedBy}
              disabled={disabled}
              placeholder="For example pandas, NumPy, scikit-learn"
            />
          )}
        </Field>
        <Field
          name="years_hands_on"
          label="Years of hands-on technical experience"
          optional
          description="Count only work where you built or ran technical things yourself. Zero is a fine answer."
          error={errors.years_hands_on}
        >
          {(ids) => (
            <Input
              id={ids.id}
              data-testid="intake-field-years_hands_on"
              inputMode="decimal"
              value={draft.years_hands_on}
              onChange={(e) => set("years_hands_on", e.target.value)}
              aria-invalid={ids.invalid || undefined}
              aria-describedby={ids.describedBy}
              disabled={disabled}
              className="max-w-[9rem]"
            />
          )}
        </Field>
      </Section>

      <Section id="details-training" title="Training and availability">
        <Field
          name="experience_level_comfort"
          label="This program prepares people for roles that usually expect around five years of hands-on experience. Are you comfortable with the responsibilities and skills expected at that level?"
          description="Answer honestly: the hiring team records your real experience, and the training is there to close the gap."
          error={errors.experience_level_comfort}
        >
          {(ids) => (
            <ChoiceGroup ids={ids} name="experience_level_comfort" value={draft.experience_level_comfort} options={COMFORT_OPTIONS} onChange={(v) => set("experience_level_comfort", v)} disabled={disabled} />
          )}
        </Field>
        <Field name="training_commitment" label="The training is intensive and time-boxed. Can you commit to it?" error={errors.training_commitment}>
          {(ids) => <ChoiceGroup ids={ids} name="training_commitment" value={draft.training_commitment} options={COMMITMENT_OPTIONS} onChange={(v) => set("training_commitment", v)} disabled={disabled} />}
        </Field>
        <Field name="weekly_hours" label="Hours a week you could give the training" optional error={errors.weekly_hours}>
          {(ids) => (
            <Input
              id={ids.id}
              data-testid="intake-field-weekly_hours"
              inputMode="numeric"
              value={draft.weekly_hours}
              onChange={(e) => set("weekly_hours", e.target.value)}
              aria-invalid={ids.invalid || undefined}
              aria-describedby={ids.describedBy}
              disabled={disabled}
              className="max-w-[9rem]"
            />
          )}
        </Field>
        <Field name="availability" label="When could you start?" error={errors.availability}>
          {(ids) => <ChoiceGroup ids={ids} name="availability" value={draft.availability} options={AVAILABILITY_OPTIONS} onChange={(v) => set("availability", v)} disabled={disabled} />}
        </Field>
        {draft.availability === "later" ? (
          <Field name="available_from" label="From when?" optional error={errors.available_from}>
            {(ids) => (
              <Input
                id={ids.id}
                type="date"
                data-testid="intake-field-available_from"
                value={draft.available_from}
                onChange={(e) => set("available_from", e.target.value)}
                aria-invalid={ids.invalid || undefined}
                aria-describedby={ids.describedBy}
                disabled={disabled}
                className="max-w-[12rem]"
              />
            )}
          </Field>
        ) : null}
      </Section>

      {eligibility.enabled ? (
        <Section
          id="details-eligibility"
          title="Work eligibility"
          lede={
            <span className="flex items-start gap-2">
              <LockKeyhole className="mt-1 size-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
              Used only by the hiring team, never by the AI interviewer or in any assessment.
            </span>
          }
        >
          <Field name="work_authorized" label={`Are you legally authorised to work in ${country} for this role?`} error={errors.work_authorized}>
            {(ids) => <ChoiceGroup ids={ids} name="work_authorized" value={draft.work_authorized} options={ELIGIBILITY_OPTIONS} onChange={(v) => set("work_authorized", v)} disabled={disabled} layout="stack" className="max-w-md" />}
          </Field>
          {eligibility.askSponsorship ? (
            <Field name="needs_sponsorship" label="Will you need sponsorship now or in the future?" error={errors.needs_sponsorship}>
              {(ids) => (
                <ChoiceGroup ids={ids} name="needs_sponsorship" value={draft.needs_sponsorship} options={ELIGIBILITY_OPTIONS} onChange={(v) => set("needs_sponsorship", v)} disabled={disabled} layout="stack" className="max-w-md" />
              )}
            </Field>
          ) : null}
        </Section>
      ) : null}

      <section aria-label="Confirm your answers" className="grid grid-cols-1 gap-2 border-t border-line pt-8" data-field="accuracy_confirmed">
        <Checkbox
          data-testid="intake-field-accuracy_confirmed"
          checked={draft.accuracy_confirmed}
          onCheckedChange={(v) => set("accuracy_confirmed", v === true)}
          disabled={disabled}
          invalid={Boolean(errors.accuracy_confirmed)}
          label="My answers are accurate and describe my own experience."
          description="The interviewer asks about some of them, and the hiring team reads them alongside the interview."
        />
        <div aria-live="polite" className="empty:hidden">
          {errors.accuracy_confirmed ? (
            <p data-testid="intake-field-error-accuracy_confirmed" className="pl-6.5 text-[13px] leading-5 font-medium text-danger-fg">
              {errors.accuracy_confirmed}
            </p>
          ) : null}
        </div>
      </section>

      {formError ? (
        <Callout tone="danger" title="Your details weren't saved" data-testid="intake-details-error">
          {formError}
        </Callout>
      ) : null}

      <StepFooter
        onBack={onBack}
        note={
          attempted && errorCount > 0 ? (
            <p role="status" className="font-medium text-danger-fg">
              {errorCount === 1 ? "One answer needs attention." : `${errorCount} answers need attention.`} They're marked above.
            </p>
          ) : undefined
        }
      >
        {locked ? (
          lockedAction
        ) : (
          <Button type="submit" size="lg" loading={submitting} loadingText="Saving" data-testid="intake-details-submit">
            Save and continue
          </Button>
        )}
      </StepFooter>
    </form>
  );
}

function serverErrorsFor(serverErrors: Partial<Record<OnboardingField, string>>, cleared: ReadonlySet<string>): Partial<Record<OnboardingField, string>> {
  const out: Partial<Record<OnboardingField, string>> = {};
  for (const [field, message] of Object.entries(serverErrors) as Array<[OnboardingField, string]>) {
    if (!cleared.has(field)) out[field] = message;
  }
  return out;
}

/** Move focus (and the view) to the first answer that needs attention. */
function focusField(form: HTMLFormElement | null, field: OnboardingField): void {
  const container = form?.querySelector<HTMLElement>(`[data-field="${field}"]`);
  const target = container?.querySelector<HTMLElement>(
    'input:not([type="hidden"]), textarea, button[role="radio"][data-state="checked"], button[role="radio"], button[role="checkbox"]',
  );
  target?.focus();
  container?.scrollIntoView?.({ block: "center", behavior: "smooth" });
}
