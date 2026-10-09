/**
 * Job editor: role details, description, skills, rounds, candidate experience,
 * integrity and recording. One draft, validated live; saving maps server
 * errors back onto fields and rounds.
 */

import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, ListChecks, Plus, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { MultiSelect } from "@/components/ui/Combobox";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/Popover";
import { Select } from "@/components/ui/Select";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/Sheet";
import { Textarea } from "@/components/ui/Textarea";
import { CANDIDATE_REPORT_MODE_LABELS, JOB_STATUS_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import { queryKeys } from "@/lib/query";
import type { CandidateReportMode, CodingQuestion, Competency, Job, JobRequirement, JobStatus, OrganizationSettings, Round } from "@/lib/types";
import { cn } from "@/lib/utils";

import { fetchRequirements, fetchRounds } from "../api/jobs";
import { FieldShell, NumberInput, SectionBlock, ToggleRow } from "../components/Field";
import { MarkdownField } from "../components/MarkdownField";
import { useUnsavedChanges } from "../components/useUnsavedChanges";
import type { FieldErrors } from "../lib/serverErrors";
import { jsonEqual, localId } from "../lib/utils";
import { AddRoundDialog } from "../rounds/AddRoundDialog";
import { competencyOptionsFrom } from "../rounds/CompetencyListEditor";
import { RoundCard } from "../rounds/RoundCard";
import { RoundConfigEditor } from "../rounds/RoundConfigEditor";
import { EMPLOYMENT_TYPES, newRoundDraft, roundDraftFrom, SENIORITY_LEVELS, validateJobDraft, type JobDraft, type JobIssue } from "./jobDraft";
import { IntegrityOverrides, RecordingOverrides } from "./JobPolicySections";
import { saveJobDraft } from "./saveJob";

const SECTIONS = [
  { id: "role", label: "Role" },
  { id: "description", label: "Description" },
  { id: "skills", label: "Skills" },
  { id: "rounds", label: "Rounds" },
  { id: "experience", label: "Candidate experience" },
  { id: "integrity", label: "Integrity" },
  { id: "recording", label: "Recording and retention" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

function sectionOf(path: string): SectionId {
  const head = path.split(".")[0] ?? path;
  if (["title", "department", "location", "employment_type", "seniority", "experience_min_years", "experience_max_years", "status"].includes(head)) return "role";
  if (head === "description" || head === "interview_instructions") return "description";
  if (head === "required_skills" || head === "preferred_skills") return "skills";
  if (head === "rounds") return "rounds";
  if (head === "integrity_settings") return "integrity";
  if (head === "recording_settings") return "recording";
  return "experience";
}

const STATUS_OPTIONS: Array<{ value: JobStatus; label: string; description: string }> = [
  { value: "draft", label: JOB_STATUS_LABELS.draft, description: "Only your team can see it; no invitations yet" },
  { value: "open", label: JOB_STATUS_LABELS.open, description: "Ready to invite candidates" },
  { value: "closed", label: JOB_STATUS_LABELS.closed, description: "No new invitations; existing interviews continue" },
];

const ORG_DEFAULT = "__org__";

export interface JobEditorProps {
  job: Job | null;
  requirements: JobRequirement[];
  rounds: Round[];
  initialDraft: JobDraft;
  catalog: Competency[];
  settings: OrganizationSettings;
  problems: CodingQuestion[] | undefined;
}

export function JobEditor({ job, requirements, rounds, initialDraft, catalog, settings, problems }: JobEditorProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JobDraft>(initialDraft);
  const [baseline, setBaseline] = useState<JobDraft>(initialDraft);
  const [jobId, setJobId] = useState<string | null>(job?.id ?? null);
  const [serverRequirements, setServerRequirements] = useState(requirements);
  const [serverRounds, setServerRounds] = useState(rounds);
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [jobErrors, setJobErrors] = useState<FieldErrors>({});
  const [roundErrors, setRoundErrors] = useState<Record<string, FieldErrors>>({});
  const [messages, setMessages] = useState<string[]>([]);
  const [editingUid, setEditingUid] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [activeSection, setActiveSection] = useState<SectionId>("role");
  const contentRef = useRef<HTMLDivElement>(null);

  const dirty = !jsonEqual(draft, baseline);
  const { allowNext } = useUnsavedChanges(dirty && !saving);

  const issues = useMemo(() => validateJobDraft(draft), [draft]);
  const blocking = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const showErrors = attempted;
  const fieldError = (path: string): string | undefined =>
    jobErrors[path] ?? (showErrors ? issues.find((i) => i.path === path && i.severity === "error")?.message : undefined);
  const fieldWarning = (path: string): string | undefined => issues.find((i) => i.path === path && i.severity === "warning")?.message;

  const issueCount = (section: SectionId) =>
    (showErrors ? blocking.filter((i) => sectionOf(i.path) === section).length : 0) +
    Object.keys(jobErrors).filter((p) => sectionOf(p) === section).length +
    (section === "rounds" ? Object.keys(roundErrors).length : 0);

  const set = <K extends keyof JobDraft>(key: K, value: JobDraft[K]) => setDraft((d) => ({ ...d, [key]: value }));
  const setRounds = (update: (rounds: JobDraft["rounds"]) => JobDraft["rounds"]) => setDraft((d) => ({ ...d, rounds: update(d.rounds) }));

  // Track the section in view for the side navigation.
  useEffect(() => {
    const root = document.getElementById("main-content");
    const nodes = SECTIONS.map((s) => document.getElementById(`job-${s.id}`)).filter((n): n is HTMLElement => Boolean(n));
    if (!("IntersectionObserver" in window) || nodes.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) setActiveSection(visible.target.id.replace("job-", "") as SectionId);
      },
      { root, rootMargin: "-10% 0px -70% 0px" },
    );
    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);

  const goTo = (section: SectionId) => {
    document.getElementById(`job-${section}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActiveSection(section);
  };

  const skillOptions = useMemo(() => competencyOptionsFrom(catalog), [catalog]);
  const editing = draft.rounds.find((r) => r.uid === editingUid) ?? null;
  const editingIndex = editing ? draft.rounds.indexOf(editing) : -1;

  const save = async () => {
    setAttempted(true);
    setMessages([]);
    if (blocking.length > 0) {
      const first = blocking[0]!;
      toast.error(blocking.length === 1 ? "One thing needs fixing before saving" : `${blocking.length} things need fixing before saving`, { description: first.message });
      if (first.roundUid) {
        setEditingUid(first.roundUid);
      } else goTo(sectionOf(first.path));
      return;
    }
    setSaving(true);
    try {
      const result = await saveJobDraft({
        jobId,
        draft,
        requirements: serverRequirements,
        rounds: serverRounds,
        catalog,
        fallbackReportMode: settings.candidate_report_mode,
      });
      setJobErrors(result.jobErrors);
      setRoundErrors(result.roundErrors);
      setMessages(result.messages);
      if (!result.job) {
        toast.error("The job wasn't saved", { description: result.messages[0] ?? Object.values(result.jobErrors)[0] });
        const firstPath = Object.keys(result.jobErrors)[0];
        if (firstPath) goTo(sectionOf(firstPath));
        return;
      }
      const savedId = result.job.id;
      setJobId(savedId);
      queryClient.setQueryData(queryKeys.jobs.detail(savedId), result.job);
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });

      // Fresh server state becomes the new baseline for the next diff.
      const [freshRequirements, freshRounds] = await Promise.all([
        fetchRequirements(savedId).catch(() => serverRequirements),
        fetchRounds(savedId).catch(() => serverRounds),
      ]);
      setServerRequirements(freshRequirements);
      setServerRounds(freshRounds);
      const nextDraft: JobDraft = {
        ...draft,
        status: result.job.status ?? draft.status,
        rounds: draft.rounds.map((r) => {
          const id = r.id ?? result.createdRoundIds[r.uid] ?? null;
          const server = id ? freshRounds.find((fr) => fr.id === id) : undefined;
          return server && !result.roundErrors[r.uid] ? { ...roundDraftFrom(server), uid: r.uid } : { ...r, id };
        }),
      };
      setDraft(nextDraft);

      if (result.ok) {
        setBaseline(nextDraft);
        toast.success(job ? "Job saved" : "Job created");
        allowNext();
        navigate(`/app/jobs/${savedId}`);
      } else {
        // Only the parts that saved become the baseline; keep the rest dirty.
        setBaseline({ ...nextDraft, rounds: nextDraft.rounds.filter((r) => !result.roundErrors[r.uid]) });
        const failedRound = Object.keys(result.roundErrors)[0];
        toast.error("Some changes didn't save", { description: failedRound ? "A round needs attention." : (result.messages[0] ?? Object.values(result.jobErrors)[0]) });
        if (failedRound) setEditingUid(failedRound);
      }
    } finally {
      setSaving(false);
    }
  };

  const title = job ? job.title : draft.title.trim() || "New job";
  const breadcrumbs = job
    ? [{ label: "Jobs", to: "/app/jobs" }, { label: job.title, to: `/app/jobs/${job.id}` }, { label: "Edit" }]
    : [{ label: "Jobs", to: "/app/jobs" }, { label: "New job" }];

  const allIssues: JobIssue[] = [...blocking, ...warnings];

  return (
    <div>
      <PageHeader
        breadcrumbs={breadcrumbs}
        title={job ? `Edit ${title}` : "New job"}
        description="Describe the role, then set up the rounds the AI interviewer runs. Candidates only see what you mark as theirs."
        titleAddon={dirty ? <Badge tone="warning" size="sm">Unsaved changes</Badge> : null}
      />

      <div className="grid gap-8 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <nav aria-label="Job sections" className="max-lg:hidden">
          <ul className="sticky top-6 grid gap-0.5">
            {SECTIONS.map((section) => {
              const count = issueCount(section.id);
              return (
                <li key={section.id}>
                  <button
                    type="button"
                    onClick={() => goTo(section.id)}
                    aria-current={activeSection === section.id ? "true" : undefined}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-control px-3 py-1.5 text-left text-[13px] font-medium transition-colors",
                      activeSection === section.id ? "bg-surface-active text-fg" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                    )}
                  >
                    {section.label}
                    {count > 0 ? <span className="rounded-full bg-danger px-1.5 text-2xs font-semibold text-danger-contrast tabular-nums">{count}</span> : null}
                    {section.id === "rounds" && count === 0 ? <span className="text-xs text-fg-subtle tabular-nums">{draft.rounds.length}</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div ref={contentRef} className="grid min-w-0 gap-6">
          {messages.length > 0 ? (
            <Callout tone="danger" title="Some changes didn't save">
              <ul className="mt-1 list-disc pl-4">
                {messages.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </Callout>
          ) : null}

          {/* --------------------------------------------------------- Role */}
          <SectionBlock id="job-role" title="Role" description="The basics candidates and your team see first.">
            <div className="grid gap-4">
              <div className="grid gap-4 md:grid-cols-[1fr_12rem]">
                <FieldShell label="Job title" required error={fieldError("title")}>
                  {(control) => (
                    <Input {...control} value={draft.title} maxLength={200} placeholder="e.g. Senior DevOps Engineer" onChange={(e) => set("title", e.target.value)} autoFocus={!job} />
                  )}
                </FieldShell>
                <FieldShell label="Status" error={fieldError("status")}>
                  {(control) => <Select<JobStatus> id={control.id} value={draft.status} options={STATUS_OPTIONS} onValueChange={(v) => set("status", v)} />}
                </FieldShell>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <FieldShell label="Department" optional error={fieldError("department")}>
                  {(control) => <Input {...control} value={draft.department} placeholder="e.g. Platform Engineering" onChange={(e) => set("department", e.target.value)} />}
                </FieldShell>
                <FieldShell label="Location" optional error={fieldError("location")}>
                  {(control) => <Input {...control} value={draft.location} placeholder="e.g. Remote (Europe)" onChange={(e) => set("location", e.target.value)} />}
                </FieldShell>
                <FieldShell label="Employment type" optional>
                  {(control) => (
                    <Select<string> id={control.id} value={draft.employment_type || null} placeholder="Choose" options={EMPLOYMENT_TYPES} onValueChange={(v) => set("employment_type", v)} />
                  )}
                </FieldShell>
                <FieldShell label="Seniority" optional>
                  {(control) => (
                    <Select<string>
                      id={control.id}
                      value={draft.seniority || null}
                      placeholder="Choose"
                      options={
                        draft.seniority && !SENIORITY_LEVELS.some((s) => s.value === draft.seniority)
                          ? [{ value: draft.seniority, label: draft.seniority }, ...SENIORITY_LEVELS]
                          : SENIORITY_LEVELS
                      }
                      onValueChange={(v) => set("seniority", v)}
                    />
                  )}
                </FieldShell>
              </div>
              <div className="grid max-w-md gap-4 sm:grid-cols-2">
                <FieldShell label="Experience from" optional error={fieldError("experience_min_years")}>
                  {(control) => <NumberInput {...control} value={draft.experience_min_years} nullable min={0} max={60} unit="years" onChange={(v) => set("experience_min_years", v)} />}
                </FieldShell>
                <FieldShell label="Up to" optional error={fieldError("experience_max_years")}>
                  {(control) => <NumberInput {...control} value={draft.experience_max_years} nullable min={0} max={60} unit="years" onChange={(v) => set("experience_max_years", v)} />}
                </FieldShell>
              </div>
            </div>
          </SectionBlock>

          {/* -------------------------------------------------- Description */}
          <SectionBlock id="job-description" title="Description" description="Used for resume matching and interview planning, and shown to candidates.">
            <div className="grid gap-5">
              <FieldShell label="Job description" error={fieldError("description")}>
                {(control) => (
                  <MarkdownField
                    {...control}
                    split
                    rows={16}
                    maxLength={20_000}
                    value={draft.description}
                    placeholder={"## About the role\n\n## What you'll do\n\n## What we're looking for"}
                    onChange={(v) => set("description", v)}
                  />
                )}
              </FieldShell>
              <FieldShell label="Practical notes for candidates" optional description="Setup tips shown before the interview, e.g. a quiet room and a working microphone.">
                {(control) => (
                  <Textarea {...control} rows={3} autoResize maxRows={8} value={draft.interview_instructions} onChange={(e) => set("interview_instructions", e.target.value)} />
                )}
              </FieldShell>
            </div>
          </SectionBlock>

          {/* ------------------------------------------------------- Skills */}
          <SectionBlock id="job-skills" title="Skills" description="Linked to your competency catalog where possible, so resumes are matched and interviews probe the same skills.">
            <div className="grid gap-5">
              <FieldShell label="Required skills" error={fieldError("required_skills")} warning={fieldWarning("required_skills")} description="Pick from the catalog or type a skill and press Enter.">
                {(control) => (
                  <MultiSelect
                    {...control}
                    creatable
                    value={draft.required_skills}
                    options={skillOptions}
                    placeholder="e.g. Kubernetes, Terraform"
                    onChange={(v) => set("required_skills", v)}
                  />
                )}
              </FieldShell>
              <FieldShell label="Nice to have" optional error={fieldError("preferred_skills")}>
                {(control) => (
                  <MultiSelect {...control} creatable value={draft.preferred_skills} options={skillOptions} placeholder="e.g. Go, cost optimisation" onChange={(v) => set("preferred_skills", v)} />
                )}
              </FieldShell>
              <SkillLinkSummary values={[...draft.required_skills, ...draft.preferred_skills]} catalog={catalog} />
            </div>
          </SectionBlock>

          {/* ------------------------------------------------------- Rounds */}
          <SectionBlock
            id="job-rounds"
            title="Rounds"
            description="Each round is one AI interview. Candidates take the round you invite them to, or choose one when you allow it."
            actions={
              <Button size="sm" leftIcon={<Plus />} onClick={() => setAddOpen(true)}>
                Add round
              </Button>
            }
          >
            <div className="grid gap-3">
              {fieldError("rounds") ? <p className="text-xs font-medium text-danger-fg">{fieldError("rounds")}</p> : null}
              {!fieldError("rounds") && fieldWarning("rounds") ? <p className="text-xs font-medium text-warning-fg">{fieldWarning("rounds")}</p> : null}
              {draft.rounds.length === 0 ? (
                <div className="rounded-lg border border-dashed border-line-strong bg-surface-subtle/60 px-6 py-10 text-center">
                  <p className="text-sm font-medium text-fg">No rounds yet</p>
                  <p className="mt-1 text-sm text-fg-muted">Start from a round type such as Technical screening, or from one of your templates.</p>
                  <Button className="mt-4" size="sm" variant="outline" leftIcon={<Plus />} onClick={() => setAddOpen(true)}>
                    Add the first round
                  </Button>
                </div>
              ) : (
                draft.rounds.map((round, index) => {
                  const roundIssueList = issues.filter((i) => i.roundUid === round.uid);
                  const errorCount = roundIssueList.filter((i) => i.severity === "error").length + Object.keys(roundErrors[round.uid] ?? {}).length;
                  return (
                    <RoundCard
                      key={round.uid}
                      index={index}
                      config={round.config}
                      catalog={catalog}
                      inactive={!round.is_active}
                      errorCount={errorCount}
                      warningCount={roundIssueList.filter((i) => i.severity === "warning").length}
                      onEdit={() => setEditingUid(round.uid)}
                      onMoveUp={index > 0 ? () => setRounds((rs) => swap(rs, index, index - 1)) : undefined}
                      onMoveDown={index < draft.rounds.length - 1 ? () => setRounds((rs) => swap(rs, index, index + 1)) : undefined}
                      onDuplicate={() =>
                        setRounds((rs) => [
                          ...rs.slice(0, index + 1),
                          { ...round, uid: localId("round"), id: null, config: { ...round.config, name: `${round.config.name} (copy)` } },
                          ...rs.slice(index + 1),
                        ])
                      }
                      onRemove={() => setRounds((rs) => rs.filter((r) => r.uid !== round.uid))}
                      footer={
                        roundErrors[round.uid]?.[""] ? <p className="text-xs font-medium text-danger-fg">{roundErrors[round.uid]![""]}</p> : null
                      }
                    />
                  );
                })
              )}
            </div>
          </SectionBlock>

          {/* ------------------------------------------- Candidate experience */}
          <SectionBlock id="job-experience" title="Candidate experience" description="What candidates choose and see.">
            <div className="grid gap-5">
              <FieldShell label="Candidate report" description="Candidates get a short, evidence-based feedback report when this allows it.">
                {(control) => (
                  <Select<string>
                    id={control.id}
                    className="max-w-md"
                    value={draft.candidate_report_mode ?? ORG_DEFAULT}
                    onValueChange={(v) => set("candidate_report_mode", v === ORG_DEFAULT ? null : (v as CandidateReportMode))}
                    options={[
                      { value: ORG_DEFAULT, label: `Organization default: ${CANDIDATE_REPORT_MODE_LABELS[settings.candidate_report_mode].toLowerCase()}` },
                      ...(Object.keys(CANDIDATE_REPORT_MODE_LABELS) as CandidateReportMode[]).map((m) => ({ value: m, label: CANDIDATE_REPORT_MODE_LABELS[m] })),
                    ]}
                  />
                )}
              </FieldShell>
              <ToggleRow
                className="py-0"
                label="Let candidates choose their round"
                description={
                  fieldWarning("allow_candidate_round_selection") ??
                  "Invitations can offer every round marked “Candidate can pick”. Otherwise you choose the round when inviting."
                }
                checked={draft.allow_candidate_round_selection}
                onCheckedChange={(v) => set("allow_candidate_round_selection", v)}
              />
            </div>
          </SectionBlock>

          {/* ---------------------------------------------------- Integrity */}
          <SectionBlock
            id="job-integrity"
            title="Integrity"
            description="Signals are observations for a human reviewer, never a verdict. Candidates see exactly what is monitored before they consent."
          >
            <IntegrityOverrides
              defaults={settings.integrity}
              overrides={draft.integrity_overrides}
              onChange={(v) => set("integrity_overrides", v)}
              errors={jobErrors}
            />
          </SectionBlock>

          {/* ---------------------------------------------------- Recording */}
          <SectionBlock id="job-recording" title="Recording and retention" description="What is stored for this job and for how long. Defaults come from your organization settings.">
            <RecordingOverrides
              settings={settings}
              recording={draft.recording_overrides}
              retention={draft.retention_overrides}
              onRecordingChange={(v) => set("recording_overrides", v)}
              onRetentionChange={(v) => set("retention_overrides", v)}
              errors={jobErrors}
            />
          </SectionBlock>
        </div>
      </div>

      {/* --------------------------------------------------------- Save bar */}
      <div className="sticky bottom-0 z-30 -mx-4 mt-8 -mb-6 border-t border-line bg-surface/95 backdrop-blur-sm sm:-mx-6 lg:-mx-8 lg:-mb-8">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <Popover open={issuesOpen} onOpenChange={setIssuesOpen}>
            <PopoverTrigger asChild>
              <button type="button" className="inline-flex items-center gap-2 rounded-control px-2 py-1 text-sm hover:bg-surface-hover" disabled={allIssues.length === 0}>
                {blocking.length > 0 ? (
                  <>
                    <AlertCircle className="size-4 text-danger" aria-hidden="true" />
                    <span className="font-medium text-fg">
                      {blocking.length} {blocking.length === 1 ? "issue" : "issues"} to fix
                    </span>
                  </>
                ) : warnings.length > 0 ? (
                  <>
                    <ListChecks className="size-4 text-warning" aria-hidden="true" />
                    <span className="text-fg-muted">
                      Ready to save, {warnings.length} {warnings.length === 1 ? "suggestion" : "suggestions"}
                    </span>
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
                    <span className="text-fg-muted">Everything checks out</span>
                  </>
                )}
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-[26rem] max-w-[calc(100vw-2rem)] p-0">
              <ul className="max-h-80 divide-y divide-line-subtle overflow-y-auto scrollbar-thin">
                {allIssues.map((issue, index) => {
                  const round = issue.roundUid ? draft.rounds.find((r) => r.uid === issue.roundUid) : null;
                  return (
                    <li key={`${issue.path}-${index}`}>
                      <button
                        type="button"
                        className="flex w-full items-start gap-2.5 px-4 py-2.5 text-left text-sm hover:bg-surface-hover"
                        onClick={() => {
                          setIssuesOpen(false);
                          if (issue.roundUid) setEditingUid(issue.roundUid);
                          else goTo(sectionOf(issue.path));
                        }}
                      >
                        <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", issue.severity === "error" ? "bg-danger" : "bg-warning")} aria-hidden="true" />
                        <span className="grid gap-0.5">
                          <span className="text-xs text-fg-muted">{round ? `Round: ${round.config.name || "Untitled"}` : SECTIONS.find((s) => s.id === sectionOf(issue.path))?.label}</span>
                          <span className="text-fg">{issue.message}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </PopoverContent>
          </Popover>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => navigate(job ? `/app/jobs/${job.id}` : "/app/jobs")} disabled={saving}>
              Cancel
            </Button>
            <Button leftIcon={<Save />} loading={saving} loadingText="Saving…" onClick={save} disabled={!dirty && Boolean(jobId) && Object.keys(roundErrors).length === 0}>
              {job || jobId ? "Save changes" : "Create job"}
            </Button>
          </div>
        </div>
      </div>

      <AddRoundDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onPick={(config, templateId) => {
          const round = newRoundDraft(config, templateId);
          setRounds((rs) => [...rs, round]);
          setEditingUid(round.uid);
        }}
      />

      <Sheet open={editing !== null} onOpenChange={(open) => !open && setEditingUid(null)}>
        <SheetContent size="xl" className="max-w-[min(960px,100vw)]">
          {editing ? (
            <>
              <SheetHeader>
                <SheetTitle>
                  Round {editingIndex + 1}: {editing.config.name || "Untitled"}
                </SheetTitle>
                <SheetDescription>
                  {ROUND_TYPE_LABELS[editing.config.round_type]}. Changes apply to this job when you save it.
                </SheetDescription>
              </SheetHeader>
              <SheetBody>
                {roundErrors[editing.uid]?.[""] ? (
                  <Callout tone="danger" className="mb-4">
                    {roundErrors[editing.uid]![""]}
                  </Callout>
                ) : null}
                <RoundConfigEditor
                  value={editing.config}
                  onChange={(config) => {
                    setRounds((rs) => rs.map((r) => (r.uid === editing.uid ? { ...r, config } : r)));
                    if (roundErrors[editing.uid]) setRoundErrors((errs) => ({ ...errs, [editing.uid]: {} }));
                  }}
                  catalog={catalog}
                  problems={problems}
                  serverErrors={roundErrors[editing.uid]}
                />
              </SheetBody>
              <SheetFooter className="justify-between">
                <ToggleRow
                  className="py-0"
                  label="Active"
                  checked={editing.is_active}
                  onCheckedChange={(is_active) => setRounds((rs) => rs.map((r) => (r.uid === editing.uid ? { ...r, is_active } : r)))}
                />
                <Button onClick={() => setEditingUid(null)}>Done</Button>
              </SheetFooter>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function swap<T>(items: T[], a: number, b: number): T[] {
  const next = [...items];
  [next[a], next[b]] = [next[b]!, next[a]!];
  return next;
}

function SkillLinkSummary({ values, catalog }: { values: string[]; catalog: Competency[] }) {
  if (values.length === 0) return null;
  const linked = values.filter((v) => catalog.some((c) => c.key === v)).length;
  const free = values.length - linked;
  return (
    <p className="text-xs text-fg-muted">
      <span className="font-medium text-fg tabular-nums">{linked}</span> linked to competencies
      {free > 0 ? (
        <>
          , <span className="font-medium text-fg tabular-nums">{free}</span> free-text {free === 1 ? "skill" : "skills"} (matched against resumes by meaning)
        </>
      ) : null}
      .
    </p>
  );
}
