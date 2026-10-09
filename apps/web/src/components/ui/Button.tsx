import { Slot, Slottable } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Spinner } from "./Spinner";

export const buttonVariants = cva(
  [
    "relative inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-control font-medium",
    "transition-[background-color,border-color,color,box-shadow,opacity] duration-150 ease-out",
    "disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-primary text-primary-fg shadow-[0_1px_2px_rgb(10_14_31/0.16),inset_0_1px_0_rgb(255_255_255/0.07)] hover:bg-primary-hover",
        secondary: "bg-neutral-soft text-fg hover:bg-surface-active",
        outline:
          "border border-line-strong bg-surface text-fg shadow-[0_1px_1px_rgb(16_24_40/0.04)] hover:border-line-emphasis hover:bg-surface-subtle",
        ghost: "text-fg-muted hover:bg-surface-hover hover:text-fg",
        danger:
          "bg-danger text-danger-contrast shadow-[0_1px_2px_rgb(127_29_29/0.2),inset_0_1px_0_rgb(255_255_255/0.08)] hover:bg-danger-fg",
        link: "h-auto rounded-xs px-0 text-accent underline-offset-4 hover:text-accent-hover hover:underline",
      },
      size: {
        xs: "h-7 gap-1.5 rounded-[7px] px-2.5 text-xs [&_svg]:size-3.5",
        sm: "h-8 gap-1.5 px-3 text-[13px] [&_svg]:size-4",
        md: "h-9 px-3.5 text-sm [&_svg]:size-4",
        lg: "h-10 px-4 text-[15px] [&_svg]:size-[18px]",
        xl: "h-12 rounded-md px-6 text-base [&_svg]:size-5",
      },
      fullWidth: { true: "w-full" },
    },
    compoundVariants: [{ variant: "link", className: "h-auto px-0" }],
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** Render the single child element with button styling (e.g. a router Link). */
  asChild?: boolean;
  /** Shows a spinner, disables the button and sets aria-busy. */
  loading?: boolean;
  /** Label shown while loading (defaults to the children). */
  loadingText?: ReactNode;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    className,
    variant,
    size,
    fullWidth,
    asChild = false,
    loading = false,
    loadingText,
    leftIcon,
    rightIcon,
    disabled,
    children,
    type,
    ...props
  },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  const spinnerSize = size === "xs" ? "xs" : size === "xl" ? "lg" : "sm";
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : (type ?? "button")}
      className={cn(buttonVariants({ variant, size, fullWidth }), loading && "cursor-progress disabled:opacity-100", className)}
      disabled={asChild ? undefined : disabled || loading}
      aria-disabled={asChild && (disabled || loading) ? true : undefined}
      aria-busy={loading || undefined}
      data-loading={loading || undefined}
      {...props}
    >
      {loading ? <Spinner size={spinnerSize} label={null} /> : leftIcon}
      <Slottable>{loading && loadingText ? loadingText : children}</Slottable>
      {!loading && rightIcon}
    </Comp>
  );
});
