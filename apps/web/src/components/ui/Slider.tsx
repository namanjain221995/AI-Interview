import * as SliderPrimitive from "@radix-ui/react-slider";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef } from "react";

import { cn } from "@/lib/utils";

export interface SliderProps extends ComponentPropsWithoutRef<typeof SliderPrimitive.Root> {
  /** Accessible names for each thumb (one per value). */
  thumbLabels?: string[];
  /** Show the current value(s) under the track. */
  showValue?: boolean;
  formatValue?: (value: number) => string;
}

export const Slider = forwardRef<ComponentRef<typeof SliderPrimitive.Root>, SliderProps>(function Slider(
  { className, thumbLabels, showValue, formatValue = String, value, defaultValue, ...props },
  ref,
) {
  const values = value ?? defaultValue ?? [props.min ?? 0];
  return (
    <div className={cn("w-full", className)}>
      <SliderPrimitive.Root
        ref={ref}
        value={value}
        defaultValue={defaultValue}
        className="relative flex h-5 w-full touch-none select-none items-center data-[disabled]:opacity-50"
        {...props}
      >
        <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-track">
          <SliderPrimitive.Range className="absolute h-full rounded-full bg-primary" />
        </SliderPrimitive.Track>
        {values.map((v, index) => (
          <SliderPrimitive.Thumb
            key={index}
            aria-label={thumbLabels?.[index]}
            aria-valuetext={formatValue(v)}
            className={cn(
              "block size-4 rounded-full border border-line-emphasis bg-white shadow-[0_1px_3px_rgb(16_24_40/0.2)]",
              "transition-[box-shadow] hover:shadow-[0_0_0_4px_color-mix(in_srgb,var(--primary)_12%,transparent)]",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
            )}
          />
        ))}
      </SliderPrimitive.Root>
      {showValue ? (
        <p className="mt-1.5 text-xs text-fg-muted tabular-nums" aria-hidden="true">
          {values.map((v) => formatValue(v)).join(" – ")}
        </p>
      ) : null}
    </div>
  );
});
