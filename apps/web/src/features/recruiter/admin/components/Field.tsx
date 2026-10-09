/**
 * Form building blocks for the controlled (non react-hook-form) editors:
 * labelled fields with errors, number inputs that tolerate partial typing,
 * string-list editors and settings-style toggle rows.
 */

import { Plus, Trash2, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { FieldError } from "@/components/ui/Form";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/utils";

// -------------------------------------------------------------- FieldShell

export interface FieldShellProps {
  label?: ReactNode;
  description?: ReactNode;
  error?: string;
  /** Non-blocking advice shown in amber when there is no error. */
  warning?: string;
  required?: boolean;
  optional?: boolean;
  /** Content to the right of the label (e.g. a "Custom" badge or reset link). */
  aside?: ReactNode;
  className?: string;
  /** Render-prop receives the control id and aria props. */
  children: (control: { id: string; "aria-invalid": boolean | undefined; "aria-describedby": string | undefined }) => ReactNode;
}

export function FieldShell({ label, description, error, warning, required, optional, aside, className, children }: FieldShellProps) {
  const id = useId();
  const descriptionId = description ? `${id}-d` : undefined;
  const errorId = error ? `${id}-e` : undefined;
  return (
    <div className={cn("grid content-start gap-1.5", className)} data-invalid={error ? true : undefined}>
      {label || aside ? (
        <div className="flex min-h-5 items-center justify-between gap-3">
          {label ? (
            <Label htmlFor={id} required={required} optional={optional}>
              {label}
            </Label>
          ) : (
            <span />
          )}
          {aside}
        </div>
      ) : null}
      {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": [descriptionId, errorId].filter(Boolean).join(" ") || undefined })}
      {description && !error ? (
        <p id={descriptionId} className="text-xs leading-[1.125rem] text-fg-muted">
          {description}
        </p>
      ) : null}
      {!error && warning ? <p className="text-xs leading-[1.125rem] font-medium text-warning-fg">{warning}</p> : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}

// ------------------------------------------------------------- NumberInput

export interface NumberInputProps {
  value: number | null;
  onChange: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Allow clearing to null (otherwise blur restores the last valid value). */
  nullable?: boolean;
  unit?: ReactNode;
  placeholder?: string;
  id?: string;
  size?: "sm" | "md";
  className?: string;
  disabled?: boolean;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
  "aria-label"?: string;
}

function format(value: number | null): string {
  return value === null || Number.isNaN(value) ? "" : String(value);
}

/**
 * Number field that keeps the raw text while typing ("0.", "-") and reports
 * parsed numbers as they become valid. Validation happens in the editor.
 */
export function NumberInput({ value, onChange, min, max, step, nullable, unit, placeholder, id, size = "md", className, disabled, ...aria }: NumberInputProps) {
  const [text, setText] = useState(format(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(format(value));
  }, [value]);

  return (
    <div className={cn("relative flex items-center", className)}>
      <Input
        id={id}
        type="text"
        inputMode={step !== undefined && step < 1 ? "decimal" : "numeric"}
        size={size}
        value={text}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete="off"
        onFocus={() => {
          focused.current = true;
        }}
        onChange={(event) => {
          const raw = event.target.value;
          setText(raw);
          const trimmed = raw.trim();
          if (trimmed === "") {
            if (nullable) onChange(null);
            return;
          }
          const parsed = Number(trimmed);
          if (Number.isFinite(parsed)) onChange(parsed);
        }}
        onBlur={() => {
          focused.current = false;
          const trimmed = text.trim();
          if (trimmed === "" && !nullable) setText(format(value));
          else if (trimmed !== "" && !Number.isFinite(Number(trimmed))) setText(format(value));
          else setText(format(trimmed === "" ? null : Number(trimmed)));
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
          event.preventDefault();
          const delta = (step ?? 1) * (event.key === "ArrowUp" ? 1 : -1) * (event.shiftKey ? 10 : 1);
          let next = Math.round(((value ?? min ?? 0) + delta) * 1000) / 1000;
          if (min !== undefined) next = Math.max(min, next);
          if (max !== undefined) next = Math.min(max, next);
          setText(format(next));
          onChange(next);
        }}
        className={cn("tabular-nums", unit ? "pr-14" : undefined)}
        {...aria}
      />
      {unit ? <span className="pointer-events-none absolute right-3 text-xs text-fg-subtle">{unit}</span> : null}
    </div>
  );
}

// -------------------------------------------------------------- ListEditor

export interface ListEditorProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  addLabel?: string;
  /** Multi-line entries (hints, follow-ups). */
  multiline?: boolean;
  /** Fixed number of rows (no add/remove), e.g. exactly three hints. */
  fixedCount?: number;
  /** Label per row, e.g. (i) => `Hint ${i + 1}`. */
  rowLabel?: (index: number) => string;
  maxItems?: number;
  errors?: Record<number, string>;
  /** Read-only: no add/remove controls. */
  disabled?: boolean;
  id?: string;
  "aria-describedby"?: string;
}

/** Editable list of short texts with add/remove and Enter-to-add. */
export function ListEditor({ value, onChange, placeholder, addLabel = "Add", multiline, fixedCount, rowLabel, maxItems, errors, disabled, id, ...aria }: ListEditorProps) {
  const rows = fixedCount !== undefined ? Array.from({ length: fixedCount }, (_, i) => value[i] ?? "") : value;
  const refs = useRef<Array<HTMLInputElement | HTMLTextAreaElement | null>>([]);
  const pendingFocus = useRef<number | null>(null);
  useEffect(() => {
    if (pendingFocus.current !== null) {
      refs.current[pendingFocus.current]?.focus();
      pendingFocus.current = null;
    }
  });

  const update = (index: number, text: string) => {
    const next = [...rows];
    next[index] = text;
    onChange(next);
  };
  const remove = (index: number) => onChange(rows.filter((_, i) => i !== index));
  const add = () => {
    pendingFocus.current = rows.length;
    onChange([...rows, ""]);
  };
  const canAdd = !disabled && fixedCount === undefined && (maxItems === undefined || rows.length < maxItems);

  return (
    <div className="grid content-start gap-2" id={id} {...aria}>
      {rows.map((row, index) => (
        <div key={index} className="grid gap-1">
          <div className="flex items-start gap-2">
            {rowLabel ? <span className="mt-2 w-14 shrink-0 text-xs font-medium text-fg-muted">{rowLabel(index)}</span> : null}
            {multiline ? (
              <Textarea
                ref={(el) => {
                  refs.current[index] = el;
                }}
                rows={1}
                autoResize
                maxRows={8}
                className="min-h-9 py-1.5"
                disabled={disabled}
                value={row}
                placeholder={placeholder}
                aria-label={rowLabel ? rowLabel(index) : `${placeholder ?? "Item"} ${index + 1}`}
                aria-invalid={errors?.[index] ? true : undefined}
                onChange={(event) => update(index, event.target.value)}
              />
            ) : (
              <Input
                ref={(el) => {
                  refs.current[index] = el;
                }}
                value={row}
                placeholder={placeholder}
                disabled={disabled}
                aria-label={rowLabel ? rowLabel(index) : `${placeholder ?? "Item"} ${index + 1}`}
                aria-invalid={errors?.[index] ? true : undefined}
                onChange={(event) => update(index, event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && canAdd) {
                    event.preventDefault();
                    if (row.trim()) add();
                  } else if (event.key === "Backspace" && row === "" && fixedCount === undefined && rows.length > 0) {
                    event.preventDefault();
                    pendingFocus.current = Math.max(0, index - 1);
                    remove(index);
                  }
                }}
              />
            )}
            {fixedCount === undefined && !disabled ? (
              <Button variant="ghost" size="sm" className="mt-0.5 size-8 shrink-0 px-0" aria-label={`Remove item ${index + 1}`} onClick={() => remove(index)}>
                {multiline ? <Trash2 /> : <X />}
              </Button>
            ) : null}
          </div>
          {errors?.[index] ? <p className={cn("text-xs font-medium text-danger-fg", rowLabel && "pl-16")}>{errors[index]}</p> : null}
        </div>
      ))}
      {canAdd ? (
        <Button variant="ghost" size="sm" className="w-fit text-fg-muted" leftIcon={<Plus />} onClick={add}>
          {addLabel}
        </Button>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------- ToggleRow

export interface ToggleRowProps {
  label: ReactNode;
  description?: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Right side extras (e.g. "Custom" badge + reset). */
  aside?: ReactNode;
  className?: string;
}

/** Settings row: text on the left, switch on the right. */
export function ToggleRow({ label, description, checked, onCheckedChange, disabled, aside, className }: ToggleRowProps) {
  return (
    <div className={cn("flex items-start justify-between gap-6 py-3", className)}>
      <div className="min-w-0 flex-1">
        <Switch label={label} description={description} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} labelPosition="start" />
      </div>
      {aside ? <div className="flex shrink-0 items-center gap-2 pt-0.5">{aside}</div> : null}
    </div>
  );
}

// ------------------------------------------------------------ SectionBlock

export interface SectionBlockProps {
  id?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Visual weight: framed card (default) or flat with a top rule. */
  variant?: "card" | "plain";
}

/** A titled form section. Cards for the job and settings pages, plain inside sheets. */
export function SectionBlock({ id, title, description, actions, children, className, variant = "card" }: SectionBlockProps) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-title` : undefined}
      className={cn(
        "scroll-mt-24",
        variant === "card" ? "rounded-lg border border-line bg-surface shadow-[0_1px_2px_rgb(16_24_40/0.04)]" : "border-t border-line-subtle pt-6 first:border-t-0 first:pt-0",
        className,
      )}
    >
      <header className={cn("flex flex-wrap items-start justify-between gap-3", variant === "card" ? "border-b border-line-subtle px-5 py-4" : "pb-4")}>
        <div className="grid min-w-0 gap-0.5">
          <h2 id={id ? `${id}-title` : undefined} className="text-[15px] leading-6 font-semibold tracking-[-0.006em] text-fg">
            {title}
          </h2>
          {description ? <p className="max-w-2xl text-[13px] leading-5 text-fg-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </header>
      <div className={variant === "card" ? "px-5 py-5" : undefined}>{children}</div>
    </section>
  );
}
