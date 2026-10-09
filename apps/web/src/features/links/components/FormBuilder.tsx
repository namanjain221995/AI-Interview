import { Plus } from "lucide-react";
import { useMemo, useRef } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";

import { warningKey } from "../fairness";
import { checkSchema, FIELD_TYPE_HINTS, FIELD_TYPE_LABELS, MAX_FIELDS, deriveKey, moveField, newCustomField } from "../formSchema";
import { FIELD_TYPES, type FieldType, type FormField, type FormIssue, type FormSchema, type FormWarning } from "../types";
import { BuiltinFieldRow, FieldEditor } from "./FieldEditor";

export interface FormIssues {
  errors: FormIssue[];
  warnings: FormWarning[];
}

/** Merges the local fairness mirror with the server's answer (the server's message wins per field). */
export function mergeIssues(local: FormIssues, server: FormIssues | null | undefined, acknowledged: readonly string[]): FormIssues {
  const acked = new Set(acknowledged);
  const errors = new Map<string, FormIssue>();
  for (const e of local.errors) errors.set(e.key, e);
  for (const e of server?.errors ?? []) errors.set(e.key, e);
  const warnings = new Map<string, FormWarning>();
  for (const w of [...local.warnings, ...(server?.warnings ?? [])]) {
    const id = warningKey(w.key, w.code);
    warnings.set(id, { ...warnings.get(id), ...w, acknowledged: acked.has(id) });
  }
  return { errors: [...errors.values()], warnings: [...warnings.values()] };
}

export interface FormBuilderProps {
  schema: FormSchema;
  onChange: (schema: FormSchema) => void;
  acknowledged: string[];
  onAcknowledgedChange: (acknowledged: string[]) => void;
  issues: FormIssues;
  /** Server `validation_failed` messages keyed by field key. */
  serverFieldErrors?: Record<string, string>;
  maxFields?: number;
  disabled?: boolean;
}

/**
 * Registration form builder: built-in fields as toggle rows, custom questions as
 * editable rows, keyboard-accessible ordering (no drag library), add-field menu.
 */
export function FormBuilder({ schema, onChange, acknowledged, onAcknowledgedChange, issues, serverFieldErrors, maxFields = MAX_FIELDS, disabled }: FormBuilderProps) {
  // Keys of custom fields added in this editing session: their key follows the label until saved.
  const fresh = useRef(new Set<string>());
  const structural = useMemo(() => ({ ...checkSchema(schema), ...(serverFieldErrors ?? {}) }), [schema, serverFieldErrors]);
  const errorsByKey = useMemo(() => new Map(issues.errors.map((e) => [e.key, e])), [issues.errors]);

  const replace = (index: number, next: FormField) => onChange({ ...schema, fields: schema.fields.map((f, i) => (i === index ? next : f)) });
  const add = (type: FieldType) => {
    const field = newCustomField(type, schema.fields.map((f) => f.key));
    fresh.current.add(field.key);
    onChange({ ...schema, fields: [...schema.fields, field] });
    requestAnimationFrame(() => document.querySelector<HTMLInputElement>(`[data-testid="form-field-label-${CSS.escape(field.key)}"]`)?.select());
  };
  const remove = (index: number) => {
    const key = schema.fields[index]?.key;
    onChange({ ...schema, fields: schema.fields.filter((_, i) => i !== index) });
    if (key) onAcknowledgedChange(acknowledged.filter((a) => !a.startsWith(`${key}:`)));
  };
  const rekey = (index: number) => {
    const field = schema.fields[index];
    if (!field || !fresh.current.has(field.key)) return;
    const others = schema.fields.filter((_, i) => i !== index).map((f) => f.key);
    const key = deriveKey(field.label, others);
    if (key === field.key) return;
    fresh.current.delete(field.key);
    fresh.current.add(key);
    onAcknowledgedChange(acknowledged.filter((a) => !a.startsWith(`${field.key}:`)));
    replace(index, { ...field, key });
  };
  const acknowledge = (key: string, code: string, value: boolean) => {
    const id = warningKey(key, code);
    onAcknowledgedChange(value ? [...new Set([...acknowledged, id])] : acknowledged.filter((a) => a !== id));
  };

  const customCount = schema.fields.filter((f) => !f.builtin).length;
  const full = schema.fields.length >= maxFields;

  return (
    <div data-testid="form-builder" className="grid grid-cols-1 gap-4">
      {issues.errors.length ? (
        <Callout tone="danger" title="Remove the highlighted questions to save">
          We never collect age, gender, family status, religion, nationality, photos, health or pay history. Questions that ask for them are blocked.
        </Callout>
      ) : null}
      {structural._form ? <Callout tone="danger">{structural._form}</Callout> : null}

      <ul className="divide-y divide-line-subtle overflow-hidden rounded-lg border border-line bg-surface" aria-label="Form fields in display order">
        {schema.fields.map((field, index) => {
          const shared = {
            field,
            index,
            count: schema.fields.length,
            onChange: (next: FormField) => replace(index, next),
            onMove: (delta: -1 | 1) => onChange({ ...schema, fields: moveField(schema.fields, index, delta) }),
            onRemove: () => remove(index),
            onLabelCommit: () => rekey(index),
            structuralError: structural[field.key],
            fairnessError: errorsByKey.get(field.key),
            warnings: issues.warnings.filter((w) => w.key === field.key),
            onAcknowledge: (code: string, value: boolean) => acknowledge(field.key, code, value),
            disabled,
          };
          return field.builtin ? <BuiltinFieldRow key={field.key} {...shared} /> : <FieldEditor key={field.key} {...shared} />;
        })}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" leftIcon={<Plus />} disabled={disabled || full} data-testid="form-add-field-button">
              Add question
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-72">
            <DropdownMenuLabel>Answer type</DropdownMenuLabel>
            {FIELD_TYPES.map((type) => (
              <DropdownMenuItem key={type} onSelect={() => add(type)} data-testid={`form-add-field-${type}`}>
                <span className="grid">
                  <span className="text-sm text-fg">{FIELD_TYPE_LABELS[type]}</span>
                  <span className="text-xs text-fg-muted">{FIELD_TYPE_HINTS[type]}</span>
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <p className="text-xs text-fg-muted tabular-nums">
          {customCount} custom {customCount === 1 ? "question" : "questions"}, {schema.fields.length} of {maxFields} fields
        </p>
      </div>
      <p className="text-xs leading-5 text-fg-muted">
        Answers go to your team only. They are never sent to the AI and never affect scores: the interview is planned from the résumé and the round.
      </p>
    </div>
  );
}
