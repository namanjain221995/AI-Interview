import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, Radio, X } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

const calloutVariants = cva("relative flex gap-3 rounded-md border px-3.5 py-3 text-sm", {
  variants: {
    tone: {
      neutral: "border-neutral-line bg-neutral-soft",
      info: "border-accent-line bg-accent-soft",
      success: "border-success-line bg-success-soft",
      warning: "border-warning-line bg-warning-soft",
      danger: "border-danger-line bg-danger-soft",
      signal: "border-signal-line bg-signal-soft",
    },
  },
  defaultVariants: { tone: "info" },
});

const ICON_COLOR = {
  neutral: "text-fg-muted",
  info: "text-accent",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  signal: "text-signal",
} as const;

const DEFAULT_ICONS = {
  neutral: Info,
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: OctagonAlert,
  signal: Radio,
} as const;

export interface CalloutProps extends Omit<HTMLAttributes<HTMLDivElement>, "title">, VariantProps<typeof calloutVariants> {
  title?: ReactNode;
  /** Replace the tone icon; pass `null` for none. */
  icon?: ReactNode | null;
  /** Actions rendered under the body (buttons, links). */
  actions?: ReactNode;
  onDismiss?: () => void;
}

export function Callout({ tone = "info", title, icon, actions, onDismiss, className, children, role, ...props }: CalloutProps) {
  const toneKey = tone ?? "info";
  const IconComponent = DEFAULT_ICONS[toneKey];
  return (
    <div
      role={role ?? (toneKey === "danger" || toneKey === "warning" ? "alert" : "status")}
      className={cn(calloutVariants({ tone }), className)}
      {...props}
    >
      {icon === null ? null : (
        <span className={cn("mt-px shrink-0 [&_svg]:size-4", ICON_COLOR[toneKey])} aria-hidden="true">
          {icon ?? <IconComponent />}
        </span>
      )}
      <div className="min-w-0 flex-1">
        {title ? <p className="font-medium text-fg">{title}</p> : null}
        {children ? <div className={cn("text-fg-muted [&_a]:font-medium [&_a]:text-accent [&_a]:underline-offset-2 hover:[&_a]:underline", title ? "mt-0.5" : undefined)}>{children}</div> : null}
        {actions ? <div className="mt-2.5 flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="-mr-1 -mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-sm text-fg-subtle hover:bg-surface-hover hover:text-fg"
        >
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}
