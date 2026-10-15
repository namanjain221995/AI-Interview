import { Archive, ChevronDown, CircleSlash, Code2, Pencil, Play, UserPlus } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { confirm } from "@/components/ui/AlertDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { EmptyState } from "@/components/ui/EmptyState";
import { Markdown } from "@/components/ui/Markdown";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard, StatGroup } from "@/components/ui/StatCard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { usePermission } from "@/lib/auth";
import { formatNumber } from "@/lib/format";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { CANDIDATE_REPORT_MODE_LABELS, CODE_LANGUAGE_LABELS, DIFFICULTY_LABELS } from "@/lib/labels";
import type { JobRequirement, JobStatus, RequirementKind } from "@/lib/types";

import { useOrgSettings } from "@/features/recruiter/admin/api/admin";
import { useCompetencies } from "@/features/recruiter/admin/api/catalog";
import { useJobPipeline, type PipelineStage } from "@/features/recruiter/admin/api/candidates";
import { useJob, useJobRequirements, useJobRounds, useUpdateJobStatus } from "@/features/recruiter/admin/api/jobs";
import { DetailSkeleton, Fact, LoadError } from "@/features/recruiter/admin/components/Controls";
import { InviteDialog } from "@/features/recruiter/admin/jobs/InviteDialog";
import { EMPLOYMENT_TYPES, SENIORITY_LEVELS } from "@/features/recruiter/admin/jobs/jobDraft";
import { JobPipeline } from "@/features/recruiter/admin/jobs/JobPipeline";
import { describeFailure } from "@/features/recruiter/admin/lib/serverErrors";
import { RoundCard } from "@/features/recruiter/admin/rounds/RoundCard";
import { differingIntegrityKeys } from "@/features/recruiter/admin/jobs/jobDraft";
import { JobLinksPanel } from "@/features/links/components/JobLinksPanel";

function labelOf(list: ReadonlyArray<{ value: string; label: string }>, value: string | null | undefined): string | null {
  if (!value) return null;
  return list.find((o) => o.value === value)?.label ?? value;
}

function experienceRange(min: number | null, max: number | null): string | null {
  if (min === null && max === null) return null;
  if (min !== null && max !== null) return `${min} to ${max} years`;
  if (min !== null) return `${min}+ years`;
  return `Up to ${max} years`;
}

const KIND_ORDER: RequirementKind[] = ["required_skill", "preferred_skill", "responsibility", "qualification"];
const KIND_HEADINGS: Record<RequirementKind, string> = {
  required_skill: "Required skills",
  preferred_skill: "Nice to have",
  responsibility: "Responsibilities",
  qualification: "Qualifications",
};

const PIPELINE_PAGE_SIZE = 25;

/** /app/jobs/:jobId */
export default function JobDetailPage() {
  const { jobId } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "pipeline";
  const job = useJob(jobId);
  const requirements = useJobRequirements(jobId);
  const rounds = useJobRounds(jobId);
  const [pipelinePage, setPipelinePage] = useState(0);
  const [stage, setStage] = useState<PipelineStage | "all">("all");
  const [search, setSearch] = useState("");
  const q = useDebounce(search.trim(), 250);
  const pipeline = useJobPipeline(jobId, { stage, q, limit: PIPELINE_PAGE_SIZE, offset: pipelinePage * PIPELINE_PAGE_SIZE });
  const competencies = useCompetencies();
  const settings = useOrgSettings();
  const updateStatus = useUpdateJobStatus();
  const canWrite = usePermission("jobs:write");
  const canInvite = usePermission("interviews:invite");
  const [inviteOpen, setInviteOpen] = useState(false);
  useDocumentTitle(job.data?.title ?? "Job");

  // Exact counts for the whole job from the server (S14): the stat cards and the tab badge never cap.
  const counts = pipeline.data?.counts ?? { invited: 0, in_progress: 0, completed: 0, closed: 0, all: 0 };
  const activeRounds = (rounds.data ?? []).filter((r) => r.is_active !== false);

  if (job.error) {
    return (
      <PageContainer>
        <PageHeader breadcrumbs={[{ label: "Jobs", to: "/app/jobs" }, { label: "Job" }]} title="Job" />
        <LoadError error={job.error} onRetry={() => void job.refetch()} />
      </PageContainer>
    );
  }
  if (job.isLoading || !job.data) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }
  const data = job.data;

  const changeStatus = async (status: JobStatus) => {
    if (status === "archived") {
      const ok = await confirm({
        title: `Archive ${data.title}?`,
        description: "Archived jobs leave the active list. Interviews, reports and data stay available.",
        confirmLabel: "Archive job",
        tone: "danger",
      });
      if (!ok) return;
    }
    if (status === "open" && activeRounds.length === 0) {
      toast.error("Add a round before opening this job", { description: "Candidates are invited to a round." });
      return;
    }
    try {
      await updateStatus.mutateAsync({ job: data, status });
      toast.success(status === "open" ? "Job opened" : status === "closed" ? "Job closed" : status === "archived" ? "Job archived" : "Job moved to draft");
    } catch (error) {
      toast.error("Couldn't change the status", { description: describeFailure(error) });
    }
  };

  const meta = [
    data.department,
    data.location,
    labelOf(SENIORITY_LEVELS, data.seniority),
    labelOf(EMPLOYMENT_TYPES, data.employment_type),
  ].filter(Boolean) as string[];

  const reqsByKind = new Map<RequirementKind, JobRequirement[]>();
  for (const r of requirements.data ?? []) reqsByKind.set(r.kind, [...(reqsByKind.get(r.kind) ?? []), r]);
  const integrityCustom = settings.data ? differingIntegrityKeys(data.integrity_settings ?? {}, settings.data.integrity).length : Object.keys(data.integrity_settings ?? {}).length;
  const reportMode = data.candidate_report_mode ?? settings.data?.candidate_report_mode ?? "after_review";

  return (
    <PageContainer>
      <PageHeader
        breadcrumbs={[{ label: "Jobs", to: "/app/jobs" }, { label: data.title }]}
        title={data.title}
        titleAddon={<StatusBadge kind="job" value={data.status} />}
        description={meta.length ? meta.join(", ") : undefined}
        actions={
          <>
            {canWrite ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" rightIcon={<ChevronDown />} loading={updateStatus.isPending}>
                    Status
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {data.status !== "open" ? (
                    <DropdownMenuItem onSelect={() => void changeStatus("open")}>
                      <Play /> Open for invitations
                    </DropdownMenuItem>
                  ) : null}
                  {data.status === "open" ? (
                    <DropdownMenuItem onSelect={() => void changeStatus("closed")}>
                      <CircleSlash /> Close to new invitations
                    </DropdownMenuItem>
                  ) : null}
                  {data.status !== "archived" ? (
                    <DropdownMenuItem destructive onSelect={() => void changeStatus("archived")}>
                      <Archive /> Archive
                    </DropdownMenuItem>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
            {canWrite ? (
              <Button asChild variant="outline" leftIcon={<Pencil />}>
                <Link to={`/app/jobs/${data.id}/edit`}>Edit</Link>
              </Button>
            ) : null}
            {canInvite ? (
              <Button
                leftIcon={<UserPlus />}
                onClick={() => setInviteOpen(true)}
                data-testid="invite-candidate-button"
                disabled={data.status === "archived" || data.status === "closed"}
                title={data.status === "closed" || data.status === "archived" ? "Reopen the job to invite candidates" : undefined}
              >
                Invite candidate
              </Button>
            ) : null}
          </>
        }
      />

      {data.status === "draft" && canInvite ? (
        <p className="-mt-2 mb-5 text-sm text-fg-muted">This job is a draft. You can invite candidates to try it out, or open it when it's ready.</p>
      ) : null}

      <StatGroup columns={4} className="mb-6">
        <StatCard bare label="Invited" value={formatNumber(counts.invited)} hint="Link sent, not started" loading={pipeline.isLoading} />
        <StatCard bare label="In progress" value={formatNumber(counts.in_progress)} hint="Started, not finished" loading={pipeline.isLoading} />
        <StatCard bare label="Completed" value={formatNumber(counts.completed)} hint="Interview finished" loading={pipeline.isLoading} />
        <StatCard bare label="Expired or cancelled" value={formatNumber(counts.closed)} hint="Link no longer works" loading={pipeline.isLoading} />
      </StatGroup>

      <Tabs
        value={tab}
        onValueChange={(next) => {
          const p = new URLSearchParams(params);
          if (next === "pipeline") p.delete("tab");
          else p.set("tab", next);
          setParams(p, { replace: true });
        }}
      >
        <TabsList aria-label="Job sections">
          <TabsTrigger value="pipeline">
            Pipeline <span className="text-xs text-fg-subtle tabular-nums">{counts.all}</span>
          </TabsTrigger>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="rounds">
            Rounds <span className="text-xs text-fg-subtle tabular-nums">{rounds.data?.length ?? 0}</span>
          </TabsTrigger>
          <TabsTrigger value="links" data-testid="job-links-tab">
            Links
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pipeline">
          <Card className="overflow-hidden">
            <JobPipeline
              rows={pipeline.data?.items}
              rounds={rounds.data ?? []}
              loading={pipeline.isLoading}
              fetching={pipeline.isFetching && !pipeline.isLoading}
              error={pipeline.error}
              onRetry={() => void pipeline.refetch()}
              onInvite={() => setInviteOpen(true)}
              page={pipelinePage}
              pageSize={PIPELINE_PAGE_SIZE}
              total={pipeline.data?.total ?? 0}
              onPageChange={setPipelinePage}
              stage={stage}
              onStageChange={(next) => {
                setStage(next);
                setPipelinePage(0);
              }}
              counts={counts}
              search={search}
              onSearchChange={(next) => {
                setSearch(next);
                setPipelinePage(0);
              }}
            />
          </Card>
        </TabsContent>

        <TabsContent value="overview">
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <Card>
              <CardHeader title="Description" />
              <CardContent>
                {data.description?.trim() ? <Markdown>{data.description}</Markdown> : <p className="text-sm text-fg-muted">No description yet.</p>}
                {data.interview_instructions ? (
                  <div className="mt-6 rounded-md bg-surface-subtle px-4 py-3">
                    <p className="text-[13px] font-medium text-fg">Practical notes for candidates</p>
                    <p className="mt-1 text-sm whitespace-pre-line text-fg-muted">{data.interview_instructions}</p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
            <div className="grid content-start gap-6">
              <Card>
                <CardHeader title="Details" />
                <CardContent className="pt-2">
                  <dl className="divide-y divide-line-subtle">
                    <Fact label="Experience">{experienceRange(data.experience_min_years, data.experience_max_years) ?? "Not specified"}</Fact>
                    <Fact label="Candidate report">{CANDIDATE_REPORT_MODE_LABELS[reportMode]}</Fact>
                    <Fact label="Round choice">{data.allow_candidate_round_selection ? "Candidates can choose" : "You choose when inviting"}</Fact>
                    <Fact label="Coding">
                      {data.coding_requirements?.enabled
                        ? `${DIFFICULTY_LABELS[data.coding_requirements.difficulty ?? "medium"]} problems in ${(data.coding_requirements.languages ?? []).map((l) => CODE_LANGUAGE_LABELS[l]).join(", ") || "any language"}`
                        : "No coding exercise"}
                    </Fact>
                    <Fact label="Integrity">{integrityCustom ? `${integrityCustom} ${integrityCustom === 1 ? "setting differs" : "settings differ"} from your defaults` : "Organization defaults"}</Fact>
                  </dl>
                </CardContent>
              </Card>
              <Card>
                <CardHeader title="Requirements" />
                <CardContent className="grid gap-4">
                  {requirements.isLoading ? (
                    <p className="text-sm text-fg-muted">Loading…</p>
                  ) : (requirements.data ?? []).length === 0 ? (
                    <p className="text-sm text-fg-muted">No skills listed.</p>
                  ) : (
                    KIND_ORDER.filter((k) => reqsByKind.get(k)?.length).map((kind) => (
                      <div key={kind} className="grid gap-1.5">
                        <p className="text-xs font-medium text-fg-muted">{KIND_HEADINGS[kind]}</p>
                        <ul className="flex flex-wrap gap-1.5">
                          {reqsByKind.get(kind)!.map((r) => (
                            <li key={r.id}>
                              <Badge tone={r.competency_key ? "neutral" : "outline"} size="sm" title={r.competency_key ? `Linked to ${r.competency_key}` : "Free-text skill"}>
                                {competencies.data?.find((c) => c.key === r.competency_key)?.name ?? r.name}
                              </Badge>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="rounds">
          {rounds.isLoading ? (
            <DetailSkeleton />
          ) : (rounds.data ?? []).length === 0 ? (
            <EmptyState
              bordered
              icon={<Code2 />}
              title="No rounds yet"
              description="Add a round so candidates can be invited."
              actions={
                canWrite ? (
                  <Button asChild size="sm" leftIcon={<Pencil />}>
                    <Link to={`/app/jobs/${data.id}/edit`}>Set up rounds</Link>
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="grid gap-3">
              {(rounds.data ?? []).map((round, index) => (
                <RoundCard key={round.id} index={index} config={round.config} catalog={competencies.data} inactive={round.is_active === false} />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="links">
          <JobLinksPanel jobId={data.id} jobOpen={data.status !== "closed" && data.status !== "archived"} />
        </TabsContent>
      </Tabs>

      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} job={data} rounds={rounds.data} />
    </PageContainer>
  );
}
