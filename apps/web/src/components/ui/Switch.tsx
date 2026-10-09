import * as SwitchPrimitive from "@radix-ui/react-switch";
import { forwardRef, useId, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface SwitchProps extends ComponentPropsWithoutRef<typeof SwitchPrimitive.Root> {
  size?: "sm" | "md";
  label?: ReactNode;
  description?: ReactNode;
  /** Put the label on the left and the switch on the right (settings rows). */
  labelPosition?: "start" | "end";
}

export const Switch = forwardRef<ComponentRef<typeof SwitchPrimitive.Root>, SwitchProps>(function Switch(
  { className, size = "md", label, description, labelPosition = "end", id, ...props },
  ref,
) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const descriptionId = description ? `${controlId}-description` : undefined;

  const control = (
    <SwitchPrimitive.Root
      ref={ref}
      id={controlId}
      aria-describedby={descriptionId}
      className={cn(
        "peer inline-flex shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-150",
        "bg-line-emphasis data-[state=checked]:bg-primary disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-4 w-7" : "h-5 w-9",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        className={cn(
          "pointer-events-none block rounded-full bg-white shadow-[0_1px_2px_rgb(16_24_40/0.25)] transition-transform duration-150 ease-out data-[state=checked]:bg-primary-fg",
          size === "sm" ? "size-3 translate-x-0 data-[state=checked]:translate-x-3" : "size-4 translate-x-0 data-[state=checked]:translate-x-4",
        )}
      />
    </SwitchPrimitive.Root>
  );

  if (!label) return control;
  const text = (
    <div className="grid min-w-0 gap-0.5">
      <label htmlFor={controlId} className="text-sm leading-5 font-medium text-fg">
        {label}
      </label>
      {description ? (
        <p id={descriptionId} className="text-xs leading-[1.125rem] text-fg-muted">
          {description}
        </p>
      ) : null}
    </div>
  );
  return labelPosition === "start" ? (
    <div className="flex items-start justify-between gap-6">
      {text}
      <span className="mt-0.5">{control}</span>
    </div>
  ) : (
    <div className="flex items-start gap-3">
      <span className="mt-0.5">{control}</span>
      {text}
    </div>
  );
});
