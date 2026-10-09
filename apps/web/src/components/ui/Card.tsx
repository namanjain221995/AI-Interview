import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const cardVariants = cva("relative flex flex-col rounded-lg text-fg", {
  variants: {
    variant: {
      default: "border border-line bg-surface shadow-[0_1px_2px_rgb(16_24_40/0.04)]",
      flat: "border border-line bg-surface",
      muted: "border border-line-subtle bg-surface-subtle",
      interactive:
        "border border-line bg-surface shadow-[0_1px_2px_rgb(16_24_40/0.04)] transition-[border-color,box-shadow] duration-150 hover:border-line-strong hover:shadow-raised",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface CardProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof cardVariants> {}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card({ className, variant, ...props }, ref) {
  return <div ref={ref} className={cn(cardVariants({ variant }), className)} {...props} />;
});

export interface CardHeaderProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  description?: ReactNode;
  /** Right-aligned controls (buttons, menus, segmented tabs). */
  action?: ReactNode;
}

export function CardHeader({ className, title, description, action, children, ...props }: CardHeaderProps) {
  return (
    // Wraps on narrow cards: the action moves under the title instead of squeezing it
    // into a one-word-per-line column (the title block keeps a 14rem flex basis).
    <div className={cn("flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pt-4 pb-0", className)} {...props}>
      {title || description ? (
        <div className="grid min-w-0 flex-[1_1_14rem] gap-0.5">
          {title ? <CardTitle>{title}</CardTitle> : null}
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
      ) : null}
      {children}
      {action ? <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{action}</div> : null}
    </div>
  );
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-[15px] leading-6 font-semibold tracking-[-0.006em] text-fg", className)} {...props} />;
}

export function CardDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-[13px] leading-5 text-fg-muted", className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("mt-auto flex items-center gap-2 rounded-b-lg border-t border-line-subtle bg-surface-subtle px-5 py-3", className)}
      {...props}
    />
  );
}
