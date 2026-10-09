import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

import { fieldBase } from "./Input";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Grow with the content up to `maxRows`. */
  autoResize?: boolean;
  maxRows?: number;
  /** Show "used / maxLength" under the field (requires maxLength). */
  showCount?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, autoResize = false, maxRows = 12, showCount = false, rows = 4, onChange, value, defaultValue, maxLength, ...props },
  ref,
) {
  const innerRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => innerRef.current as HTMLTextAreaElement);

  const resize = useCallback(() => {
    const el = innerRef.current;
    if (!el || !autoResize) return;
    el.style.height = "auto";
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const maxHeight = lineHeight * maxRows + 18;
    el.style.height = `${Math.min(el.scrollHeight + 2, maxHeight)}px`;
    el.style.overflowY = el.scrollHeight + 2 > maxHeight ? "auto" : "hidden";
  }, [autoResize, maxRows]);

  useEffect(() => {
    resize();
  }, [resize, value]);

  const [uncontrolledLength, setUncontrolledLength] = useState(() => (typeof defaultValue === "string" ? defaultValue.length : 0));
  const length = typeof value === "string" ? value.length : uncontrolledLength;

  return (
    <div className="w-full">
      <textarea
        ref={innerRef}
        rows={rows}
        value={value}
        defaultValue={defaultValue}
        maxLength={maxLength}
        onChange={(event) => {
          onChange?.(event);
          if (value === undefined) setUncontrolledLength(event.target.value.length);
          resize();
        }}
        className={cn(fieldBase, "block min-h-[4.5rem] px-3 py-2 text-sm leading-6", autoResize ? "resize-none" : "resize-y", className)}
        {...props}
      />
      {showCount && maxLength ? (
        <p className="mt-1 text-right text-xs text-fg-subtle tabular-nums" aria-live="polite">
          {length} / {maxLength}
        </p>
      ) : null}
    </div>
  );
});
