import { AlertTriangle, ExternalLink, Save, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";
import { useOrganization } from "@/lib/auth";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { CANDIDATE_REPORT_MODE_LABELS, labelFor, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { CandidateReportMode } from "@/lib/types";

import { useOrgSettings } from "@/features/recruiter/admin/api/admin";
import { useJob, useJobRounds, useJobs } from "@/features/recruiter/admin/api/jobs";
import { DetailSkeleton, LoadError } from "@/features/recruiter/admin/components/Controls";
import { FieldShell, SectionBlock } from "@/features/recruiter/admin/components/Field";
import { useUnsavedChanges } from "@/features/recruiter/admin/components/useUnsavedChanges";
import { describeFailure, fieldErrorsOf } from "@/features/recruiter/admin/lib/serverErrors";
import { jsonEqual } from "@/features/recruiter/admin/lib/utils";

import { useCreateLink, useFormCatalog, useLink, useServerFormCheck, useUpdateLink } from "../api";
import { FormBuilder, mergeIssues } from "../components/FormBuilder";
import { FormPreview } from "../components/FormPreview";
import { checkForm } from "../fairness";
import { activeRounds, CAMPUS_IP_LIMIT, draftFromLink, newDraft, orderedRoundIds, policyFromJob, toCreateBody, toUpdateBody, validateDraft, type DraftErrors, type LinkDraft } from "../linkDraft";
import type { FormIssue, FormWarning, OpenLink, PolicySummary } from "../types";

/** Whole-number input that tolerates partial typing; `null` when empty. */
function IntInput({ value, onChange, testId, id, placeholder, invalid, describedBy }: { value: number | null; onChange: (v: number | null) => void; testId: string; id?: string; placeholder?: string; invalid?: boolean; describedBy?: string }) {
  const [text, setText] = useState(value === null ? "" : String(value));
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(value === null ? "" : String(value));
  }, [value]);
  return (
    <Input
      id={id}
      data-testid={testId}
      type="text"
      inputMode="numeric"
      value={text}
      placeholder={placeholder}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={() => {
        focused.current = false;
        setText(value === null ? "" : String(value));
      }}
      onChange={(e) => {
        const raw = e.target.value.replace(/[^\d]/g, "");
        setText(raw);
        onChange(raw ? Number(raw) : null);
      }}
    />
  );
}

/** Maps a refused save onto the editor (sections + form builder). */
function readSaveError(error: unknown): { draft: DraftErrors; issues: { errors: FormIssue[]; warnings: FormWarning[] } | null; formFieldErrors: Record<string, string>; message: string | null } {
  const out = { draft: {} as DraftErrors, issues: null as { errors: FormIssue[]; warnings: FormWarning[] } | null, formFieldErrors: {} as Record<string, string>, message: null as string | null };
  if (!isApiError(error)) {
    out.message = describeFailure(error);
    return out;
  }
  const details = error.details;
  switch (error.code) {
    case "protected_field":
      out.issues = { errors: Array.isArray(details.fields) ? (details.fields as FormIssue[]) : [], warnings: [] };
      out.message = error.message;
      return out;
    case "form_warnings_unacknowledged":
      out.issues = { errors: [], warnings: Array.isArray(details.warnings) ? (details.warnings as FormWarning[]) : [] };
      out.draft.form = "Confirm or remove the questions with a warning.";
      return out;
    case "no_rounds":
    case "invalid_rounds":
      out.draft.round_ids = error.message || "Choose active rounds of this job.";
      return out;
    case "round_in_use":
      out.draft.round_ids = "Some rounds already have interviews, so they can't be removed or reordered. Add rounds after them instead.";
      return out;
    case "job_not_open":
      out.message = "This job is closed or archived. Reopen it to create a link.";
      return out;
    case "link_closed":
      out.message = "This link is closed, so it can't be changed.";
      return out;
  }
  // Form schema problems: details.errors = [{index, key, message}].
  if (Array.isArray(details.errors)) {
    for (const item of details.errors as Array<Record<string, unknown>>) {
      if (item && typeof item.key === "string" && typeof item.message === "string") out.formFieldErrors[item.key] = item.message;
    }
    if (Object.keys(out.formFieldErrors).length) out.draft.form = "Fix the highlighted form fields.";
  }
  const fields = fieldErrorsOf(error);
  for (const [path, message] of Object.entries(fields)) {
    const [head, ...rest] = path.split(".");
    if (head === "form_schema") out.formFieldErrors[rest.join(".")] = message;
    else if (head && ["job_id", "name", "description", "round_ids", "opens_at", "closes_at", "max_registrations", "round_window_hours", "per_ip_hourly_limit"].includes(head)) {
      out.draft[head as keyof DraftErrors] = message;
    }
  }
  if (!Object.keys(fields).length && !Object.keys(out.formFieldErrors).length) out.message = describeFailure(error);
  return out;
}

function PolicyNotice({ policy, jobId }: { policy: PolicySummary | null; jobId: string }) {
  if (!policy) return <p className="text-sm text-fg-muted">Choose a job to see how its interviews are recorded and monitored.</p>;
  const recorded = [policy.audio_recording && "audio", policy.video_recording && "video", policy.screen_recording && "screen"].filter(Boolean).join(", ") || "transcript only";
  const monitored = [policy.camera_monitoring && "camera checks", policy.screen_monitoring && "tab and window changes"].filter(Boolean).join(", ") || "off";
  return (
    <div className="grid grid-cols-1 gap-4">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-3">
        <div className="grid gap-0.5">
          <dt className="text-xs text-fg-muted">Recording</dt>
          <dd className="font-medium text-fg capitalize">{recorded}</dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-fg-muted">Monitoring</dt>
          <dd className="font-medium text-fg first-letter:uppercase">{monitored}</dd>
        </div>
        <div className="grid gap-0.5">
          <dt className="text-xs text-fg-muted">Candidate report</dt>
          <dd className="font-medium text-fg">{labelFor(CANDIDATE_REPORT_MODE_LABELS, policy.candidate_report_mode as CandidateReportMode)}</dd>
        </div>
      </dl>
      {!policy.video_recording ? (
        <Callout tone="warning" title="Video recording is off for this job">
          Interviews from this link will have audio and a transcript but no full video. Turn on video recording in the job's policy if you need it.
        </Callout>
      ) : null}
      {jobId ? (
        <Button asChild variant="outline" size="sm" className="w-fit" leftIcon={<ExternalLink />}>
          <Link to={`/app/jobs/${jobId}/edit`}>Edit the job's policy</Link>
        </Button>
      ) : null}
    </div>
  );
}

/** "Coding, 45 minutes" (the type is left out when the round is simply named after it). */
function roundMeta(round: { name: string; round_type: string; duration_minutes: number }): string {
  const type = labelFor<string>(ROUND_TYPE_LABELS, round.round_type);
  return type.toLowerCase() === round.name.trim().toLowerCase() ? `${round.duration_minutes} minutes` : `${type}, ${round.duration_minutes} minutes`;
}

function ErrorText({ children, testId }: { children?: ReactNode; testId?: string }) {
  if (!children) return null;
  return (
    <p className="text-xs font-medium text-danger-fg" data-testid={testId}>
      {children}
    </p>
  );
}

/** /app/links/new?job_id= and /app/links/:linkId/edit */
export default function LinkEditorPage() {
  const { linkId } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const editing = Boolean(linkId);
  const existing = useLink(linkId);
  const catalog = useFormCatalog();

  if (editing && existing.error) {
    return (
      <PageContainer>
        <PageHeader breadcrumbs={[{ label: "Interview links", to: "/app/links" }, { label: "Edit" }]} title="Edit link" />
        <LoadError error={existing.error} onRetry={() => void existing.refetch()} />
      </PageContainer>
    );
  }
  if ((editing && !existing.data) || (!editing && catalog.isPending)) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }
  const initial = existing.data ? draftFromLink(existing.data) : newDraft(params.get("job_id") ?? "", catalog.data?.default_schema);
  return <LinkEditor key={linkId ?? "new"} link={existing.data ?? null} initial={initial} maxFields={catalog.data?.limits.max_fields} onDone={(id, code) => navigate(`/app/links/${id}?tab=${editing ? "settings" : "share"}`, { state: code ? { accessCode: code } : undefined })} />;
}

function LinkEditor({ link, initial, maxFields, onDone }: { link: OpenLink | null; initial: LinkDraft; maxFields?: number; onDone: (id: string, accessCode: string | null) => void }) {
  const editing = Boolean(link);
  useDocumentTitle(editing ? `Edit ${link!.name}` : "New interview link");
  const [draft, setDraft] = useState<LinkDraft>(initial);
  const [attempted, setAttempted] = useState(false);
  const [saveErrors, setSaveErrors] = useState<DraftErrors>({});
  const [saveIssues, setSaveIssues] = useState<{ errors: FormIssue[]; warnings: FormWarning[] } | null>(null);
  const [formFieldErrors, setFormFieldErrors] = useState<Record<string, string>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const create = useCreateLink();
  const update = useUpdateLink(link?.id ?? "");
  const saving = create.isPending || update.isPending;

  const jobs = useJobs({ limit: 200 }, { enabled: !editing });
  const job = useJob(draft.job_id || undefined);
  const rounds = useJobRounds(draft.job_id || undefined);
  const settings = useOrgSettings();
  const organization = useOrganization();
  const available = useMemo(() => activeRounds(rounds.data), [rounds.data]);

  const dirty = !jsonEqual(draft, initial);
  const { allowNext } = useUnsavedChanges(dirty && !saving);

  const set = <K extends keyof LinkDraft>(key: K, value: LinkDraft[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    if (saveErrors[key as keyof DraftErrors]) setSaveErrors(({ [key as keyof DraftErrors]: _gone, ...rest }) => rest);
  };

  // New link: pick the job's first active round once the rounds load.
  useEffect(() => {
    if (editing || !available.length) return;
    setDraft((prev) => {
      const valid = prev.round_ids.filter((id) => available.some((r) => r.id === id));
      if (valid.length) return valid.length === prev.round_ids.length ? prev : { ...prev, round_ids: valid };
      return { ...prev, round_ids: [available[0]!.id] };
    });
  }, [available, editing]);

  // Server fairness check, debounced (the local mirror answers instantly).
  const checkPayload = useDebounce(useMemo(() => ({ form_schema: draft.form_schema, acknowledged_warnings: draft.acknowledged_warnings }), [draft.form_schema, draft.acknowledged_warnings]), 400);
  const serverCheck = useServerFormCheck(checkPayload);
  const local = useMemo(() => checkForm(draft.form_schema, draft.acknowledged_warnings), [draft.form_schema, draft.acknowledged_warnings]);
  const issues = useMemo(() => {
    const server = serverCheck.data && checkPayload.form_schema === draft.form_schema ? serverCheck.data : null;
    const merged = mergeIssues(local, server, draft.acknowledged_warnings);
    // Errors the server sent on save stay until the field changes or disappears.
    if (saveIssues) {
      const keys = new Set(draft.form_schema.fields.filter((f) => !f.builtin).map((f) => f.key));
      for (const e of saveIssues.errors) if (keys.has(e.key) && !merged.errors.some((m) => m.key === e.key)) merged.errors.push(e);
    }
    return merged;
  }, [local, serverCheck.data, checkPayload.form_schema, draft.form_schema, draft.acknowledged_warnings, saveIssues]);

  const localErrors = validateDraft(draft);
  const errors: DraftErrors = { ...(attempted ? localErrors : {}), ...saveErrors };
  if (issues.errors.length) errors.form = localErrors.form ?? "Remove the questions that ask for information we never collect.";
  const pipeline = draft.roundMode === "pipeline";
  const jobTitle = link?.job.title ?? job.data?.title ?? "";
  const policy = link?.policy_summary ?? policyFromJob(job.data, settings.data);
  const orgName = organization?.name ?? "Your organization";

  const save = async () => {
    setAttempted(true);
    setSaveMessage(null);
    const problems = validateDraft(draft);
    if (Object.keys(problems).length || issues.errors.length || issues.warnings.some((w) => !w.acknowledged)) {
      toast.error("Check the highlighted sections", { description: Object.values(problems)[0] ?? "Some questions need attention." });
      requestAnimationFrame(() => document.querySelector("[data-section-invalid='true']")?.scrollIntoView({ behavior: "smooth", block: "start" }));
      return;
    }
    try {
      const body = { ...draft, round_ids: orderedRoundIds(draft.round_ids, available.length ? available : []) };
      if (link) {
        const saved = await update.mutateAsync(toUpdateBody(body));
        toast.success("Link saved");
        allowNext();
        onDone(saved.id, null);
      } else {
        const result = await create.mutateAsync(toCreateBody(body));
        toast.success("Link created", { description: "Share it from the Share tab." });
        allowNext();
        onDone(result.link.id, result.access_code);
      }
    } catch (error) {
      const read = readSaveError(error);
      setSaveErrors(read.draft);
      setSaveIssues(read.issues);
      setFormFieldErrors(read.formFieldErrors);
      setSaveMessage(read.message);
      toast.error("Couldn't save the link", { description: read.message ?? Object.values(read.draft)[0] ?? "Check the highlighted sections." });
    }
  };

  const invalid = (key: keyof DraftErrors) => (errors[key] ? "true" : undefined);
  const roundTestId = (id: string) => `link-round-option-${id}`;

  return (
    <PageContainer>
      <PageHeader
        breadcrumbs={[{ label: "Interview links", to: "/app/links" }, ...(link ? [{ label: link.name, to: `/app/links/${link.id}` }] : []), { label: editing ? "Edit" : "New link" }]}
        title={editing ? `Edit ${link!.name}` : "New interview link"}
        description="One link for many candidates. Each person registers once and gets their own interview, planned from their résumé."
        actions={
          <>
            <Button asChild variant="outline">
              <Link to={link ? `/app/links/${link.id}` : "/app/links"}>Cancel</Link>
            </Button>
            <Button leftIcon={<Save />} onClick={() => void save()} loading={saving} loadingText="Saving" data-testid="link-save-button">
              {editing ? "Save changes" : "Create link"}
            </Button>
          </>
        }
      />

      {saveMessage ? (
        <Callout tone="danger" title="The link wasn't saved" className="mb-6">
          {saveMessage}
        </Callout>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6">
          <SectionBlock id="link-basics" title="Basics" description="The name is shown to candidates at the top of the registration page.">
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2" data-section-invalid={invalid("name") ?? invalid("job_id")}>
              <FieldShell label="Link name" required error={errors.name}>
                {(control) => (
                  <Input {...control} data-testid="link-name-input" value={draft.name} maxLength={120} placeholder="Campus drive, Pune, October" onChange={(e) => set("name", e.target.value)} />
                )}
              </FieldShell>
              <FieldShell label="Job" required error={errors.job_id} description={editing ? "The job can't change after the link is created." : undefined}>
                {(control) =>
                  editing ? (
                    <Input {...control} data-testid="link-job-select" value={jobTitle} disabled readOnly />
                  ) : (
                    <Select
                      id={control.id}
                      data-testid="link-job-select"
                      aria-label="Job"
                      value={draft.job_id || null}
                      invalid={Boolean(errors.job_id)}
                      placeholder={jobs.isLoading ? "Loading jobs" : "Choose a job"}
                      onValueChange={(jobId) => setDraft((prev) => ({ ...prev, job_id: jobId, round_ids: [] }))}
                      options={(jobs.data?.items ?? [])
                        .filter((j) => j.status === "open" || j.status === "draft" || j.id === draft.job_id)
                        .map((j) => ({ value: j.id, label: j.title, description: j.status === "draft" ? "Draft" : undefined }))}
                    />
                  )
                }
              </FieldShell>
              <FieldShell label="Introduction" optional error={errors.description} description="Shown on the registration page. Markdown works for lists and links." className="md:col-span-2">
                {(control) => <Textarea {...control} value={draft.description} rows={3} autoResize maxRows={10} onChange={(e) => set("description", e.target.value)} />}
              </FieldShell>
            </div>
          </SectionBlock>

          <SectionBlock id="link-rounds" title="Rounds" description="Each round is a separate interview. Candidates take them in the job's order.">
            <div className="grid grid-cols-1 gap-5" data-section-invalid={invalid("round_ids")}>
              <RadioGroup
                value={draft.roundMode}
                onValueChange={(mode) => setDraft((prev) => ({ ...prev, roundMode: mode as LinkDraft["roundMode"], round_ids: mode === "single" ? prev.round_ids.slice(0, 1) : prev.round_ids }))}
                className="flex flex-wrap gap-x-6 gap-y-3"
                aria-label="How many rounds"
              >
                <RadioGroupItem value="single" label="One round" data-testid="link-round-mode-single" />
                <RadioGroupItem value="pipeline" label="Rounds in order" data-testid="link-round-mode-pipeline" />
              </RadioGroup>
              {!draft.job_id ? (
                <p className="text-sm text-fg-muted">Choose a job first.</p>
              ) : rounds.isLoading ? (
                <p className="text-sm text-fg-muted">Loading rounds…</p>
              ) : available.length === 0 ? (
                <Callout tone="warning" title="This job has no active rounds">
                  Add a round in the job editor, then come back.
                </Callout>
              ) : pipeline ? (
                <ul className="grid grid-cols-1 gap-2" aria-label="Rounds in this link">
                  {available.map((round, i) => {
                    const checked = draft.round_ids.includes(round.id);
                    const position = checked ? orderedRoundIds(draft.round_ids, available).indexOf(round.id) + 1 : null;
                    return (
                      <li key={round.id} className="flex items-center gap-3 rounded-md border border-line px-3 py-2.5">
                        <Checkbox
                          data-testid={roundTestId(round.id)}
                          checked={checked}
                          onCheckedChange={(value) => set("round_ids", value === true ? orderedRoundIds([...draft.round_ids, round.id], available) : draft.round_ids.filter((id) => id !== round.id))}
                          label={round.name}
                          description={`${roundMeta(round)}, job round ${i + 1}`}
                        />
                        {position ? <span className="ml-auto shrink-0 text-xs font-medium text-fg-muted tabular-nums">Step {position}</span> : null}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <RadioGroup value={draft.round_ids[0] ?? ""} onValueChange={(id) => set("round_ids", [id])} aria-label="Round" className="grid grid-cols-1 gap-2">
                  {available.map((round) => (
                    <div key={round.id} className="rounded-md border border-line px-3 py-2.5">
                      <RadioGroupItem
                        value={round.id}
                        data-testid={roundTestId(round.id)}
                        label={round.name}
                        description={roundMeta(round)}
                      />
                    </div>
                  ))}
                </RadioGroup>
              )}
              {link?.rounds.some((r) => r.missing) ? (
                <Callout tone="warning" title="A round of this link was removed from the job">
                  Candidates skip it. Save the link to drop it from the list.
                </Callout>
              ) : null}
              <ErrorText testId="link-rounds-error">{errors.round_ids}</ErrorText>
            </div>
          </SectionBlock>

          {pipeline && draft.round_ids.length > 1 ? (
            <SectionBlock id="link-progression" title="Moving to the next round" description="Progression never depends on AI scores.">
              <RadioGroup value={draft.progression} onValueChange={(value) => set("progression", value as LinkDraft["progression"])} aria-label="Progression">
                <RadioGroupItem
                  value="immediate"
                  data-testid="link-progression-immediate"
                  label="Continue immediately"
                  description="Everyone who finishes a round can start the next one straight away. If a round ends early because of an integrity rule or a technical problem, a person decides."
                />
                <RadioGroupItem
                  value="after_review"
                  data-testid="link-progression-after_review"
                  label="After a person reviews"
                  description="Candidates wait after each round until someone on your team advances them. They check back with their personal link."
                />
              </RadioGroup>
            </SectionBlock>
          ) : null}

          <SectionBlock id="link-schedule" title="Schedule and limits">
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2" data-section-invalid={invalid("opens_at") ?? invalid("closes_at") ?? invalid("max_registrations") ?? invalid("round_window_hours") ?? invalid("per_ip_hourly_limit")}>
              <FieldShell label="Opens" optional error={errors.opens_at} description="Empty: open as soon as you save.">
                {(control) => <Input {...control} type="datetime-local" data-testid="link-opens-at" value={draft.opens_at} onChange={(e) => set("opens_at", e.target.value)} />}
              </FieldShell>
              <FieldShell label="Closes" optional error={errors.closes_at} description="Empty: open until you close it.">
                {(control) => <Input {...control} type="datetime-local" data-testid="link-closes-at" value={draft.closes_at} onChange={(e) => set("closes_at", e.target.value)} />}
              </FieldShell>
              <FieldShell label="Most registrations" optional error={errors.max_registrations} description="Empty: no limit.">
                {(control) => (
                  <IntInput id={control.id} describedBy={control["aria-describedby"]} invalid={Boolean(errors.max_registrations)} testId="link-max-registrations" value={draft.max_registrations} onChange={(v) => set("max_registrations", v)} placeholder="No limit" />
                )}
              </FieldShell>
              <FieldShell label="Time to start each round (hours)" required error={errors.round_window_hours} description="After a round unlocks, the candidate must start it within this time.">
                {(control) => <IntInput id={control.id} describedBy={control["aria-describedby"]} invalid={Boolean(errors.round_window_hours)} testId="link-round-window" value={draft.round_window_hours} onChange={(v) => set("round_window_hours", v)} />}
              </FieldShell>
              <FieldShell label="Registrations per network per hour" required error={errors.per_ip_hourly_limit} description="Limits automated sign-ups. Raise it when many people share one network." className="md:col-span-2">
                {(control) => (
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="w-32">
                      <IntInput id={control.id} describedBy={control["aria-describedby"]} invalid={Boolean(errors.per_ip_hourly_limit)} testId="link-ip-limit" value={draft.per_ip_hourly_limit} onChange={(v) => set("per_ip_hourly_limit", v)} />
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      leftIcon={<Users />}
                      className="h-auto min-h-8 max-w-full py-1.5 text-left whitespace-normal"
                      onClick={() => set("per_ip_hourly_limit", CAMPUS_IP_LIMIT)}
                      data-testid="link-campus-preset"
                      aria-pressed={draft.per_ip_hourly_limit === CAMPUS_IP_LIMIT}
                    >
                      Campus drive (many people on one network)
                    </Button>
                  </div>
                )}
              </FieldShell>
            </div>
          </SectionBlock>

          <SectionBlock id="link-access" title="Access">
            <div className="grid grid-cols-1 gap-4">
              <Switch
                data-testid="link-require-resume"
                checked={draft.require_resume}
                onCheckedChange={(v) => set("require_resume", v)}
                label="Require a résumé"
                description="The interview can't start until the candidate uploads one. Questions are planned from it."
                labelPosition="start"
              />
              {editing ? (
                <p className="text-sm text-fg-muted">
                  Access code: <span className="font-medium text-fg">{link!.requires_access_code ? "required" : "not required"}</span>. Add, replace or remove it on the Share tab.
                </p>
              ) : (
                <Switch
                  data-testid="link-access-code-toggle"
                  checked={draft.require_access_code}
                  onCheckedChange={(v) => set("require_access_code", v)}
                  label="Ask for an access code"
                  description="We generate a code when you create the link and show it once. Share it with the link, e.g. on a slide at a campus drive."
                  labelPosition="start"
                />
              )}
            </div>
          </SectionBlock>

          <SectionBlock id="link-duplicates" title="One attempt per person" description="The same email or mobile number can never register twice. These extra signals flag registrations for a person to look at.">
            <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
              <div className="grid content-start gap-2">
                <p className="text-[13px] font-medium text-fg">Check for repeats across</p>
                <RadioGroup value={draft.duplicate_policy.scope} onValueChange={(scope) => set("duplicate_policy", { ...draft.duplicate_policy, scope: scope as "job" | "link" })} aria-label="Duplicate scope">
                  <RadioGroupItem value="job" label="Every link of this job" data-testid="link-duplicate-scope-job" />
                  <RadioGroupItem value="link" label="This link only" data-testid="link-duplicate-scope-link" />
                </RadioGroup>
              </div>
              <div className="grid content-start gap-2">
                <p className="text-[13px] font-medium text-fg">Same device as another person</p>
                <RadioGroup value={draft.duplicate_policy.device} onValueChange={(device) => set("duplicate_policy", { ...draft.duplicate_policy, device: device as "flag" | "block" })} aria-label="Same device">
                  <RadioGroupItem value="flag" label="Flag for review" data-testid="link-duplicate-device-flag" />
                  <RadioGroupItem value="block" label="Block the registration" data-testid="link-duplicate-device-block" />
                </RadioGroup>
              </div>
              <div className="grid content-start gap-2">
                <p className="text-[13px] font-medium text-fg">Same network within 15 minutes</p>
                <RadioGroup value={draft.duplicate_policy.network} onValueChange={(network) => set("duplicate_policy", { ...draft.duplicate_policy, network: network as "flag" | "off" })} aria-label="Same network">
                  <RadioGroupItem value="flag" label="Flag (low priority)" data-testid="link-duplicate-network-flag" />
                  <RadioGroupItem value="off" label="Don't check" data-testid="link-duplicate-network-off" />
                </RadioGroup>
              </div>
            </div>
            {draft.duplicate_policy.device === "block" ? (
              <Callout tone="warning" icon={<AlertTriangle />} title="Shared computers will block real candidates" className="mt-5">
                Campus labs and cyber cafés use one computer for many people. With blocking on, the second person on that computer can't register. Flagging lets a person decide.
              </Callout>
            ) : null}
          </SectionBlock>

          <SectionBlock id="link-form" title="Registration form" description="Choose what candidates fill in before their interview. Ask only for what you need.">
            <div data-section-invalid={invalid("form")} className="grid grid-cols-1 gap-3">
              <FormBuilder
                schema={draft.form_schema}
                onChange={(form_schema) => {
                  set("form_schema", form_schema);
                  setFormFieldErrors({});
                }}
                acknowledged={draft.acknowledged_warnings}
                onAcknowledgedChange={(acks) => set("acknowledged_warnings", acks)}
                issues={issues}
                serverFieldErrors={mapFormErrors(formFieldErrors, draft)}
                maxFields={maxFields}
              />
              {attempted || saveErrors.form ? <ErrorText>{errors.form}</ErrorText> : null}
            </div>
          </SectionBlock>

          <SectionBlock id="link-policy" title="Recording and monitoring" description="These follow the job's policy. Candidates see them before they register and accept them in detail before the interview.">
            <PolicyNotice policy={policy} jobId={draft.job_id} />
          </SectionBlock>

          <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-5">
            <Button asChild variant="outline">
              <Link to={link ? `/app/links/${link.id}` : "/app/links"}>Cancel</Link>
            </Button>
            <Button leftIcon={<Save />} onClick={() => void save()} loading={saving} loadingText="Saving">
              {editing ? "Save changes" : "Create link"}
            </Button>
          </div>
        </div>

        <div className="min-w-0 xl:sticky xl:top-6 xl:self-start">
          <FormPreview
            schema={draft.form_schema}
            organizationName={orgName}
            jobTitle={jobTitle}
            linkName={draft.name}
            policy={policy}
            notice={settings.data?.data_processing_notice ?? ""}
            requiresAccessCode={editing ? link!.requires_access_code : draft.require_access_code}
          />
        </div>
      </div>
    </PageContainer>
  );
}

/** `fields.3.label` style server paths -> builder field keys. */
function mapFormErrors(errors: Record<string, string>, draft: LinkDraft): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [path, message] of Object.entries(errors)) {
    const match = /^fields\.(\d+)/.exec(path);
    const field = match ? draft.form_schema.fields[Number(match[1])] : draft.form_schema.fields.find((f) => f.key === path);
    if (field) out[field.key] = message;
    else out._form = message;
  }
  return out;
}
