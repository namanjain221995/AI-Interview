import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type InputHTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Shared look of every text-entry control (inputs, select triggers, tag inputs). */
export const fieldBase = [
  "w-full min-w-0 rounded-control border border-line-strong bg-field text-fg",
  "shadow-[0_1px_1px_rgb(16_24_40/0.03)] transition-[border-color,box-shadow,background-color] duration-150",
  "placeholder:text-fg-subtle hover:border-line-emphasis",
  "focus-visible:outline-none focus-visible:border-accent focus-visible:ring-3 focus-visible:ring-accent/15",
  "disabled:cursor-not-allowed disabled:bg-surface-subtle disabled:text-fg-muted disabled:hover:border-line-strong",
  "aria-invalid:border-danger aria-invalid:hover:border-danger aria-invalid:focus-visible:ring-danger/15",
].join(" ");

export const inputVariants = cva(fieldBase, {
  variants: {
    size: {
      sm: "h-8 px-2.5 text-[13px]",
      md: "h-9 px-3 text-sm",
      lg: "h-11 px-3.5 text-[15px]",
    },
  },
  defaultVariants: { size: "md" },
});

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "size">, VariantProps<typeof inputVariants> {
  /** Decorative or interactive content inside the field, before the text. */
  leading?: ReactNode;
  /** Content inside the field, after the text (e.g. a show-password button). */
  trailing?: ReactNode;
  /** Class for the wrapper when `leading`/`trailing` are used. */
  wrapperClassName?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, size, leading, trailing, wrapperClassName, type = "text", ...props },
  ref,
) {
  const input = (
    <input
      ref={ref}
      type={type}
      className={cn(
        inputVariants({ size }),
        "file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-fg",
        leading ? "pl-9" : undefined,
        trailing ? "pr-10" : undefined,
        className,
      )}
      {...props}
    />
  );
  if (!leading && !trailing) return input;
  return (
    <div className={cn("relative flex w-full items-center", wrapperClassName)}>
      {leading ? (
        <span className="pointer-events-none absolute left-3 flex items-center text-fg-subtle [&_svg]:size-4">{leading}</span>
      ) : null}
      {input}
      {trailing ? <span className="absolute right-1.5 flex items-center text-fg-subtle [&_svg]:size-4">{trailing}</span> : null}
    </div>
  );
});
