import { Archive, CheckCheck, ChevronDown, Pencil, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Combobox } from "@/components/ui/Combobox";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Progress } from "@/components/ui/Progress";
import { Select } from "@/components/ui/Select";
import { DIFFICULTY_LABELS, QUESTION_KIND_LABELS } from "@/lib/labels";
import type { BankQuestion, BankStatus, Competency, Difficulty } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useGenerateQuestions, useSetQuestionStatus } from "../api/library";
import { BankStatusBadge } from "../components/Controls";
import { FieldShell, NumberInput } from "../components/Field";
import { describeFailure, fieldErrorsOf } from "../lib/serverErrors";
import type { SkippedQuestion } from "../types";
import { competencyOptionsFrom } from "../rounds/CompetencyListEditor";

export interface GenerateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalog: Competency[] | undefined;
  onEdit: (question: BankQuestion) => void;
  defaultCompetency?: string;
}

/** Generate questions with the model, then review: activate the good ones, retire the rest. */
export function GenerateDialog({ open, onOpenChange, catalog, onEdit, defaultCompetency }: GenerateDialogProps) {
  const [competency, setCompetency] = useState<string | null>(defaultCompetency ?? null);
  const [difficulty, setDifficulty] = useState<Difficulty>("medium");
  const [count, setCount] = useState<number | null>(5);
  const [role, setRole] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<BankQuestion[] | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [skippedItems, setSkippedItems] = useState<SkippedQuestion[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const generate = useGenerateQuestions();
  const setStatus = useSetQuestionStatus();

  useEffect(() => {
    if (!open) {
      setDrafts(null);
      setSelected(new Set());
      setErrors({});
      setSkipped(0);
      generate.reset();
    } else if (defaultCompetency) setCompetency(defaultCompetency);
    // Reset only when the dialog opens or closes.
  }, [open]);

  useEffect(() => {
    if (!generate.isPending) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [generate.isPending]);

  const options = useMemo(() => competencyOptionsFrom(catalog), [catalog]);

  const run = async () => {
    const found: Record<string, string> = {};
    if (!competency) found.competency_key = "Choose a competency.";
    if (!count || count < 1 || count > 10 || !Number.isInteger(count)) found.count = "Generate 1 to 10 questions at a time.";
    if (role.trim().length < 2) found.role = "Describe the role, e.g. Senior backend engineer.";
    else if (role.trim().length > 120) found.role = "Keep the role under 120 characters.";
    setErrors(found);
    if (Object.keys(found).length) return;
    try {
      const result = await generate.mutateAsync({ competency_key: competency!, difficulty, count: count!, role: role.trim() });
      setDrafts(result.items);
      setSkipped(result.skipped);
      setSkippedItems(result.skippedItems);
      setSelected(new Set(result.items.map((q) => q.id)));
    } catch (error) {
      const fields = fieldErrorsOf(error);
      setErrors(fields);
      if (!Object.keys(fields).length) setErrors({ form: describeFailure(error, "Question generation failed. Try again in a moment.") });
    }
  };

  const apply = async (status: BankStatus) => {
    const ids = [...selected];
    if (!ids.length) return;
    try {
      const { updated, failed } = await setStatus.mutateAsync({ ids, status });
      setDrafts((list) => (list ?? []).map((q) => (selected.has(q.id) ? { ...q, status } : q)));
      setSelected(new Set());
      toast.success(`${updated} ${updated === 1 ? "question" : "questions"} ${status === "active" ? "activated" : status === "retired" ? "retired" : "kept as drafts"}`, {
        description: failed ? `${failed} couldn't be updated.` : undefined,
      });
    } catch (error) {
      toast.error("Couldn't update the questions", { description: describeFailure(error) });
    }
  };

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-accent" aria-hidden="true" />
            {drafts ? "Review generated questions" : "Generate questions with AI"}
          </DialogTitle>
          <DialogDescription>
            {drafts
              ? "New questions are saved as drafts. Activate the ones you'd ask; retire the rest. Near-duplicates of existing questions were skipped."
              : "Drafts are written for one competency and difficulty, with expected concepts, a rubric and follow-ups. Nothing is used in interviews until you activate it."}
          </DialogDescription>
        </DialogHeader>

        {drafts ? (
          <DialogBody className="grid gap-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Checkbox
                label={`${selected.size} of ${drafts.length} selected`}
                checked={selected.size === 0 ? false : selected.size === drafts.length ? true : "indeterminate"}
                onCheckedChange={(v) => setSelected(v ? new Set(drafts.map((q) => q.id)) : new Set())}
              />
              {skipped ? <p className="text-xs text-fg-muted">{skipped} near-duplicate{skipped === 1 ? " was" : "s were"} skipped.</p> : null}
            </div>
            {skippedItems.length ? (
              <details className="rounded-md border border-line-subtle bg-surface-subtle/60 px-3.5 py-2 text-sm">
                <summary className="cursor-pointer text-xs font-medium text-fg-muted">Why some questions were skipped</summary>
                <ul className="mt-2 grid gap-1.5">
                  {skippedItems.map((item, i) => (
                    <li key={i} className="grid gap-0.5">
                      <span className="text-fg">{item.text}</span>
                      <span className="text-xs text-fg-muted">
                        {item.reason}
                        {typeof item.similarity === "number" ? ` (${Math.round(item.similarity * 100)}% similar to an existing question)` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            {drafts.length === 0 ? (
              <Callout tone="neutral">Every generated question was too similar to one you already have. Try another difficulty or role.</Callout>
            ) : (
              <ul className="grid max-h-[55vh] gap-2 overflow-y-auto pr-1 scrollbar-thin">
                {drafts.map((q) => {
                  const open = expanded === q.id;
                  return (
                    <li key={q.id} className={cn("rounded-md border bg-surface", selected.has(q.id) ? "border-line-strong" : "border-line")}>
                      <div className="flex items-start gap-3 px-3.5 py-3">
                        <Checkbox aria-label="Select question" checked={selected.has(q.id)} onCheckedChange={() => toggle(q.id)} className="mt-1" />
                        <div className="grid min-w-0 flex-1 gap-1.5">
                          <p className="text-sm text-fg">{q.text}</p>
                          <div className="flex flex-wrap items-center gap-2">
                            <DifficultyBadge value={q.difficulty} size="sm" />
                            <span className="text-xs text-fg-muted">{QUESTION_KIND_LABELS[q.kind] ?? q.kind}</span>
                            {q.topic ? <span className="text-xs text-fg-muted">{q.topic}</span> : null}
                            <BankStatusBadge status={q.status} />
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <Button variant="ghost" size="sm" className="size-8 px-0" aria-label="Edit question" onClick={() => onEdit(q)}>
                            <Pencil />
                          </Button>
                          <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={open ? "Hide details" : "Show details"} aria-expanded={open} onClick={() => setExpanded(open ? null : q.id)}>
                            <ChevronDown className={cn("transition-transform", open && "rotate-180")} />
                          </Button>
                        </div>
                      </div>
                      {open ? (
                        <div className="grid gap-3 border-t border-line-subtle px-3.5 py-3 pl-10 text-sm md:grid-cols-2">
                          <Detail title="Expected concepts" items={q.expected_concepts} />
                          <Detail title="Follow-ups" items={q.followups} />
                          {q.rubric?.excellent ? <Detail title="Excellent answer" items={[q.rubric.excellent]} /> : null}
                          {q.rubric?.weak ? <Detail title="Weak answer" items={[q.rubric.weak]} /> : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </DialogBody>
        ) : (
          <DialogBody className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <FieldShell label="Competency" required error={errors.competency_key}>
                {(control) => <Combobox {...control} options={options} value={competency} onChange={setCompetency} placeholder="Search competencies" />}
              </FieldShell>
              <FieldShell label="Role" required error={errors.role} description="Grounds the scenarios in the right context.">
                {(control) => <Input {...control} value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Senior DevOps engineer" />}
              </FieldShell>
              <FieldShell label="Difficulty" error={errors.difficulty}>
                {(control) => (
                  <Select<Difficulty> id={control.id} value={difficulty} onValueChange={setDifficulty} options={(["easy", "medium", "hard"] as const).map((d) => ({ value: d, label: DIFFICULTY_LABELS[d] }))} />
                )}
              </FieldShell>
              <FieldShell label="How many" error={errors.count}>
                {(control) => <NumberInput {...control} value={count} min={1} max={10} nullable onChange={setCount} unit="questions" />}
              </FieldShell>
            </div>
            {generate.isPending ? (
              <div className="grid gap-2 rounded-md bg-surface-subtle px-4 py-3" aria-live="polite">
                <p className="text-sm text-fg">Writing {count} questions and checking them against your bank…</p>
                <Progress value={null} size="xs" aria-label="Generating" />
                <p className="text-xs text-fg-muted tabular-nums">{elapsed}s. This usually takes 20 to 60 seconds.</p>
              </div>
            ) : null}
            {errors.form ? <Callout tone="danger">{errors.form}</Callout> : null}
          </DialogBody>
        )}

        <DialogFooter>
          {drafts ? (
            <>
              <Button variant="outline" onClick={() => setDrafts(null)} className="sm:mr-auto">
                Generate more
              </Button>
              <Button variant="outline" leftIcon={<Archive />} disabled={!selected.size} loading={setStatus.isPending && setStatus.variables?.status === "retired"} onClick={() => void apply("retired")}>
                Retire selected
              </Button>
              <Button leftIcon={<CheckCheck />} disabled={!selected.size} loading={setStatus.isPending && setStatus.variables?.status === "active"} onClick={() => void apply("active")}>
                Activate selected
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)} disabled={generate.isPending}>
                Cancel
              </Button>
              <Button leftIcon={<Sparkles />} onClick={run} loading={generate.isPending} loadingText="Generating…">
                Generate drafts
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="grid content-start gap-1">
      <p className="text-xs font-medium text-fg-muted">{title}</p>
      <ul className="grid gap-0.5 text-sm text-fg">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
