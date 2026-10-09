/**
 * Coding problem editor: prompt (Markdown with preview), signature builder,
 * starter code and reference solutions per language (Monaco), tests with
 * visible/hidden toggles, exactly three hints, complexity and rubric, and a
 * sandbox validation run across every language.
 */

import { AlertCircle, ArrowLeft, Copy, FlaskConical, Save, Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { MultiSelect } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { CODE_LANGUAGE_LABELS, DIFFICULTY_LABELS } from "@/lib/labels";
import type { BankStatus, CodeLanguage, CodingQuestion, Competency, Difficulty } from "@/lib/types";
import { cn } from "@/lib/utils";

import { usePermission } from "@/lib/auth";

import { useSaveCodingQuestion, useValidateCodingQuestion } from "../api/library";
import { BankStatusBadge } from "../components/Controls";
import { FieldShell, ListEditor, NumberInput, SectionBlock } from "../components/Field";
import { LanguagePicker } from "../components/LanguagePicker";
import { MarkdownField } from "../components/MarkdownField";
import { useUnsavedChanges } from "../components/useUnsavedChanges";
import { describeFailure, fieldErrorsOf, unplacedMessages } from "../lib/serverErrors";
import { isApiError } from "@/lib/api";
import { jsonEqual } from "../lib/utils";
import { competencyOptionsFrom } from "../rounds/CompetencyListEditor";
import type { CodingValidationSummary } from "../types";
import { CodeEditor } from "./CodeEditor";
import { codingDraftFrom, emptyCodingDraft, FUNCTION_LANGUAGES, slugFromTitle, starterTemplate, toCodingInput, validateCodingDraft, type CodingDraft, type CodingMode } from "./codingModel";
import { SignatureBuilder } from "./SignatureBuilder";
import { TestsTable } from "./TestsTable";
import { ValidationIntro, ValidationResults } from "./ValidationResults";

type Tab = "problem" | "signature" | "code" | "tests" | "evaluation" | "validate";

function tabOf(path: string): Tab {
  if (path.startsWith("signature") || path === "sql_setup") return "signature";
  if (path.startsWith("starter_code") || path.startsWith("reference_solutions") || path === "languages") return "code";
  if (path.startsWith("tests")) return "tests";
  if (
    path.startsWith("hints") ||
    path.startsWith("expected_concepts") ||
    path.startsWith("rubric") ||
    path.startsWith("followups") ||
    path.startsWith("common_errors") ||
    path.endsWith("_complexity") ||
    path.startsWith("optimal_complexity")
  )
    return "evaluation";
  return "problem";
}

const STATUS_OPTIONS: Array<{ value: BankStatus; label: string; description: string }> = [
  { value: "active", label: "Active", description: "Can be chosen for interviews" },
  { value: "draft", label: "Draft", description: "Not used yet" },
  { value: "retired", label: "Retired", description: "Never used again" },
];
const ROLE_TAGS = ["general", "backend", "frontend", "fullstack", "devops", "data", "ml", "mobile"].map((t) => ({ value: t, label: t }));

export interface CodingQuestionEditorProps {
  /** Existing problem being edited (built-in problems are read-only). */
  question: CodingQuestion | null;
  /** Start a new organization problem as a copy of this one. */
  copyOf?: CodingQuestion | null;
  mode?: CodingMode;
  catalog: Competency[] | undefined;
  onClose: () => void;
  onSaved: (question: CodingQuestion) => void;
  onDuplicate?: (question: CodingQuestion) => void;
}

export function CodingQuestionEditor({ question, copyOf, mode = "function", catalog, onClose, onSaved, onDuplicate }: CodingQuestionEditorProps) {
  const initial = useMemo(() => {
    if (question) return codingDraftFrom(question);
    if (copyOf) {
      const base = codingDraftFrom(copyOf);
      return { ...base, title: `${base.title} (copy)`, slug: `${base.slug}_copy`.slice(0, 120), status: "draft" as const };
    }
    return emptyCodingDraft(mode);
  }, [question, copyOf, mode]);
  const [draft, setDraft] = useState<CodingDraft>(initial);
  const [baseline, setBaseline] = useState<CodingDraft>(initial);
  const [tab, setTab] = useState<Tab>("problem");
  const [codeLanguage, setCodeLanguage] = useState<CodeLanguage>(initial.languages[0] ?? "python");
  const [attempted, setAttempted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [formDetails, setFormDetails] = useState<string[]>([]);
  const [savedId, setSavedId] = useState<string | null>(question?.id ?? null);
  const [validation, setValidation] = useState<CodingValidationSummary | null>(null);
  const save = useSaveCodingQuestion();
  const validate = useValidateCodingQuestion();
  const canWrite = usePermission("question_bank:write");
  const isGlobal = Boolean(question && (!question.organization_id || (question as { is_global?: boolean }).is_global));
  const readOnly = !canWrite || isGlobal;
  const dirty = !jsonEqual(draft, baseline);
  const { allowNext } = useUnsavedChanges(dirty && !readOnly);

  const issues = useMemo(() => validateCodingDraft(draft), [draft]);
  const errors = { ...(attempted ? issues.errors : {}), ...serverErrors };
  const errorCounts = Object.keys(errors).reduce<Record<Tab, number>>(
    (acc, path) => {
      acc[tabOf(path)] += 1;
      return acc;
    },
    { problem: 0, signature: 0, code: 0, tests: 0, evaluation: 0, validate: 0 },
  );

  const set = <K extends keyof CodingDraft>(key: K, value: CodingDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const competencyOptions = useMemo(() => competencyOptionsFrom(catalog), [catalog]);
  const languageOptions = draft.mode === "sql" ? (["sql"] as CodeLanguage[]) : FUNCTION_LANGUAGES;

  const submit = async () => {
    setAttempted(true);
    setFormError(null);
    setFormDetails([]);
    setServerErrors({});
    if (Object.keys(issues.errors).length) {
      const first = Object.keys(issues.errors)[0]!;
      setTab(tabOf(first));
      toast.error("Some fields need attention", { description: issues.errors[first] });
      return;
    }
    try {
      const saved = await save.mutateAsync({ id: savedId, input: toCodingInput(draft) });
      setSavedId(saved.id);
      setBaseline(draft);
      setValidation(null);
      toast.success(savedId ? "Problem saved" : "Problem created", { description: "Run a sandbox validation to check every reference solution." });
      allowNext();
      onSaved(saved);
    } catch (error) {
      const fields = fieldErrorsOf(error);
      if (isApiError(error) && error.status === 409 && !fields.slug) fields.slug = "Another problem in your organization already uses this slug.";
      setServerErrors(fields);
      const first = Object.keys(fields)[0];
      if (first) setTab(tabOf(first));
      // Model-level problems (e.g. "test t3: expected 2 positional args") have no field: list them.
      const extra = unplacedMessages(error, Object.keys(fields));
      if (!first || extra.length) {
        setFormError(describeFailure(error));
        setFormDetails(extra);
      }
    }
  };

  const runValidation = async () => {
    if (!savedId) return;
    if (dirty) {
      const ok = await confirm({ title: "Validate the saved version?", description: "You have unsaved changes. Validation runs what's saved on the server.", confirmLabel: "Validate saved version" });
      if (!ok) return;
    }
    setTab("validate");
    try {
      setValidation(await validate.mutateAsync({ id: savedId }));
    } catch (error) {
      toast.error("Validation couldn't run", { description: describeFailure(error) });
    }
  };

  const fillStarter = async () => {
    if (draft.mode !== "function" || codeLanguage === "sql") return;
    if (draft.starter_code[codeLanguage]?.trim()) {
      const ok = await confirm({ title: "Replace the starter code?", description: `The current ${CODE_LANGUAGE_LABELS[codeLanguage]} starter code is replaced with a skeleton from the signature.`, confirmLabel: "Replace" });
      if (!ok) return;
    }
    set("starter_code", { ...draft.starter_code, [codeLanguage]: starterTemplate(draft.signature, codeLanguage) });
  };

  const fillAllStarters = () => {
    if (draft.mode !== "function") return;
    const next = { ...draft.starter_code };
    for (const language of draft.languages) if (language !== "sql" && !next[language]?.trim()) next[language] = starterTemplate(draft.signature, language);
    set("starter_code", next);
    toast.success("Starter code added for languages that had none");
  };

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: "Coding questions", to: "/app/coding-questions" }, { label: question ? question.title : "New problem" }]}
        title={question ? question.title : copyOf ? `Copy of ${copyOf.title}` : draft.mode === "sql" ? "New SQL problem" : "New coding problem"}
        titleAddon={
          <span className="flex items-center gap-2">
            {question ? <BankStatusBadge status={question.status} size="md" /> : null}
            {isGlobal ? <Badge tone="outline">Built-in, read-only</Badge> : !canWrite ? <Badge tone="outline">Read-only</Badge> : null}
            {dirty ? <Badge tone="warning" size="sm">Unsaved changes</Badge> : null}
          </span>
        }
        actions={
          <>
            <Button variant="ghost" leftIcon={<ArrowLeft />} onClick={onClose}>
              Back
            </Button>
            {!canWrite ? null : isGlobal && question && onDuplicate ? (
              <Button leftIcon={<Copy />} onClick={() => onDuplicate(question)}>
                Duplicate to edit
              </Button>
            ) : (
              <>
                <Button variant="outline" leftIcon={<FlaskConical />} disabled={!savedId} loading={validate.isPending} loadingText="Running…" onClick={() => void runValidation()} title={savedId ? undefined : "Save the problem first"}>
                  Validate in sandbox
                </Button>
                <Button leftIcon={<Save />} loading={save.isPending} loadingText="Saving…" onClick={() => void submit()} disabled={readOnly}>
                  {savedId ? "Save problem" : "Create problem"}
                </Button>
              </>
            )}
          </>
        }
      />

      {formError ? (
        <Callout tone="danger" className="mb-4" title={formError}>
          {formDetails.length ? (
            <ul className="mt-1 list-disc pl-4">
              {formDetails.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          ) : null}
        </Callout>
      ) : null}

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <TabsList aria-label="Problem sections">
          {(
            [
              ["problem", "Problem"],
              ["signature", draft.mode === "sql" ? "Schema" : "Signature"],
              ["code", "Code"],
              ["tests", `Tests (${draft.tests.length})`],
              ["evaluation", "Hints and evaluation"],
              ["validate", "Validation"],
            ] as Array<[Tab, string]>
          ).map(([key, label]) => (
            <TabsTrigger key={key} value={key}>
              {label}
              {errorCounts[key] ? (
                <span className="flex h-4.5 min-w-4.5 items-center justify-center rounded-full bg-danger px-1 text-2xs font-semibold text-danger-contrast tabular-nums">{errorCounts[key]}</span>
              ) : null}
            </TabsTrigger>
          ))}
        </TabsList>

        <fieldset disabled={readOnly} className="min-w-0">
          <TabsContent value="problem" className="grid gap-6">
            <SectionBlock title="Problem" description="Shown to candidates in the coding workspace.">
              <div className="grid gap-4">
                <div className="grid gap-4 md:grid-cols-[1fr_16rem]">
                  <FieldShell label="Title" required error={errors.title}>
                    {(control) => (
                      <Input
                        {...control}
                        value={draft.title}
                        onChange={(e) => {
                          const title = e.target.value;
                          setDraft((d) => ({ ...d, title, slug: d.slugTouched ? d.slug : slugFromTitle(title) }));
                        }}
                      />
                    )}
                  </FieldShell>
                  <FieldShell label="Slug" required error={errors.slug} description="Permanent id used in links and logs.">
                    {(control) => <Input {...control} className="font-mono" value={draft.slug} onChange={(e) => setDraft((d) => ({ ...d, slug: e.target.value.toLowerCase(), slugTouched: true }))} />}
                  </FieldShell>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <FieldShell label="Difficulty">
                    {(control) => <Select<Difficulty> id={control.id} value={draft.difficulty} options={(["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }))} onValueChange={(v) => set("difficulty", v)} />}
                  </FieldShell>
                  <FieldShell label="Suggested time" error={errors.time_limit_minutes}>
                    {(control) => <NumberInput {...control} value={draft.time_limit_minutes} min={5} max={120} unit="min" onChange={(v) => set("time_limit_minutes", v ?? 20)} />}
                  </FieldShell>
                  <FieldShell label="Status">
                    {(control) => <Select<BankStatus> id={control.id} value={draft.status} options={STATUS_OPTIONS} onValueChange={(v) => set("status", v)} />}
                  </FieldShell>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <FieldShell label="Competencies" optional description="Scores from this problem roll into these." error={errors.competency_keys}>
                    {(control) => <MultiSelect {...control} maxItems={8} value={draft.competency_keys} options={competencyOptions} onChange={(v) => set("competency_keys", v)} placeholder="e.g. Algorithms" />}
                  </FieldShell>
                  <FieldShell label="Roles" optional error={errors.role_tags}>
                    {(control) => (
                      <MultiSelect
                        {...control}
                        creatable
                        maxItems={8}
                        value={draft.role_tags}
                        options={ROLE_TAGS}
                        normalize={(raw) => raw.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")}
                        onChange={(v) => set("role_tags", v)}
                      />
                    )}
                  </FieldShell>
                </div>
                <FieldShell label="Prompt" required error={errors.prompt_markdown} description="Markdown. Include examples and constraints; candidates see exactly this.">
                  {(control) => <MarkdownField {...control} split rows={18} value={draft.prompt_markdown} onChange={(v) => set("prompt_markdown", v)} placeholder={"## Problem\n\n### Example 1\n```\nInput: ...\nOutput: ...\n```\n\n### Constraints\n- ..."} />}
                </FieldShell>
              </div>
            </SectionBlock>
          </TabsContent>

          <TabsContent value="signature" className="grid gap-6">
            {draft.mode === "function" ? (
              <SectionBlock title="Function signature" description="One language-neutral signature; the sandbox generates the harness for every language from it.">
                <SignatureBuilder value={draft.signature} onChange={(v) => set("signature", v)} languages={draft.languages} errors={errors} disabled={readOnly} />
              </SectionBlock>
            ) : (
              <SectionBlock title="Schema" description="Runs before every test. Use portable SQL that works on PostgreSQL and SQLite.">
                <FieldShell error={errors.sql_setup}>
                  {() => <CodeEditor language="sql" value={draft.sql_setup} onChange={(v) => set("sql_setup", v)} height={260} ariaLabel="Schema SQL" readOnly={readOnly} />}
                </FieldShell>
              </SectionBlock>
            )}
          </TabsContent>

          <TabsContent value="code" className="grid gap-6">
            <SectionBlock
              title="Languages and code"
              description="Starter code is what candidates see first. Reference solutions are private and used to validate the tests."
              actions={
                draft.mode === "function" ? (
                  <Button size="sm" variant="outline" leftIcon={<Wand2 />} onClick={fillAllStarters} disabled={readOnly}>
                    Fill missing starters
                  </Button>
                ) : undefined
              }
            >
              <div className="grid gap-5">
                {draft.mode === "function" ? (
                  <FieldShell label="Languages offered" error={errors.languages}>
                    {(control) => (
                      <LanguagePicker
                        {...control}
                        value={draft.languages}
                        options={languageOptions}
                        disabled={readOnly}
                        onChange={(languages) => {
                          set("languages", languages);
                          if (!languages.includes(codeLanguage) && languages[0]) setCodeLanguage(languages[0]);
                        }}
                      />
                    )}
                  </FieldShell>
                ) : null}
                <div className="grid gap-4 lg:grid-cols-[11rem_minmax(0,1fr)]">
                  <div role="tablist" aria-label="Language" aria-orientation="vertical" className="flex gap-1 overflow-x-auto lg:flex-col">
                    {draft.languages.map((language) => {
                      const hasStarter = Boolean(draft.starter_code[language]?.trim());
                      const hasReference = Boolean(draft.reference_solutions[language]?.trim());
                      return (
                        <button
                          key={language}
                          type="button"
                          role="tab"
                          aria-selected={codeLanguage === language}
                          onClick={() => setCodeLanguage(language)}
                          className={cn(
                            "flex shrink-0 items-center justify-between gap-3 rounded-control px-3 py-2 text-left text-sm font-medium transition-colors",
                            codeLanguage === language ? "bg-surface-active text-fg" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                          )}
                        >
                          {CODE_LANGUAGE_LABELS[language]}
                          <span className="flex gap-1" aria-label={`${hasStarter ? "Has" : "No"} starter, ${hasReference ? "has" : "no"} reference`}>
                            <span className={cn("size-1.5 rounded-full", hasStarter ? "bg-fg-muted" : "bg-line-strong")} title="Starter code" />
                            <span className={cn("size-1.5 rounded-full", hasReference ? "bg-success" : "bg-warning")} title="Reference solution" />
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {draft.languages.includes(codeLanguage) ? (
                    <div className="grid min-w-0 gap-4" key={codeLanguage}>
                      <FieldShell
                        label={`Starter code (${CODE_LANGUAGE_LABELS[codeLanguage]})`}
                        warning={issues.warnings[`starter_code.${codeLanguage}`]}
                        aside={
                          draft.mode === "function" && codeLanguage !== "sql" ? (
                            <Button size="xs" variant="ghost" leftIcon={<Wand2 />} onClick={() => void fillStarter()} disabled={readOnly}>
                              From signature
                            </Button>
                          ) : null
                        }
                      >
                        {() => (
                          <CodeEditor
                            language={codeLanguage}
                            value={draft.starter_code[codeLanguage] ?? ""}
                            onChange={(v) => set("starter_code", { ...draft.starter_code, [codeLanguage]: v })}
                            height={220}
                            readOnly={readOnly}
                            ariaLabel={`${CODE_LANGUAGE_LABELS[codeLanguage]} starter code`}
                          />
                        )}
                      </FieldShell>
                      <FieldShell label={`Reference solution (${CODE_LANGUAGE_LABELS[codeLanguage]})`} warning={issues.warnings[`reference_solutions.${codeLanguage}`]} description="Never shown to candidates.">
                        {() => (
                          <CodeEditor
                            language={codeLanguage}
                            value={draft.reference_solutions[codeLanguage] ?? ""}
                            onChange={(v) => set("reference_solutions", { ...draft.reference_solutions, [codeLanguage]: v })}
                            height={260}
                            readOnly={readOnly}
                            ariaLabel={`${CODE_LANGUAGE_LABELS[codeLanguage]} reference solution`}
                          />
                        )}
                      </FieldShell>
                    </div>
                  ) : (
                    <p className="text-sm text-fg-muted">Choose at least one language.</p>
                  )}
                </div>
              </div>
            </SectionBlock>
          </TabsContent>

          <TabsContent value="tests" className="grid gap-6">
            <SectionBlock title="Test cases" description={draft.mode === "function" ? "Arguments are a JSON array with one item per parameter; the expected value is JSON of the return type." : "Each test inserts its rows, runs the candidate's query and compares the result set."}>
              <TestsTable mode={draft.mode} value={draft.tests} onChange={(v) => set("tests", v)} signature={draft.signature} errors={errors} warning={issues.warnings.tests} disabled={readOnly} />
            </SectionBlock>
          </TabsContent>

          <TabsContent value="evaluation" className="grid gap-6">
            <SectionBlock title="Hints" description="Three levels, from a gentle nudge to a near-solution. Candidates request them one at a time, in order; hint use is shown to reviewers. A level left empty is written live by the interviewer.">
              {issues.warnings.hints && !Object.keys(errors).some((k) => k.startsWith("hints.")) ? <p className="mb-3 text-xs font-medium text-warning-fg">{issues.warnings.hints}</p> : null}
              <ListEditor
                fixedCount={3}
                multiline
                value={draft.hints}
                onChange={(v) => set("hints", [v[0] ?? "", v[1] ?? "", v[2] ?? ""])}
                rowLabel={(i) => `Hint ${i + 1}`}
                errors={{ 0: errors["hints.0"] ?? "", 1: errors["hints.1"] ?? "", 2: errors["hints.2"] ?? "" }}
              />
            </SectionBlock>
            <SectionBlock title="Evaluation" description="Guides the coding evaluation and the interviewer's questions about the approach.">
              <div className="grid gap-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <FieldShell label="Optimal time complexity" optional error={errors.time_complexity}>
                    {(control) => <Input {...control} className="font-mono" value={draft.time_complexity} placeholder="O(n)" onChange={(e) => set("time_complexity", e.target.value)} />}
                  </FieldShell>
                  <FieldShell label="Optimal space complexity" optional error={errors.space_complexity}>
                    {(control) => <Input {...control} className="font-mono" value={draft.space_complexity} placeholder="O(n)" onChange={(e) => set("space_complexity", e.target.value)} />}
                  </FieldShell>
                </div>
                <FieldShell label="Expected concepts" optional warning={issues.warnings.expected_concepts} error={errors.expected_concepts}>
                  {(control) => <ListEditor {...control} value={draft.expected_concepts} onChange={(v) => set("expected_concepts", v)} placeholder="e.g. hash map lookup of the complement" addLabel="Add concept" />}
                </FieldShell>
                <div className="grid gap-4 md:grid-cols-3">
                  <FieldShell label="Excellent" optional>
                    {(control) => <Textarea {...control} rows={3} autoResize value={draft.rubric_excellent} onChange={(e) => set("rubric_excellent", e.target.value)} />}
                  </FieldShell>
                  <FieldShell label="Good" optional>
                    {(control) => <Textarea {...control} rows={3} autoResize value={draft.rubric_good} onChange={(e) => set("rubric_good", e.target.value)} />}
                  </FieldShell>
                  <FieldShell label="Weak" optional>
                    {(control) => <Textarea {...control} rows={3} autoResize value={draft.rubric_weak} onChange={(e) => set("rubric_weak", e.target.value)} />}
                  </FieldShell>
                </div>
                <div className="grid gap-2">
                  <span className="text-[13px] font-medium text-fg">Criteria</span>
                  {draft.rubric_criteria.map((criterion, index) => (
                    <div key={index} className="grid gap-2 sm:grid-cols-[11rem_1fr]">
                      <Input aria-label={`Criterion ${index + 1} name`} className="font-mono text-[13px]" value={criterion.key} onChange={(e) => set("rubric_criteria", draft.rubric_criteria.map((c, i) => (i === index ? { ...c, key: e.target.value } : c)))} />
                      <Input aria-label={`Criterion ${index + 1} guidance`} value={criterion.value} placeholder="What good looks like" onChange={(e) => set("rubric_criteria", draft.rubric_criteria.map((c, i) => (i === index ? { ...c, value: e.target.value } : c)))} />
                    </div>
                  ))}
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                  <FieldShell label="Follow-up questions" optional error={errors.followups}>
                    {(control) => <ListEditor {...control} multiline maxItems={8} value={draft.followups} onChange={(v) => set("followups", v)} placeholder="Follow-up" addLabel="Add follow-up" />}
                  </FieldShell>
                  <FieldShell label="Common errors" optional error={errors.common_errors}>
                    {(control) => <ListEditor {...control} multiline maxItems={8} value={draft.common_errors} onChange={(v) => set("common_errors", v)} placeholder="Common error" addLabel="Add error" />}
                  </FieldShell>
                </div>
              </div>
            </SectionBlock>
          </TabsContent>
        </fieldset>

        <TabsContent value="validate" className="grid gap-4">
          <SectionBlock
            title="Sandbox validation"
            actions={
              !readOnly ? (
                <Button size="sm" leftIcon={<FlaskConical />} disabled={!savedId} loading={validate.isPending} loadingText="Running…" onClick={() => void runValidation()}>
                  {validation ? "Run again" : "Run validation"}
                </Button>
              ) : undefined
            }
          >
            {validate.isPending ? (
              <p className="text-sm text-fg-muted" aria-live="polite">
                Running every reference solution against every test. Compiled languages take a little longer…
              </p>
            ) : validation ? (
              <ValidationResults summary={validation} languages={draft.languages} />
            ) : (
              <ValidationIntro />
            )}
            {!savedId ? (
              <p className="mt-3 flex items-center gap-1.5 text-xs text-fg-muted">
                <AlertCircle className="size-3.5" aria-hidden="true" />
                Save the problem to validate it.
              </p>
            ) : null}
          </SectionBlock>
        </TabsContent>
      </Tabs>
    </div>
  );
}
