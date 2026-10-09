import { BriefcaseBusiness, Code2, MapPin, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { usePermission } from "@/lib/auth";
import { formatNumber, formatRelativeOrDate } from "@/lib/format";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { JOB_STATUS_LABELS } from "@/lib/labels";
import type { Job, JobStatus } from "@/lib/types";

import { JOB_STATUS_FILTERS, useJobs, useJobStatusCounts } from "@/features/recruiter/admin/api/jobs";
import { SearchInput, SegmentedFilter } from "@/features/recruiter/admin/components/Controls";
import { EMPLOYMENT_TYPES, SENIORITY_LEVELS } from "@/features/recruiter/admin/jobs/jobDraft";

const PAGE_SIZE = 25;

function labelOf(list: ReadonlyArray<{ value: string; label: string }>, value: string | null): string | null {
  if (!value) return null;
  return list.find((o) => o.value === value)?.label ?? value;
}

const count = (value: number | undefined) => (typeof value === "number" ? formatNumber(value) : "—");

const columns: DataTableColumn<Job>[] = [
  {
    id: "title",
    header: "Job",
    cell: (job) => (
      <div className="grid min-w-0 gap-0.5">
        <Link to={`/app/jobs/${job.id}`} className="truncate font-medium text-fg hover:underline">
          {job.title}
        </Link>
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-fg-muted">
          {job.department ? <span className="truncate">{job.department}</span> : null}
          {job.location ? (
            <span className="inline-flex min-w-0 items-center gap-1">
              <MapPin className="size-3 shrink-0" aria-hidden="true" />
              <span className="truncate">{job.location}</span>
            </span>
          ) : null}
          {[labelOf(SENIORITY_LEVELS, job.seniority), labelOf(EMPLOYMENT_TYPES, job.employment_type)].filter(Boolean).join(", ") || null}
        </span>
      </div>
    ),
  },
  { id: "status", header: "Status", cell: (job) => <StatusBadge kind="job" value={job.status} size="sm" />, width: "8rem" },
  {
    id: "rounds",
    header: "Rounds",
    numeric: true,
    hideBelow: "md",
    width: "6rem",
    cell: (job) => (
      <span className="inline-flex items-center justify-end gap-1.5">
        {job.coding_requirements?.enabled ? <Code2 className="size-3.5 text-fg-subtle" aria-label="Includes coding" /> : null}
        {count(job.rounds_count)}
      </span>
    ),
  },
  { id: "candidates", header: "Candidates", numeric: true, hideBelow: "md", width: "7rem", cell: (job) => count(job.candidates_count) },
  { id: "sessions", header: "Interviews", numeric: true, hideBelow: "lg", width: "7rem", cell: (job) => count(job.sessions_count) },
  {
    id: "updated",
    header: "Updated",
    hideBelow: "sm",
    width: "8rem",
    cell: (job) => <span className="text-fg-muted">{formatRelativeOrDate(job.updated_at)}</span>,
  },
];

/** /app/jobs */
export default function JobsPage() {
  useDocumentTitle("Jobs");
  const navigate = useNavigate();
  const canWrite = usePermission("jobs:write");
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") as JobStatus | "all" | null) ?? "all";
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(0);
  const q = useDebounce(search.trim(), 250);

  const query = useMemo(
    () => ({ status: status === "all" ? undefined : status, q: q || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [status, q, page],
  );
  const jobs = useJobs(query);
  const counts = useJobStatusCounts(q);

  const setStatus = (next: JobStatus | "all") => {
    setPage(0);
    const nextParams = new URLSearchParams(params);
    if (next === "all") nextParams.delete("status");
    else nextParams.set("status", next);
    setParams(nextParams, { replace: true });
  };

  const filtered = Boolean(q) || status !== "all";

  return (
    <PageContainer>
      <PageHeader
        title="Jobs"
        description="Roles you're hiring for, with their interview rounds and pipeline."
        actions={
          canWrite ? (
            <Button asChild leftIcon={<Plus />}>
              <Link to="/app/jobs/new">New job</Link>
            </Button>
          ) : null
        }
      />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line-subtle px-4 py-3">
          <SegmentedFilter<JobStatus | "all">
            label="Filter by status"
            value={status}
            onChange={setStatus}
            options={JOB_STATUS_FILTERS.map((s) => ({ value: s, label: s === "all" ? "All" : JOB_STATUS_LABELS[s], count: counts[s] }))}
          />
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(0);
            }}
            placeholder="Search title, department, location"
            className="w-full sm:w-72"
          />
        </div>
        <DataTable
          columns={columns}
          data={jobs.data?.items}
          getRowId={(job) => job.id}
          loading={jobs.isLoading}
          fetching={jobs.isFetching && !jobs.isLoading}
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
          onRowClick={(job) => navigate(`/app/jobs/${job.id}`)}
          rowLabel={(job) => `Open ${job.title}`}
          pageSize={PAGE_SIZE}
          page={page}
          total={jobs.data?.total ?? 0}
          onPageChange={setPage}
          caption="Jobs"
          empty={
            filtered ? (
              <EmptyState
                size="sm"
                icon={<BriefcaseBusiness />}
                title="No jobs match"
                description={q ? `Nothing matches “${q}”${status !== "all" ? ` among ${JOB_STATUS_LABELS[status as JobStatus].toLowerCase()} jobs` : ""}.` : "No jobs have this status."}
                actions={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setSearch("");
                      setStatus("all");
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={<BriefcaseBusiness />}
                title="Create your first job"
                description="Describe the role and set up its interview rounds. You can start from built-in round types."
                actions={
                  canWrite ? (
                    <Button asChild size="sm" leftIcon={<Plus />}>
                      <Link to="/app/jobs/new">New job</Link>
                    </Button>
                  ) : (
                    <Badge tone="neutral">Ask a recruiter or admin to create jobs</Badge>
                  )
                }
              />
            )
          }
        />
      </Card>
    </PageContainer>
  );
}
