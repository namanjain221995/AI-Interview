import { CheckCircle2, CircleAlert, EyeOff, FlaskConical } from "lucide-react";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { formatDateTime } from "@/lib/format";
import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import type { CodeLanguage } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { CodingValidationRow, CodingValidationSummary } from "../types";

function short(value: unknown): string {
  if (value === undefined) return "—";
  const text = JSON.stringify(value);
  return text.length > 120 ? `${text.slice(0, 117)}…` : text;
}

function ResultBadge({ row }: { row: CodingValidationRow }) {
  if (row.status === "skipped") {
    return (
      <Badge tone="outline" size="sm">
        Skipped
      </Badge>
    );
  }
  return <StatusBadge kind="run" value={row.status} size="sm" />;
}

function ResultsTable({ rows, languages, caption, missingNote }: { rows: CodingValidationRow[]; languages: CodeLanguage[]; caption: string; missingNote: string }) {
  const missing = languages.filter((l) => !rows.some((r) => r.language === l));
  return (
    <div className="overflow-hidden rounded-md border border-line">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line bg-surface-subtle text-left text-xs text-fg-muted">
            <th className="px-3 py-2 font-medium">Language</th>
            <th className="px-3 py-2 font-medium">Result</th>
            <th className="px-3 py-2 text-right font-medium">Tests passed</th>
            <th className="px-3 py-2 text-right font-medium">Runtime</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.language} className="border-b border-line-subtle align-top last:border-b-0">
              <td className="px-3 py-2.5 font-medium text-fg">{CODE_LANGUAGE_LABELS[row.language] ?? row.language}</td>
              <td className="px-3 py-2.5">
                <ResultBadge row={row} />
                {row.error ? (
                  <pre className="mt-1.5 max-h-28 overflow-auto rounded-xs bg-danger-soft px-2 py-1 font-mono text-[11.5px] whitespace-pre-wrap text-danger-fg scrollbar-thin">{row.error}</pre>
                ) : null}
                {row.failures.length ? (
                  <ul className="mt-1.5 grid gap-1">
                    {row.failures.slice(0, 5).map((failure) => (
                      <li key={failure.id} className="rounded-xs bg-surface-subtle px-2 py-1 font-mono text-[11.5px] text-fg-muted">
                        <span className="inline-flex items-center gap-1 font-semibold text-fg">
                          {failure.hidden ? <EyeOff className="size-3" aria-label="Hidden test" /> : null}
                          {failure.id}
                        </span>
                        {failure.error ? <span>: {failure.error}</span> : <span>: expected {short(failure.expected)}, got {short(failure.actual)}</span>}
                      </li>
                    ))}
                    {row.failures.length > 5 ? <li className="text-xs text-fg-subtle">and {row.failures.length - 5} more</li> : null}
                  </ul>
                ) : null}
              </td>
              <td className={cn("px-3 py-2.5 text-right tabular-nums", row.status === "skipped" ? "text-fg-subtle" : row.passed === row.total && row.total > 0 ? "text-success-fg" : "text-danger-fg")}>
                {row.status === "skipped" ? "—" : `${row.passed} / ${row.total}`}
              </td>
              <td className="px-3 py-2.5 text-right text-fg-muted tabular-nums">{row.runtime_ms !== null ? `${row.runtime_ms} ms` : "—"}</td>
            </tr>
          ))}
          {missing.map((language) => (
            <tr key={language} className="border-b border-line-subtle last:border-b-0">
              <td className="px-3 py-2.5 font-medium text-fg">{CODE_LANGUAGE_LABELS[language]}</td>
              <td className="px-3 py-2.5 text-xs text-fg-muted" colSpan={3}>
                {missingNote}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A starter "works" when it compiles and runs; failing tests are expected (it isn't a solution). */
function starterVerdict(row: CodingValidationRow): { label: string; tone: "success" | "warning" | "danger" | "outline" } {
  if (row.status === "skipped") return { label: "Skipped", tone: "outline" };
  if (row.status === "passed" || row.status === "failed") return { label: "Compiles and runs", tone: "success" };
  if (row.status === "compile_error") return { label: "Doesn't compile", tone: "danger" };
  if (row.status === "timeout" || row.status === "memory_exceeded") return { label: "Doesn't finish", tone: "warning" };
  return { label: "Crashes before the tests", tone: "danger" };
}

function StarterResults({ rows }: { rows: CodingValidationRow[] }) {
  const broken = rows.filter((r) => starterVerdict(r).tone === "danger").length;
  return (
    <div className="grid gap-2">
      <div className="grid gap-0.5">
        <h3 className="text-sm font-semibold text-fg">Starter code</h3>
        <p className="text-xs text-fg-muted">
          {broken
            ? `${broken} ${broken === 1 ? "starter doesn't" : "starters don't"} run, so candidates would be stuck before they begin.`
            : "Every starter compiles and runs. Failing tests are expected: starters aren't solutions."}
        </p>
      </div>
      <ul className="divide-y divide-line-subtle overflow-hidden rounded-md border border-line">
        {rows.map((row) => {
          const verdict = starterVerdict(row);
          return (
            <li key={row.language} className="grid gap-1.5 px-3 py-2.5 text-sm sm:grid-cols-[8rem_1fr]">
              <span className="font-medium text-fg">{CODE_LANGUAGE_LABELS[row.language] ?? row.language}</span>
              <span className="grid gap-1.5">
                <Badge tone={verdict.tone} size="sm" dot className="w-fit">
                  {verdict.label}
                </Badge>
                {row.error && verdict.tone !== "success" ? (
                  <pre className="max-h-28 overflow-auto rounded-xs bg-danger-soft px-2 py-1 font-mono text-[11.5px] whitespace-pre-wrap text-danger-fg scrollbar-thin">{row.error}</pre>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ValidationResults({ summary, languages }: { summary: CodingValidationSummary; languages: CodeLanguage[] }) {
  return (
    <div className="grid gap-4" data-testid="coding-validation-results">
      <Callout
        tone={summary.ok ? "success" : "danger"}
        icon={summary.ok ? <CheckCircle2 /> : <CircleAlert />}
        title={summary.ok ? "Every reference solution passes every test" : "Some reference solutions don't pass"}
      >
        {summary.ok
          ? "The problem is safe to use in interviews in these languages."
          : "Fix the reference solution or the tests before candidates see this problem. Hidden tests count too."}
        {summary.duration_ms !== null || summary.validated_at ? (
          <span className="mt-1 block text-xs">
            {summary.validated_at ? `Checked ${formatDateTime(summary.validated_at)}` : null}
            {summary.duration_ms !== null ? ` in ${(summary.duration_ms / 1000).toFixed(1)} s` : null}
          </span>
        ) : null}
      </Callout>
      <ResultsTable rows={summary.results} languages={languages} caption="Reference solutions" missingNote="Not run: no reference solution saved." />
      {summary.starters.length ? <StarterResults rows={summary.starters} /> : null}
    </div>
  );
}

export function ValidationIntro() {
  return (
    <div className="flex items-start gap-3 rounded-md border border-dashed border-line-strong bg-surface-subtle/60 px-4 py-4 text-sm text-fg-muted">
      <FlaskConical className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
      <p>
        Runs each saved reference solution against every test, hidden ones included, and checks that every starter compiles, in the same locked-down sandbox candidates use. Validation uses the saved version, so save your changes first.
      </p>
    </div>
  );
}
