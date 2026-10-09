import * as LabelPrimitive from "@radix-ui/react-label";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef } from "react";

import { cn } from "@/lib/utils";

export interface LabelProps extends ComponentPropsWithoutRef<typeof LabelPrimitive.Root> {
  /** Adds a visual required marker (pair it with `required` on the control). */
  required?: boolean;
  /** Adds a muted "Optional" hint. */
  optional?: boolean;
}

export const Label = forwardRef<ComponentRef<typeof LabelPrimitive.Root>, LabelProps>(function Label(
  { className, required, optional, children, ...props },
  ref,
) {
  return (
    <LabelPrimitive.Root
      ref={ref}
      className={cn(
        "inline-flex items-baseline gap-1 text-[13px] leading-5 font-medium text-fg select-none",
        "peer-disabled:cursor-not-allowed peer-disabled:opacity-60",
        className,
      )}
      {...props}
    >
      {children}
      {required ? (
        <span className="text-danger" aria-hidden="true">
          *
        </span>
      ) : null}
      {optional ? <span className="text-xs font-normal text-fg-subtle">Optional</span> : null}
    </LabelPrimitive.Root>
  );
});
