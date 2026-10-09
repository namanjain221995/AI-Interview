import { useMemo, useState } from "react";

import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { collapseUnchanged, diffLines, diffStats } from "../lib/diff";

export interface CodeDiffProps {
  before: string;
  after: string;
  /** Lines of unchanged context around each change (collapsed runs can be expanded). */
  context?: number;
  maxHeight?: string;
  className?: string;
  beforeLabel?: string;
  afterLabel?: string;
}

/** Unified line diff with line numbers on both sides. */
export function CodeDiff({ before, after, context = 3, maxHeight = "28rem", className, beforeLabel = "Previous", afterLabel = "This snapshot" }: CodeDiffProps) {
  const [expanded, setExpanded] = useState(false);
  const lines = useMemo(() => diffLines(before, after), [before, after]);
  const stats = useMemo(() => diffStats(lines), [lines]);
  const display = useMemo(() => (expanded ? lines : collapseUnchanged(lines, context)), [lines, expanded, context]);
  const unchanged = stats.added === 0 && stats.removed === 0;

  return (
    <div className={cn("overflow-hidden rounded-md border border-line bg-surface", className)}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-subtle bg-surface-subtle px-3 py-2 text-xs text-fg-muted">
        <span>
          {beforeLabel} → {afterLabel}
        </span>
        <span className="flex items-center gap-3 tabular-nums">
          {unchanged ? (
            "No changes"
          ) : (
            <>
              <span className="text-success-fg">+{pluralize(stats.added, "line")}</span>
              <span className="text-danger-fg">−{pluralize(stats.removed, "line")}</span>
            </>
          )}
          {!unchanged && lines.length > display.length ? (
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => setExpanded(true)}>
              Show all lines
            </button>
          ) : expanded ? (
            <button type="button" className="font-medium text-accent hover:underline" onClick={() => setExpanded(false)}>
              Collapse unchanged
            </button>
          ) : null}
        </span>
      </div>
      <div className="overflow-auto scrollbar-thin" style={{ maxHeight }}>
        <table className="w-full border-collapse font-mono text-[12.5px] leading-5" aria-label="Code changes">
          <tbody>
            {display.map((line, i) => {
              if (line.kind === "gap") {
                return (
                  <tr key={`gap-${i}`}>
                    <td colSpan={4} className="bg-surface-subtle px-3 py-1 text-center font-sans text-xs text-fg-subtle">
                      <button type="button" className="hover:text-fg hover:underline" onClick={() => setExpanded(true)}>
                        {pluralize(line.count, "unchanged line")}
                      </button>
                    </td>
                  </tr>
                );
              }
              const tone = line.kind === "add" ? "bg-success-soft" : line.kind === "del" ? "bg-danger-soft" : "";
              const sign = line.kind === "add" ? "+" : line.kind === "del" ? "−" : " ";
              return (
                <tr key={i} className={tone}>
                  <td className="w-10 border-r border-line-subtle px-2 text-right text-fg-subtle select-none tabular-nums">{line.oldNumber ?? ""}</td>
                  <td className="w-10 border-r border-line-subtle px-2 text-right text-fg-subtle select-none tabular-nums">{line.newNumber ?? ""}</td>
                  <td
                    className={cn("w-5 text-center select-none", line.kind === "add" ? "text-success-fg" : line.kind === "del" ? "text-danger-fg" : "text-fg-subtle")}
                    aria-label={line.kind === "add" ? "added" : line.kind === "del" ? "removed" : undefined}
                  >
                    {sign}
                  </td>
                  <td className="pr-4 whitespace-pre text-fg">{line.text || " "}</td>
                </tr>
              );
            })}
            {display.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-center font-sans text-sm text-fg-muted">Empty file</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
