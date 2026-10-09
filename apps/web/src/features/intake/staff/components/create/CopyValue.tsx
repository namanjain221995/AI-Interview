import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

import { copyText } from "@/features/recruiter/admin/lib/utils";

export interface CopyValueProps {
  label: string;
  value: string;
  valueTestId?: string;
  copyTestId?: string;
  /** Large spaced monospace for short codes. */
  emphasis?: boolean;
  onCopied?: () => void;
  className?: string;
}

/** Read-only secret with a copy button. The value element's text is exactly `value`. */
export function CopyValue({ label, value, valueTestId, copyTestId, emphasis, onCopied, className }: CopyValueProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  const copy = async () => {
    if (!(await copyText(value))) return;
    setCopied(true);
    onCopied?.();
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1800);
  };
  return (
    <div className={cn("grid grid-cols-1 gap-1.5", className)}>
      <span className="text-[13px] font-medium text-fg">{label}</span>
      <div className="flex items-stretch overflow-hidden rounded-control border border-line-strong bg-surface-subtle">
        <code
          data-testid={valueTestId}
          className={cn("min-w-0 flex-1 px-3 py-2 font-mono break-all text-fg select-all", emphasis ? "text-lg leading-7 tracking-[0.18em]" : "text-[13px] leading-5")}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          data-testid={copyTestId}
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
