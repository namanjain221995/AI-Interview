import { AlertCircle, RotateCcw, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { useDocumentTitle } from "@/lib/hooks";
import { CANDIDATE_REPORT_MODE_LABELS } from "@/lib/labels";
import type { CandidateReportMode, OrganizationSettings } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useOrgSettings, useSaveSettings } from "@/features/recruiter/admin/api/admin";
import { DetailSkeleton, LoadError } from "@/features/recruiter/admin/components/Controls";
import { FieldShell, SectionBlock, ToggleRow } from "@/features/recruiter/admin/components/Field";
import { MarkdownField } from "@/features/recruiter/admin/components/MarkdownField";
import { useUnsavedChanges } from "@/features/recruiter/admin/components/useUnsavedChanges";
import { describeFailure, fieldErrorsOf } from "@/features/recruiter/admin/lib/serverErrors";
import { jsonEqual } from "@/features/recruiter/admin/lib/utils";
import { IntegrityRulesEditor } from "@/features/recruiter/admin/policies/IntegrityRulesEditor";
import { PolicyFieldsEditor } from "@/features/recruiter/admin/policies/PolicyFieldsEditor";
import { useEnforcedPolicyFields } from "@/lib/auth";
import { INTEGRITY_FIELDS, INTEGRITY_GROUPS, RECORDING_FIELDS, RETENTION_FIELDS } from "@/features/recruiter/admin/policies/policyFields";
import { InterviewerSettings } from "@/features/recruiter/admin/settings/InterviewerSettings";
import { describeDays, toOrganizationSettings, validateSettings } from "@/features/recruiter/admin/settings/settingsModel";

const SECTIONS = [
  { id: "integrity", label: "Integrity" },
  { id: "recording", label: "Recording" },
  { id: "retention", label: "Data retention" },
  { id: "interviewer", label: "Interviewer" },
  { id: "reports", label: "Candidate reports" },
  { id: "access", label: "Access" },
  { id: "notice", label: "Data notice" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

function sectionOf(path: string): SectionId {
  const head = path.split(".")[0];
  switch (head) {
    case "integrity":
      return "integrity";
    case "recording":
      return "recording";
    case "retention":
      return "retention";
    case "avatar":
      return "interviewer";
    case "candidate_report_mode":
      return "reports";
    case "require_access_code":
      return "access";
    default:
      return "notice";
  }
}

const REPORT_HELP: Record<CandidateReportMode, string> = {
  immediate: "Candidates see their feedback report as soon as it's generated.",
  after_review: "A reviewer reads the report and releases it to the candidate.",
  never: "Candidates don't receive a report.",
};

/** /app/settings (settings:manage) */
export default function SettingsPage() {
  useDocumentTitle("Settings");
  const query = useOrgSettings();

  if (query.error) {
    return (
      <PageContainer>
        <PageHeader title="Settings" />
        <LoadError error={query.error} onRetry={() => void query.refetch()} />
      </PageContainer>
    );
  }
  if (!query.data) {
    return (
      <PageContainer>
        <PageHeader title="Settings" description="Organization defaults for interviews, privacy and retention." />
        <DetailSkeleton />
      </PageContainer>
    );
  }
  return <SettingsForm initial={query.data} />;
}

function SettingsForm({ initial }: { initial: OrganizationSettings }) {
  const lockedIntegrity = useEnforcedPolicyFields("integrity");
  const lockedRecording = useEnforcedPolicyFields("recording");
  const [draft, setDraft] = useState<OrganizationSettings>(initial);
  const [baseline, setBaseline] = useState<OrganizationSettings>(initial);
  const [attempted, setAttempted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [active, setActive] = useState<SectionId>("integrity");
  const save = useSaveSettings();
  const dirty = !jsonEqual(draft, baseline);
  useUnsavedChanges(dirty);

  const issues = useMemo(() => validateSettings(draft), [draft]);
  const issueMap = useMemo(() => Object.fromEntries(issues.map((i) => [i.path, i.message])), [issues]);
  const errors = { ...(attempted ? issueMap : {}), ...serverErrors };
  const countFor = (section: SectionId) => Object.keys(errors).filter((p) => sectionOf(p) === section).length;

  useEffect(() => {
    const root = document.getElementById("main-content");
    const nodes = SECTIONS.map((s) => document.getElementById(`settings-${s.id}`)).filter((n): n is HTMLElement => Boolean(n));
    if (!("IntersectionObserver" in window) || !nodes.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id.replace("settings-", "") as SectionId);
      },
      { root, rootMargin: "-10% 0px -70% 0px" },
    );
    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);

  const goTo = (id: SectionId) => {
    document.getElementById(`settings-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(id);
  };

  const set = <K extends keyof OrganizationSettings>(key: K, value: OrganizationSettings[K]) => setDraft((d) => ({ ...d, [key]: value }));

  const submit = async () => {
    setAttempted(true);
    setFormError(null);
    setServerErrors({});
    if (issues.length) {
      goTo(sectionOf(issues[0]!.path));
      toast.error(issues.length === 1 ? "One setting needs attention" : `${issues.length} settings need attention`, { description: issues[0]!.message });
      return;
    }
    try {
      const saved = await save.mutateAsync(toOrganizationSettings(draft));
      const normalized = { ...draft, ...saved };
      setDraft(normalized);
      setBaseline(normalized);
      toast.success("Settings saved", { description: "They apply to invitations sent from now on. Existing invitations keep the policy they were sent with." });
    } catch (error) {
      const fields = fieldErrorsOf(error);
      setServerErrors(fields);
      if (!Object.keys(fields).length) setFormError(describeFailure(error));
      else goTo(sectionOf(Object.keys(fields)[0]!));
    }
  };

  return (
    <PageContainer>
      <PageHeader
        title="Settings"
        description="Organization defaults for interviews, privacy and retention. Jobs can override integrity and recording."
        titleAddon={dirty ? <Badge tone="warning" size="sm">Unsaved changes</Badge> : null}
      />
      <div className="grid gap-8 lg:grid-cols-[12rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="max-lg:hidden">
          <ul className="sticky top-6 grid gap-0.5">
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <button
                  type="button"
                  onClick={() => goTo(section.id)}
                  aria-current={active === section.id ? "true" : undefined}
                  className={cn(
                    "flex w-full items-center justify-between rounded-control px-3 py-1.5 text-left text-[13px] font-medium transition-colors",
                    active === section.id ? "bg-surface-active text-fg" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                  )}
                >
                  {section.label}
                  {countFor(section.id) ? <span className="rounded-full bg-danger px-1.5 text-2xs font-semibold text-danger-contrast tabular-nums">{countFor(section.id)}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="grid min-w-0 gap-6">
          {formError ? <Callout tone="danger">{formError}</Callout> : null}

          <SectionBlock
            id="settings-integrity"
            title="Integrity defaults"
            description="What is monitored during interviews. Every signal is an observation for a human reviewer, never a verdict, and candidates see exactly what is monitored before they consent."
          >
            <div className="grid gap-8">
              <PolicyFieldsEditor
                fields={INTEGRITY_FIELDS}
                groups={INTEGRITY_GROUPS}
                values={draft.integrity as unknown as Record<string, unknown>}
                onChange={(key, value) => set("integrity", { ...draft.integrity, [key]: value })}
                errors={errors}
                errorPrefix="integrity"
                locked={lockedIntegrity}
              />
              <div className="grid gap-3">
                <div className="grid gap-0.5">
                  <h3 className="text-sm font-semibold text-fg">Signal rules</h3>
                  <p className="text-xs text-fg-muted">How specific signals are handled, for example only flagging repeated tab switches. Camera and screen observations can never pause or end an interview.</p>
                </div>
                <IntegrityRulesEditor value={draft.integrity.rules} onChange={(rules) => set("integrity", { ...draft.integrity, rules })} errors={errors} />
              </div>
            </div>
          </SectionBlock>

          <SectionBlock id="settings-recording" title="Recording" description="What is stored from each interview, beyond the transcript.">
            <PolicyFieldsEditor
              fields={RECORDING_FIELDS}
              values={draft.recording as unknown as Record<string, unknown>}
              onChange={(key, value) => set("recording", { ...draft.recording, [key]: value })}
              errors={errors}
              errorPrefix="recording"
              locked={lockedRecording}
            />
          </SectionBlock>

          <SectionBlock id="settings-retention" title="Data retention" description="Recordings, snapshots and uploaded files are deleted automatically after these periods. Transcripts, reports and text read from résumés are not deleted automatically yet: candidates are told they stay until you delete them.">
            <PolicyFieldsEditor
              fields={RETENTION_FIELDS.map((f) => ({ ...f, help: `${f.help} ${describeDays(draft.retention[f.key])}.` }))}
              values={draft.retention as unknown as Record<string, unknown>}
              onChange={(key, value) => set("retention", { ...draft.retention, [key]: value })}
              errors={errors}
              errorPrefix="retention"
            />
          </SectionBlock>

          <SectionBlock id="settings-interviewer" title="Interviewer" description="The AI interviewer's name, voice and look.">
            <InterviewerSettings value={draft.avatar} onChange={(avatar) => set("avatar", avatar)} errors={errors} />
          </SectionBlock>

          <SectionBlock id="settings-reports" title="Candidate reports" description="Candidates can receive a short, evidence-based feedback report. Jobs can choose differently.">
            <RadioGroup value={draft.candidate_report_mode} onValueChange={(v) => set("candidate_report_mode", v as CandidateReportMode)} aria-label="When candidates see their report">
              {(Object.keys(CANDIDATE_REPORT_MODE_LABELS) as CandidateReportMode[]).map((mode) => (
                <RadioGroupItem key={mode} value={mode} label={CANDIDATE_REPORT_MODE_LABELS[mode]} description={REPORT_HELP[mode]} />
              ))}
            </RadioGroup>
          </SectionBlock>

          <SectionBlock id="settings-access" title="Access" description="How candidates open their interview.">
            <ToggleRow
              className="py-0"
              label="Always require an access code"
              description="Every invitation gets an access code to send separately from the link. When off, recruiters choose per invitation."
              checked={draft.require_access_code}
              onCheckedChange={(v) => set("require_access_code", v)}
            />
          </SectionBlock>

          <SectionBlock id="settings-notice" title="Data processing notice" description="Shown to every candidate before they consent. Plain language works best.">
            <FieldShell error={errors.data_processing_notice}>
              {(control) => <MarkdownField {...control} rows={6} maxLength={4000} value={draft.data_processing_notice} onChange={(v) => set("data_processing_notice", v)} />}
            </FieldShell>
          </SectionBlock>
        </div>
      </div>

      <div className="sticky bottom-0 z-30 -mx-4 mt-8 -mb-6 border-t border-line bg-surface/95 backdrop-blur-sm sm:-mx-6 lg:-mx-8 lg:-mb-8">
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <p className="flex items-center gap-2 text-sm text-fg-muted">
            {attempted && issues.length ? (
              <>
                <AlertCircle className="size-4 text-danger" aria-hidden="true" />
                {issues.length} {issues.length === 1 ? "setting needs" : "settings need"} attention
              </>
            ) : dirty ? (
              "Changes apply to invitations sent after you save."
            ) : (
              "All changes saved."
            )}
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" leftIcon={<RotateCcw />} disabled={!dirty || save.isPending} onClick={() => { setDraft(baseline); setAttempted(false); setServerErrors({}); }}>
              Discard
            </Button>
            <Button leftIcon={<Save />} disabled={!dirty} loading={save.isPending} loadingText="Saving…" onClick={() => void submit()}>
              Save settings
            </Button>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
