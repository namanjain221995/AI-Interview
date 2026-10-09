import { Info } from "lucide-react";

import { Input } from "@/components/ui/Input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Slider } from "@/components/ui/Slider";
import { Switch } from "@/components/ui/Switch";

import { FieldShell, NumberInput, SectionBlock } from "@/features/recruiter/admin/components/Field";

import { ELIGIBILITY_NOTE } from "../../labels";
import { COUNTRY_MAX, DURATION_MAX, DURATION_MIN, EXPIRY_CHOICES, RECONNECTS_MAX, type FormErrors, type InviteSettingsForm, type Mq1Mode } from "../../lib/createForm";
import type { WorkAuthorizationPolicy } from "../../types";
import type { ReviewerOption } from "../reviewers";

const NO_REVIEWER = "__none";

export interface InviteSettingsFieldsProps {
  value: InviteSettingsForm;
  onChange: (value: InviteSettingsForm) => void;
  errors: FormErrors;
  orgWorkAuthorization: WorkAuthorizationPolicy | null;
  /** Organisation setting that forces an access code. */
  orgRequiresCode: boolean;
  reviewers: ReviewerOption[];
  disabled?: boolean;
}

function orgPolicyText(policy: WorkAuthorizationPolicy | null): string {
  if (!policy) return "the organisation default";
  if (!policy.enabled) return "currently not asked";
  return `currently asked for ${policy.country ?? "the configured country"}${policy.ask_sponsorship ? ", with the sponsorship question" : ""}`;
}

/** Interview, link, eligibility and review settings shared by single and batch creation. */
export function InviteSettingsFields({ value, onChange, errors, orgWorkAuthorization, orgRequiresCode, reviewers, disabled }: InviteSettingsFieldsProps) {
  const set = <K extends keyof InviteSettingsForm>(key: K, next: InviteSettingsForm[K]) => onChange({ ...value, [key]: next });
  const expiryOptions = EXPIRY_CHOICES.some((c) => c.value === value.expires_in_hours)
    ? EXPIRY_CHOICES
    : [...EXPIRY_CHOICES, { value: value.expires_in_hours, label: `${value.expires_in_hours} hours` }].sort((a, b) => a.value - b.value);

  return (
    <fieldset disabled={disabled} className="grid min-w-0 grid-cols-1 gap-5">
      <SectionBlock title="Interview" description="Seven parts in a fixed order, timed by the platform. The duration sets each part's time budget.">
        <div className="grid grid-cols-1 gap-5">
          <FieldShell
            label="Duration"
            error={errors.duration_minutes}
            aside={
              <span className="text-sm font-semibold text-fg tabular-nums" aria-hidden="true">
                {value.duration_minutes} min
              </span>
            }
            description={`Between ${DURATION_MIN} and ${DURATION_MAX} minutes; 45 is the kit's target.`}
          >
            {(control) => (
              <Slider
                data-testid="intake-duration"
                aria-describedby={control["aria-describedby"]}
                value={[value.duration_minutes]}
                min={DURATION_MIN}
                max={DURATION_MAX}
                step={1}
                thumbLabels={["Interview duration in minutes"]}
                formatValue={(v) => `${v} minutes`}
                onValueChange={([v]) => set("duration_minutes", v ?? 45)}
                className="pt-1"
              />
            )}
          </FieldShell>
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div className="grid grid-cols-1 content-start gap-1.5">
              <span className="text-[13px] font-medium text-fg">Interviewer presence</span>
              <Switch
                data-testid="intake-presence-toggle"
                label="Show the voice orb"
                description="The animated orb that moves with the interviewer's voice. Off: a plain audio indicator."
                checked={value.presence === "orb"}
                onCheckedChange={(on) => set("presence", on ? "orb" : "none")}
              />
            </div>
            <FieldShell label="Reconnections allowed" error={errors.max_reconnects} description="How often the candidate may rejoin after a dropped connection. The clock pauses while away.">
              {(control) => (
                <NumberInput
                  {...control}
                  data-testid="intake-max-reconnects"
                  value={value.max_reconnects}
                  min={0}
                  max={RECONNECTS_MAX}
                  onChange={(v) => set("max_reconnects", v ?? 0)}
                />
              )}
            </FieldShell>
          </div>
        </div>
      </SectionBlock>

      <SectionBlock title="Link" description="The invite link is personal and shown once after you create it.">
        <div className="grid grid-cols-1 gap-5">
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <FieldShell label="Link expires after" error={errors.expires_in_hours}>
              {(control) => (
                <Select<string>
                  id={control.id}
                  data-testid="intake-expiry"
                  value={String(value.expires_in_hours)}
                  onValueChange={(v) => set("expires_in_hours", Number(v))}
                  options={expiryOptions.map((c) => ({ value: String(c.value), label: c.label }))}
                />
              )}
            </FieldShell>
            <FieldShell label="Earliest start" optional error={errors.scheduled_for} description="Leave empty to let the candidate start as soon as they open the link.">
              {(control) => (
                <Input {...control} type="datetime-local" data-testid="intake-schedule" value={value.scheduled_for} onChange={(e) => set("scheduled_for", e.target.value)} />
              )}
            </FieldShell>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Switch
              data-testid="intake-access-code-toggle"
              label="Require an access code"
              description={orgRequiresCode ? "Required by your organisation's settings." : "A short code you send separately from the link."}
              checked={value.require_access_code || orgRequiresCode}
              disabled={orgRequiresCode}
              onCheckedChange={(on) => set("require_access_code", on)}
            />
            <Switch
              data-testid="intake-resume-required"
              label="Résumé required"
              description="The interviewer asks about the résumé's claims. Without one, those questions use a general prompt."
              checked={value.resume_required}
              onCheckedChange={(on) => set("resume_required", on)}
            />
          </div>
        </div>
      </SectionBlock>

      <SectionBlock title="Work authorisation" description="Two yes-or-no questions on the candidate's form, only when your policy needs them.">
        <div className="grid grid-cols-1 gap-4">
          <RadioGroup data-testid="intake-mq1-mode" value={value.mq1_mode} onValueChange={(v) => set("mq1_mode", v as Mq1Mode)} aria-label="Work authorisation questions" className="gap-2.5">
            <RadioGroupItem value="inherit" label="Use the organisation default" description={`Follows the intake settings (${orgPolicyText(orgWorkAuthorization)}).`} />
            <RadioGroupItem value="ask" label="Ask for this interview" description="“Are you legally authorised to work in <country> for this role?” and, optionally, whether sponsorship will be needed." />
            <RadioGroupItem value="off" label="Don't ask" />
          </RadioGroup>
          {value.mq1_mode === "ask" ? (
            <div className="grid grid-cols-1 gap-4 border-l-2 border-line pl-4 sm:grid-cols-2">
              <FieldShell label="Country" required error={errors.mq1_country} description="Written as it reads in the question.">
                {(control) => (
                  <Input {...control} data-testid="intake-mq1-country" maxLength={COUNTRY_MAX} placeholder="e.g. the United States" value={value.mq1_country} onChange={(e) => set("mq1_country", e.target.value)} />
                )}
              </FieldShell>
              <div className="grid grid-cols-1 content-start gap-1.5 sm:pt-6">
                <Switch label="Also ask about sponsorship" checked={value.mq1_sponsorship} onCheckedChange={(on) => set("mq1_sponsorship", on)} />
              </div>
            </div>
          ) : null}
          <p className="flex items-start gap-1.5 text-xs text-fg-muted">
            <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
            {ELIGIBILITY_NOTE}. Citizenship, nationality and visa type are never asked.
          </p>
        </div>
      </SectionBlock>

      <SectionBlock title="Review" description="Who looks at the result. Notifications are shown in the app; the platform sends no email.">
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <FieldShell label="Assigned reviewer" optional error={errors.assigned_reviewer_id}>
            {(control) => (
              <Select<string>
                id={control.id}
                data-testid="intake-reviewer-select"
                value={value.assigned_reviewer_id ?? NO_REVIEWER}
                onValueChange={(v) => set("assigned_reviewer_id", v === NO_REVIEWER ? null : v)}
                options={[{ value: NO_REVIEWER, label: "No one yet" }, ...reviewers]}
              />
            )}
          </FieldShell>
          <div className="grid grid-cols-1 content-start gap-1.5 sm:pt-6">
            <Switch
              label="Notify when it's ready for review"
              description="In-app only."
              checked={value.notify_on_completion}
              onCheckedChange={(on) => set("notify_on_completion", on)}
            />
          </div>
        </div>
      </SectionBlock>
    </fieldset>
  );
}
