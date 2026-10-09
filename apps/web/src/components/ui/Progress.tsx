import * as ProgressPrimitive from "@radix-ui/react-progress";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode } from "react";

import { clamp, cn } from "@/lib/utils";

const TONES = {
  default: "bg-primary",
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  signal: "bg-signal",
} as const;

const SIZES = { xs: "h-1", sm: "h-1.5", md: "h-2" } as const;

export interface ProgressProps extends Omit<ComponentPropsWithoutRef<typeof ProgressPrimitive.Root>, "value"> {
  /** 0-100; null or undefined renders an indeterminate bar. */
  value?: number | null;
  tone?: keyof typeof TONES;
  size?: keyof typeof SIZES;
  label?: ReactNode;
  /** Right-aligned value text (e.g. "64%"). */
  valueLabel?: ReactNode;
}

export const Progress = forwardRef<ComponentRef<typeof ProgressPrimitive.Root>, ProgressProps>(function Progress(
  { value, tone = "default", size = "sm", label, valueLabel, className, ...props },
  ref,
) {
  const determinate = typeof value === "number" && Number.isFinite(value);
  const pct = determinate ? clamp(value, 0, 100) : 0;
  const bar = (
    <ProgressPrimitive.Root
      ref={ref}
      value={determinate ? pct : null}
      max={100}
      className={cn("relative w-full overflow-hidden rounded-full bg-track", SIZES[size], label || valueLabel ? undefined : className)}
      {...props}
    >
      {determinate ? (
        <ProgressPrimitive.Indicator
          className={cn("h-full rounded-full transition-[width] duration-300 ease-out", TONES[tone])}
          style={{ width: `${pct}%` }}
        />
      ) : (
        <ProgressPrimitive.Indicator className={cn("absolute inset-y-0 left-0 w-full origin-left rounded-full animate-indeterminate", TONES[tone])} />
      )}
    </ProgressPrimitive.Root>
  );
  if (!label && !valueLabel) return bar;
  return (
    <div className={cn("grid content-start gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="font-medium text-fg">{label}</span>
        <span className="text-fg-muted tabular-nums">{valueLabel}</span>
      </div>
      {bar}
    </div>
  );
});
