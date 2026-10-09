import { ArrowLeft, Copy, Save, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import type { CodingQuestion, Competency, InterviewTemplate, RoundConfig } from "@/lib/types";

import { useDeleteTemplate, useSaveTemplate } from "../api/catalog";
import { FieldShell } from "../components/Field";
import { useUnsavedChanges } from "../components/useUnsavedChanges";
import { describeFailure, fieldErrorsOf, roundConfigErrorsOf, type FieldErrors } from "../lib/serverErrors";
import { jsonEqual } from "../lib/utils";
import { hasBlockingIssues, toRoundConfig, validateRoundConfig } from "../rounds/roundConfig";
import { RoundConfigEditor } from "../rounds/RoundConfigEditor";

export interface TemplateDraft {
  name: string;
  description: string;
  is_default: boolean;
  config: RoundConfig;
}

export interface TemplateEditorProps {
  template: InterviewTemplate | null;
  initial: TemplateDraft;
  catalog: Competency[] | undefined;
  problems: CodingQuestion[] | undefined;
  onClose: () => void;
  onSaved: (template: InterviewTemplate) => void;
  onDuplicate?: (template: InterviewTemplate) => void;
  canWrite: boolean;
}

/** Template editor: name, description and the shared round editor. */
export function TemplateEditor({ template, initial, catalog, problems, onClose, onSaved, onDuplicate, canWrite }: TemplateEditorProps) {
  const [draft, setDraft] = useState<TemplateDraft>(initial);
  const [baseline, setBaseline] = useState<TemplateDraft>(initial);
  const [attempted, setAttempted] = useState(false);
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const save = useSaveTemplate();
  const remove = useDeleteTemplate();
  const readOnly = !canWrite || Boolean(template && !template.organization_id);
  const dirty = !jsonEqual(draft, baseline);
  const { allowNext } = useUnsavedChanges(dirty && !readOnly);
  const configIssues = useMemo(() => validateRoundConfig(draft.config), [draft.config]);
  const nameError = attempted && !draft.name.trim() ? "Name the template." : serverErrors.name;

  const submit = async () => {
    setAttempted(true);
    setFormError(null);
    if (!draft.name.trim() || hasBlockingIssues(configIssues)) {
      toast.error("Some fields need attention", { description: !draft.name.trim() ? "Name the template." : configIssues.find((i) => i.severity === "error")?.message });
      return;
    }
    const config = toRoundConfig({ ...draft.config, name: draft.config.name.trim() || draft.name.trim() });
    try {
      const saved = await save.mutateAsync({
        id: template?.organization_id ? template.id : null,
        input: { name: draft.name.trim(), description: draft.description.trim() || null, round_type: config.round_type, config, is_default: draft.is_default },
      });
      setBaseline(draft);
      toast.success(template?.organization_id ? "Template saved" : "Template created");
      allowNext();
      onSaved(saved);
    } catch (error) {
      const fields = fieldErrorsOf(error);
      const configErrors = roundConfigErrorsOf(error);
      setServerErrors({ ...configErrors, ...(fields.name ? { name: fields.name } : {}) });
      if (!Object.keys(configErrors).length && !fields.name) setFormError(describeFailure(error));
    }
  };

  const onDelete = async () => {
    if (!template?.organization_id) return;
    const ok = await confirm({
      title: `Delete ${template.name}?`,
      description: "Rounds already created from it keep their configuration.",
      confirmLabel: "Delete template",
      tone: "danger",
    });
    if (!ok) return;
    await remove.mutateAsync(template.id);
    allowNext();
    onClose();
  };

  return (
    <div>
      <PageHeader
        breadcrumbs={[{ label: "Templates", to: "/app/templates" }, { label: template?.organization_id ? template.name : template ? template.name : "New template" }]}
        title={template ? template.name : "New template"}
        titleAddon={
          <span className="flex items-center gap-2">
            {template && !template.organization_id ? <Badge tone="outline">Built-in, read-only</Badge> : null}
            {dirty ? <Badge tone="warning" size="sm">Unsaved changes</Badge> : null}
          </span>
        }
        description="Reusable round setups. Jobs copy a template when a round is added, so later edits here don't change existing jobs."
        actions={
          <>
            <Button variant="ghost" leftIcon={<ArrowLeft />} onClick={onClose}>
              Back
            </Button>
            {template?.organization_id && canWrite ? (
              <Button variant="outline" leftIcon={<Trash2 />} onClick={() => void onDelete()} loading={remove.isPending}>
                Delete
              </Button>
            ) : null}
            {readOnly && template && onDuplicate && canWrite ? (
              <Button leftIcon={<Copy />} onClick={() => onDuplicate(template)}>
                Duplicate to edit
              </Button>
            ) : null}
            {!readOnly ? (
              <Button leftIcon={<Save />} onClick={() => void submit()} loading={save.isPending} loadingText="Saving…">
                {template?.organization_id ? "Save template" : "Create template"}
              </Button>
            ) : null}
          </>
        }
      />
      {formError ? <Callout tone="danger" className="mb-4">{formError}</Callout> : null}
      <div className="grid max-w-5xl gap-6">
        <RoundConfigEditor
          value={draft.config}
          onChange={(config) => setDraft((d) => ({ ...d, config }))}
          catalog={catalog}
          problems={problems}
          serverErrors={serverErrors}
          readOnly={readOnly}
          setupExtra={
            <fieldset disabled={readOnly} className="grid min-w-0 gap-4 rounded-md border border-line bg-surface-subtle/50 p-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
                <FieldShell label="Template name" required error={nameError}>
                  {(control) => <Input {...control} value={draft.name} maxLength={200} placeholder="e.g. Platform technical screen" onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} />}
                </FieldShell>
                <div className="flex items-end pb-1.5">
                  <Switch label="Default for its round type" checked={draft.is_default} onCheckedChange={(is_default) => setDraft((d) => ({ ...d, is_default }))} />
                </div>
              </div>
              <FieldShell label="Description" optional description="Helps colleagues pick the right template.">
                {(control) => <Textarea {...control} rows={2} autoResize value={draft.description} onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))} />}
              </FieldShell>
            </fieldset>
          }
        />
      </div>
    </div>
  );
}
