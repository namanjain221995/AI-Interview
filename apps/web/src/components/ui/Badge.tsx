import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export const badgeVariants = cva(
  "inline-flex max-w-full shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm font-medium ring-1 ring-inset [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        neutral: "bg-neutral-soft text-neutral-fg ring-neutral-line",
        accent: "bg-accent-soft text-accent-fg ring-accent-line",
        success: "bg-success-soft text-success-fg ring-success-line",
        warning: "bg-warning-soft text-warning-fg ring-warning-line",
        danger: "bg-danger-soft text-danger-fg ring-danger-line",
        signal: "bg-signal-soft text-signal-fg ring-signal-line",
        outline: "bg-transparent text-fg-muted ring-line-strong",
      },
      size: {
        sm: "h-5 px-1.5 text-2xs [&_svg]:size-3",
        md: "h-6 px-2 text-xs [&_svg]:size-3.5",
      },
    },
    defaultVariants: { tone: "neutral", size: "md" },
  },
);

const DOT_COLOR = {
  neutral: "bg-fg-subtle",
  accent: "bg-accent",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  signal: "bg-signal",
  outline: "bg-fg-subtle",
} as const;

export type BadgeTone = NonNullable<VariantProps<typeof badgeVariants>["tone"]>;

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {
  /** Leading status dot in the tone colour. */
  dot?: boolean;
  /** Animate the dot (live states). */
  pulse?: boolean;
  icon?: ReactNode;
}

export function Badge({ tone = "neutral", size, dot, pulse, icon, className, children, ...props }: BadgeProps) {
  const toneKey = tone ?? "neutral";
  return (
    <span className={cn(badgeVariants({ tone, size }), className)} {...props}>
      {dot ? (
        <span className="relative flex size-1.5 shrink-0" aria-hidden="true">
          {pulse ? <span className={cn("absolute inset-0 rounded-full animate-live-ring", DOT_COLOR[toneKey])} /> : null}
          <span className={cn("relative size-1.5 rounded-full", DOT_COLOR[toneKey])} />
        </span>
      ) : null}
      {icon}
      <span className="truncate">{children}</span>
    </span>
  );
}
