import { Check } from "lucide-react";

import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import type { CodeLanguage } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface LanguagePickerProps {
  value: CodeLanguage[];
  onChange: (value: CodeLanguage[]) => void;
  options: readonly CodeLanguage[];
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
}

/** Toggle chips for programming languages (order follows `options`). */
export function LanguagePicker({ value, onChange, options, disabled, id, ...aria }: LanguagePickerProps) {
  const toggle = (language: CodeLanguage) => {
    const next = value.includes(language) ? value.filter((l) => l !== language) : [...value, language];
    onChange(options.filter((l) => next.includes(l)));
  };
  return (
    <div id={id} role="group" aria-label={aria["aria-label"] ?? "Languages"} aria-describedby={aria["aria-describedby"]} className="flex flex-wrap gap-1.5">
      {options.map((language) => {
        const on = value.includes(language);
        return (
          <button
            key={language}
            type="button"
            aria-pressed={on}
            disabled={disabled}
            onClick={() => toggle(language)}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-control border px-3 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              on ? "border-primary bg-primary text-primary-fg" : "border-line-strong bg-surface text-fg-muted hover:border-line-emphasis hover:text-fg",
            )}
          >
            {on ? <Check className="size-3.5" aria-hidden="true" /> : null}
            {CODE_LANGUAGE_LABELS[language]}
          </button>
        );
      })}
    </div>
  );
}
