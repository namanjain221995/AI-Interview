import { FileUp, Mail, MapPin, Phone, Send, Sparkles, Tag } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";

import { ScoreRing } from "@/components/domain/ScoreRing";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useHasRole, usePermission } from "@/lib/auth";
import { formatDate, formatRelativeOrDate } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import type { ClaimStatus } from "@/lib/types";

import {
  useCandidate,
  useCandidateInterviews,
  useCandidateProfile,
  useCandidateResumes,
  useSessionDetail,
  useSessions,
} from "@/features/recruiter/admin/api/candidates";
import { ArchivedCandidateBanner, CandidateLifecycleMenu } from "@/features/recruiter/admin/candidates/CandidateLifecycle";
import { InterviewsPanel } from "@/features/recruiter/admin/candidates/InterviewsPanel";
import { ProfileView } from "@/features/recruiter/admin/candidates/ProfileView";
import { ResumeList } from "@/features/recruiter/admin/candidates/ResumePanel";
import { ResumePipeline, ResumeUploader } from "@/features/recruiter/admin/candidates/ResumeUpload";
import { DetailSkeleton, Fact, LoadError } from "@/features/recruiter/admin/components/Controls";
import { InviteDialog } from "@/features/recruiter/admin/jobs/InviteDialog";
import { CandidateRegistrationsCard } from "@/features/links/components/CandidateRegistrationsCard";

/** /app/candidates/:candidateId */
export default function CandidateDetailPage() {
  const { candidateId } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "profile";
  const candidate = useCandidate(candidateId);
  const profile = useCandidateProfile(candidateId);
  const resumes = useCandidateResumes(candidateId);
  const interviews = useCandidateInterviews(candidateId);
  const sessions = useSessions({ candidate_id: candidateId }, Boolean(candidateId));
  const navigate = useNavigate();
  const canWrite = usePermission("candidates:write");
  const canInvite = usePermission("interviews:invite");
  // Permanent (data-subject) deletion is for administrators only, as the API enforces.
  const canErase = useHasRole("ADMIN", "SUPER_ADMIN") && canWrite;
  const [uploadOpen, setUploadOpen] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  useDocumentTitle(candidate.data?.full_name ?? "Candidate");

  // Claim verification status comes from the most recent session that tracked claims.
  const latestSession = useMemo(() => {
    const list = [...(sessions.data?.items ?? [])].filter((s) => s.started_at || s.status === "completed");
    return list.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] ?? null;
  }, [sessions.data]);
  const sessionDetail = useSessionDetail(latestSession?.id);
  const sessionClaims = useMemo(() => {
    const detail = sessionDetail.data as (typeof sessionDetail.data & { claims?: Array<{ id: string; status: ClaimStatus }> }) | undefined;
    return detail?.claims?.length ? detail.claims : (detail?.state_digest?.claims ?? []);
  }, [sessionDetail.data]);
  const claimStatuses = useMemo(() => {
    const out: Record<string, ClaimStatus> = {};
    for (const claim of sessionClaims) out[claim.id] = claim.status;
    return out;
  }, [sessionClaims]);
  const claimSource = latestSession && sessionClaims.length
    ? `the ${latestSession.round?.name ?? "interview"} on ${formatDate(latestSession.started_at ?? latestSession.created_at)}`
    : null;

  const setTab = (next: string) => {
    const p = new URLSearchParams(params);
    if (next === "profile") p.delete("tab");
    else p.set("tab", next);
    setParams(p, { replace: true });
  };

  if (candidate.error) {
    return (
      <PageContainer>
        <PageHeader breadcrumbs={[{ label: "Candidates", to: "/app/candidates" }, { label: "Candidate" }]} title="Candidate" />
        <LoadError error={candidate.error} onRetry={() => void candidate.refetch()} />
      </PageContainer>
    );
  }
  if (candidate.isLoading || !candidate.data) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }
  const c = candidate.data;
  const archived = Boolean(c.archived_at);
  const resumeList = resumes.data ?? [];
  const latestResume = resumeList[0] ?? null;
  const processing = latestResume && !["ready", "failed", "rejected"].includes(latestResume.status);
  const record = profile.data ?? null;
  const p = record?.profile;

  return (
    <PageContainer>
      <PageHeader
        breadcrumbs={[{ label: "Candidates", to: "/app/candidates" }, { label: c.full_name }]}
        title={
          <span className="flex items-center gap-3">
            <span aria-hidden="true" className="flex">
              <Avatar name={c.full_name} size="lg" />
            </span>
            {c.full_name}
          </span>
        }
        actions={
          <>
            {canWrite ? (
              <Button variant="outline" leftIcon={<FileUp />} onClick={() => setUploadOpen(true)} disabled={archived} title={archived ? "Restore the candidate to add a résumé" : undefined}>
                Upload resume
              </Button>
            ) : null}
            {canInvite ? (
              <Button leftIcon={<Send />} onClick={() => setInviteOpen(true)} disabled={archived} title={archived ? "Restore the candidate to invite them" : undefined}>
                Invite to a job
              </Button>
            ) : null}
            <CandidateLifecycleMenu candidate={c} canWrite={canWrite} canErase={canErase} onErased={() => navigate("/app/candidates", { replace: true })} />
          </>
        }
      >
        <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-fg-muted">
          <li className="inline-flex items-center gap-1.5">
            <Mail className="size-3.5" aria-hidden="true" />
            <a href={`mailto:${c.email}`} className="hover:text-fg hover:underline">
              {c.email}
            </a>
          </li>
          {c.phone ? (
            <li className="inline-flex items-center gap-1.5">
              <Phone className="size-3.5" aria-hidden="true" />
              {c.phone}
            </li>
          ) : null}
          {c.location ? (
            <li className="inline-flex items-center gap-1.5">
              <MapPin className="size-3.5" aria-hidden="true" />
              {c.location}
            </li>
          ) : null}
          {c.source ? (
            <li className="inline-flex items-center gap-1.5">
              <Tag className="size-3.5" aria-hidden="true" />
              {c.source}
            </li>
          ) : null}
        </ul>
      </PageHeader>

      {archived ? (
        <ArchivedCandidateBanner candidate={c} canWrite={canWrite} canErase={canErase} onErased={() => navigate("/app/candidates", { replace: true })} />
      ) : null}

      <div className={archived ? "mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]" : "grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]"}>
        <Tabs value={tab} onValueChange={setTab} className="min-w-0">
          <TabsList aria-label="Candidate sections">
            <TabsTrigger value="profile">Profile</TabsTrigger>
            <TabsTrigger value="interviews">
              Interviews <span className="text-xs text-fg-subtle tabular-nums">{interviews.data?.length ?? 0}</span>
            </TabsTrigger>
            <TabsTrigger value="resumes">
              Resumes <span className="text-xs text-fg-subtle tabular-nums">{resumeList.length}</span>
            </TabsTrigger>
          </TabsList>

          <TabsContent value="profile">
            {processing && latestResume ? (
              <Card className="mb-6">
                <CardHeader title="Reading the new resume" description="The profile updates when processing finishes." />
                <CardContent>
                  <ResumePipeline resume={latestResume} />
                </CardContent>
              </Card>
            ) : null}
            {profile.isLoading ? (
              <DetailSkeleton />
            ) : profile.error ? (
              <LoadError error={profile.error} onRetry={() => void profile.refetch()} title="Couldn't load the profile" />
            ) : record && p ? (
              <ProfileView record={record} claimStatuses={claimStatuses} claimSource={claimSource} />
            ) : (
              <EmptyState
                bordered
                icon={<Sparkles />}
                title="No profile yet"
                description={latestResume ? "The profile appears once the resume is processed." : "Upload a resume to build a structured profile: roles, skills, claims to verify and job match."}
                actions={
                  canWrite && !latestResume ? (
                    <Button size="sm" leftIcon={<FileUp />} onClick={() => setUploadOpen(true)}>
                      Upload resume
                    </Button>
                  ) : undefined
                }
              />
            )}
          </TabsContent>

          <TabsContent value="interviews">
            <InterviewsPanel
              interviews={interviews.data ?? []}
              sessions={sessions.data?.items ?? []}
              loading={interviews.isLoading}
              onInvite={canInvite ? () => setInviteOpen(true) : undefined}
            />
          </TabsContent>

          <TabsContent value="resumes">
            <ResumeList resumes={resumeList} loading={resumes.isLoading} onUpload={canWrite ? () => setUploadOpen(true) : undefined} />
          </TabsContent>
        </Tabs>

        <aside className="grid grid-cols-1 content-start gap-4 lg:pt-[3.75rem]" aria-label="At a glance">
          <Card>
            <CardContent className="grid grid-cols-1 gap-4 py-5">
              <div className="flex items-center gap-4">
                <ScoreRing value={record?.job_match_score ?? p?.job_match?.overall_fit ?? null} max={100} size="sm" tone="accent" ariaLabel="Resume match with the job" />
                <div className="grid grid-cols-1 gap-0.5">
                  <p className="text-sm font-medium text-fg">Resume fit</p>
                  <p className="text-xs text-fg-muted">{record?.job_id ? "Against the job it was analysed for" : record ? "General analysis, no job" : "No profile yet"}</p>
                </div>
              </div>
              <dl className="divide-y divide-line-subtle border-t border-line-subtle">
                <Fact label="Experience">{typeof record?.experience_years === "number" ? `${record.experience_years} years` : "—"}</Fact>
                <Fact label="Relevant">{typeof record?.relevant_experience_years === "number" ? `${record.relevant_experience_years} years` : "—"}</Fact>
                <Fact label="Claims">{record ? record.claims_count : "—"}</Fact>
                <Fact label="Resume">{latestResume ? <StatusBadge kind="resume" value={latestResume.status} size="sm" /> : "None"}</Fact>
                <Fact label="Added">{formatRelativeOrDate(c.created_at)}</Fact>
              </dl>
            </CardContent>
          </Card>
          <CandidateRegistrationsCard candidateId={c.id} />
          {c.notes ? (
            <Card>
              <CardHeader title="Notes" />
              <CardContent>
                <p className="text-sm whitespace-pre-line text-fg-muted">{c.notes}</p>
              </CardContent>
            </Card>
          ) : null}
          {p?.companies?.length ? (
            <Card>
              <CardHeader title="Companies" />
              <CardContent>
                <p className="text-sm text-fg-muted">{p.companies.join(", ")}</p>
              </CardContent>
            </Card>
          ) : null}
        </aside>
      </div>

      <Dialog open={uploadOpen} onOpenChange={setUploadOpen}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Upload a resume</DialogTitle>
            <DialogDescription>The file is checked for safety and read (with OCR for scans) within seconds; building the full profile takes a few minutes. You can close this window meanwhile.</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <ResumeUploader
              candidateId={c.id}
              onFinished={() => {
                void profile.refetch();
                void resumes.refetch();
              }}
            />
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setUploadOpen(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} candidate={c} />
    </PageContainer>
  );
}
