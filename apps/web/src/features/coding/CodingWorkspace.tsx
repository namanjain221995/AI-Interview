import { Lightbulb, Play, Send, Timer } from "lucide-react";
import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Markdown } from "@/components/ui/Markdown";
import { formatClock } from "@/lib/format";
import type { CodeLanguage, RunKind } from "@/lib/realtime/protocol";
import { cn } from "@/lib/utils";

import type { CodingView } from "@/features/realtime/store";

import {
  availableLanguages,
  currentCode,
  decidePaste,
  editBuffer,
  initialBuffers,
  LANGUAGE_LABELS,
  loadBuffers,
  saveBuffers,
  switchLanguage,
  type BufferState,
} from "./buffers";
import { formatValue, ResultsPanel } from "./ResultsPanel";

const CodeEditor = lazy(() => import("./CodeEditor"));

/**
 * The editor is a separate chunk loaded mid-interview; when it fails to load (flaky network, blocked CDN,
 * an old cached bundle) the coding round continues in a plain text area instead of a blank panel (S5).
 */
class EditorBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.warn("code editor unavailable; using the plain text fallback", error);
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

export const CODE_UPDATE_DEBOUNCE_MS = 800;

export interface CodingWorkspaceProps {
  coding: CodingView;
  remainingS: number | null;
  blockPaste: boolean;
  pasteThreshold: number;
  onCodeUpdate: (update: { code: string; language: CodeLanguage; version: number; cursorLine?: number }) => void;
  onRun: (kind: RunKind, code: string, language: CodeLanguage) => void;
  onSubmit: (code: string, language: CodeLanguage) => void;
  onHint: () => void;
  onLanguage: (language: CodeLanguage) => void;
  /** Size of a paste into the editor (never its content) and whether it was blocked. */
  onPaste: (length: number, lines: number, blocked: boolean) => void;
  /** Compact interviewer + subtitles shown beside the editor. */
  aside?: ReactNode;
  className?: string;
}

/** The prompt often repeats the title as its first heading; the panel already shows it. */
export function withoutLeadingTitle(markdown: string, title: string): string {
  const match = /^\s*#{1,3}\s+(.+?)\s*#*\s*(?:\n|$)/.exec(markdown);
  if (!match) return markdown;
  const normalise = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  return normalise(match[1]!) === normalise(title) ? markdown.slice(match[0].length).replace(/^\s+/, "") : markdown;
}

/** Problem statement text: code samples wrap in the narrow panel and carry no copy button. */
function ProblemText({ coding }: { coding: CodingView }) {
  const q = coding.question;
  return (
    <Markdown size="sm" wrapCode copyableCode={false}>
      {withoutLeadingTitle(q.prompt_markdown, q.title)}
    </Markdown>
  );
}

function ProblemPanel({ coding }: { coding: CodingView }) {
  const q = coding.question;
  return (
    <section aria-labelledby="problem-title" className="flex min-h-0 flex-col">
      <header className="shrink-0 border-b border-line px-5 py-3.5">
        <div className="flex items-center gap-2">
          <Badge size="sm" tone="outline" className="capitalize">
            {q.difficulty}
          </Badge>
        </div>
        <h2 id="problem-title" className="mt-2 text-heading text-fg">
          {q.title}
        </h2>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-5 py-4 scrollbar-thin">
        <ProblemText coding={coding} />
        {q.visible_tests?.length ? (
          <div className="mt-6">
            <h3 className="text-[13px] font-medium text-fg">Examples</h3>
            <ul className="mt-2 grid gap-2">
              {q.visible_tests.map((t, i) => (
                <li key={String(t.id ?? i)} className="rounded-md bg-surface-sunken px-3 py-2 font-mono text-xs leading-5">
                  <p className="text-fg-muted">
                    <span className="text-fg-subtle">input </span>
                    {formatValue(t.args ?? t.input ?? t.setup_sql ?? "", 220)}
                  </p>
                  <p className="text-fg">
                    <span className="text-fg-subtle">expected </span>
                    {formatValue(t.expected ?? t.expected_rows, 220)}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function HintsPanel({ coding, onHint }: { coding: CodingView; onHint: () => void }) {
  const ended = Boolean(coding.ended);
  return (
    <section aria-labelledby="hints-heading" className="grid content-start gap-3">
      <div className="flex items-center justify-between gap-2">
        <h3 id="hints-heading" className="text-[13px] font-medium text-fg">
          Hints
        </h3>
        <span data-testid="coding-hints-remaining" className="text-[13px] text-fg-muted tabular-nums">
          {`Hints remaining: ${coding.hintsRemaining}`}
        </span>
      </div>
      <Button
        size="sm"
        variant="outline"
        leftIcon={<Lightbulb />}
        onClick={onHint}
        disabled={ended || coding.hintsRemaining <= 0}
        loading={coding.hintPending}
        loadingText="Asking for a hint"
        data-testid="coding-hint-button"
      >
        Request a hint
      </Button>
      <p className="text-xs leading-[1.125rem] text-fg-subtle">Hints are fine to use. Each one gives a little more direction; the number you used is noted for the reviewers.</p>
      {coding.hints.length ? (
        <ol className="grid gap-2">
          {coding.hints.map((hint) => (
            <li key={hint.level} data-testid={`coding-hint-${hint.level}`} className="animate-float-in rounded-md border border-accent-line bg-accent-soft px-3 py-2.5">
              <p className="text-xs font-medium text-accent-fg">Hint {hint.level}</p>
              <p className="mt-0.5 text-[13px] leading-5 text-fg">{hint.text}</p>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

/** The coding exercise: problem, editor with per-language buffers, tests, hints. */
export function CodingWorkspace({ coding, remainingS, blockPaste, pasteThreshold, onCodeUpdate, onRun, onSubmit, onHint, onLanguage, onPaste, aside, className }: CodingWorkspaceProps) {
  const question = coding.question;
  const languages = useMemo(() => availableLanguages(question), [question]);
  const [state, setState] = useState<BufferState>(() =>
    initialBuffers(coding.sessionId, question, coding.language, coding.initialCode, loadBuffers(coding.sessionId)),
  );
  const [pasteNotice, setPasteNotice] = useState<string | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const cursorLine = useRef<number | undefined>(undefined);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const ended = Boolean(coding.ended);

  // A different exercise (or a reconnect that resumed another one) resets the buffers.
  useEffect(() => {
    if (state.codingSessionId !== coding.sessionId) {
      setState(initialBuffers(coding.sessionId, question, coding.language, coding.initialCode, loadBuffers(coding.sessionId)));
    }
  }, [coding.sessionId, coding.language, coding.initialCode, question, state.codingSessionId]);

  const flush = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = null;
    const s = stateRef.current;
    onCodeUpdate({ code: currentCode(s), language: s.language, version: s.version, cursorLine: cursorLine.current });
  }, [onCodeUpdate]);

  useEffect(() => () => {
    if (debounce.current) clearTimeout(debounce.current);
  }, []);

  const onChange = (code: string) => {
    setState((prev) => {
      const next = editBuffer(prev, code);
      if (next !== prev) saveBuffers(next);
      return next;
    });
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(flush, CODE_UPDATE_DEBOUNCE_MS);
  };

  const changeLanguage = (language: CodeLanguage) => {
    if (debounce.current) flush();
    const next = switchLanguage(stateRef.current, question, language);
    stateRef.current = next;
    setState(next);
    saveBuffers(next);
    onLanguage(language);
    onCodeUpdate({ code: currentCode(next), language, version: next.version });
  };

  // Paste/drop interception inside the editor (capture phase, before Monaco).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onPasteEvent = (event: ClipboardEvent) => {
      const text = event.clipboardData?.getData("text") ?? "";
      const decision = decidePaste(text.length, { threshold: pasteThreshold, block: blockPaste });
      onPaste(text.length, text ? text.split(/\r\n|\r|\n/).length : 0, !decision.allow);
      if (!decision.allow) {
        event.preventDefault();
        event.stopPropagation();
        setPasteNotice("Pasting is turned off for this exercise. Type your solution instead.");
      } else if (decision.large) {
        setPasteNotice(null);
      }
    };
    const onDrop = (event: DragEvent) => {
      if (!blockPaste) return;
      event.preventDefault();
      event.stopPropagation();
      setPasteNotice("Dropping text into the editor is turned off for this exercise.");
    };
    el.addEventListener("paste", onPasteEvent, true);
    el.addEventListener("drop", onDrop, true);
    return () => {
      el.removeEventListener("paste", onPasteEvent, true);
      el.removeEventListener("drop", onDrop, true);
    };
  }, [blockPaste, pasteThreshold, onPaste]);

  useEffect(() => {
    if (!pasteNotice) return;
    const t = setTimeout(() => setPasteNotice(null), 6000);
    return () => clearTimeout(t);
  }, [pasteNotice]);

  const busy = coding.pendingRun !== null;
  const lowTime = remainingS !== null && remainingS <= 120;

  return (
    <div
      data-testid="coding-workspace"
      className={cn(
        "grid h-full min-h-0 grid-cols-1 content-start gap-px overflow-y-auto bg-line lg:content-stretch lg:grid-cols-[minmax(280px,0.85fr)_minmax(0,2fr)_minmax(240px,0.7fr)] lg:overflow-hidden",
        className,
      )}
    >
      <div className="hidden min-h-0 bg-surface lg:flex lg:flex-col">
        <ProblemPanel coding={coding} />
      </div>
      {/* Narrow screens: the statement comes first (open), then the editor, then the interviewer. */}
      <details open className="bg-surface px-4 py-3 lg:hidden">
        <summary className="cursor-pointer text-sm font-medium text-fg">
          Problem: {question.title}
        </summary>
        <div className="mt-3">
          <ProblemText coding={coding} />
        </div>
      </details>

      <div className="flex min-h-[75vh] flex-col bg-surface-sunken lg:min-h-0">
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-3 py-2">
          <div className="flex items-center gap-2">
            <label htmlFor="coding-language" className="sr-only">
              Language
            </label>
            <select
              id="coding-language"
              data-testid="coding-language-select"
              value={state.language}
              disabled={ended}
              onChange={(e) => changeLanguage(e.target.value as CodeLanguage)}
              className="h-8 rounded-control border border-line-strong bg-surface px-2.5 text-[13px] font-medium text-fg hover:border-line-emphasis focus-visible:outline-2 focus-visible:outline-focus disabled:opacity-60"
            >
              {languages.map((l) => (
                <option key={l} value={l}>
                  {LANGUAGE_LABELS[l]}
                </option>
              ))}
            </select>
            {remainingS !== null ? (
              <span className={cn("flex items-center gap-1.5 text-[13px] tabular-nums", lowTime ? "text-warning-fg" : "text-fg-muted")} aria-label={`Coding time left ${formatClock(remainingS)}`}>
                <Timer className="size-3.5" aria-hidden="true" />
                {formatClock(remainingS)}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              leftIcon={<Play />}
              disabled={ended || busy}
              loading={coding.pendingRun === "test"}
              loadingText="Running"
              onClick={() => {
                flush();
                onRun("test", currentCode(stateRef.current), stateRef.current.language);
              }}
              data-testid="coding-run-tests"
            >
              Run tests
            </Button>
            <Button
              size="sm"
              leftIcon={<Send />}
              disabled={ended || busy}
              loading={coding.pendingRun === "submit"}
              loadingText="Submitting"
              onClick={() => {
                // The test-id contract has no confirm step for submission; the label says exactly what happens.
                flush();
                onSubmit(currentCode(stateRef.current), stateRef.current.language);
              }}
              data-testid="coding-submit"
            >
              {ended ? "Submitted" : "Submit solution"}
            </Button>
          </div>
        </div>

        <div ref={containerRef} data-testid="coding-editor" data-coding-editor="" className="relative min-h-[240px] flex-[3]">
          <EditorBoundary
            fallback={
              <div className="flex size-full flex-col" data-testid="coding-editor-fallback">
                <p role="status" className="border-b border-warning-line bg-warning-soft px-3 py-1.5 text-[13px] text-fg">
                  The code editor couldn't load. You can still write your code here; it is saved and run the same way.
                </p>
                <textarea
                  // One textarea per session and language, like the editor's model path: a language switch shows
                  // that language's buffer instead of keeping the previous code on screen.
                  key={`${coding.sessionId}/${state.language}`}
                  aria-label="Your code"
                  className="min-h-0 flex-1 resize-none bg-transparent p-3 font-mono text-sm leading-6 text-fg outline-none"
                  defaultValue={state.buffers[state.language] ?? ""}
                  readOnly={ended}
                  spellCheck={false}
                  onChange={(event) => onChange(event.target.value)}
                />
              </div>
            }
          >
            <Suspense fallback={<div className="flex size-full items-center justify-center text-sm text-fg-muted">Loading the editor…</div>}>
              <CodeEditor
                path={`${coding.sessionId}/${state.language}`}
                language={state.language}
                defaultValue={state.buffers[state.language] ?? ""}
                readOnly={ended}
                onChange={onChange}
                onCursorLine={(line) => (cursorLine.current = line)}
              />
            </Suspense>
          </EditorBoundary>
          {pasteNotice ? (
            <div role="status" className="absolute right-3 bottom-3 left-3 rounded-md border border-warning-line bg-surface-raised px-3 py-2 text-[13px] text-fg shadow-overlay">
              {pasteNotice}
            </div>
          ) : null}
        </div>

        <ResultsPanel result={coding.lastRun} pending={coding.pendingRun} className="min-h-[140px] flex-[2] border-t border-line bg-surface" />
      </div>

      <aside className="flex min-h-0 flex-col gap-4 overflow-auto bg-surface p-4 scrollbar-thin">
        {aside}
        <HintsPanel coding={coding} onHint={onHint} />
      </aside>
    </div>
  );
}
