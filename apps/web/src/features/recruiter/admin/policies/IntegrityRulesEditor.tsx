import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Combobox } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { INTEGRITY_ACTION_LABELS, INTEGRITY_EVENT_LABELS } from "@/lib/labels";
import type { IntegrityAction, IntegrityEventType, IntegrityRule } from "@/lib/types";
import { cn } from "@/lib/utils";

import { FieldShell, NumberInput } from "../components/Field";
import type { FieldErrors } from "../lib/serverErrors";
import { ALL_EVENT_TYPES, defaultRule, OBSERVATION_ONLY_EVENTS, POLICY_ACTIONS } from "../settings/settingsModel";

const ACTIONS: IntegrityAction[] = ["log_only", "warn_candidate", "flag_for_human_review", "pause_interview", "terminate_interview"];

const ACTION_HELP: Record<IntegrityAction, string> = {
  log_only: "Record it in the session timeline only",
  warn_candidate: "Show the candidate a neutral reminder",
  flag_for_human_review: "Ask a reviewer to look at it",
  pause_interview: "Pause until the candidate resolves it",
  terminate_interview: "End the interview (objective conditions only)",
};

function formatWindow(seconds: number): string {
  if (seconds % 60 === 0) return seconds === 60 ? "1 minute" : `${seconds / 60} minutes`;
  return `${seconds} seconds`;
}

/** One-sentence reading of a rule, e.g. "Ask a reviewer … when it happens 3 times within 5 minutes." */
export function describeRule(rule: IntegrityRule): string {
  if (!rule.enabled) return "Ignored: this signal is not recorded.";
  const action = ACTION_HELP[rule.action];
  const parts: string[] = [];
  if (rule.min_occurrences > 1) parts.push(`it happens ${rule.min_occurrences} times within ${formatWindow(rule.window_seconds)}`);
  else parts.push("it happens");
  if (rule.min_duration_seconds > 0) parts.push(`lasts at least ${rule.min_duration_seconds} s`);
  if (OBSERVATION_ONLY_EVENTS.has(rule.event_type)) parts.push(`the camera model is at least ${Math.round(rule.min_confidence * 100)}% confident`);
  return `${action} when ${parts.join(", ")}.`;
}

export interface IntegrityRulesEditorProps {
  value: IntegrityRule[];
  onChange: (value: IntegrityRule[]) => void;
  errors?: FieldErrors;
  errorPrefix?: string;
  disabled?: boolean;
  /** Text when there are no rules. */
  emptyText?: string;
  /** Placeholder of the "add a rule" picker. */
  addLabel?: string;
}

/** Per-signal overrides of the built-in integrity rules. */
export function IntegrityRulesEditor({
  value,
  onChange,
  errors,
  errorPrefix = "integrity.rules",
  disabled,
  emptyText = "Built-in rules apply: most signals are logged, and camera observations are flagged for a reviewer. Add a rule to handle a signal differently.",
  addLabel = "Add a rule for a signal…",
}: IntegrityRulesEditorProps) {
  const used = new Set(value.map((r) => r.event_type));
  const update = (index: number, patch: Partial<IntegrityRule>) => onChange(value.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  const available = ALL_EVENT_TYPES.filter((t) => !used.has(t));

  return (
    <div className="grid gap-3">
      {value.length === 0 ? (
        <p className="rounded-md border border-dashed border-line-strong bg-surface-subtle/60 px-4 py-5 text-sm text-fg-muted">{emptyText}</p>
      ) : (
        <ul className="grid gap-2.5">
          {value.map((rule, index) => {
            const observation = OBSERVATION_ONLY_EVENTS.has(rule.event_type);
            const path = `${errorPrefix}.${index}`;
            return (
              <li key={`${rule.event_type}-${index}`} className={cn("rounded-md border border-line bg-surface p-4", !rule.enabled && "bg-surface-subtle")}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="grid min-w-0 gap-0.5">
                    <p className="text-sm font-medium text-fg">{INTEGRITY_EVENT_LABELS[rule.event_type]}</p>
                    <p className="text-xs text-fg-muted">{describeRule(rule)}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <Switch size="sm" label="On" checked={rule.enabled} disabled={disabled} onCheckedChange={(enabled) => update(index, { enabled })} />
                    <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Remove rule for ${INTEGRITY_EVENT_LABELS[rule.event_type]}`} disabled={disabled} onClick={() => onChange(value.filter((_, i) => i !== index))}>
                      <Trash2 />
                    </Button>
                  </div>
                </div>
                {rule.enabled ? (
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-[1.4fr_repeat(4,minmax(0,1fr))]">
                    <FieldShell label="Then" error={errors?.[`${path}.action`]}>
                      {(control) => (
                        <Select<IntegrityAction>
                          id={control.id}
                          value={rule.action}
                          disabled={disabled}
                          onValueChange={(action) => update(index, { action })}
                          options={ACTIONS.map((a) => ({
                            value: a,
                            label: INTEGRITY_ACTION_LABELS[a],
                            description: observation && POLICY_ACTIONS.has(a) ? "Not available for camera or screen observations" : ACTION_HELP[a],
                            disabled: observation && POLICY_ACTIONS.has(a),
                          }))}
                        />
                      )}
                    </FieldShell>
                    <FieldShell label="Times" error={errors?.[`${path}.min_occurrences`]}>
                      {(control) => <NumberInput {...control} value={rule.min_occurrences} min={1} disabled={disabled} onChange={(v) => update(index, { min_occurrences: v ?? 1 })} />}
                    </FieldShell>
                    <FieldShell label="Within" error={errors?.[`${path}.window_seconds`]}>
                      {(control) => <NumberInput {...control} value={rule.window_seconds} min={1} unit="sec" disabled={disabled} onChange={(v) => update(index, { window_seconds: v ?? 300 })} />}
                    </FieldShell>
                    <FieldShell label="Lasting" error={errors?.[`${path}.min_duration_seconds`]}>
                      {(control) => <NumberInput {...control} value={rule.min_duration_seconds} min={0} unit="sec" disabled={disabled} onChange={(v) => update(index, { min_duration_seconds: v ?? 0 })} />}
                    </FieldShell>
                    <FieldShell label="Confidence" error={errors?.[`${path}.min_confidence`]}>
                      {(control) => (
                        <NumberInput
                          {...control}
                          value={Math.round(rule.min_confidence * 100)}
                          min={0}
                          max={100}
                          unit="%"
                          disabled={disabled || !observation}
                          onChange={(v) => update(index, { min_confidence: Math.max(0, Math.min(100, v ?? 60)) / 100 })}
                        />
                      )}
                    </FieldShell>
                    {rule.action === "warn_candidate" || rule.candidate_message ? (
                      <FieldShell label="Message to the candidate" optional className="sm:col-span-2 lg:col-span-5">
                        {(control) => (
                          <Input
                            {...control}
                            value={rule.candidate_message ?? ""}
                            disabled={disabled}
                            maxLength={240}
                            placeholder="e.g. Please keep the interview tab visible while you answer."
                            onChange={(e) => update(index, { candidate_message: e.target.value })}
                          />
                        )}
                      </FieldShell>
                    ) : null}
                    {rule.action === "terminate_interview" ? (
                      <p className="text-xs font-medium text-warning-fg sm:col-span-2 lg:col-span-5">
                        Ending an interview automatically is only appropriate for objective conditions that candidates are told about in advance.
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {available.length > 0 ? (
        <div className="max-w-sm">
          <Combobox
            aria-label="Add a rule for a signal"
            options={available.map((t) => ({ value: t, label: INTEGRITY_EVENT_LABELS[t], description: OBSERVATION_ONLY_EVENTS.has(t) ? "Camera or screen observation" : undefined }))}
            value={null}
            disabled={disabled}
            placeholder={addLabel}
            onChange={(eventType) => {
              if (eventType) onChange([...value, defaultRule(eventType as IntegrityEventType)]);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
