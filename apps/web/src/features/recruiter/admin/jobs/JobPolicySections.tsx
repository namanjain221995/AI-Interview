import { RotateCcw, SlidersHorizontal } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useEnforcedPolicyFields } from "@/lib/auth";
import { INTEGRITY_EVENT_LABELS } from "@/lib/labels";
import type { IntegrityPolicy, IntegrityRule, OrganizationSettings, RecordingPolicy, RetentionPolicy } from "@/lib/types";

import type { FieldErrors } from "../lib/serverErrors";
import { describeRule, IntegrityRulesEditor } from "../policies/IntegrityRulesEditor";
import { PolicyFieldsEditor } from "../policies/PolicyFieldsEditor";
import { INTEGRITY_FIELDS, INTEGRITY_GROUPS, RECORDING_FIELDS, RETENTION_FIELDS } from "../policies/policyFields";
import { describeDays } from "../settings/settingsModel";
import { differingIntegrityKeys } from "./jobDraft";

function withoutKey<T extends object>(source: Partial<T>, key: keyof T): Partial<T> {
  const next = { ...source };
  delete next[key];
  return next;
}

export interface IntegrityOverridesProps {
  defaults: IntegrityPolicy;
  overrides: Partial<IntegrityPolicy>;
  onChange: (overrides: Partial<IntegrityPolicy>) => void;
  errors?: FieldErrors;
}

/** Job-level integrity: organization defaults with per-field overrides. */
export function IntegrityOverrides({ defaults, overrides, onChange, errors }: IntegrityOverridesProps) {
  const locked = useEnforcedPolicyFields("integrity");
  const effective = { ...defaults, ...overrides } as IntegrityPolicy;
  // Pinned values equal to the default behave like the default, so only differences count.
  const differing = new Set(differingIntegrityKeys(overrides, defaults));
  const customCount = differing.size;
  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-surface-subtle px-4 py-3 text-sm">
        <p className="text-fg-muted">
          {customCount === 0 ? (
            "Using your organization's defaults. Change any setting to customise it for this job."
          ) : (
            <>
              <span className="font-medium text-fg">{customCount}</span> {customCount === 1 ? "setting differs" : "settings differ"} from your organization's defaults.
            </>
          )}
        </p>
        {customCount > 0 ? (
          <Button size="xs" variant="ghost" leftIcon={<RotateCcw />} onClick={() => onChange({})}>
            Reset all
          </Button>
        ) : null}
      </div>
      <PolicyFieldsEditor
        fields={INTEGRITY_FIELDS}
        locked={locked}
        groups={INTEGRITY_GROUPS}
        values={effective as unknown as Record<string, unknown>}
        defaults={defaults as unknown as Record<string, unknown>}
        isOverridden={(key) => differing.has(key)}
        onReset={(key) => onChange(withoutKey(overrides, key as keyof IntegrityPolicy))}
        onChange={(key, value) => onChange({ ...overrides, [key]: value })}
        errors={errors}
        errorPrefix="integrity_settings"
      />
      <SignalRuleOverrides defaults={defaults} overrides={overrides} onChange={onChange} errors={errors} />
    </div>
  );
}

/**
 * Job rules replace the organization's rule for the same signal; organization
 * rules for other signals keep applying (app/services/jobs/policy.py).
 */
function SignalRuleOverrides({ defaults, overrides, onChange, errors }: IntegrityOverridesProps) {
  const jobRules = overrides.rules ?? [];
  const replaced = new Set(jobRules.map((r) => r.event_type));
  const inherited = defaults.rules.filter((r) => !replaced.has(r.event_type));
  const setRules = (rules: IntegrityRule[]) => onChange(rules.length ? { ...overrides, rules } : withoutKey(overrides, "rules"));
  return (
    <div className="grid gap-3">
      <div className="grid gap-0.5">
        <h3 className="text-sm font-semibold text-fg">Signal rules</h3>
        <p className="text-xs text-fg-muted">A rule here replaces your organization's rule for the same signal. Organization rules for other signals keep applying.</p>
      </div>
      {inherited.length ? (
        <ul className="divide-y divide-line-subtle rounded-md border border-line bg-surface-subtle/50">
          {inherited.map((rule) => (
            <li key={rule.event_type} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
              <div className="grid min-w-0 gap-0.5">
                <span className="text-sm font-medium text-fg">
                  {INTEGRITY_EVENT_LABELS[rule.event_type]} <span className="text-xs font-normal text-fg-subtle">from your organization</span>
                </span>
                <span className="text-xs text-fg-muted">{describeRule(rule)}</span>
              </div>
              <Button size="xs" variant="outline" leftIcon={<SlidersHorizontal />} onClick={() => setRules([...jobRules, { ...rule }])}>
                Change for this job
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      <IntegrityRulesEditor
        value={jobRules}
        onChange={setRules}
        errors={errors}
        errorPrefix="integrity_settings.rules"
        emptyText="No job-specific rules. Add one to handle a signal differently for this job only."
        addLabel="Add a job rule for a signal…"
      />
    </div>
  );
}

export interface RecordingOverridesProps {
  settings: OrganizationSettings;
  recording: Partial<RecordingPolicy>;
  retention: Partial<RetentionPolicy>;
  onRecordingChange: (value: Partial<RecordingPolicy>) => void;
  onRetentionChange: (value: Partial<RetentionPolicy>) => void;
  errors?: FieldErrors;
}

export function RecordingOverrides({ settings, recording, retention, onRecordingChange, onRetentionChange, errors }: RecordingOverridesProps) {
  const locked = useEnforcedPolicyFields("recording");
  const effectiveRetention = { ...settings.retention, ...retention } as RetentionPolicy;
  return (
    <div className="grid gap-6">
      <PolicyFieldsEditor
        fields={RECORDING_FIELDS}
        locked={locked}
        values={{ ...settings.recording, ...recording } as unknown as Record<string, unknown>}
        defaults={settings.recording as unknown as Record<string, unknown>}
        isOverridden={(key) => key in recording && recording[key as keyof RecordingPolicy] !== settings.recording[key as keyof RecordingPolicy]}
        onReset={(key) => onRecordingChange(withoutKey(recording, key as keyof RecordingPolicy))}
        onChange={(key, value) => onRecordingChange({ ...recording, [key]: value })}
        errors={errors}
        errorPrefix="recording_settings"
      />
      <div className="grid gap-2">
        <div className="grid gap-0.5">
          <h3 className="text-sm font-semibold text-fg">How long this job's data is kept</h3>
          <p className="text-xs text-fg-muted">Recordings, snapshots and uploaded files are deleted automatically after this many days. Transcripts, reports and text read from résumés are not deleted automatically yet; candidates are told so before they consent.</p>
        </div>
        <PolicyFieldsEditor
          fields={RETENTION_FIELDS.map((f) => ({ ...f, help: `${f.help} ${describeDays(effectiveRetention[f.key])}.` }))}
          values={effectiveRetention as unknown as Record<string, unknown>}
          defaults={settings.retention as unknown as Record<string, unknown>}
          isOverridden={(key) => key in retention && retention[key as keyof RetentionPolicy] !== settings.retention[key as keyof RetentionPolicy]}
          onReset={(key) => onRetentionChange(withoutKey(retention, key as keyof RetentionPolicy))}
          onChange={(key, value) => onRetentionChange({ ...retention, [key]: value })}
          errors={errors}
          errorPrefix="recording_settings.retention"
        />
      </div>
    </div>
  );
}
