import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

/** Page heading for a journey step: one clear title, one plain sentence. */
export function StepHeader({ title, lede, aside, className }: { title: ReactNode; lede?: ReactNode; aside?: ReactNode; className?: string }) {
  return (
    <header className={cn("flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="max-w-2xl">
        <h1 className="text-title text-fg sm:text-[1.75rem] sm:leading-9">{title}</h1>
        {lede ? <p className="mt-2 text-[15px] leading-6 text-fg-muted">{lede}</p> : null}
      </div>
      {aside ? <div className="shrink-0">{aside}</div> : null}
    </header>
  );
}

/** Back (quiet) on the left, the one primary action on the right. */
export function StepFooter({ onBack, backLabel = "Back", children, note }: { onBack?: (() => void) | null; backLabel?: string; children: ReactNode; note?: ReactNode }) {
  return (
    <footer className="mt-10 border-t border-line pt-6">
      {note ? <div className="mb-4 text-[13px] leading-5 text-fg-muted">{note}</div> : null}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        {onBack ? (
          <Button variant="ghost" leftIcon={<ArrowLeft />} onClick={onBack} className="self-start">
            {backLabel}
          </Button>
        ) : (
          <span />
        )}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">{children}</div>
      </div>
    </footer>
  );
}

/** A labelled fact in a definition grid (duration, round, interviewer). */
export function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid content-start gap-1">
      <dt className="text-[13px] leading-5 text-fg-muted">{label}</dt>
      <dd className="text-[15px] leading-6 font-medium text-fg">{children}</dd>
    </div>
  );
}
