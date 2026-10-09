import type { ReactNode } from "react";

import { BrandMark, Logo } from "@/components/brand/Logo";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";

/** Neutral splash while the app resolves the session or the first route chunk. */
export function AppSplash({ label = "Loading Interview Studio" }: { label?: string }) {
  return (
    <div className="flex h-dvh w-full flex-col items-center justify-center gap-5 bg-surface-muted" role="status" aria-label={label}>
      <BrandMark size={40} />
      <Spinner size="md" label={null} className="text-fg-subtle" />
    </div>
  );
}

export interface FullPageMessageProps {
  /** Short code or number shown above the title (e.g. 404). */
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/** Whole-page message for errors and not-found states outside the shells. */
export function FullPageMessage({ eyebrow, title, description, actions, icon, children, className }: FullPageMessageProps) {
  return (
    <div className={cn("flex min-h-dvh flex-col bg-surface-muted", className)}>
      <header className="px-6 py-5">
        <a href="/" className="inline-flex rounded-sm" aria-label="Interview Studio home">
          <Logo />
        </a>
      </header>
      <main className="flex flex-1 items-center justify-center px-6 pb-24">
        <div className="flex max-w-md flex-col items-center text-center">
          {icon ? (
            <div className="mb-5 flex size-12 items-center justify-center rounded-lg border border-line bg-surface text-fg-muted shadow-card [&_svg]:size-6" aria-hidden="true">
              {icon}
            </div>
          ) : null}
          {eyebrow ? <p className="mb-2 text-sm font-medium text-fg-subtle tabular-nums">{eyebrow}</p> : null}
          <h1 className="text-title text-fg">{title}</h1>
          {description ? <p className="mt-2 text-[15px] leading-6 text-fg-muted">{description}</p> : null}
          {children}
          {actions ? <div className="mt-7 flex flex-wrap items-center justify-center gap-2.5">{actions}</div> : null}
        </div>
      </main>
    </div>
  );
}
