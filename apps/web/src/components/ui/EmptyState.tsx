import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  /** Primary and secondary actions. */
  actions?: ReactNode;
  /** Dashed frame for empty regions inside a page (not needed inside cards/tables). */
  bordered?: boolean;
  size?: "sm" | "md";
  className?: string;
  children?: ReactNode;
}

export function EmptyState({ icon, title, description, actions, bordered, size = "md", className, children }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        size === "md" ? "gap-1 px-6 py-12" : "gap-0.5 px-4 py-8",
        bordered && "rounded-lg border border-dashed border-line-strong bg-surface-subtle/60",
        className,
      )}
    >
      {icon ? (
        <div
          className={cn(
            "mb-3 flex items-center justify-center rounded-md border border-line bg-surface text-fg-muted shadow-[0_1px_2px_rgb(16_24_40/0.05)]",
            size === "md" ? "size-10 [&_svg]:size-5" : "size-8 [&_svg]:size-4",
          )}
          aria-hidden="true"
        >
          {icon}
        </div>
      ) : null}
      <h3 className={cn("font-semibold text-fg", size === "md" ? "text-[15px] leading-6" : "text-sm")}>{title}</h3>
      {description ? <p className="max-w-sm text-sm text-fg-muted">{description}</p> : null}
      {children}
      {actions ? <div className="mt-4 flex flex-wrap items-center justify-center gap-2">{actions}</div> : null}
    </div>
  );
}
