import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check, Minus } from "lucide-react";
import { forwardRef, useId, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface CheckboxProps extends ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root> {
  label?: ReactNode;
  description?: ReactNode;
  invalid?: boolean;
}

export const Checkbox = forwardRef<ComponentRef<typeof CheckboxPrimitive.Root>, CheckboxProps>(function Checkbox(
  { className, label, description, invalid, id, ...props },
  ref,
) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const descriptionId = description ? `${controlId}-description` : undefined;

  const box = (
    <CheckboxPrimitive.Root
      ref={ref}
      id={controlId}
      aria-invalid={invalid || undefined}
      aria-describedby={descriptionId}
      className={cn(
        "peer flex size-4 shrink-0 items-center justify-center rounded-[5px] border border-line-emphasis bg-surface text-primary-fg",
        "shadow-[0_1px_1px_rgb(16_24_40/0.05)] transition-colors duration-100",
        "hover:border-fg-subtle data-[state=checked]:border-primary data-[state=checked]:bg-primary",
        "data-[state=indeterminate]:border-primary data-[state=indeterminate]:bg-primary",
        "disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger",
        label ? "mt-0.5" : undefined,
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center">
        {props.checked === "indeterminate" ? <Minus className="size-3" strokeWidth={3} /> : <Check className="size-3" strokeWidth={3} />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );

  if (!label) return box;
  return (
    <div className="flex items-start gap-2.5">
      {box}
      <div className="grid gap-0.5">
        <label htmlFor={controlId} className="text-sm leading-5 font-medium text-fg peer-disabled:opacity-60">
          {label}
        </label>
        {description ? (
          <p id={descriptionId} className="text-xs leading-[1.125rem] text-fg-muted">
            {description}
          </p>
        ) : null}
      </div>
    </div>
  );
});
