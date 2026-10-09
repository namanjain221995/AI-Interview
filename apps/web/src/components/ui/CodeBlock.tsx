import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import { cn } from "@/lib/utils";

export interface CodeBlockProps {
  code: string;
  language?: string | null;
  /** Shown in the header instead of the language name (e.g. a file name). */
  title?: string;
  showLineNumbers?: boolean;
  copyable?: boolean;
  wrap?: boolean;
  /** Max height before the block scrolls (CSS length). */
  maxHeight?: string;
  className?: string;
}

function languageLabel(language: string | null | undefined): string | null {
  if (!language) return null;
  const key = language.toLowerCase();
  return (CODE_LANGUAGE_LABELS as Record<string, string>)[key] ?? language;
}

export function CodeBlock({ code, language, title, showLineNumbers = false, copyable = true, wrap = false, maxHeight, className }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  const lines = code.replace(/\n$/, "").split("\n");
  const header = title ?? languageLabel(language);

  return (
    // contain-inline: long lines scroll inside the block instead of widening the parent
    // (a <pre> in a grid/flex column used to push the column over its neighbour).
    <div className={cn("group/code relative min-w-0 overflow-hidden rounded-md border border-line bg-surface-subtle contain-inline", className)}>
      {header || copyable ? (
        <div className="flex h-9 items-center justify-between border-b border-line-subtle pr-1.5 pl-3.5">
          <span className="text-xs font-medium text-fg-muted">{header}</span>
          {copyable ? (
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy code"}
              className="flex h-7 items-center gap-1.5 rounded-sm px-2 text-xs font-medium text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
            >
              {copied ? <Check className="size-3.5 text-success" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
              {copied ? "Copied" : "Copy"}
            </button>
          ) : null}
        </div>
      ) : null}
      <pre
        className={cn("overflow-auto py-3 font-mono text-[13px] leading-6 text-fg scrollbar-thin", wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre")}
        style={maxHeight ? { maxHeight } : undefined}
        tabIndex={0}
      >
        <code className="block min-w-fit">
          {showLineNumbers
            ? lines.map((line, index) => (
                <span key={index} className="flex">
                  <span className="sticky left-0 inline-block w-10 shrink-0 bg-surface-subtle pr-3 text-right text-fg-subtle select-none" aria-hidden="true">
                    {index + 1}
                  </span>
                  <span className="pr-4">{line || " "}</span>
                </span>
              ))
            : <span className="block px-4">{code}</span>}
        </code>
      </pre>
    </div>
  );
}
