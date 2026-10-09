import { Plus, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Combobox, MultiSelect } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/Sheet";
import { Textarea } from "@/components/ui/Textarea";
import { DIFFICULTY_LABELS, QUESTION_KIND_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { BankQuestion, BankStatus, Competency, Difficulty, QuestionKind, RoundType } from "@/lib/types";

import { usePermission } from "@/lib/auth";

import { useSaveQuestion } from "../api/library";
import { FieldShell, ListEditor, SectionBlock } from "../components/Field";
import { describeFailure, fieldErrorsOf } from "../lib/serverErrors";
import { competencyOptionsFrom } from "../rounds/CompetencyListEditor";
import { RUBRIC_DIMENSIONS, ROUND_TYPES } from "../rounds/roundConfig";
import { BANK_KINDS, emptyQuestionDraft, mapQuestionServerErrors, questionDraftFrom, toQuestionInput, validateQuestion, type QuestionDraft } from "./questionModel";

const ROLE_TAG_SUGGESTIONS = ["backend", "frontend", "fullstack", "devops", "data", "ml", "mobile", "security", "general"].map((t) => ({ value: t, label: t }));
const STATUS_OPTIONS: Array<{ value: BankStatus; label: string; description: string }> = [
  { value: "active", label: "Active", description: "The interviewer can use it" },
  { value: "draft", label: "Draft", description: "Saved, not used in interviews" },
  { value: "retired", label: "Retired", description: "Kept for history, never used" },
];

export interface QuestionSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  question: BankQuestion | null;
  catalog: Competency[] | undefined;
  /** Prefill for "New question" from a filtered view. */
  defaults?: Partial<QuestionDraft>;
  onSaved?: (question: BankQuestion) => void;
}

export function QuestionSheet({ open, onOpenChange, question, catalog, defaults, onSaved }: QuestionSheetProps) {
  const [draft, setDraft] = useState<QuestionDraft>(emptyQuestionDraft());
  const [attempted, setAttempted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const save = useSaveQuestion();
  const [asCopy, setAsCopy] = useState(false);
  const canWrite = usePermission("question_bank:write");
  const readOnly = !canWrite || Boolean(question && !question.organization_id && !asCopy);
  const editingId = asCopy ? null : question?.id;

  useEffect(() => {
    if (!open) return;
    setDraft(question ? questionDraftFrom(question) : { ...emptyQuestionDraft(), ...defaults });
    setAttempted(false);
    setServerErrors({});
    setFormError(null);
    setAsCopy(false);
    // Reset only when the sheet opens or the question changes.
  }, [open, question?.id]);

  const clientErrors = useMemo(() => validateQuestion(draft), [draft]);
  const errors = { ...(attempted ? clientErrors : {}), ...serverErrors };
  const set = <K extends keyof QuestionDraft>(key: K, value: QuestionDraft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    if (serverErrors[key as string]) setServerErrors((e) => ({ ...e, [key]: "" }));
  };

  const submit = async () => {
    setAttempted(true);
    setFormError(null);
    if (Object.keys(clientErrors).length) return;
    try {
      const saved = await save.mutateAsync({ id: editingId, input: toQuestionInput(draft) });
      toast.success(editingId ? "Question saved" : "Question added");
      onSaved?.(saved);
      onOpenChange(false);
    } catch (error) {
      const mapped = mapQuestionServerErrors(fieldErrorsOf(error));
      setServerErrors(mapped);
      if (Object.keys(mapped).length === 0) setFormError(describeFailure(error));
    }
  };

  const competencyOptions = useMemo(() => competencyOptionsFrom(catalog), [catalog]);
  const dimensionKeys = new Set(draft.rubric_dimensions.map((d) => d.key));

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent size="xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {asCopy ? "Copy of a built-in question" : readOnly ? "Built-in question" : question ? "Edit question" : "New question"}
            {question?.source === "generated" ? (
              <Badge size="sm" tone="accent" icon={<Sparkles />}>
                AI-generated
              </Badge>
            ) : null}
          </SheetTitle>
          <SheetDescription>
            {!canWrite
              ? "You can view questions; editing needs question bank access."
              : readOnly
                ? "Built-in questions are read-only. Duplicate one to adapt it."
                : "The interviewer adapts the wording to the conversation; the rubric and concepts guide evaluation."}
          </SheetDescription>
        </SheetHeader>
        <SheetBody>
          <fieldset disabled={readOnly} className="grid min-w-0 gap-8">
            <SectionBlock variant="plain" title="Question">
              <div className="grid gap-4">
                <FieldShell label="Question" required error={errors.text}>
                  {(control) => (
                    <Textarea {...control} rows={4} autoResize maxRows={10} value={draft.text} placeholder="e.g. Walk me through how you'd roll out a breaking database migration with zero downtime." onChange={(e) => set("text", e.target.value)} />
                  )}
                </FieldShell>
                <div className="grid gap-4 sm:grid-cols-2">
                  <FieldShell label="Competency" required error={errors.competency_key}>
                    {(control) => (
                      <Combobox {...control} options={competencyOptions} value={draft.competency_key || null} clearable={false} onChange={(v) => set("competency_key", v ?? "")} placeholder="Search competencies" />
                    )}
                  </FieldShell>
                  <FieldShell label="Topic" optional error={errors.topic}>
                    {(control) => <Input {...control} value={draft.topic} placeholder="e.g. Zero-downtime migrations" onChange={(e) => set("topic", e.target.value)} />}
                  </FieldShell>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <FieldShell label="Difficulty">
                    {(control) => (
                      <Select<Difficulty> id={control.id} value={draft.difficulty} options={(["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }))} onValueChange={(v) => set("difficulty", v)} />
                    )}
                  </FieldShell>
                  <FieldShell label="Kind">
                    {(control) => (
                      <Select<QuestionKind> id={control.id} value={draft.kind} options={BANK_KINDS.map((k) => ({ value: k, label: QUESTION_KIND_LABELS[k] }))} onValueChange={(v) => set("kind", v)} />
                    )}
                  </FieldShell>
                  <FieldShell label="Status">
                    {(control) => <Select<BankStatus> id={control.id} value={draft.status} options={STATUS_OPTIONS} onValueChange={(v) => set("status", v)} />}
                  </FieldShell>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <FieldShell label="Roles" optional description="Who this question suits." error={errors.role_tags}>
                    {(control) => <MultiSelect {...control} disabled={readOnly} creatable value={draft.role_tags} options={ROLE_TAG_SUGGESTIONS} normalize={(raw) => raw.trim().toLowerCase()} onChange={(v) => set("role_tags", v)} placeholder="e.g. backend" />}
                  </FieldShell>
                  <FieldShell label="Round types" optional description="Empty means any round.">
                    {(control) => (
                      <MultiSelect
                        {...control}
                        disabled={readOnly}
                        value={draft.round_types}
                        options={ROUND_TYPES.map((t) => ({ value: t, label: ROUND_TYPE_LABELS[t] }))}
                        onChange={(v) => set("round_types", v as RoundType[])}
                        placeholder="Any round"
                      />
                    )}
                  </FieldShell>
                </div>
              </div>
            </SectionBlock>

            <SectionBlock variant="plain" title="What a good answer covers" description="Concepts the evaluator checks for. Specific beats generic.">
              <FieldShell error={errors.expected_concepts}>
                {(control) => <ListEditor {...control} disabled={readOnly} value={draft.expected_concepts} onChange={(v) => set("expected_concepts", v)} placeholder="e.g. expand-then-contract schema change" addLabel="Add concept" />}
              </FieldShell>
            </SectionBlock>

            <SectionBlock variant="plain" title="Rubric" description="Describe observable evidence at each level. Never personality, accent or confidence.">
              <div className="grid gap-4">
                <FieldShell label="Excellent" optional error={errors.rubric_excellent}>
                  {(control) => <Textarea {...control} rows={2} autoResize value={draft.rubric_excellent} onChange={(e) => set("rubric_excellent", e.target.value)} />}
                </FieldShell>
                <FieldShell label="Good" optional error={errors.rubric_good}>
                  {(control) => <Textarea {...control} rows={2} autoResize value={draft.rubric_good} onChange={(e) => set("rubric_good", e.target.value)} />}
                </FieldShell>
                <FieldShell label="Weak" optional error={errors.rubric_weak}>
                  {(control) => <Textarea {...control} rows={2} autoResize value={draft.rubric_weak} onChange={(e) => set("rubric_weak", e.target.value)} />}
                </FieldShell>
                <div className="grid gap-2">
                  <span className="text-[13px] font-medium text-fg">
                    Per-dimension guidance <span className="text-xs font-normal text-fg-subtle">Optional</span>
                  </span>
                  {draft.rubric_dimensions.map((dim, index) => (
                    <div key={index} className="grid gap-2 sm:grid-cols-[12rem_1fr_auto]">
                      <Select<string>
                        aria-label={`Dimension ${index + 1}`}
                        value={dim.key || null}
                        placeholder="Dimension"
                        invalid={Boolean(errors[`rubric_dimensions.${index}`])}
                        options={RUBRIC_DIMENSIONS.map((d) => ({ value: d.value, label: d.label, disabled: d.value !== dim.key && dimensionKeys.has(d.value) }))}
                        onValueChange={(key) => set("rubric_dimensions", draft.rubric_dimensions.map((d, i) => (i === index ? { ...d, key } : d)))}
                      />
                      <Input
                        aria-label={`Guidance for dimension ${index + 1}`}
                        value={dim.value}
                        placeholder="What to look for"
                        onChange={(e) => set("rubric_dimensions", draft.rubric_dimensions.map((d, i) => (i === index ? { ...d, value: e.target.value } : d)))}
                      />
                      <Button variant="ghost" size="sm" className="size-9 px-0" aria-label={`Remove dimension ${index + 1}`} onClick={() => set("rubric_dimensions", draft.rubric_dimensions.filter((_, i) => i !== index))}>
                        <Trash2 />
                      </Button>
                    </div>
                  ))}
                  <Button variant="ghost" size="sm" className="w-fit text-fg-muted" leftIcon={<Plus />} onClick={() => set("rubric_dimensions", [...draft.rubric_dimensions, { key: "", value: "" }])}>
                    Add dimension
                  </Button>
                </div>
              </div>
            </SectionBlock>

            <SectionBlock variant="plain" title="Follow-ups and common errors">
              <div className="grid gap-5 md:grid-cols-2">
                <FieldShell label="Follow-up questions" optional description="Used when the first answer stays vague." error={errors.followups}>
                  {(control) => <ListEditor {...control} disabled={readOnly} multiline value={draft.followups} onChange={(v) => set("followups", v)} placeholder="Follow-up" addLabel="Add follow-up" />}
                </FieldShell>
                <FieldShell label="Common errors" optional description="Misconceptions the evaluator should notice." error={errors.common_errors}>
                  {(control) => <ListEditor {...control} disabled={readOnly} multiline value={draft.common_errors} onChange={(v) => set("common_errors", v)} placeholder="Common error" addLabel="Add error" />}
                </FieldShell>
              </div>
            </SectionBlock>
            {formError ? <Callout tone="danger">{formError}</Callout> : null}
          </fieldset>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {readOnly ? "Close" : "Cancel"}
          </Button>
          {readOnly && !canWrite ? null : readOnly ? (
            <Button
              onClick={() => {
                setAsCopy(true);
                setDraft((d) => ({ ...d, status: "draft" }));
              }}
            >
              Duplicate to edit
            </Button>
          ) : (
            <Button onClick={submit} loading={save.isPending} loadingText="Saving…">
              {editingId ? "Save question" : "Add question"}
            </Button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
