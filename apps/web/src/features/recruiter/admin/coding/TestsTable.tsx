import { Eye, EyeOff, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import type { CodingSignature } from "@/lib/types";
import { cn } from "@/lib/utils";

import { emptyTest, nextTestId, type CodingMode, type TestRow } from "./codingModel";

export interface TestsTableProps {
  mode: CodingMode;
  value: TestRow[];
  onChange: (value: TestRow[]) => void;
  signature: CodingSignature;
  errors: Record<string, string>;
  warning?: string;
  disabled?: boolean;
}

/** Test cases with visible/hidden toggles. Hidden tests never reach candidates. */
export function TestsTable({ mode, value, onChange, signature, errors, warning, disabled }: TestsTableProps) {
  const update = (index: number, patch: Partial<TestRow>) => onChange(value.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  const visible = value.filter((t) => !t.hidden).length;
  const argsHint = signature.params.length ? `[${signature.params.map((p) => `${p.name}: ${p.type}`).join(", ")}]` : "[]";

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg tabular-nums">{visible}</span> visible to candidates, <span className="font-medium text-fg tabular-nums">{value.length - visible}</span> hidden (used on submit; inputs and outputs are never shown).
        </p>
        <Button size="sm" variant="outline" leftIcon={<Plus />} disabled={disabled} onClick={() => onChange([...value, { ...emptyTest(value.length, visible >= 2), id: nextTestId(value) }])}>
          Add test
        </Button>
      </div>
      {errors.tests ? <p className="text-xs font-medium text-danger-fg">{errors.tests}</p> : warning ? <p className="text-xs font-medium text-warning-fg">{warning}</p> : null}

      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full min-w-[58rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line bg-surface-subtle text-left text-xs font-medium text-fg-muted">
              <th className="w-72 px-3 py-2 font-medium">Test</th>
              <th className="px-3 py-2 font-medium">{mode === "function" ? <>Arguments <span className="font-mono text-2xs text-fg-subtle">{argsHint}</span></> : "Setup SQL (inserts)"}</th>
              <th className="px-3 py-2 font-medium">{mode === "function" ? <>Expected <span className="font-mono text-2xs text-fg-subtle">{signature.return_type}</span></> : "Expected rows"}</th>
              <th className="w-28 px-3 py-2 font-medium">Visibility</th>
              <th className="w-10 px-2 py-2">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {value.map((test, index) => {
              const argError = mode === "function" ? errors[`tests.${index}.args`] : undefined;
              const expectedError = mode === "function" ? errors[`tests.${index}.expected`] : errors[`tests.${index}.expected_rows`];
              return (
                <tr key={index} className={cn("border-b border-line-subtle align-top last:border-b-0", test.hidden && "bg-surface-subtle/50")}>
                  <td className="px-3 py-2">
                    <div className="grid grid-cols-[4.5rem_1fr] gap-1.5">
                      <Input size="sm" aria-label={`Test ${index + 1} id`} value={test.id} disabled={disabled} className="font-mono" aria-invalid={errors[`tests.${index}.id`] ? true : undefined} onChange={(e) => update(index, { id: e.target.value.trim() })} />
                      <Input size="sm" aria-label={`Test ${index + 1} description`} value={test.description} disabled={disabled} placeholder="What it checks" maxLength={300} onChange={(e) => update(index, { description: e.target.value })} />
                    </div>
                    {errors[`tests.${index}.id`] ? <p className="mt-1 text-xs font-medium text-danger-fg">{errors[`tests.${index}.id`]}</p> : null}
                  </td>
                  <td className="px-3 py-2">
                    {mode === "function" ? (
                      <Textarea
                        rows={1}
                        autoResize
                        maxRows={6}
                        aria-label={`Test ${index + 1} arguments`}
                        value={test.argsText}
                        disabled={disabled}
                        placeholder="[[2, 7, 11, 15], 9]"
                        className="min-h-8 py-1.5 font-mono text-[12.5px] leading-5"
                        aria-invalid={argError ? true : undefined}
                        onChange={(e) => update(index, { argsText: e.target.value })}
                      />
                    ) : (
                      <Textarea
                        rows={1}
                        autoResize
                        maxRows={6}
                        aria-label={`Test ${index + 1} setup SQL`}
                        value={test.setupSql}
                        disabled={disabled}
                        placeholder="Empty: the query runs against empty tables"
                        className="min-h-8 py-1.5 font-mono text-[12.5px] leading-5"
                        onChange={(e) => update(index, { setupSql: e.target.value })}
                      />
                    )}
                    {argError ? <p className="mt-1 text-xs font-medium text-danger-fg">{argError}</p> : null}
                  </td>
                  <td className="px-3 py-2">
                    <Textarea
                      rows={1}
                      autoResize
                      maxRows={6}
                      aria-label={`Test ${index + 1} expected`}
                      value={mode === "function" ? test.expectedText : test.expectedRowsText}
                      disabled={disabled}
                      placeholder={mode === "function" ? "[0, 1]" : "[[200]]"}
                      className="min-h-8 py-1.5 font-mono text-[12.5px] leading-5"
                      aria-invalid={expectedError ? true : undefined}
                      onChange={(e) => update(index, mode === "function" ? { expectedText: e.target.value } : { expectedRowsText: e.target.value })}
                    />
                    {expectedError ? <p className="mt-1 text-xs font-medium text-danger-fg">{expectedError}</p> : null}
                    {mode === "sql" ? (
                      <div className="mt-1.5">
                        <label className="inline-flex items-center gap-2 text-xs text-fg-muted">
                          <Switch size="sm" checked={test.ordered} disabled={disabled} onCheckedChange={(ordered) => update(index, { ordered })} aria-label={`Test ${index + 1}: row order matters`} />
                          Row order matters
                        </label>
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => update(index, { hidden: !test.hidden })}
                      aria-pressed={test.hidden}
                      aria-label={`Test ${index + 1} is ${test.hidden ? "hidden" : "visible"}. Toggle`}
                      className={cn(
                        "inline-flex h-8 items-center gap-1.5 rounded-control border px-2.5 text-xs font-medium transition-colors",
                        test.hidden ? "border-line-strong bg-surface-sunken/60 text-fg-muted" : "border-accent-line bg-accent-soft text-accent-fg",
                      )}
                    >
                      {test.hidden ? <EyeOff className="size-3.5" aria-hidden="true" /> : <Eye className="size-3.5" aria-hidden="true" />}
                      {test.hidden ? "Hidden" : "Visible"}
                    </button>
                  </td>
                  <td className="px-2 py-2">
                    <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`Remove test ${test.id || index + 1}`} disabled={disabled} onClick={() => onChange(value.filter((_, i) => i !== index))}>
                      <Trash2 />
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
