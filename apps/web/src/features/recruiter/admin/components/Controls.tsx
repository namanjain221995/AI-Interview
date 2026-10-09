/** List-page controls and small display pieces shared by the admin pages. */

import { Check, Copy, Search, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Input } from "@/components/ui/Input";
import { Select, type SelectOption } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { scoreTone, TONE_BG } from "@/components/domain/scoreBands";
import { errorMessage, isApiError } from "@/lib/api";
import { formatScore } from "@/lib/format";
import { useOverflowEdges } from "@/lib/hooks/useOverflowEdges";
import type { BankStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import { copyText } from "../lib/utils";

// ------------------------------------------------------------- SearchInput

export interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
  /** Trailing content inside the field (e.g. a mode switch). */
  trailing?: ReactNode;
  autoFocus?: boolean;
}

export function SearchInput({ value, onChange, placeholder = "Search", className, trailing, autoFocus, ...aria }: SearchInputProps) {
  return (
    <Input
      type="text"
      role="searchbox"
      inputMode="search"
      enterKeyHint="search"
      value={value}
      placeholder={placeholder}
      autoFocus={autoFocus}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Escape" && value) {
          event.preventDefault();
          onChange("");
        }
      }}
      leading={<Search />}
      trailing={
        trailing ??
        (value ? (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => onChange("")}
            className="flex size-6 items-center justify-center rounded-sm text-fg-subtle hover:bg-surface-hover hover:text-fg"
          >
            <X className="size-3.5" />
          </button>
        ) : null)
      }
      wrapperClassName={className}
      aria-label={aria["aria-label"] ?? placeholder}
    />
  );
}

// ------------------------------------------------------------ FilterSelect

export const ALL = "__all__";

export interface FilterSelectProps<V extends string> {
  value: V | undefined;
  onChange: (value: V | undefined) => void;
  options: readonly SelectOption<V>[];
  allLabel: string;
  label: string;
  className?: string;
}

/** Select with an "All …" choice mapped to `undefined` (Radix forbids empty values). */
export function FilterSelect<V extends string>({ value, onChange, options, allLabel, label, className }: FilterSelectProps<V>) {
  const all: SelectOption<string> = { value: ALL, label: allLabel };
  return (
    <Select<string>
      aria-label={label}
      size="md"
      value={value ?? ALL}
      onValueChange={(next) => onChange(next === ALL ? undefined : (next as V))}
      options={[all, ...(options as readonly SelectOption<string>[])]}
      className={cn("w-auto min-w-36", className)}
    />
  );
}

// --------------------------------------------------------- SegmentedFilter

export interface SegmentedFilterProps<V extends string> {
  value: V;
  onChange: (value: V) => void;
  options: ReadonlyArray<{ value: V; label: string; count?: number }>;
  label: string;
  className?: string;
}

/**
 * Status tabs with counts ("Open 12"). A radio group for assistive tech. Scrolls
 * sideways on narrow screens (no scrollbar, edge fade on the side that hides options).
 */
export function SegmentedFilter<V extends string>({ value, onChange, options, label, className }: SegmentedFilterProps<V>) {
  const groupRef = useRef<HTMLDivElement | null>(null);
  useOverflowEdges(groupRef);
  return (
    <div
      ref={groupRef}
      role="radiogroup"
      aria-label={label}
      className={cn(
        "inline-flex w-fit max-w-full items-center gap-0.5 overflow-x-auto overflow-y-hidden rounded-control bg-surface-sunken/70 p-0.5 scrollbar-none edge-fade",
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-[6px] px-3 text-[13px] font-medium whitespace-nowrap transition-colors focus-visible:outline-offset-[-2px]",
              active ? "bg-surface text-fg shadow-[0_1px_2px_rgb(16_24_40/0.08),0_0_0_1px_var(--border)]" : "text-fg-muted hover:text-fg",
            )}
          >
            {option.label}
            {option.count !== undefined ? <span className={cn("tabular-nums text-xs", active ? "text-fg-muted" : "text-fg-subtle")}>{option.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------- CopyField

export interface SecretFieldProps {
  label: string;
  value: string;
  /** data-testid for the element whose text is the secret. */
  testId?: string;
  /** Monospace, larger text for short codes. */
  emphasis?: boolean;
  onCopied?: () => void;
}

/** Read-only value with a copy button; the value element's text is exactly `value`. */
export function SecretField({ label, value, testId, emphasis, onCopied }: SecretFieldProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = async () => {
    const ok = await copyText(value);
    if (!ok) return;
    setCopied(true);
    onCopied?.();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1800);
  };

  return (
    <div className="grid gap-1.5">
      <span className="text-[13px] font-medium text-fg">{label}</span>
      <div className="flex items-stretch overflow-hidden rounded-control border border-line-strong bg-surface-subtle">
        <code
          data-testid={testId}
          className={cn(
            "min-w-0 flex-1 px-3 py-2 font-mono break-all text-fg select-all",
            emphasis ? "text-lg leading-7 tracking-[0.18em]" : "text-[13px] leading-5",
          )}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={copy}
          aria-label={copied ? `${label} copied` : `Copy ${label.toLowerCase()}`}
          className="flex shrink-0 items-center gap-1.5 border-l border-line-strong bg-surface px-3 text-xs font-medium text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
        >
          {copied ? <Check className="size-3.5 text-success" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------- BankStatusBadge

const BANK_STATUS: Record<BankStatus, { tone: BadgeTone; label: string }> = {
  active: { tone: "success", label: "Active" },
  draft: { tone: "neutral", label: "Draft" },
  retired: { tone: "outline", label: "Retired" },
};

export function BankStatusBadge({ status, size = "sm" }: { status: BankStatus; size?: "sm" | "md" }) {
  const style = BANK_STATUS[status] ?? { tone: "neutral" as BadgeTone, label: status };
  return (
    <Badge tone={style.tone} size={size} dot>
      {style.label}
    </Badge>
  );
}

// -------------------------------------------------------------- page states

export function LoadError({ error, onRetry, title = "Couldn't load this page" }: { error: unknown; onRetry?: () => void; title?: string }) {
  const notFound = isApiError(error) && error.isNotFound;
  return (
    <Callout
      tone={notFound ? "neutral" : "danger"}
      title={notFound ? "This item doesn't exist or was removed" : title}
      actions={
        onRetry && !notFound ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {notFound ? "Check the link, or go back to the list." : errorMessage(error)}
      {isApiError(error) && error.requestId && !notFound ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
    </Callout>
  );
}

export function DetailSkeleton() {
  return (
    <div className="grid gap-6" aria-busy="true" aria-label="Loading">
      <div className="grid gap-2">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <Skeleton className="h-28 w-full" />
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-64 lg:col-span-2" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}

/** Definition-list row for fact panels. */
export function Fact({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-[minmax(7rem,38%)_1fr] items-baseline gap-3 py-2 text-sm", className)}>
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 text-fg">{children}</dd>
    </div>
  );
}

// --------------------------------------------------------------- ScoreCell

/** Compact 0-100 score for table cells: the number first, a thin tinted bar after. */
export function ScoreCell({ value, max = 100 }: { value: number | null | undefined; max?: 10 | 100 }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className="text-fg-subtle">—</span>;
  const tone = scoreTone(value, max);
  return (
    <span className="inline-flex items-center gap-2" aria-label={`${formatScore(value, max)} out of ${max}`}>
      <span className="w-7 text-right text-sm font-semibold text-fg tabular-nums">{formatScore(value, max)}</span>
      <span className="relative h-1.5 w-14 rounded-full bg-track" aria-hidden="true">
        <span className={cn("absolute inset-y-0 left-0 rounded-full", TONE_BG[tone])} style={{ width: `${Math.min(100, Math.max(0, (value / max) * 100))}%` }} />
      </span>
    </span>
  );
}
