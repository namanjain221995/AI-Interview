import { Check, Loader2, X } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { CodingRunResultMsg, RunKind, RunStatus } from "@/lib/realtime/protocol";
import { cn } from "@/lib/utils";

export function formatValue(value: unknown, max = 160): string {
  if (value === undefined) return "—";
  let text: string;
  try {
    text = typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value) ?? String(value);
  } catch {
    text = String(value);
  }
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

const STATUS: Record<RunStatus, { label: string; tone: BadgeTone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "accent" },
  passed: { label: "All tests passed", tone: "success" },
  failed: { label: "Some tests failed", tone: "warning" },
  error: { label: "Runtime error", tone: "danger" },
  compile_error: { label: "Didn't compile", tone: "danger" },
  timeout: { label: "Timed out", tone: "warning" },
  memory_exceeded: { label: "Memory limit exceeded", tone: "warning" },
  rejected: { label: "Couldn't run", tone: "neutral" },
};

interface VisibleTest {
  id: string;
  passed: boolean;
  input: unknown;
  expected: unknown;
  actual: unknown;
  error: string | null;
}

export function visibleTests(result: CodingRunResultMsg): VisibleTest[] {
  return (result.tests ?? [])
    .filter((t) => t && t.hidden !== true)
    .map((t, i) => ({
      id: String(t.name ?? t.id ?? `Test ${i + 1}`),
      passed: t.passed === true,
      input: t.args ?? t.input ?? t.stdin,
      expected: t.expected ?? t.expected_rows,
      actual: t.actual ?? t.actual_rows,
      error: typeof t.error === "string" && t.error ? t.error : null,
    }));
}

/** Hidden tests are only ever shown as counts. */
export function hiddenCounts(result: CodingRunResultMsg): { passed: number; total: number } | null {
  const visible = visibleTests(result);
  const hiddenTotal = Math.max(0, (result.total ?? 0) - visible.length);
  if (hiddenTotal === 0) return null;
  const visiblePassed = visible.filter((t) => t.passed).length;
  return { passed: Math.max(0, Math.min(hiddenTotal, (result.passed ?? 0) - visiblePassed)), total: hiddenTotal };
}

export function ResultsPanel({ result, pending, className }: { result: CodingRunResultMsg | null; pending: RunKind | null; className?: string }) {
  const status = pending ? "running" : (result?.status ?? "idle");
  return (
    <section data-testid="coding-results" data-status={status} aria-label="Test results" aria-live="polite" className={cn("flex min-h-0 flex-col", className)}>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <h3 className="text-[13px] font-medium text-fg">Results</h3>
        {pending ? (
          <span className="flex items-center gap-1.5 text-[13px] text-fg-muted">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            {pending === "submit" ? "Submitting and running all tests…" : "Running your code…"}
          </span>
        ) : result ? (
          <div className="flex items-center gap-2">
            {result.total > 0 && !result.platform_fault ? (
              <span className="text-[13px] text-fg-muted tabular-nums">
                {result.passed}/{result.total} passed
              </span>
            ) : null}
            {result.runtime_ms !== null && result.runtime_ms !== undefined ? <span className="text-[13px] text-fg-subtle tabular-nums">{result.runtime_ms} ms</span> : null}
            <Badge size="sm" tone={result.platform_fault ? "neutral" : (STATUS[result.status]?.tone ?? "neutral")}>
              {result.platform_fault ? "Couldn't run" : (STATUS[result.status]?.label ?? result.status)}
            </Badge>
          </div>
        ) : null}
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-4 py-3 scrollbar-thin">
        {!result && !pending ? <p className="text-[13px] text-fg-muted">Run the tests to check your solution against the examples. Hidden tests run when you submit.</p> : null}
        {result ? <ResultBody result={result} /> : null}
      </div>
    </section>
  );
}

function ResultBody({ result }: { result: CodingRunResultMsg }) {
  const tests = visibleTests(result);
  const hidden = hiddenCounts(result);
  if (result.platform_fault) {
    // The code runner failed, not the candidate's code (S20): neutral, and nothing was submitted.
    return (
      <p data-testid="coding-platform-fault" className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[13px] leading-5 text-fg">
        We couldn't run your code just now. That's on our side, not your code, and nothing was submitted. Try again in a moment.
      </p>
    );
  }
  return (
    <div className="grid gap-3">
      {result.compile_output ? (
        <div>
          <p className="text-[13px] font-medium text-danger-fg">Compiler output</p>
          <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-surface-sunken p-3 font-mono text-xs leading-5 whitespace-pre-wrap text-fg">{result.compile_output}</pre>
        </div>
      ) : null}
      {result.status === "rejected" ? (
        // The service explains why it didn't run (session ended, language not allowed,
        // a run already in progress, an identical run a moment ago) in stderr.
        <p className="rounded-md border border-line bg-surface-sunken px-3 py-2 text-[13px] leading-5 text-fg">
          {result.stderr?.trim() || "This code couldn't be run as submitted. Check that the function signature matches the starter code."}
        </p>
      ) : result.stderr && result.status !== "compile_error" ? (
        <div>
          <p className="text-[13px] font-medium text-danger-fg">Error output</p>
          <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-sunken p-3 font-mono text-xs leading-5 whitespace-pre-wrap text-fg">{result.stderr}</pre>
        </div>
      ) : null}
      {tests.length ? (
        <ul className="grid gap-2">
          {tests.map((t) => (
            <li key={t.id} className={cn("rounded-md border px-3 py-2", t.passed ? "border-success-line bg-success-soft/40" : "border-danger-line bg-danger-soft/40")}>
              <div className="flex items-center gap-2 text-[13px] font-medium text-fg">
                {t.passed ? <Check className="size-3.5 text-success-fg" aria-hidden="true" /> : <X className="size-3.5 text-danger-fg" aria-hidden="true" />}
                {t.id}
                <span className="sr-only">{t.passed ? "passed" : "failed"}</span>
              </div>
              <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 font-mono text-xs leading-5">
                {t.input !== undefined ? (
                  <>
                    <dt className="text-fg-subtle">input</dt>
                    <dd className="truncate text-fg-muted">{formatValue(t.input)}</dd>
                  </>
                ) : null}
                <dt className="text-fg-subtle">expected</dt>
                <dd className="truncate text-fg-muted">{formatValue(t.expected)}</dd>
                {!t.passed ? (
                  <>
                    <dt className="text-fg-subtle">{t.error ? "error" : "actual"}</dt>
                    <dd className="break-words text-fg">{t.error ?? formatValue(t.actual)}</dd>
                  </>
                ) : null}
              </dl>
            </li>
          ))}
        </ul>
      ) : null}
      {hidden ? (
        <p className="text-[13px] text-fg-muted">
          Hidden tests: <span className="font-medium text-fg tabular-nums">{hidden.passed} of {hidden.total}</span> passed
        </p>
      ) : null}
      {result.stdout ? (
        <div>
          <p className="text-[13px] font-medium text-fg-muted">Output</p>
          <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-sunken p-3 font-mono text-xs leading-5 whitespace-pre-wrap text-fg">{result.stdout}</pre>
        </div>
      ) : null}
    </div>
  );
}
