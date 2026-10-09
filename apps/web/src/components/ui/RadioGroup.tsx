import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { forwardRef, useId, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const RadioGroup = forwardRef<
  ComponentRef<typeof RadioGroupPrimitive.Root>,
  ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Root>
>(function RadioGroup({ className, ...props }, ref) {
  return <RadioGroupPrimitive.Root ref={ref} className={cn("grid content-start gap-3", className)} {...props} />;
});

export interface RadioGroupItemProps extends ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Item> {
  label?: ReactNode;
  description?: ReactNode;
}

export const RadioGroupItem = forwardRef<ComponentRef<typeof RadioGroupPrimitive.Item>, RadioGroupItemProps>(function RadioGroupItem(
  { className, label, description, id, ...props },
  ref,
) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const descriptionId = description ? `${controlId}-description` : undefined;
  const radio = (
    <RadioGroupPrimitive.Item
      ref={ref}
      id={controlId}
      aria-describedby={descriptionId}
      className={cn(
        "peer flex size-4 shrink-0 items-center justify-center rounded-full border border-line-emphasis bg-surface",
        "shadow-[0_1px_1px_rgb(16_24_40/0.05)] transition-colors hover:border-fg-subtle",
        "data-[state=checked]:border-primary disabled:cursor-not-allowed disabled:opacity-50",
        label ? "mt-0.5" : undefined,
        className,
      )}
      {...props}
    >
      <RadioGroupPrimitive.Indicator className="block size-2 rounded-full bg-primary" />
    </RadioGroupPrimitive.Item>
  );
  if (!label) return radio;
  return (
    <div className="flex items-start gap-2.5">
      {radio}
      <div className="grid gap-0.5">
        <label htmlFor={controlId} className="text-sm leading-5 font-medium text-fg">
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

export interface RadioCardProps extends Omit<ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Item>, "children" | "title"> {
  title: ReactNode;
  description?: ReactNode;
  /** Right-aligned detail (duration, badge). */
  aside?: ReactNode;
  icon?: ReactNode;
}

/** A selectable card for prominent choices (e.g. choosing an interview round). */
export const RadioCard = forwardRef<ComponentRef<typeof RadioGroupPrimitive.Item>, RadioCardProps>(function RadioCard(
  { className, title, description, aside, icon, ...props },
  ref,
) {
  return (
    <RadioGroupPrimitive.Item
      ref={ref}
      className={cn(
        "group relative flex w-full items-start gap-3 rounded-lg border border-line bg-surface p-4 text-left transition-[border-color,box-shadow,background-color] duration-150",
        "hover:border-line-strong data-[state=checked]:border-primary data-[state=checked]:shadow-[0_0_0_1px_var(--primary)]",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-line-emphasis group-data-[state=checked]:border-primary"
      >
        <span className="size-2 scale-0 rounded-full bg-primary transition-transform duration-150 group-data-[state=checked]:scale-100" />
      </span>
      {icon ? <span className="mt-px text-fg-muted [&_svg]:size-5">{icon}</span> : null}
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="text-sm font-semibold text-fg">{title}</span>
        {description ? <span className="text-sm text-fg-muted">{description}</span> : null}
      </span>
      {aside ? <span className="shrink-0 text-sm text-fg-muted">{aside}</span> : null}
    </RadioGroupPrimitive.Item>
  );
});
