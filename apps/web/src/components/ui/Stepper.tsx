import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface StepperStep {
  key: string;
  label: string;
  description?: ReactNode;
}

export type StepStatus = "complete" | "current" | "upcoming";

export interface StepperProps {
  steps: readonly StepperStep[];
  /** Key of the current step. Steps before it count as complete unless `completed` is given. */
  current: string | null;
  /** Explicitly completed step keys (overrides positional completion). */
  completed?: readonly string[];
  orientation?: "horizontal" | "vertical";
  /** Navigate to a completed step. */
  onStepClick?: (key: string) => void;
  "aria-label"?: string;
  className?: string;
  /** Show the compact "Step n of N" form below this breakpoint (long journeys need `lg`). */
  compactBelow?: "sm" | "lg";
}

// Static class names (Tailwind scans them): compact form / horizontal list per breakpoint.
const COMPACT_CLASSES = { sm: ["sm:hidden", "hidden sm:flex"], lg: ["lg:hidden", "hidden lg:flex"] } as const;

export function stepStatus(steps: readonly StepperStep[], key: string, current: string | null, completed?: readonly string[]): StepStatus {
  if (key === current) return "current";
  if (completed) return completed.includes(key) ? "complete" : "upcoming";
  const currentIndex = steps.findIndex((s) => s.key === current);
  const index = steps.findIndex((s) => s.key === key);
  if (currentIndex === -1) return "upcoming";
  return index < currentIndex ? "complete" : "upcoming";
}

function Marker({ status, index }: { status: StepStatus; index: number }) {
  return (
    <span
      className={cn(
        "relative flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums transition-colors duration-200",
        status === "complete" && "bg-primary text-primary-fg",
        status === "current" && "bg-surface text-fg ring-2 ring-primary ring-offset-2 ring-offset-surface",
        status === "upcoming" && "border border-line-strong bg-surface text-fg-subtle",
      )}
      aria-hidden="true"
    >
      {status === "complete" ? <Check className="size-3.5" strokeWidth={3} /> : index + 1}
    </span>
  );
}

/** Journey progress (candidate flow). Collapses to "Step n of N" on narrow screens. */
export function Stepper({ steps, current, completed, orientation = "horizontal", onStepClick, className, compactBelow = "sm", ...aria }: StepperProps) {
  const [compactClass, listClass] = COMPACT_CLASSES[compactBelow];
  const currentIndex = steps.findIndex((s) => s.key === current);
  const currentStep = currentIndex >= 0 ? steps[currentIndex] : undefined;
  const label = aria["aria-label"] ?? "Progress";

  if (orientation === "vertical") {
    return (
      <nav aria-label={label} className={className}>
        <ol className="grid gap-0">
          {steps.map((step, index) => {
            const status = stepStatus(steps, step.key, current, completed);
            const clickable = status === "complete" && onStepClick;
            const content = (
              <>
                <Marker status={status} index={index} />
                <span className="grid gap-0.5 pt-0.5 text-left">
                  <span className={cn("text-sm font-medium", status === "upcoming" ? "text-fg-muted" : "text-fg")}>{step.label}</span>
                  {step.description ? <span className="text-xs text-fg-muted">{step.description}</span> : null}
                </span>
              </>
            );
            return (
              <li key={step.key} className="relative flex gap-3 pb-6 last:pb-0" aria-current={status === "current" ? "step" : undefined}>
                {index < steps.length - 1 ? (
                  <span aria-hidden="true" className={cn("absolute top-7 bottom-1 left-3 w-px -translate-x-1/2", status === "complete" ? "bg-primary" : "bg-line")} />
                ) : null}
                {clickable ? (
                  <button type="button" onClick={() => onStepClick(step.key)} className="flex gap-3 rounded-sm">
                    {content}
                  </button>
                ) : (
                  <div className="flex gap-3">{content}</div>
                )}
                {status === "complete" ? <span className="sr-only">(completed)</span> : null}
              </li>
            );
          })}
        </ol>
      </nav>
    );
  }

  return (
    <nav aria-label={label} className={className}>
      {/* Compact form for narrow screens */}
      <div className={cn("grid gap-2", compactClass)}>
        <p className="flex items-baseline justify-between text-xs text-fg-muted">
          <span>
            Step {Math.max(currentIndex, 0) + 1} of {steps.length}
          </span>
          <span className="font-medium text-fg">{currentStep?.label}</span>
        </p>
        <div className="flex gap-1" aria-hidden="true">
          {steps.map((step) => {
            const status = stepStatus(steps, step.key, current, completed);
            return (
              <span
                key={step.key}
                className={cn("h-1 flex-1 rounded-full", status === "upcoming" ? "bg-track" : status === "current" ? "bg-primary/60" : "bg-primary")}
              />
            );
          })}
        </div>
      </div>

      <ol className={cn("items-center", listClass)}>
        {steps.map((step, index) => {
          const status = stepStatus(steps, step.key, current, completed);
          const clickable = status === "complete" && onStepClick;
          const inner = (
            <>
              <Marker status={status} index={index} />
              <span
                className={cn(
                  "text-[13px] whitespace-nowrap",
                  status === "current" ? "font-semibold text-fg" : status === "complete" ? "font-medium text-fg" : "text-fg-muted",
                )}
              >
                {step.label}
              </span>
            </>
          );
          return (
            <li key={step.key} className="flex min-w-0 flex-1 items-center last:flex-none" aria-current={status === "current" ? "step" : undefined}>
              {clickable ? (
                <button type="button" onClick={() => onStepClick(step.key)} className="flex items-center gap-2 rounded-sm hover:opacity-80">
                  {inner}
                </button>
              ) : (
                <span className="flex items-center gap-2">{inner}</span>
              )}
              {status === "complete" ? <span className="sr-only">(completed)</span> : null}
              {index < steps.length - 1 ? (
                <span aria-hidden="true" className={cn("mx-3 h-px min-w-4 flex-1", status === "complete" ? "bg-primary" : "bg-line-strong")} />
              ) : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
