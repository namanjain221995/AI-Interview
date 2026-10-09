import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Section({ id, title, description, children }: { id: string; title: string; description: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-24 border-t border-line pt-10 first:border-t-0 first:pt-0">
      <h2 id={`${id}-title`} className="text-heading text-fg">
        {title}
      </h2>
      <p className="mt-1 max-w-2xl text-sm text-fg-muted">{description}</p>
      <div className="mt-6 grid gap-5">{children}</div>
    </section>
  );
}

export function Specimen({
  title,
  note,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <div className={cn("overflow-hidden rounded-lg border border-line bg-surface", className)}>
      <div className="flex items-baseline justify-between gap-4 border-b border-line-subtle bg-surface-subtle px-4 py-2.5">
        <h3 className="text-[13px] font-medium text-fg">{title}</h3>
        {note ? <p className="text-xs text-fg-muted">{note}</p> : null}
      </div>
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </div>
  );
}

export function Row({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap items-center gap-3", className)}>{children}</div>;
}

export function Caption({ children }: { children: ReactNode }) {
  return <p className="text-xs text-fg-muted">{children}</p>;
}
