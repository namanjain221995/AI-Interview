import { ArrowDown, ArrowUp, Lock, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { IconButton } from "@/components/ui/IconButton";
import { Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/utils";

import { FieldShell, ListEditor, NumberInput } from "@/features/recruiter/admin/components/Field";

import { FIELD_TYPE_HINTS, FIELD_TYPE_LABELS, HELP_MAX, LABEL_MAX, LOCKED_BUILTINS, MAX_OPTIONS, PLACEHOLDER_MAX } from "../formSchema";
import type { FormField, FormIssue, FormWarning } from "../types";

export interface FieldEditorProps {
  field: FormField;
  index: number;
  count: number;
  onChange: (next: FormField) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  /** Called when a label edit ends (fresh fields re-derive their key then). */
  onLabelCommit?: () => void;
  structuralError?: string;
  fairnessError?: FormIssue;
  warnings: FormWarning[];
  onAcknowledge: (code: string, acknowledged: boolean) => void;
  disabled?: boolean;
}

function MoveButtons({ index, count, label, onMove, disabled }: { index: number; count: number; label: string; onMove: (delta: -1 | 1) => void; disabled?: boolean }) {
  return (
    <span className="flex shrink-0 items-center">
      <IconButton size="sm" label={`Move ${label} up`} icon={<ArrowUp />} disabled={disabled || index === 0} onClick={() => onMove(-1)} tooltip={false} />
      <IconButton size="sm" label={`Move ${label} down`} icon={<ArrowDown />} disabled={disabled || index === count - 1} onClick={() => onMove(1)} tooltip={false} />
    </span>
  );
}

function Issues({ field, fairnessError, structuralError, warnings, onAcknowledge, disabled }: Pick<FieldEditorProps, "field" | "fairnessError" | "structuralError" | "warnings" | "onAcknowledge" | "disabled">) {
  const items: ReactNode[] = [];
  if (fairnessError) {
    items.push(
      <Callout key="fair" tone="danger" title="This field can't be saved" data-testid={`form-field-error-${field.key}`}>
        {fairnessError.message}
      </Callout>,
    );
  } else if (structuralError) {
    items.push(
      <p key="struct" className="text-xs font-medium text-danger-fg" data-testid={`form-field-error-${field.key}`}>
        {structuralError}
      </p>,
    );
  }
  warnings.forEach((w) =>
    items.push(
      <Callout key={w.code} tone="warning" title="Check this question">
        <span className="block">{w.message}</span>
        <Checkbox
          className="mt-0.5"
          data-testid={`form-field-warning-ack-${field.key}`}
          data-warning-code={w.code}
          checked={w.acknowledged}
          disabled={disabled}
          onCheckedChange={(checked) => onAcknowledge(w.code, checked === true)}
          label="I understand, keep this question"
        />
      </Callout>,
    ),
  );
  return items.length ? <div className="grid grid-cols-1 gap-2">{items}</div> : null;
}

/** One built-in field: ask it or not, required or optional. */
export function BuiltinFieldRow({ field, index, count, onChange, onMove, structuralError, disabled }: FieldEditorProps) {
  const locked = LOCKED_BUILTINS.has(field.key);
  return (
    <li data-testid={`form-field-row-${field.key}`} data-builtin="true" className={cn("grid grid-cols-1 gap-2 px-4 py-3", !field.enabled && "bg-surface-subtle/60")}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="grid min-w-0 flex-1 basis-40">
          <span className={cn("truncate text-sm font-medium", field.enabled ? "text-fg" : "text-fg-muted")}>{field.label}</span>
          <span className="text-xs text-fg-subtle">{FIELD_TYPE_LABELS[field.type]}</span>
        </div>
        {locked ? (
          <Badge tone="outline" size="sm" icon={<Lock />}>
            Always asked
          </Badge>
        ) : (
          <>
            <Switch
              size="sm"
              data-testid={`form-field-enabled-${field.key}`}
              checked={field.enabled}
              disabled={disabled}
              onCheckedChange={(enabled) => onChange({ ...field, enabled, required: enabled ? field.required : false })}
              label="Ask"
              aria-label={`Ask for ${field.label}`}
            />
            <Switch
              size="sm"
              data-testid={`form-field-required-${field.key}`}
              checked={field.required}
              disabled={disabled || !field.enabled}
              onCheckedChange={(required) => onChange({ ...field, required })}
              label="Required"
              aria-label={`${field.label} is required`}
            />
          </>
        )}
        <MoveButtons index={index} count={count} label={field.label} onMove={onMove} disabled={disabled} />
      </div>
      {structuralError ? (
        <p className="text-xs font-medium text-danger-fg" data-testid={`form-field-error-${field.key}`}>
          {structuralError}
        </p>
      ) : null}
    </li>
  );
}

/** One custom question: label, type, help, required, options or limits, order, delete. */
export function FieldEditor(props: FieldEditorProps) {
  const { field, index, count, onChange, onMove, onRemove, onLabelCommit, fairnessError, disabled } = props;
  const set = (patch: Partial<FormField>) => onChange({ ...field, ...patch });
  const hasOptions = field.type === "select" || field.type === "multiselect";
  const hasPlaceholder = ["text", "textarea", "number", "email", "phone", "url", "select"].includes(field.type);
  const blocked = Boolean(fairnessError);
  return (
    <li
      data-testid={`form-field-row-${field.key}`}
      data-builtin="false"
      className={cn("grid grid-cols-1 gap-4 px-4 py-4", blocked && "bg-danger-soft/40 shadow-[inset_2px_0_0_var(--danger)]", !field.enabled && !blocked && "bg-surface-subtle/60")}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="neutral" size="sm" title={FIELD_TYPE_HINTS[field.type]}>
          {FIELD_TYPE_LABELS[field.type]}
        </Badge>
        <code className="min-w-0 truncate font-mono text-[11px] text-fg-subtle" title="Column name in the export">
          {field.key}
        </code>
        <span className="ml-auto flex items-center gap-1">
          <MoveButtons index={index} count={count} label={field.label || "field"} onMove={onMove} disabled={disabled} />
          <IconButton size="sm" label={`Delete ${field.label || "field"}`} icon={<Trash2 />} onClick={onRemove} disabled={disabled} tooltip={false} data-testid={`form-field-delete-${field.key}`} />
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <FieldShell label={field.type === "checkbox" ? "Statement to tick" : "Question"} required className="md:col-span-2">
          {(control) => (
            <Input
              {...control}
              data-testid={`form-field-label-${field.key}`}
              value={field.label}
              maxLength={LABEL_MAX}
              disabled={disabled}
              onChange={(e) => set({ label: e.target.value })}
              onBlur={onLabelCommit}
            />
          )}
        </FieldShell>
        <FieldShell label="Help text" optional description="Shown under the field." className={hasPlaceholder ? undefined : "md:col-span-2"}>
          {(control) => (
            <Input {...control} value={field.help_text ?? ""} maxLength={HELP_MAX} disabled={disabled} onChange={(e) => set({ help_text: e.target.value || null })} />
          )}
        </FieldShell>
        {hasPlaceholder ? (
          <FieldShell label={field.type === "select" ? "Empty choice text" : "Placeholder"} optional>
            {(control) => (
              <Input {...control} value={field.placeholder ?? ""} maxLength={PLACEHOLDER_MAX} disabled={disabled} onChange={(e) => set({ placeholder: e.target.value || null })} />
            )}
          </FieldShell>
        ) : null}
        {hasOptions ? (
          <FieldShell label="Options" required description={`Up to ${MAX_OPTIONS}, each different.`} className="md:col-span-2">
            {(control) => (
              <ListEditor
                id={control.id}
                aria-describedby={control["aria-describedby"]}
                value={field.options}
                onChange={(options) => set({ options })}
                placeholder="Option"
                addLabel="Add option"
                maxItems={MAX_OPTIONS}
                disabled={disabled}
              />
            )}
          </FieldShell>
        ) : null}
        {field.type === "number" ? (
          <>
            <FieldShell label="Lowest allowed" optional>
              {(control) => <NumberInput {...control} value={field.min} nullable onChange={(min) => set({ min })} disabled={disabled} />}
            </FieldShell>
            <FieldShell label="Highest allowed" optional>
              {(control) => <NumberInput {...control} value={field.max} nullable onChange={(max) => set({ max })} disabled={disabled} />}
            </FieldShell>
          </>
        ) : null}
        {field.type === "multiselect" ? (
          <FieldShell label="Most choices allowed" optional description="Leave empty for no limit.">
            {(control) => <NumberInput {...control} value={field.max} nullable min={1} max={MAX_OPTIONS} onChange={(max) => set({ max })} disabled={disabled} />}
          </FieldShell>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <Switch
          size="sm"
          data-testid={`form-field-enabled-${field.key}`}
          checked={field.enabled}
          disabled={disabled}
          onCheckedChange={(enabled) => set({ enabled })}
          label="Ask"
          aria-label={`Ask ${field.label || "this question"}`}
        />
        <Switch
          size="sm"
          data-testid={`form-field-required-${field.key}`}
          checked={field.required}
          disabled={disabled || !field.enabled}
          onCheckedChange={(required) => set({ required })}
          label={field.type === "checkbox" ? "Must be ticked" : "Required"}
          aria-label={`${field.label || "This question"} is required`}
        />
      </div>

      <Issues {...props} />
    </li>
  );
}
