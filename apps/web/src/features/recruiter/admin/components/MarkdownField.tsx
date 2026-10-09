import { useState } from "react";

import { Markdown } from "@/components/ui/Markdown";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { cn } from "@/lib/utils";

export interface MarkdownFieldProps {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  placeholder?: string;
  rows?: number;
  maxLength?: number;
  /** Side-by-side on wide screens instead of Write/Preview tabs. */
  split?: boolean;
  className?: string;
  "aria-invalid"?: boolean;
  "aria-describedby"?: string;
}

/** Markdown textarea with a rendered preview (same renderer candidates see). */
export function MarkdownField({ value, onChange, id, placeholder, rows = 12, maxLength, split, className, ...aria }: MarkdownFieldProps) {
  const [mode, setMode] = useState<"write" | "preview">("write");
  const editor = (
    <Textarea
      id={id}
      rows={rows}
      value={value}
      maxLength={maxLength}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className="font-mono text-[13px] leading-6"
      {...aria}
    />
  );
  const preview = (
    <div className={cn("min-h-40 overflow-y-auto rounded-control border border-line bg-surface-subtle/60 px-4 py-3 scrollbar-thin", split && "max-h-[32rem]")} aria-label="Preview">
      {value.trim() ? <Markdown>{value}</Markdown> : <p className="text-sm text-fg-subtle">Nothing to preview yet.</p>}
    </div>
  );

  if (split) {
    return (
      <div className={cn("grid gap-3 lg:grid-cols-2", className)}>
        {editor}
        <div className="max-lg:hidden">{preview}</div>
      </div>
    );
  }

  return (
    <div className={cn("grid gap-2", className)}>
      <div className="flex items-center justify-between gap-3">
        <Tabs value={mode} onValueChange={(v) => setMode(v as "write" | "preview")} variant="segmented">
          <TabsList aria-label="Editor mode">
            <TabsTrigger value="write">Write</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="text-xs text-fg-subtle">Markdown supported</span>
      </div>
      {mode === "write" ? editor : preview}
    </div>
  );
}
