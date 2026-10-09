import { Info, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Slider } from "@/components/ui/Slider";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import { toast } from "@/components/ui/Toaster";
import { isApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";

import { FieldShell, NumberInput, SectionBlock } from "@/features/recruiter/admin/components/Field";
import { useUnsavedChanges } from "@/features/recruiter/admin/components/useUnsavedChanges";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import { jsonEqual } from "@/features/recruiter/admin/lib/utils";

import { isIntakeDisabled, useIntakeSettings, useSaveIntakeSettings } from "../api";
import { IntakeDisabledState, IntakeLoadError } from "../components/IntakeStates";
import { ELIGIBILITY_NOTE } from "../labels";
import { COUNTRY_MAX, DURATION_MAX, DURATION_MIN, EXPIRY_CHOICES, RECONNECTS_MAX } from "../lib/createForm";
import { MAX_INFO, payloadOf, validateOrgSettings } from "../lib/settings";
import type { IntakeOrgSettingsIn } from "../types";

/** /app/intake/settings: organisation defaults for intake interviews (admins). */
export default function IntakeSettingsPage() {
  useDocumentTitle("Intake settings");
  const settings = useIntakeSettings();
  const save = useSaveIntakeSettings();
  const [value, setValue] = useState<IntakeOrgSettingsIn | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);

  useEffect(() => {
    if (settings.data && !value) {
      const { updated_at: _u, updated_by: _b, ...rest } = settings.data;
      setValue(rest);
    }
  }, [settings.data, value]);

  const baseline = useMemo(() => {
    if (!settings.data) return null;
    const { updated_at: _u, updated_by: _b, ...rest } = settings.data;
    return rest;
  }, [settings.data]);
  const dirty = Boolean(value && baseline && !jsonEqual(payloadOf(value), payloadOf(baseline)));
  useUnsavedChanges(dirty);

  useEffect(() => {
    if (attempted && value) setErrors(validateOrgSettings(value));
  }, [value, attempted]);

  const header = <PageHeader breadcrumbs={[{ label: "Intake interviews", to: "/app/intake" }, { label: "Settings" }]} title="Intake settings" description="Defaults for every new intake interview in this organisation. Each invitation can still change its own settings." />;

  if (isIntakeDisabled(settings.error)) {
    return (
      <PageContainer width="narrow">
        {header}
        <div data-testid="intake-settings-page">
          <IntakeDisabledState />
        </div>
      </PageContainer>
    );
  }
  if (settings.isError && !settings.data) {
    return (
      <PageContainer width="narrow">
        {header}
        <div data-testid="intake-settings-page">
          <IntakeLoadError error={settings.error} title="Couldn't load the intake settings" onRetry={() => void settings.refetch()} />
        </div>
      </PageContainer>
    );
  }
  if (!value) {
    return (
      <PageContainer width="narrow">
        {header}
        <div className="grid grid-cols-1 gap-5" data-testid="intake-settings-page" aria-busy="true">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
      </PageContainer>
    );
  }

  const set = <K extends keyof IntakeOrgSettingsIn>(key: K, next: IntakeOrgSettingsIn[K]) => setValue({ ...value, [key]: next });
  const setDefault = <K extends keyof IntakeOrgSettingsIn["defaults"]>(key: K, next: IntakeOrgSettingsIn["defaults"][K]) => set("defaults", { ...value.defaults, [key]: next });

  const submit = async () => {
    setAttempted(true);
    setFormError(null);
    const found = validateOrgSettings(value);
    setErrors(found);
    if (Object.keys(found).length) {
      setFormError("Check the highlighted fields.");
      return;
    }
    try {
      const saved = await save.mutateAsync(payloadOf(value));
      const { updated_at: _u, updated_by: _b, ...rest } = saved;
      setValue(rest);
      setAttempted(false);
      toast.success("Intake settings saved");
    } catch (error) {
      const code = isApiError(error) ? error.code : undefined;
      if (code === "work_authorization_country_required") setErrors((e) => ({ ...e, country: "Name the country for the work authorisation question." }));
      if (code === "protected_topic" && isApiError(error)) {
        const index = typeof error.details.index === "number" ? error.details.index : null;
        if (index !== null) setErrors((e) => ({ ...e, [`approved_info.${index}.answer`]: error.message }));
      }
      setFormError(describeFailure(error));
    }
  };

  return (
    <PageContainer width="narrow">
      {header}
      <div className="grid grid-cols-1 gap-5" data-testid="intake-settings-page">
        <SectionBlock title="Program" description="Spoken in the interviewer's opening and closing.">
          <FieldShell label="Program name" required error={errors.program_name}>
            {(control) => <Input {...control} maxLength={200} value={value.program_name} onChange={(e) => set("program_name", e.target.value)} />}
          </FieldShell>
        </SectionBlock>

        <SectionBlock title="Work authorisation" description="Default for new intake interviews. Two yes-or-no questions on the candidate's form, never asked aloud.">
          <div className="grid grid-cols-1 gap-4">
            <Switch
              label="Ask about work authorisation"
              description="“Are you legally authorised to work in <country> for this role?”"
              checked={value.work_authorization.enabled}
              onCheckedChange={(on) => set("work_authorization", { ...value.work_authorization, enabled: on })}
            />
            {value.work_authorization.enabled ? (
              <div className="grid grid-cols-1 gap-4 border-l-2 border-line pl-4 sm:grid-cols-2">
                <FieldShell label="Country" required error={errors.country} description="Written as it reads in the question.">
                  {(control) => (
                    <Input {...control} maxLength={COUNTRY_MAX} placeholder="e.g. the United States" value={value.work_authorization.country ?? ""} onChange={(e) => set("work_authorization", { ...value.work_authorization, country: e.target.value })} />
                  )}
                </FieldShell>
                <div className="grid grid-cols-1 content-start sm:pt-6">
                  <Switch
                    label="Also ask about sponsorship"
                    checked={value.work_authorization.ask_sponsorship}
                    onCheckedChange={(on) => set("work_authorization", { ...value.work_authorization, ask_sponsorship: on })}
                  />
                </div>
              </div>
            ) : null}
            <p className="flex items-start gap-1.5 text-xs text-fg-muted">
              <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
              {ELIGIBILITY_NOTE}. Citizenship, nationality and visa type are never asked.
            </p>
          </div>
        </SectionBlock>

        <SectionBlock
          title="Approved program information"
          description="The only facts the interviewer may state when a candidate asks about the program. Anything else gets “That's a good question for the hiring team”."
          actions={
            <Button
              variant="outline"
              size="sm"
              leftIcon={<Plus />}
              disabled={value.approved_info.length >= MAX_INFO}
              onClick={() => set("approved_info", [...value.approved_info, { topic: "", answer: "" }])}
            >
              Add an answer
            </Button>
          }
        >
          {value.approved_info.length === 0 ? (
            <p className="text-sm text-fg-muted">No approved answers yet. Add the questions candidates usually ask: schedule, format, location, what happens after the interview.</p>
          ) : (
            <ol className="grid grid-cols-1 gap-4">
              {value.approved_info.map((item, index) => (
                <li key={index} data-testid="intake-approved-info-row" className="grid grid-cols-1 gap-3 border-b border-line-subtle pb-4 last:border-b-0 last:pb-0 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto]">
                  <FieldShell label={`Topic ${index + 1}`} error={errors[`approved_info.${index}.topic`]}>
                    {(control) => (
                      <Input
                        {...control}
                        maxLength={120}
                        placeholder="e.g. Training schedule"
                        value={item.topic}
                        onChange={(e) => set("approved_info", value.approved_info.map((a, i) => (i === index ? { ...a, topic: e.target.value } : a)))}
                      />
                    )}
                  </FieldShell>
                  <FieldShell label="Answer" error={errors[`approved_info.${index}.answer`]}>
                    {(control) => (
                      <Textarea
                        {...control}
                        rows={2}
                        autoResize
                        maxRows={8}
                        maxLength={1200}
                        value={item.answer}
                        onChange={(e) => set("approved_info", value.approved_info.map((a, i) => (i === index ? { ...a, answer: e.target.value } : a)))}
                      />
                    )}
                  </FieldShell>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="size-8 px-0 sm:mt-6"
                    aria-label={`Remove topic ${index + 1}`}
                    onClick={() => set("approved_info", value.approved_info.filter((_, i) => i !== index))}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ol>
          )}
          {errors.approved_info ? <p className="mt-2 text-xs font-medium text-danger-fg">{errors.approved_info}</p> : null}
        </SectionBlock>

        <SectionBlock title="Defaults for new invitations">
          <div className="grid grid-cols-1 gap-5">
            <FieldShell label="Duration" error={errors.duration_minutes} aside={<span className="text-sm font-semibold text-fg tabular-nums">{value.defaults.duration_minutes} min</span>}>
              {() => (
                <Slider
                  value={[value.defaults.duration_minutes]}
                  min={DURATION_MIN}
                  max={DURATION_MAX}
                  step={1}
                  thumbLabels={["Default duration in minutes"]}
                  formatValue={(v) => `${v} minutes`}
                  onValueChange={([v]) => setDefault("duration_minutes", v ?? 45)}
                  className="pt-1"
                />
              )}
            </FieldShell>
            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
              <FieldShell label="Reconnections allowed" error={errors.max_reconnects}>
                {(control) => <NumberInput {...control} value={value.defaults.max_reconnects} min={0} max={RECONNECTS_MAX} onChange={(v) => setDefault("max_reconnects", v ?? 0)} />}
              </FieldShell>
              <FieldShell label="Link expires after">
                {(control) => (
                  <Select<string>
                    id={control.id}
                    value={String(value.defaults.expires_in_hours)}
                    onValueChange={(v) => setDefault("expires_in_hours", Number(v))}
                    options={(EXPIRY_CHOICES.some((c) => c.value === value.defaults.expires_in_hours) ? EXPIRY_CHOICES : [...EXPIRY_CHOICES, { value: value.defaults.expires_in_hours, label: `${value.defaults.expires_in_hours} hours` }]).map((c) => ({ value: String(c.value), label: c.label }))}
                  />
                )}
              </FieldShell>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Switch label="Show the voice orb" checked={value.defaults.presence === "orb"} onCheckedChange={(on) => setDefault("presence", on ? "orb" : "none")} />
              <Switch label="Résumé required" checked={value.defaults.resume_required} onCheckedChange={(on) => setDefault("resume_required", on)} />
            </div>
          </div>
        </SectionBlock>

        <div className="sticky bottom-0 z-10 -mx-4 border-t border-line-subtle bg-surface/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-lg sm:border sm:px-5">
          {formError ? (
            <Callout tone="danger" className="mb-3">
              {formError}
            </Callout>
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-fg-muted">
              {settings.data?.updated_at ? `Last saved ${formatDateTime(settings.data.updated_at)}${settings.data.updated_by ? ` by ${settings.data.updated_by.full_name}` : ""}.` : "Not saved yet: these are the platform defaults."}
            </p>
            <Button
              leftIcon={<Save />}
              loading={save.isPending}
              onClick={() => void submit()}
              disabled={!dirty && Boolean(settings.data?.updated_at)}
              data-testid="intake-settings-save"
            >
              Save settings
            </Button>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
