import { UserPlus, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Combobox } from "@/components/ui/Combobox";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { usePermission } from "@/lib/auth";
import { formatNumber, formatRelativeOrDate } from "@/lib/format";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { INTERVIEW_STATUS_LABELS } from "@/lib/labels";
import type { InterviewStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useCandidates, type ArchivedFilter } from "@/features/recruiter/admin/api/candidates";
import { useJobs } from "@/features/recruiter/admin/api/jobs";
import { CreateCandidateDialog } from "@/features/recruiter/admin/candidates/CreateCandidateDialog";
import { FilterSelect, SearchInput, SegmentedFilter } from "@/features/recruiter/admin/components/Controls";
import type { AdminCandidate } from "@/features/recruiter/admin/types";

const PAGE_SIZE = 25;

const columns: DataTableColumn<AdminCandidate>[] = [
  {
    id: "name",
    header: "Candidate",
    cell: (c) => (
      <div className="flex min-w-0 items-center gap-3">
        <Avatar name={c.full_name} />
        <div className="grid min-w-0">
          <span className="flex min-w-0 items-center gap-2">
            <Link to={`/app/candidates/${c.id}`} className={cn("truncate font-medium hover:underline", c.archived_at ? "text-fg-muted" : "text-fg")}>
              {c.full_name}
            </Link>
            {c.archived_at ? (
              <Badge tone="neutral" size="sm">
                Archived
              </Badge>
            ) : null}
          </span>
          <span className="truncate text-xs text-fg-muted">{c.headline || c.email}</span>
        </div>
      </div>
    ),
  },
  {
    id: "skills",
    header: "Top skills",
    hideBelow: "xl",
    cell: (c) =>
      c.top_skills?.length ? (
        <span className="line-clamp-1 text-xs text-fg-muted">
          {c.top_skills.slice(0, 4).join(", ")}
          {typeof c.experience_years === "number" ? <span className="text-fg-subtle">, {c.experience_years} yrs</span> : null}
        </span>
      ) : (
        <span className="text-xs text-fg-subtle">—</span>
      ),
  },
  {
    id: "resume",
    header: "Resume",
    hideBelow: "md",
    width: "10rem",
    cell: (c) => (c.latest_resume_status ? <StatusBadge kind="resume" value={c.latest_resume_status} size="sm" /> : <span className="text-xs text-fg-subtle">None yet</span>),
  },
  {
    id: "interview",
    header: "Latest interview",
    width: "10rem",
    cell: (c) => (c.latest_interview_status ? <StatusBadge kind="interview" value={c.latest_interview_status} size="sm" /> : <span className="text-xs text-fg-subtle">Not invited</span>),
  },
  { id: "interviews", header: "Interviews", numeric: true, hideBelow: "lg", width: "7rem", cell: (c) => (typeof c.interviews_count === "number" ? formatNumber(c.interviews_count) : "—") },
  { id: "source", header: "Source", hideBelow: "lg", cell: (c) => <span className="whitespace-nowrap text-fg-muted">{c.source ?? "—"}</span> },
  { id: "added", header: "Added", hideBelow: "sm", width: "8rem", cell: (c) => <span className="whitespace-nowrap text-fg-muted">{formatRelativeOrDate(c.created_at)}</span> },
];

const STATUS_OPTIONS = (Object.keys(INTERVIEW_STATUS_LABELS) as InterviewStatus[]).map((s) => ({ value: s, label: INTERVIEW_STATUS_LABELS[s] }));

const ARCHIVE_VIEWS: ReadonlyArray<{ value: ArchivedFilter; label: string }> = [
  { value: "exclude", label: "Active" },
  { value: "only", label: "Archived" },
  { value: "include", label: "All" },
];

/** /app/candidates */
export default function CandidatesPage() {
  useDocumentTitle("Candidates");
  const navigate = useNavigate();
  const canWrite = usePermission("candidates:write");
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(0);
  const [createOpenState, setCreateOpenState] = useState(false);
  // `?new=1` (the dashboard's "Add candidate" and the command menu) opens the dialog; closing it drops the param.
  const createOpen = canWrite && (createOpenState || params.get("new") === "1");
  const setCreateOpen = (open: boolean) => {
    setCreateOpenState(open);
    if (!open && params.has("new")) {
      const next = new URLSearchParams(params);
      next.delete("new");
      setParams(next, { replace: true });
    }
  };
  const jobId = params.get("job") ?? undefined;
  const status = (params.get("status") as InterviewStatus | null) ?? undefined;
  const archivedParam = params.get("archived");
  const archived: ArchivedFilter = archivedParam === "only" || archivedParam === "include" ? archivedParam : "exclude";
  const q = useDebounce(search.trim(), 250);

  const query = useMemo(() => ({ q: q || undefined, job_id: jobId, status, archived, limit: PAGE_SIZE, offset: page * PAGE_SIZE }), [q, jobId, status, archived, page]);
  const candidates = useCandidates(query);
  const jobs = useJobs({ limit: 200, offset: 0 });

  const setParam = (key: string, value: string | undefined) => {
    setPage(0);
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const rows = candidates.data?.items;

  const filtered = Boolean(q || jobId || status || archived !== "exclude");

  return (
    <PageContainer>
      <PageHeader
        title="Candidates"
        description="Everyone you've added or invited, with their resume profile and interviews."
        actions={
          canWrite ? (
            <Button leftIcon={<UserPlus />} onClick={() => setCreateOpen(true)}>
              Add candidate
            </Button>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
          <SegmentedFilter<ArchivedFilter>
            label="Show candidates"
            value={archived}
            onChange={(value) => setParam("archived", value === "exclude" ? undefined : value)}
            options={ARCHIVE_VIEWS}
          />
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(0);
            }}
            placeholder="Search name, email or skill"
            className="w-full sm:w-72"
          />
          <div className="w-full sm:w-60">
            <Combobox
              aria-label="Filter by job"
              options={(jobs.data?.items ?? []).map((j) => ({ value: j.id, label: j.title, description: j.department ?? undefined }))}
              value={jobId ?? null}
              onChange={(v) => setParam("job", v ?? undefined)}
              placeholder="All jobs"
            />
          </div>
          <FilterSelect<InterviewStatus> label="Filter by interview status" value={status} onChange={(v) => setParam("status", v)} options={STATUS_OPTIONS} allLabel="Any interview status" />
          {filtered ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                setParams(new URLSearchParams(), { replace: true });
              }}
            >
              Clear
            </Button>
          ) : null}
          <span className="ml-auto text-xs text-fg-muted tabular-nums max-sm:hidden">{candidates.data ? `${formatNumber(candidates.data.total)} candidates` : null}</span>
        </div>
        <DataTable
          columns={columns}
          data={rows}
          getRowId={(c) => c.id}
          loading={candidates.isLoading}
          fetching={candidates.isFetching && !candidates.isLoading}
          error={candidates.error}
          onRetry={() => void candidates.refetch()}
          onRowClick={(c) => navigate(`/app/candidates/${c.id}`)}
          rowLabel={(c) => `Open ${c.full_name}`}
          pageSize={PAGE_SIZE}
          page={page}
          total={candidates.data?.total ?? 0}
          onPageChange={setPage}
          caption="Candidates"
          empty={
            archived === "only" && !q && !jobId && !status ? (
              <EmptyState size="sm" icon={<Users />} title="No archived candidates" description="Candidates you archive appear here. You can restore them at any time." />
            ) : filtered ? (
              <EmptyState size="sm" icon={<Users />} title="No candidates match" description="Try another name, or clear the filters." />
            ) : (
              <EmptyState
                icon={<Users />}
                title="No candidates yet"
                description="Add a candidate with their resume, or invite someone straight from a job."
                actions={
                  canWrite ? (
                    <Button size="sm" leftIcon={<UserPlus />} onClick={() => setCreateOpen(true)}>
                      Add candidate
                    </Button>
                  ) : undefined
                }
              />
            )
          }
        />
      </Card>
      <CreateCandidateDialog open={createOpen} onOpenChange={setCreateOpen} />
    </PageContainer>
  );
}
