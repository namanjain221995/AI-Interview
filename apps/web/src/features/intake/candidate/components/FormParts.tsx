/**
 * Small form parts for the intake details form: a labelled field with a test-id'd
 * error, single-choice pills (a radio group that wraps on phones) and the 1-10
 * self-rating scale. Selection never relies on colour alone: pills fill their dot,
 * the rating cell inverts.
 */
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { AlertCircle } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Label } from "@/components/ui/Label";
import { cn } from "@/lib/utils";

export interface FieldIds {
  id: string;
  labelId: string;
  describedBy: string | undefined;
  invalid: boolean;
}

export function Field({
  name,
  label,
  description,
  error,
  optional,
  className,
  children,
}: {
  /** Form field name: test ids `intake-field-<name>` (on the control) and `intake-field-error-<name>`. */
  name: string;
  label: ReactNode;
  description?: ReactNode;
  error?: string;
  optional?: boolean;
  className?: string;
  children: (ids: FieldIds) => ReactNode;
}) {
  const base = useId();
  const id = `${base}-control`;
  const labelId = `${base}-label`;
  const descriptionId = description ? `${base}-description` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("grid min-w-0 content-start gap-2", className)} data-field={name} data-invalid={error ? "true" : undefined}>
      <div className="grid gap-1">
        <Label id={labelId} htmlFor={id} optional={optional} className="text-sm leading-5">
          {label}
        </Label>
        {description ? (
          <p id={descriptionId} className="max-w-xl text-[13px] leading-5 text-fg-muted">
            {description}
          </p>
        ) : null}
      </div>
      {children({ id, labelId, describedBy, invalid: Boolean(error) })}
      <div aria-live="polite" className="empty:hidden">
        {error ? (
          <p id={errorId} data-testid={`intake-field-error-${name}`} className="flex items-start gap-1.5 text-[13px] leading-5 font-medium text-danger-fg">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export interface ChoiceOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

/** Single choice as wrapping pills; arrow keys move between them (Radix radio group). */
export function ChoiceGroup<T extends string>({
  ids,
  name,
  value,
  options,
  onChange,
  disabled,
  layout = "wrap",
  className,
}: {
  ids: FieldIds;
  name: string;
  value: T | "";
  options: ReadonlyArray<ChoiceOption<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  /** `wrap`: pills in a row; `stack`: one per line (long labels); `grid`: two columns from sm (options with hints). */
  layout?: "wrap" | "stack" | "grid";
  className?: string;
}) {
  return (
    <RadioGroupPrimitive.Root
      id={ids.id}
      data-testid={`intake-field-${name}`}
      aria-labelledby={ids.labelId}
      aria-describedby={ids.describedBy}
      aria-invalid={ids.invalid || undefined}
      value={value || ""}
      onValueChange={(v) => onChange(v as T)}
      disabled={disabled}
      className={cn(
        layout === "wrap" && "flex flex-wrap gap-2",
        layout === "stack" && "grid grid-cols-1 gap-2",
        layout === "grid" && "grid grid-cols-1 gap-2 sm:grid-cols-2",
        className,
      )}
    >
      {options.map((option) => (
        <RadioGroupPrimitive.Item
          key={option.value}
          value={option.value}
          data-testid={`intake-field-${name}-${option.value}`}
          className={cn(
            "group flex min-h-10 min-w-0 items-start gap-2.5 rounded-control border bg-field px-3.5 py-2.5 text-left text-sm text-fg",
            "border-line-strong transition-[border-color,box-shadow,background-color] duration-150 hover:border-line-emphasis",
            "data-[state=checked]:border-primary data-[state=checked]:shadow-[0_0_0_1px_var(--primary)]",
            "disabled:cursor-not-allowed disabled:opacity-60",
            ids.invalid && "border-danger",
            layout === "wrap" && "items-center py-0",
          )}
        >
          <span
            aria-hidden="true"
            className={cn("flex size-4 shrink-0 items-center justify-center rounded-full border border-line-emphasis group-data-[state=checked]:border-primary", layout !== "wrap" && "mt-0.5")}
          >
            <span className="size-2 scale-0 rounded-full bg-primary transition-transform duration-150 group-data-[state=checked]:scale-100" />
          </span>
          <span className="grid min-w-0 gap-0.5">
            <span className="font-medium">{option.label}</span>
            {option.hint ? <span className="text-[13px] leading-5 text-fg-muted">{option.hint}</span> : null}
          </span>
        </RadioGroupPrimitive.Item>
      ))}
    </RadioGroupPrimitive.Root>
  );
}

/** 1-10 self-rating: ten cells (two rows of five on phones) with the scale's two ends named. */
export function RatingScale({ ids, value, onChange, disabled }: { ids: FieldIds; value: number | null; onChange: (value: number) => void; disabled?: boolean }) {
  return (
    <div className="grid max-w-xl gap-2">
      <RadioGroupPrimitive.Root
        id={ids.id}
        data-testid="intake-field-self_rating"
        aria-labelledby={ids.labelId}
        aria-describedby={ids.describedBy}
        aria-invalid={ids.invalid || undefined}
        value={value === null ? "" : String(value)}
        onValueChange={(v) => onChange(Number(v))}
        disabled={disabled}
        orientation="horizontal"
        className="grid grid-cols-5 gap-2 sm:grid-cols-10"
      >
        {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
          <RadioGroupPrimitive.Item
            key={n}
            value={String(n)}
            aria-label={n === 1 ? "1, just starting out" : n === 10 ? "10, expert" : String(n)}
            data-testid={`intake-field-self_rating-${n}`}
            className={cn(
              "flex h-10 items-center justify-center rounded-control border border-line-strong bg-field text-[15px] font-medium text-fg tabular-nums",
              "transition-[background-color,border-color,color] duration-150 hover:border-line-emphasis",
              "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-fg",
              "disabled:cursor-not-allowed disabled:opacity-60",
              ids.invalid && "border-danger",
            )}
          >
            {n}
          </RadioGroupPrimitive.Item>
        ))}
      </RadioGroupPrimitive.Root>
      <div className="flex justify-between text-[13px] leading-5 text-fg-muted" aria-hidden="true">
        <span>1 = just starting out</span>
        <span>10 = expert</span>
      </div>
    </div>
  );
}
