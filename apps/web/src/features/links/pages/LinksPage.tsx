import { Link2, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { usePermission } from "@/lib/auth";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";

import { useJobs } from "@/features/recruiter/admin/api/jobs";
import { FilterSelect, SearchInput, SegmentedFilter } from "@/features/recruiter/admin/components/Controls";

import { useLinks } from "../api";
import { LinksTable } from "../components/LinksTable";
import { LINK_STATUS_LABELS } from "../status";
import { LINK_STATUSES, type LinkStatus } from "../types";

const PAGE_SIZE = 25;

/** /app/links: every open interview link of the organization. */
export default function LinksPage() {
  useDocumentTitle("Interview links");
  const canInvite = usePermission("interviews:invite");
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") as LinkStatus | null) ?? "all";
  const jobId = params.get("job") ?? undefined;
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(0);
  const q = useDebounce(search.trim(), 250);
  const jobs = useJobs({ limit: 200 });

  const query = useMemo(
    () => ({ status: status === "all" ? undefined : status, job_id: jobId, q: q || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [status, jobId, q, page],
  );
  const links = useLinks(query);

  const setParam = (key: string, value: string | undefined) => {
    setPage(0);
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const filtered = Boolean(q || jobId || status !== "all");

  return (
    <PageContainer>
      <PageHeader
        title="Interview links"
        description="One shareable link per job. People register themselves, upload a résumé and take the interview; each person gets one attempt."
        actions={
          canInvite ? (
            <Button asChild leftIcon={<Plus />} data-testid="link-create-button">
              <Link to={jobId ? `/app/links/new?job_id=${encodeURIComponent(jobId)}` : "/app/links/new"}>Create link</Link>
            </Button>
          ) : null
        }
      />
      <div data-testid="links-page">
        <Card className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
            <SegmentedFilter<LinkStatus | "all">
              label="Filter by status"
              value={status}
              onChange={(next) => setParam("status", next === "all" ? undefined : next)}
              options={[{ value: "all", label: "All" }, ...LINK_STATUSES.map((s) => ({ value: s, label: LINK_STATUS_LABELS[s] }))]}
            />
            <FilterSelect<string>
              label="Filter by job"
              allLabel="All jobs"
              value={jobId}
              onChange={(next) => setParam("job", next)}
              options={(jobs.data?.items ?? []).map((job) => ({ value: job.id, label: job.title }))}
              className="max-sm:w-full"
            />
            <SearchInput
              value={search}
              onChange={(v) => {
                setSearch(v);
                setPage(0);
              }}
              placeholder="Search link names"
              className="w-full sm:ml-auto sm:w-64"
            />
          </div>
          <LinksTable
            rows={links.data?.items}
            loading={links.isLoading}
            fetching={links.isFetching && !links.isLoading}
            error={links.error}
            onRetry={() => void links.refetch()}
            page={page}
            pageSize={PAGE_SIZE}
            total={links.data?.total}
            onPageChange={setPage}
            empty={
              filtered ? (
                <EmptyState size="sm" icon={<Link2 />} title="No links match" description="Try another job, status or search." />
              ) : (
                <EmptyState
                  icon={<Link2 />}
                  title="Share one link with many candidates"
                  description="Create a link for a job and its rounds, post it in a job ad or a campus group, and everyone who registers gets their own interview."
                  actions={
                    canInvite ? (
                      <Button asChild size="sm" leftIcon={<Plus />}>
                        <Link to="/app/links/new">Create link</Link>
                      </Button>
                    ) : undefined
                  }
                />
              )
            }
          />
        </Card>
      </div>
    </PageContainer>
  );
}
