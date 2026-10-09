import { Rows3, UserRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Callout } from "@/components/ui/Callout";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { toast } from "@/components/ui/Toaster";
import { errorMessage } from "@/lib/api";
import { useOrganization } from "@/lib/auth";
import { useDocumentTitle } from "@/lib/hooks";

import { useOrgSettings } from "@/features/recruiter/admin/api/admin";
import { uploadResume } from "@/features/recruiter/admin/api/candidates";

import { DEFAULT_INTAKE_DEFAULTS, isIntakeDisabled, useIntakeConfig, useIntakeDetail, useIntakeSettings } from "../api";
import { BatchCreate } from "../components/create/BatchCreate";
import { InviteResult, type ResumeUploadState } from "../components/create/InviteResult";
import { SingleCreateForm } from "../components/create/SingleCreateForm";
import { IntakeDisabledState, IntakeLoadError } from "../components/IntakeStates";
import { useReviewerOptions } from "../components/reviewers";
import { defaultSettingsForm, defaultSingleForm, formFromDraft, type InviteSettingsForm, type SingleForm } from "../lib/createForm";
import type { IntakeCreateResponse } from "../types";

/** /app/intake/new: one invitation, a batch (`?mode=batch`) or a saved draft (`?draft=<id>`). */
export default function IntakeCreatePage() {
  const [params, setParams] = useSearchParams();
  const draftId = params.get("draft");
  const mode = !draftId && params.get("mode") === "batch" ? "batch" : "single";
  useDocumentTitle(draftId ? "Edit intake draft" : "Create intake interview");
  const navigate = useNavigate();
  const organization = useOrganization();

  const config = useIntakeConfig();
  const intakeSettings = useIntakeSettings();
  const orgSettings = useOrgSettings();
  const draft = useIntakeDetail(draftId ?? undefined);
  const reviewers = useReviewerOptions(draft.data ? [draft.data.assigned_reviewer] : []);

  const settingsSettled = !intakeSettings.isPending || intakeSettings.isError;
  const configSettled = !config.isPending || config.isError;
  const defaults = intakeSettings.data?.defaults ?? config.data?.defaults ?? DEFAULT_INTAKE_DEFAULTS;
  const orgRequiresCode = orgSettings.data?.require_access_code ?? false;
  const programName = intakeSettings.data?.program_name ?? null;

  const [form, setForm] = useState<SingleForm | null>(null);
  const [batchSettings, setBatchSettings] = useState<InviteSettingsForm | null>(null);
  const [result, setResult] = useState<IntakeCreateResponse | null>(null);
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [resume, setResume] = useState<ResumeUploadState>({ status: "idle" });

  // Start from the organisation defaults once they're known (or the draft once loaded).
  useEffect(() => {
    if (form || !settingsSettled || !configSettled) return;
    if (draftId) {
      if (draft.data) setForm(formFromDraft(draft.data, defaults));
      return;
    }
    const onlyJob = config.data?.jobs.length === 1 ? config.data.jobs[0]! : null;
    setForm(defaultSingleForm(defaults, { requireAccessCode: orgRequiresCode, jobId: onlyJob?.job_id ?? null, roundId: onlyJob?.round_id ?? null }));
  }, [form, settingsSettled, configSettled, draftId, draft.data, defaults, orgRequiresCode, config.data]);

  useEffect(() => {
    if (!batchSettings && settingsSettled) setBatchSettings(defaultSettingsForm(defaults, { requireAccessCode: orgRequiresCode }));
  }, [batchSettings, settingsSettled, defaults, orgRequiresCode]);

  const upload = useCallback(
    async (candidateId: string, file: File, jobId: string | null) => {
      setResume({ status: "uploading", name: file.name, percent: 0 });
      try {
        await uploadResume(candidateId, file, { jobId, onProgress: (percent) => setResume({ status: "uploading", name: file.name, percent }) });
        setResume({ status: "done", name: file.name });
      } catch (error) {
        setResume({ status: "failed", name: file.name, message: errorMessage(error) });
      }
    },
    [],
  );

  const onCreated = (created: IntakeCreateResponse) => {
    setResult(created);
    if (resumeFile && created.intake.candidate.id) void upload(created.intake.candidate.id, resumeFile, created.intake.job.id || form?.job_id || null);
  };

  const onDraftSaved = (id: string) => {
    toast.success("Draft saved", { description: "Find it under Drafts in the intake list." });
    if (resumeFile && form?.candidate?.id) void upload(form.candidate.id, resumeFile, form.job_id);
    navigate(`/app/intake?bucket=drafts`, { state: { savedDraft: id } });
  };

  const startAnother = () => {
    setResult(null);
    setResume({ status: "idle" });
    setResumeFile(null);
    setForm(defaultSingleForm(defaults, { requireAccessCode: orgRequiresCode, jobId: form?.job_id ?? null, roundId: form?.round_id ?? null }));
    if (draftId) setParams({}, { replace: true });
  };

  const disabled = isIntakeDisabled(config.error) || isIntakeDisabled(draft.error);
  const notDraft = draft.data && draft.data.status !== "draft";

  return (
    <PageContainer>
      <PageHeader
        breadcrumbs={[{ label: "Intake interviews", to: "/app/intake" }, { label: draftId ? "Draft" : "Create" }]}
        title={draftId ? "Finish the draft" : "Create intake interview"}
        description="The candidate gets a personal link to a 35-50 minute interview in seven parts. A person reviews the result and decides."
      />
      <div className="grid w-full max-w-3xl grid-cols-1 gap-5" data-testid="intake-create-page">
        {disabled ? (
          <IntakeDisabledState />
        ) : result ? (
          <InviteResult
            result={result}
            programName={programName}
            organizationName={organization?.name ?? null}
            durationMinutes={form?.settings.duration_minutes ?? defaults.duration_minutes}
            resume={resume}
            onRetryResume={resumeFile ? () => void upload(result.intake.candidate.id, resumeFile, result.intake.job.id || null) : undefined}
            onCreateAnother={startAnother}
          />
        ) : draftId && draft.isError ? (
          <IntakeLoadError error={draft.error} title="Couldn't load this draft" onRetry={() => void draft.refetch()} />
        ) : notDraft ? (
          <Callout tone="info" title="This intake was already sent">
            Follow it, or generate a new link, from its page. <Link to={`/app/intake/${draft.data!.id}`}>Open the intake</Link>
          </Callout>
        ) : (
          <>
            {!draftId ? (
              <Tabs value={mode} onValueChange={(v) => setParams(v === "batch" ? { mode: "batch" } : {}, { replace: true })} variant="segmented">
                <TabsList aria-label="How many candidates">
                  <TabsTrigger value="single" data-testid="intake-mode-single">
                    <UserRound /> One candidate
                  </TabsTrigger>
                  <TabsTrigger value="batch" data-testid="intake-mode-batch">
                    <Rows3 /> Batch
                  </TabsTrigger>
                </TabsList>
              </Tabs>
            ) : null}
            {config.isError && !isIntakeDisabled(config.error) ? <IntakeLoadError error={config.error} title="Couldn't load the intake configuration" onRetry={() => void config.refetch()} /> : null}
            {mode === "batch" ? (
              batchSettings ? (
                <BatchCreate config={config.data} configLoading={config.isPending} orgRequiresCode={orgRequiresCode} reviewers={reviewers} settings={batchSettings} onSettingsChange={setBatchSettings} />
              ) : (
                <Skeleton className="h-96 w-full" />
              )
            ) : form ? (
              <SingleCreateForm
                form={form}
                onChange={setForm}
                config={config.data}
                configLoading={config.isPending}
                orgRequiresCode={orgRequiresCode}
                reviewers={reviewers}
                draft={draftId ? (draft.data ?? null) : null}
                resumeFile={resumeFile}
                onResumeFile={setResumeFile}
                onCreated={onCreated}
                onDraftSaved={onDraftSaved}
              />
            ) : (
              <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
                <Skeleton className="h-40 w-full" />
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-64 w-full" />
              </div>
            )}
          </>
        )}
      </div>
    </PageContainer>
  );
}
