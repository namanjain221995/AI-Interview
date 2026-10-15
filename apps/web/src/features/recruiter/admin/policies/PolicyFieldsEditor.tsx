import { RotateCcw } from "lucide-react";
import { Fragment } from "react";

import { Badge } from "@/components/ui/Badge";
import { Switch } from "@/components/ui/Switch";
import { cn } from "@/lib/utils";

import { FieldShell, NumberInput } from "../components/Field";
import type { FieldErrors } from "../lib/serverErrors";
import { isDependencyOff, type PolicyFieldDef } from "./policyFields";

export interface PolicyFieldsEditorProps<K extends string> {
  fields: ReadonlyArray<PolicyFieldDef<K>>;
  /** Effective values (defaults merged with overrides). */
  values: Record<K, unknown>;
  onChange: (key: K, value: boolean | number) => void;
  /** Job overrides: mark customised fields and offer a reset. */
  isOverridden?: (key: K) => boolean;
  onReset?: (key: K) => void;
  /** Shown as "Organization default: On" next to customised fields. */
  defaults?: Partial<Record<K, unknown>>;
  groups?: ReadonlyArray<{ key: string; label: string; description: string }>;
  errors?: FieldErrors;
  /** Error path prefix (e.g. "integrity"). */
  errorPrefix?: string;
  disabled?: boolean;
  /** Boolean fields the platform forces on: shown on, locked, with the reason. */
  locked?: ReadonlySet<string>;
}

function describeValue(field: PolicyFieldDef<string>, value: unknown): string {
  if (field.kind.type === "boolean") return value ? "On" : "Off";
  return `${String(value)} ${field.kind.unit}`;
}

/** A list of policy switches and numbers with plain-language help. */
export function PolicyFieldsEditor<K extends string>({
  fields,
  values,
  onChange,
  isOverridden,
  onReset,
  defaults,
  groups,
  errors,
  errorPrefix,
  disabled,
  locked,
}: PolicyFieldsEditorProps<K>) {
  const grouped = groups
    ? groups.map((g) => ({ ...g, fields: fields.filter((f) => f.group === g.key) })).filter((g) => g.fields.length > 0)
    : [{ key: "all", label: "", description: "", fields: [...fields] }];

  const renderField = (field: PolicyFieldDef<K>) => {
    const value = values[field.key];
    const off = isDependencyOff(field, fields, values);
    const custom = isOverridden?.(field.key) ?? false;
    const errorKey = errorPrefix ? `${errorPrefix}.${field.key}` : field.key;
    const aside = custom ? (
      <span className="flex items-center gap-1.5">
        <Badge tone="accent" size="sm" title={defaults ? `Organization default: ${describeValue(field, defaults[field.key])}` : undefined}>
          Custom
        </Badge>
        {onReset ? (
          <button
            type="button"
            onClick={() => onReset(field.key)}
            className="inline-flex items-center gap-1 rounded-xs text-xs font-medium text-fg-muted hover:text-fg"
            aria-label={`Reset ${field.label} to the organization default`}
          >
            <RotateCcw className="size-3" aria-hidden="true" />
            Default{defaults ? `: ${describeValue(field, defaults[field.key])}` : ""}
          </button>
        ) : null}
      </span>
    ) : null;

    if (field.kind.type === "boolean") {
      const isLocked = locked?.has(field.key) ?? false;
      return (
        <div key={field.key} className={cn("flex items-start justify-between gap-6 py-3.5", off && !isLocked && "opacity-55")} data-locked={isLocked || undefined}>
          <div className="min-w-0 flex-1">
            <Switch
              label={field.label}
              description={field.help}
              checked={isLocked || Boolean(value)}
              disabled={disabled || off || isLocked}
              labelPosition="start"
              onCheckedChange={(checked) => onChange(field.key, checked)}
            />
            {isLocked ? (
              <div className="mt-1.5">
                <Badge tone="neutral" size="sm" title="Video is required for every interview on this platform.">
                  Always on
                </Badge>
              </div>
            ) : aside ? (
              <div className="mt-1.5">{aside}</div>
            ) : null}
          </div>
        </div>
      );
    }
    const kind = field.kind;
    return (
      <div key={field.key} className={cn("grid gap-4 py-3.5 sm:grid-cols-[1fr_11rem] sm:items-start", off && "opacity-55")}>
        <div className="grid gap-0.5">
          <span className="text-sm leading-5 font-medium text-fg">{field.label}</span>
          <span className="text-xs leading-[1.125rem] text-fg-muted">{field.help}</span>
          {aside ? <div className="mt-1.5">{aside}</div> : null}
        </div>
        <FieldShell error={errors?.[errorKey]}>
          {(control) => (
            <NumberInput
              {...control}
              aria-label={field.label}
              value={typeof value === "number" ? value : null}
              min={kind.min}
              max={kind.max}
              step={kind.step ?? 1}
              unit={kind.unit}
              disabled={disabled || off}
              onChange={(next) => onChange(field.key, next ?? kind.min)}
            />
          )}
        </FieldShell>
      </div>
    );
  };

  return (
    <div className="grid gap-6">
      {grouped.map((group) => (
        <Fragment key={group.key}>
          <div className="grid gap-1">
            {group.label ? (
              <div className="mb-1 grid gap-0.5">
                <h3 className="text-sm font-semibold text-fg">{group.label}</h3>
                <p className="text-xs text-fg-muted">{group.description}</p>
              </div>
            ) : null}
            <div className="divide-y divide-line-subtle rounded-md border border-line px-4">{group.fields.map(renderField)}</div>
          </div>
        </Fragment>
      ))}
    </div>
  );
}
