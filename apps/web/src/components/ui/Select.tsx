import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ComponentRef, type ReactNode, type Ref } from "react";

import { cn } from "@/lib/utils";

import { fieldBase } from "./Input";

export const SelectRoot = SelectPrimitive.Root;
export const SelectGroup = SelectPrimitive.Group;
export const SelectValue = SelectPrimitive.Value;

const TRIGGER_SIZES = {
  sm: "h-8 px-2.5 text-[13px]",
  md: "h-9 px-3 text-sm",
  lg: "h-11 px-3.5 text-[15px]",
} as const;

export interface SelectTriggerProps extends ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger> {
  size?: keyof typeof TRIGGER_SIZES;
  invalid?: boolean;
}

export const SelectTrigger = forwardRef<ComponentRef<typeof SelectPrimitive.Trigger>, SelectTriggerProps>(function SelectTrigger(
  { className, children, size = "md", invalid, ...props },
  ref,
) {
  return (
    <SelectPrimitive.Trigger
      ref={ref}
      aria-invalid={invalid || props["aria-invalid"] || undefined}
      className={cn(
        fieldBase,
        TRIGGER_SIZES[size],
        "flex items-center justify-between gap-2 text-left data-[placeholder]:text-fg-subtle [&>span]:truncate",
        "data-[state=open]:border-accent data-[state=open]:ring-3 data-[state=open]:ring-accent/15",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
});

export const SelectContent = forwardRef<
  ComponentRef<typeof SelectPrimitive.Content>,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(function SelectContent({ className, children, position = "popper", sideOffset = 4, ...props }, ref) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        ref={ref}
        position={position}
        sideOffset={sideOffset}
        collisionPadding={8}
        className={cn(
          "relative z-50 overflow-hidden rounded-md border border-line bg-surface-raised text-fg shadow-overlay",
          "max-h-(--radix-select-content-available-height) min-w-32",
          position === "popper" && "w-full min-w-(--radix-select-trigger-width)",
          "origin-(--radix-select-content-transform-origin) animate-float-in data-[side=top]:[--float-y:-4px]",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.ScrollUpButton className="flex h-6 items-center justify-center text-fg-subtle">
          <ChevronUp className="size-4" />
        </SelectPrimitive.ScrollUpButton>
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
        <SelectPrimitive.ScrollDownButton className="flex h-6 items-center justify-center text-fg-subtle">
          <ChevronDown className="size-4" />
        </SelectPrimitive.ScrollDownButton>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
});

export interface SelectItemProps extends ComponentPropsWithoutRef<typeof SelectPrimitive.Item> {
  description?: ReactNode;
}

export const SelectItem = forwardRef<ComponentRef<typeof SelectPrimitive.Item>, SelectItemProps>(function SelectItem(
  { className, children, description, ...props },
  ref,
) {
  return (
    <SelectPrimitive.Item
      ref={ref}
      className={cn(
        "relative flex w-full cursor-default select-none items-start rounded-sm py-1.5 pr-2 pl-8 text-sm outline-none",
        "data-[highlighted]:bg-surface-hover data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className,
      )}
      {...props}
    >
      <span className="absolute left-2 top-2 flex size-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <Check className="size-4 text-fg" strokeWidth={2.25} />
        </SelectPrimitive.ItemIndicator>
      </span>
      <span className="flex min-w-0 flex-col">
        <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
        {description ? <span className="text-xs text-fg-muted">{description}</span> : null}
      </span>
    </SelectPrimitive.Item>
  );
});

export const SelectLabel = forwardRef<
  ComponentRef<typeof SelectPrimitive.Label>,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Label>
>(function SelectLabel({ className, ...props }, ref) {
  return <SelectPrimitive.Label ref={ref} className={cn("px-2 pt-2 pb-1 text-xs font-medium text-fg-subtle", className)} {...props} />;
});

export const SelectSeparator = forwardRef<
  ComponentRef<typeof SelectPrimitive.Separator>,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>
>(function SelectSeparator({ className, ...props }, ref) {
  return <SelectPrimitive.Separator ref={ref} className={cn("-mx-1 my-1 h-px bg-line", className)} {...props} />;
});

export interface SelectOption<V extends string = string> {
  value: V;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}

export interface SelectProps<V extends string = string> {
  options: readonly SelectOption<V>[];
  value?: V | null;
  defaultValue?: V;
  onValueChange?: (value: V) => void;
  placeholder?: string;
  disabled?: boolean;
  invalid?: boolean;
  size?: keyof typeof TRIGGER_SIZES;
  id?: string;
  name?: string;
  required?: boolean;
  className?: string;
  contentClassName?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  onBlur?: () => void;
}

/** Single-choice select from a static option list. Radix forbids empty-string values. */
export const Select = forwardRef<HTMLButtonElement, SelectProps>(function Select(
  {
    options,
    value,
    defaultValue,
    onValueChange,
    placeholder = "Select…",
    disabled,
    invalid,
    size,
    id,
    name,
    required,
    className,
    contentClassName,
    onBlur,
    ...aria
  },
  ref,
) {
  return (
    <SelectPrimitive.Root
      value={value ?? undefined}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
      name={name}
      required={required}
      onOpenChange={(open) => {
        if (!open) onBlur?.();
      }}
    >
      <SelectTrigger ref={ref} id={id} size={size} invalid={invalid} className={className} {...aria}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={contentClassName}>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value} disabled={option.disabled} description={option.description}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectPrimitive.Root>
  );
}) as <V extends string = string>(props: SelectProps<V> & { ref?: Ref<HTMLButtonElement> }) => ReactNode;
