import { useMemo } from "react";
import { useParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { PageHeader } from "@/components/ui/PageHeader";
import { useDocumentTitle } from "@/lib/hooks";

import { useOrgSettings } from "@/features/recruiter/admin/api/admin";
import { useCompetencies } from "@/features/recruiter/admin/api/catalog";
import { useJob, useJobRequirements, useJobRounds } from "@/features/recruiter/admin/api/jobs";
import { useCodingQuestions } from "@/features/recruiter/admin/api/library";
import { DetailSkeleton, LoadError } from "@/features/recruiter/admin/components/Controls";
import { emptyJobDraft, jobDraftFrom } from "@/features/recruiter/admin/jobs/jobDraft";
import { JobEditor } from "@/features/recruiter/admin/jobs/JobEditor";
import { defaultOrganizationSettings } from "@/features/recruiter/admin/settings/settingsModel";

/** /app/jobs/new and /app/jobs/:jobId/edit */
export default function JobEditorPage() {
  const { jobId } = useParams();
  const isNew = !jobId;
  const job = useJob(jobId);
  const requirements = useJobRequirements(jobId);
  const rounds = useJobRounds(jobId);
  const competencies = useCompetencies();
  const settings = useOrgSettings();
  const problems = useCodingQuestions();
  useDocumentTitle(isNew ? "New job" : job.data ? `Edit ${job.data.title}` : "Edit job");

  const loading = (!isNew && (job.isLoading || requirements.isLoading || rounds.isLoading)) || competencies.isLoading || settings.isLoading;
  const error = !isNew ? (job.error ?? requirements.error ?? rounds.error) : null;

  const initialDraft = useMemo(() => {
    if (isNew) return emptyJobDraft();
    if (!job.data || !requirements.data || !rounds.data) return null;
    return jobDraftFrom(job.data, requirements.data, rounds.data);
    // The draft is seeded once per job; later refetches must not overwrite edits.
  }, [isNew, jobId, Boolean(job.data && requirements.data && rounds.data)]);

  if (error) {
    return (
      <PageContainer>
        <PageHeader breadcrumbs={[{ label: "Jobs", to: "/app/jobs" }, { label: "Edit" }]} title="Edit job" />
        <LoadError error={error} onRetry={() => void Promise.all([job.refetch(), requirements.refetch(), rounds.refetch()])} />
      </PageContainer>
    );
  }

  if (loading || !initialDraft) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <JobEditor
        key={jobId ?? "new"}
        job={job.data ?? null}
        requirements={requirements.data ?? []}
        rounds={rounds.data ?? []}
        initialDraft={initialDraft}
        catalog={competencies.data ?? []}
        settings={settings.data ?? defaultOrganizationSettings()}
        problems={problems.data?.items}
      />
    </PageContainer>
  );
}
