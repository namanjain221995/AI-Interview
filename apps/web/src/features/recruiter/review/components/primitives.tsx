/**
 * Small presentational pieces shared by the review screens.
 */

import type { UseQueryResult } from "@tanstack/react-query";
import { CircleAlert, Lock, SearchX } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tooltip } from "@/components/ui/Tooltip";
import { errorMessage, isApiError } from "@/lib/api";
import { formatPercent } from "@/lib/format";
import { revealInScroller, useOverflowEdges } from "@/lib/hooks/useOverflowEdges";
import { cn } from "@/lib/utils";

/** Coloured dot + competency name; the colour follows the round configuration order. */
export function CompetencyTag({ name, color, className, size = "md" }: { name: string; color: string; className?: string; size?: "sm" | "md" }) {
  return (
    <span className={cn("inline-flex max-w-full min-w-0 items-center gap-1.5 text-fg-muted", size === "sm" ? "text-xs" : "text-[13px]", className)}>
      <span className="size-2 shrink-0 rounded-full" style={{ background: color }} aria-hidden="true" />
      <span className="truncate">{name}</span>
    </span>
  );
}

/** A labelled fact in a definition grid. */
export function Fact({ label, children, hint, className }: { label: ReactNode; children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <div className={cn("grid min-w-0 content-start gap-0.5", className)}>
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="min-w-0 text-sm font-medium text-fg">{children}</dd>
      {hint ? <dd className="text-xs text-fg-subtle">{hint}</dd> : null}
    </div>
  );
}

export function FactGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cn("grid grid-cols-2 gap-x-6 gap-y-4", className)}>{children}</dl>;
}

/** "Strong evidence" style words for 0-1 evidence strength. */
export function strengthWord(strength: number | null | undefined): string {
  if (typeof strength !== "number" || Number.isNaN(strength)) return "Unrated";
  if (strength >= 0.7) return "Strong";
  if (strength >= 0.4) return "Moderate";
  return "Slight";
}

/** Three-step strength glyph plus the word, with the exact value on hover. */
export function StrengthMeter({ value, className }: { value: number | null | undefined; className?: string }) {
  const steps = typeof value !== "number" ? 0 : value >= 0.7 ? 3 : value >= 0.4 ? 2 : value > 0 ? 1 : 0;
  const word = strengthWord(value);
  return (
    <Tooltip content={typeof value === "number" ? `Evidence strength ${formatPercent(value)}` : "Strength not rated"}>
      <span tabIndex={0} className={cn("inline-flex items-center gap-1.5 rounded-xs text-xs text-fg-muted", className)} aria-label={`${word} evidence`}>
        <span className="flex items-center gap-[2px]" aria-hidden="true">
          {[1, 2, 3].map((step) => (
            <span key={step} className={cn("h-1.5 w-2.5 rounded-[1px]", step <= steps ? "bg-fg-muted" : "bg-line-strong")} />
          ))}
        </span>
        <span aria-hidden="true">{word}</span>
      </span>
    </Tooltip>
  );
}

/** Loading / error / forbidden states for a panel bound to one query. */
export function QueryPanel<T>({
  query,
  children,
  empty,
  isEmpty,
  skeleton,
  errorTitle = "Couldn't load this section",
  className,
}: {
  query: Pick<UseQueryResult<T>, "data" | "error" | "isPending" | "refetch" | "isError">;
  children: (data: T) => ReactNode;
  empty?: ReactNode;
  isEmpty?: (data: T) => boolean;
  skeleton?: ReactNode;
  errorTitle?: string;
  className?: string;
}) {
  if (query.isPending && query.data === undefined) {
    return <div className={className}>{skeleton ?? <PanelSkeleton />}</div>;
  }
  if (query.isError && query.data === undefined) {
    return (
      <div className={className}>
        <QueryError error={query.error} title={errorTitle} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  const data = query.data as T;
  if (empty && isEmpty?.(data)) return <div className={className}>{empty}</div>;
  return <>{children(data)}</>;
}

export function QueryError({ error, title = "Couldn't load this section", onRetry }: { error: unknown; title?: string; onRetry?: () => void }) {
  if (isApiError(error) && error.status === 403) {
    return <EmptyState size="sm" icon={<Lock />} title="You don't have access to this section" description="Ask an admin if you need it for your reviews." />;
  }
  if (isApiError(error) && error.status === 404) {
    return <EmptyState size="sm" icon={<SearchX />} title="Nothing here yet" description="This information hasn't been produced for the session." />;
  }
  return (
    <Callout
      tone="danger"
      icon={<CircleAlert />}
      title={title}
      actions={
        onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {errorMessage(error)}
      {isApiError(error) && error.requestId ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
    </Callout>
  );
}

export function PanelSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("grid gap-3 p-5", className)} aria-hidden="true">
      <Skeleton className="h-4 w-40" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" style={{ opacity: 1 - i * 0.15 }} />
      ))}
    </div>
  );
}

/** Small "+3 / -1" counter pair for supporting evidence vs gaps. */
export function SignalCounts({ positive, negative, className }: { positive: number; negative: number; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-xs tabular-nums", className)}>
      <span className="inline-flex items-center gap-1 text-success-fg" aria-label={`${positive} supporting`}>
        <span aria-hidden="true">+{positive}</span>
      </span>
      <span className="inline-flex items-center gap-1 text-danger-fg" aria-label={`${negative} gaps`}>
        <span aria-hidden="true">−{negative}</span>
      </span>
    </span>
  );
}

/** Weighted support-vs-gap bar (balance -1..1) with an accessible description. */
export function BalanceBar({ positive, negative, className }: { positive: number; negative: number; className?: string }) {
  const total = positive + negative;
  const pct = total > 0 ? (positive / total) * 100 : 0;
  return (
    <div
      className={cn("flex h-1.5 w-full overflow-hidden rounded-full bg-track", className)}
      role="img"
      aria-label={total > 0 ? `Weighted evidence: ${Math.round(pct)}% supporting, ${Math.round(100 - pct)}% gaps` : "No weighted evidence yet"}
    >
      {total > 0 ? (
        <>
          <span className="h-full bg-success" style={{ width: `${pct}%` }} />
          <span className="h-full w-[2px] bg-surface" />
          <span className="h-full flex-1 bg-danger" />
        </>
      ) : null}
    </div>
  );
}

/** Measures an element's width (for density decisions in custom charts). */
export function useElementWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Section heading used inside tab panels (h2 level under the page h1). */
export function SectionHeading({ title, description, actions, className, id }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string; id?: string }) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-3", className)}>
      <div className="grid grid-cols-1 min-w-0 gap-0.5">
        <h2 id={id} className="text-heading text-fg">
          {title}
        </h2>
        {description ? <p className="text-[13px] text-fg-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Compact single-choice switch (a radio group styled like segmented tabs). */
export function SegmentedControl<V extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: V;
  onChange: (value: V) => void;
  options: ReadonlyArray<{ value: V; label: ReactNode }>;
  label: string;
  className?: string;
}) {
  const groupRef = useRef<HTMLDivElement | null>(null);
  useOverflowEdges(groupRef);
  // Keep the selected option visible when the group scrolls on narrow screens.
  useEffect(() => {
    const group = groupRef.current;
    const checked = group?.querySelector<HTMLElement>('[aria-checked="true"]');
    if (group && checked) revealInScroller(group, checked);
  }, [value]);
  const move = (delta: number) => {
    const index = options.findIndex((o) => o.value === value);
    const next = options[(index + delta + options.length) % options.length];
    if (next) {
      onChange(next.value);
      // Roving focus: the newly checked radio takes focus, as native radio groups do.
      groupRef.current?.querySelectorAll<HTMLElement>('[role="radio"]')[options.indexOf(next)]?.focus();
    }
  };
  return (
    <div
      ref={groupRef}
      role="radiogroup"
      aria-label={label}
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-0.5 overflow-x-auto overflow-y-hidden rounded-control bg-surface-sunken/70 p-0.5 scrollbar-none edge-fade",
        className,
      )}
      onKeyDown={(e) => {
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          move(1);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          move(-1);
        }
      }}
    >
      {options.map((option) => {
        const checked = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-7 shrink-0 items-center justify-center rounded-[6px] px-3 text-[13px] font-medium whitespace-nowrap text-fg-muted transition-colors hover:text-fg focus-visible:outline-offset-[-2px]",
              checked && "bg-surface text-fg shadow-[0_1px_2px_rgb(16_24_40/0.08),0_0_0_1px_var(--border)]",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
