import { FileText, Link2, Mail, Save, Search, UserPlus, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { FileDropzone } from "@/components/ui/FileDropzone";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { useDebounce } from "@/lib/hooks";

import { useCandidates } from "@/features/recruiter/admin/api/candidates";
import { RESUME_ACCEPT, RESUME_MAX_BYTES } from "@/features/recruiter/admin/candidates/ResumeUpload";
import { FieldShell, SectionBlock } from "@/features/recruiter/admin/components/Field";
import { describeFailure, fieldErrorsOf } from "@/features/recruiter/admin/lib/serverErrors";

import { useCreateIntake, useSendIntake, useUpdateIntake } from "../../api";
import { TARGET_ROLE_LABELS } from "../../labels";
import { createPayload, draftUpdatePayload, NOTES_MAX, validateSingle, type FormErrors, type SingleForm } from "../../lib/createForm";
import { TARGET_ROLES, type IntakeConfig, type IntakeCreateResponse, type IntakeDetail, type TargetRole } from "../../types";
import type { ReviewerOption } from "../reviewers";
import { InviteSettingsFields } from "./InviteSettingsFields";

const CHOOSE = "candidate";

export interface SingleCreateFormProps {
  form: SingleForm;
  onChange: (form: SingleForm) => void;
  config: IntakeConfig | undefined;
  configLoading: boolean;
  orgRequiresCode: boolean;
  reviewers: ReviewerOption[];
  /** Editing a saved draft (PATCH + send) instead of creating. */
  draft: IntakeDetail | null;
  resumeFile: File | null;
  onResumeFile: (file: File | null) => void;
  onCreated: (result: IntakeCreateResponse) => void;
  onDraftSaved: (draftId: string) => void;
}

/** Codes the API uses for refusals the form can explain. */
function refusalText(code: string | undefined, fallback: string): string {
  switch (code) {
    case "job_has_no_intake_round":
      return "This job has no active intake round. Add an Intake interview round to the job first.";
    case "interview_exists":
      return "This candidate already has an open invitation for this job. Open it from the intake list, or cancel it first.";
    case "candidate_archived":
      return "This candidate is archived. Restore them on their profile to invite them again.";
    case "intake_disabled":
      return "Intake interviews are turned off for this workspace.";
    case "intake_not_draft":
      return "This intake was already sent. Open it from the intake list.";
    case "job_not_open":
      return "This job isn't open. Open the job, or choose another one.";
    case "invalid_reviewer":
      return "The assigned reviewer isn't an active staff member of this organisation. Choose someone else.";
    case "intake_cancelled":
      return "This intake was cancelled. Create a new one instead.";
    case "target_role_locked":
      return "The candidate already chose a target role on their form; it can't be changed now.";
    case "intake_kit_unavailable":
      return "The intake kit couldn't be loaded on the server. Try again in a moment, or ask an administrator.";
    case "candidate_or_candidate_id_required":
      return "Choose an existing candidate or enter a new one, not both.";
    case "work_authorization_country_required":
      return "Name the country for the work authorisation question.";
    default:
      return fallback;
  }
}

/** Form field for a server validation path (`details.field`, dotted). */
function fieldFor(path: string): string {
  const last = path.replace(/^body\./, "").split(".");
  if (path.includes("work_authorization")) return "mq1_country";
  if (last[0] === "candidate") return last[1] === "full_name" ? "full_name" : last[1] === "email" ? "email" : last[1] ?? "candidate";
  if (last[0] === "settings") return last[1] ?? "settings";
  return last[0] ?? path;
}

export function SingleCreateForm({ form, onChange, config, configLoading, orgRequiresCode, reviewers, draft, resumeFile, onResumeFile, onCreated, onDraftSaved }: SingleCreateFormProps) {
  const [errors, setErrors] = useState<FormErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [attempted, setAttempted] = useState(false);
  const create = useCreateIntake();
  const update = useUpdateIntake(draft?.id ?? "");
  const send = useSendIntake(draft?.id ?? "");
  const busy = create.isPending || update.isPending || send.isPending;

  const [search, setSearch] = useState("");
  const q = useDebounce(search.trim(), 250);
  const candidates = useCandidates({ q: q || undefined, limit: 6, offset: 0 }, form.candidate_mode === "existing" && !draft);

  const set = <K extends keyof SingleForm>(key: K, value: SingleForm[K]) => onChange({ ...form, [key]: value });
  const jobs = config?.jobs ?? [];
  const jobOptions = useMemo(() => jobs.map((j) => ({ value: j.job_id, label: j.title, description: j.round_name ?? undefined })), [jobs]);

  // Re-validate as the recruiter fixes things after a first attempt.
  useEffect(() => {
    if (attempted) setErrors(validateSingle(form));
  }, [form, attempted]);

  const submit = async (sendNow: boolean) => {
    setAttempted(true);
    setFormError(null);
    const found = validateSingle(form, { draft: !sendNow });
    setErrors(found);
    if (Object.keys(found).length) {
      setFormError("Check the highlighted fields.");
      return;
    }
    try {
      if (draft) {
        await update.mutateAsync(draftUpdatePayload(form));
        if (!sendNow) {
          onDraftSaved(draft.id);
          return;
        }
        const result = await send.mutateAsync({ expires_in_hours: form.settings.expires_in_hours });
        onCreated(result);
        return;
      }
      const result = await create.mutateAsync(createPayload(form, sendNow));
      if (sendNow) onCreated(result);
      else onDraftSaved(result.intake.id);
    } catch (error) {
      const mapped: FormErrors = {};
      for (const [path, message] of Object.entries(fieldErrorsOf(error))) mapped[fieldFor(path)] = message;
      const code = isApiError(error) ? error.code : undefined;
      // Intake routes name the first invalid field in `details.field` (design 10.7).
      const field = isApiError(error) && typeof error.details.field === "string" ? error.details.field : null;
      if (field && !Object.keys(mapped).length) mapped[fieldFor(field)] = refusalText(code, error instanceof Error ? error.message : "Check this field.");
      if (code === "invalid_reviewer") mapped.assigned_reviewer_id = refusalText(code, "");
      if (Object.keys(mapped).length) setErrors((e) => ({ ...e, ...mapped }));
      setFormError(refusalText(code, describeFailure(error)));
    }
  };

  const selected = form.candidate;

  return (
    <div className="grid grid-cols-1 gap-5">
      {/* ---------------------------------------------------------- candidate */}
      <SectionBlock title="Candidate" description="Who you're inviting. They confirm this email address to open the link.">
        {draft ? (
          <div className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2.5">
            <Avatar name={draft.candidate.full_name} size="sm" />
            <div className="grid grid-cols-1 min-w-0">
              <span className="truncate text-sm font-medium text-fg">{draft.candidate.full_name}</span>
              <span className="truncate text-xs text-fg-muted">{draft.candidate.email}</span>
            </div>
            <span className="ml-auto text-xs text-fg-subtle">To invite someone else, create a new intake.</span>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3">
            <Tabs value={form.candidate_mode} onValueChange={(v) => set("candidate_mode", v as SingleForm["candidate_mode"])} variant="segmented">
              <TabsList aria-label="Candidate source">
                <TabsTrigger value="existing">
                  <Users /> Existing candidate
                </TabsTrigger>
                <TabsTrigger value="new">
                  <UserPlus /> New candidate
                </TabsTrigger>
              </TabsList>
            </Tabs>
            {form.candidate_mode === "existing" ? (
              <div className="grid grid-cols-1 gap-2">
                {selected ? (
                  <div className="flex items-center gap-3 rounded-md border border-primary bg-surface px-3 py-2 shadow-[0_0_0_1px_var(--primary)]" data-testid="intake-candidate-selected">
                    <Avatar name={selected.full_name} size="sm" />
                    <span className="grid min-w-0 flex-1">
                      <span className="truncate text-sm font-medium text-fg">{selected.full_name}</span>
                      <span className="truncate text-xs text-fg-muted">{selected.email}</span>
                    </span>
                    <Button variant="ghost" size="sm" className="size-8 shrink-0 px-0" aria-label="Choose someone else" onClick={() => set("candidate", null)}>
                      <X />
                    </Button>
                  </div>
                ) : (
                  <>
                    <Input
                      type="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search by name or email"
                      aria-label="Search candidates by name or email"
                      leading={<Search />}
                      data-testid="intake-candidate-search"
                    />
                    <div role="radiogroup" aria-label="Candidates" className="grid max-h-64 grid-cols-1 gap-1 overflow-y-auto scrollbar-thin">
                      {candidates.isLoading ? (
                        Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-12 w-full" />)
                      ) : (candidates.data?.items ?? []).length === 0 ? (
                        <p className="px-1 py-3 text-sm text-fg-muted">
                          {q ? `No candidates match “${q}”.` : "No candidates yet."}{" "}
                          <button type="button" className="font-medium text-accent hover:underline" onClick={() => set("candidate_mode", "new")}>
                            Add a new candidate
                          </button>
                        </p>
                      ) : (
                        (candidates.data?.items ?? []).map((c) => (
                          <button
                            key={c.id}
                            type="button"
                            role="radio"
                            aria-checked={false}
                            data-testid="intake-candidate-option"
                            onClick={() => set("candidate", { id: c.id, full_name: c.full_name, email: c.email })}
                            className="flex items-center gap-3 rounded-md border border-transparent px-3 py-2 text-left transition-colors hover:bg-surface-hover"
                          >
                            <Avatar name={c.full_name} size="sm" />
                            <span className="grid min-w-0 flex-1">
                              <span className="truncate text-sm font-medium text-fg">{c.full_name}</span>
                              <span className="truncate text-xs text-fg-muted">{c.email}</span>
                            </span>
                          </button>
                        ))
                      )}
                    </div>
                  </>
                )}
                {errors.candidate ? <p className="text-xs font-medium text-danger-fg">{errors.candidate}</p> : null}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <FieldShell label="Full name" required error={errors.full_name}>
                  {(control) => (
                    <Input
                      {...control}
                      data-testid="intake-candidate-name"
                      autoComplete="off"
                      value={form.new_candidate.full_name}
                      onChange={(e) => set("new_candidate", { ...form.new_candidate, full_name: e.target.value })}
                    />
                  )}
                </FieldShell>
                <FieldShell label="Email" required error={errors.email}>
                  {(control) => (
                    <Input
                      {...control}
                      type="email"
                      leading={<Mail />}
                      data-testid="intake-candidate-email"
                      autoComplete="off"
                      value={form.new_candidate.email}
                      onChange={(e) => set("new_candidate", { ...form.new_candidate, email: e.target.value })}
                    />
                  )}
                </FieldShell>
                <FieldShell label="Phone" optional className="sm:col-span-2" error={errors.phone}>
                  {(control) => <Input {...control} type="tel" value={form.new_candidate.phone} onChange={(e) => set("new_candidate", { ...form.new_candidate, phone: e.target.value })} />}
                </FieldShell>
              </div>
            )}
          </div>
        )}
      </SectionBlock>

      {/* ------------------------------------------------------------ program */}
      <SectionBlock title="Program" description="The job whose intake round the candidate takes, and the role they're screened for.">
        {configLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : jobs.length === 0 ? (
          <Callout tone="info" title="No job has an intake round yet">
            Add an Intake interview round to a job, then come back. <Link to="/app/jobs">Go to jobs</Link>
          </Callout>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FieldShell label="Job" required error={errors.job_id}>
              {(control) => (
                <Select<string>
                  id={control.id}
                  data-testid="intake-job-select"
                  invalid={Boolean(errors.job_id)}
                  placeholder="Choose a job"
                  value={form.job_id}
                  onValueChange={(v) => onChange({ ...form, job_id: v, round_id: jobs.find((j) => j.job_id === v)?.round_id ?? null })}
                  options={jobOptions}
                />
              )}
            </FieldShell>
            <FieldShell label="Target role" description="Or let the candidate pick on their form.">
              {(control) => (
                <Select<string>
                  id={control.id}
                  data-testid="intake-target-role"
                  value={form.target_role}
                  onValueChange={(v) => set("target_role", v as TargetRole | typeof CHOOSE)}
                  options={[{ value: CHOOSE, label: "Let the candidate choose" }, ...TARGET_ROLES.map((r) => ({ value: r, label: TARGET_ROLE_LABELS[r] }))]}
                />
              )}
            </FieldShell>
          </div>
        )}
      </SectionBlock>

      {/* ------------------------------------------------------------- résumé */}
      <SectionBlock title="Résumé" description="Optional. Uploaded after the invitation is created; the candidate can also upload one during onboarding.">
        {resumeFile ? (
          <div className="flex items-center gap-3 rounded-md border border-line bg-surface-subtle px-3 py-2.5" data-testid="intake-resume-file">
            <FileText className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
            <span className="grid min-w-0 flex-1">
              <span className="truncate text-sm font-medium text-fg">{resumeFile.name}</span>
              <span className="text-xs text-fg-muted">{formatBytes(resumeFile.size)}, uploads when you create the invitation</span>
            </span>
            <Button variant="ghost" size="sm" className="size-8 shrink-0 px-0" aria-label="Remove the résumé" onClick={() => onResumeFile(null)}>
              <X />
            </Button>
          </div>
        ) : (
          <FileDropzone
            accept={RESUME_ACCEPT}
            acceptLabel="PDF, Word (.docx), text or an image"
            maxSizeBytes={RESUME_MAX_BYTES}
            onFiles={(files) => files[0] && onResumeFile(files[0])}
            title="Drop a résumé here or browse"
            hint={`PDF, DOCX, TXT, PNG, JPG or TIFF up to ${formatBytes(RESUME_MAX_BYTES)}.`}
            inputProps={{ "data-testid": "intake-resume-upload" } as never}
          />
        )}
      </SectionBlock>

      <InviteSettingsFields
        value={form.settings}
        onChange={(settings) => set("settings", settings)}
        errors={errors}
        orgWorkAuthorization={config?.work_authorization_default ?? null}
        orgRequiresCode={orgRequiresCode}
        reviewers={reviewers}
      />

      <SectionBlock title="Notes" description="For staff only. Never shown to the candidate or to the AI interviewer.">
        <FieldShell label="Internal notes" optional error={errors.notes}>
          {(control) => <Textarea {...control} rows={3} autoResize maxLength={NOTES_MAX} showCount value={form.notes} onChange={(e) => set("notes", e.target.value)} />}
        </FieldShell>
      </SectionBlock>

      <div className="sticky bottom-0 z-10 -mx-4 border-t border-line-subtle bg-surface/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-lg sm:border sm:px-5">
        {formError ? (
          <p className="mb-2 text-[13px] font-medium text-danger-fg" role="alert">
            {formError}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button variant="outline" leftIcon={<Save />} onClick={() => void submit(false)} disabled={busy} data-testid="intake-save-draft">
            {draft ? "Save draft" : "Save as draft"}
          </Button>
          <Button leftIcon={<Link2 />} onClick={() => void submit(true)} loading={busy} loadingText="Creating invitation…" data-testid="intake-send">
            {draft ? "Send invitation" : "Create invitation"}
          </Button>
        </div>
      </div>
    </div>
  );
}
