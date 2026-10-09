import { Component, lazy, Suspense, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

const MonacoCodeEditor = lazy(() => import("./MonacoCodeEditor"));

export interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language: string;
  height?: number;
  readOnly?: boolean;
  ariaLabel: string;
  className?: string;
}

function PlainEditor({ value, onChange, height, readOnly, ariaLabel }: CodeEditorProps) {
  return (
    <textarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      readOnly={readOnly}
      aria-label={ariaLabel}
      spellCheck={false}
      style={{ height }}
      className="block w-full resize-y bg-surface-subtle px-3 py-2 font-mono text-[13px] leading-[21px] text-fg outline-none"
      onKeyDown={(event) => {
        if (event.key !== "Tab" || readOnly) return;
        event.preventDefault();
        const el = event.currentTarget;
        const start = el.selectionStart;
        const next = `${el.value.slice(0, start)}    ${el.value.slice(el.selectionEnd)}`;
        onChange(next);
        requestAnimationFrame(() => el.setSelectionRange(start + 4, start + 4));
      }}
    />
  );
}

/** Falls back to a plain textarea when the Monaco chunk can't load (offline, old browser). */
class EditorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** Code editor: Monaco (lazy, self-hosted) in the browser, a textarea in tests. */
export function CodeEditor(props: CodeEditorProps) {
  const { height = 280, className } = props;
  const plain = <PlainEditor {...props} height={height} />;
  return (
    <div className={cn("overflow-hidden rounded-control border border-line-strong focus-within:border-accent focus-within:ring-3 focus-within:ring-accent/15", className)}>
      {import.meta.env.MODE === "test" ? (
        plain
      ) : (
        <EditorBoundary fallback={plain}>
          <Suspense fallback={<Skeleton className="w-full rounded-none" style={{ height }} />}>
            <MonacoCodeEditor value={props.value} onChange={props.onChange} language={props.language} height={height} readOnly={props.readOnly} ariaLabel={props.ariaLabel} />
          </Suspense>
        </EditorBoundary>
      )}
    </div>
  );
}
