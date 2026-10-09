import { BarChart3, ClipboardCheck, Plus, Search, SearchX, Settings2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { usePermission } from "@/lib/auth";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";

import { isIntakeDisabled, useIntakeConfig, useIntakeList, useIntakeStats } from "../api";
import { BucketTabs } from "../components/BucketTabs";
import { IntakeDisabledState, IntakeLoadError } from "../components/IntakeStates";
import { IntakeTable } from "../components/IntakeTable";
import { useReviewerOptions } from "../components/reviewers";
import { StatsStrip } from "../components/StatsStrip";
import { BAND_LABELS, BUCKET_EMPTY, BUCKET_TABS, SORT_OPTIONS, TARGET_ROLE_LABELS } from "../labels";
import { bucketCount, hasExtraFilters, PAGE_SIZE, readListState, visibleBuckets, writeListState, type IntakeListState } from "../lib/buckets";
import { DECISION_BANDS, TARGET_ROLES, type DecisionBand, type IntakeListFilters, type IntakeSort, type TargetRole } from "../types";

const ALL = "__all";

/** /app/intake: every intake interview of the organisation, by bucket. */
export default function IntakeListPage() {
  useDocumentTitle("Intake interviews");
  const canInvite = usePermission("interviews:invite");
  const canSeeAnalytics = usePermission("analytics:read");
  const canManageSettings = usePermission("settings:manage");
  const [params, setParams] = useSearchParams();
  const state = useMemo(() => readListState(params), [params]);
  const [search, setSearch] = useState(state.filters.q ?? "");
  const debounced = useDebounce(search.trim(), 300);

  const update = (patch: Partial<IntakeListFilters>, page = 0) => {
    const next: IntakeListState = { filters: { ...state.filters, ...patch }, page };
    setParams(writeListState(next), { replace: true });
  };

  useEffect(() => {
    if ((debounced || undefined) !== state.filters.q) update({ q: debounced || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- react to the debounced text only
  }, [debounced]);

  const list = useIntakeList({ ...state.filters, limit: PAGE_SIZE, offset: state.page * PAGE_SIZE });
  const stats = useIntakeStats(state.filters);
  const config = useIntakeConfig();
  const rows = list.data?.items;
  const reviewers = useReviewerOptions((rows ?? []).flatMap((r) => [r.assigned_reviewer, r.decision?.reviewer]));
  const jobOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const job of config.data?.jobs ?? []) seen.set(job.job_id, job.title);
    for (const row of rows ?? []) if (row.job.id && !seen.has(row.job.id)) seen.set(row.job.id, row.job.title);
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [config.data?.jobs, rows]);

  const disabled = [list.error, stats.error, config.error].some(isIntakeDisabled);
  const extra = hasExtraFilters(state.filters);
  const tabs = visibleBuckets(BUCKET_TABS, stats.data, state.filters.bucket);

  const header = (
    <PageHeader
      title="Intake interviews"
      description="Seven-part screening before the training program. Invite candidates, follow each interview and record the human decision."
      actions={
        disabled ? null : (
          <>
            {canSeeAnalytics ? (
              <Button variant="outline" leftIcon={<BarChart3 />} asChild>
                <Link to="/app/intake/analytics">Analytics</Link>
              </Button>
            ) : null}
            {canManageSettings ? (
              <Button variant="outline" leftIcon={<Settings2 />} asChild>
                <Link to="/app/intake/settings">Settings</Link>
              </Button>
            ) : null}
            {canInvite ? (
              <Button leftIcon={<Plus />} asChild data-testid="intake-create-button">
                <Link to="/app/intake/new">Create intake interview</Link>
              </Button>
            ) : null}
          </>
        )
      }
    />
  );

  if (disabled) {
    return (
      <PageContainer>
        {header}
        <div data-testid="intake-page">
          <IntakeDisabledState />
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      {header}
      <div data-testid="intake-page" className="grid grid-cols-1 gap-5">
        {stats.isError && !stats.data ? null : <StatsStrip stats={stats.data} loading={stats.isPending} />}

        <Card className="overflow-hidden">
          <div className="grid grid-cols-1 gap-3 border-b border-line-subtle px-4 py-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="min-w-0 max-w-full flex-1">
                <BucketTabs buckets={tabs} value={state.filters.bucket} onChange={(bucket) => update({ bucket })} counts={(bucket) => bucketCount(stats.data, bucket)} />
              </div>
              <Select
                size="sm"
                className="w-full sm:w-48"
                aria-label="Sort"
                data-testid="intake-sort"
                value={state.filters.sort}
                onValueChange={(v) => update({ sort: v as IntakeSort })}
                options={SORT_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              />
            </div>
            {/* Two columns on phones, one wrapping row from sm up. */}
            <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
              <Input
                size="sm"
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape" && search) {
                    e.preventDefault();
                    setSearch("");
                  }
                }}
                placeholder="Search name or email"
                aria-label="Search name or email"
                leading={<Search />}
                wrapperClassName="col-span-2 sm:w-56"
                data-testid="intake-search"
              />
              <Select
                size="sm"
                className="w-full sm:w-48"
                aria-label="Filter by job"
                data-testid="intake-filter-job"
                value={state.filters.job_id ?? ALL}
                onValueChange={(v) => update({ job_id: v === ALL ? undefined : v })}
                options={[{ value: ALL, label: "All jobs" }, ...jobOptions]}
              />
              <Select
                size="sm"
                className="w-full sm:w-44"
                aria-label="Filter by target role"
                data-testid="intake-filter-role"
                value={state.filters.target_role ?? ALL}
                onValueChange={(v) => update({ target_role: v === ALL ? undefined : (v as TargetRole) })}
                options={[{ value: ALL, label: "Any target role" }, ...TARGET_ROLES.map((r) => ({ value: r, label: TARGET_ROLE_LABELS[r] }))]}
              />
              <Select
                size="sm"
                className="w-full sm:w-44"
                aria-label="Filter by AI band"
                data-testid="intake-filter-band"
                value={state.filters.band ?? ALL}
                onValueChange={(v) => update({ band: v === ALL ? undefined : (v as DecisionBand) })}
                options={[{ value: ALL, label: "Any AI band" }, ...DECISION_BANDS.map((b) => ({ value: b, label: BAND_LABELS[b] }))]}
              />
              <Select
                size="sm"
                className="w-full sm:w-44"
                aria-label="Filter by reviewer"
                data-testid="intake-filter-reviewer"
                value={state.filters.reviewer_id ?? ALL}
                onValueChange={(v) => update({ reviewer_id: v === ALL ? undefined : v })}
                options={[{ value: ALL, label: "Any reviewer" }, ...reviewers.map((r) => ({ value: r.value, label: r.label }))]}
              />
              {state.filters.batch_id ? (
                <Badge tone="accent" size="md" className="pr-1">
                  Batch {state.filters.batch_id.slice(0, 8)}
                  <button type="button" className="ml-1 rounded-xs p-0.5 hover:bg-surface-hover" aria-label="Show every batch" onClick={() => update({ batch_id: undefined })}>
                    <X className="size-3" aria-hidden="true" />
                  </button>
                </Badge>
              ) : null}
              {extra ? (
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<X />}
                  className="col-span-2 justify-self-start"
                  onClick={() => {
                    setSearch("");
                    update({ q: undefined, job_id: undefined, target_role: undefined, band: undefined, outcome: undefined, reviewer_id: undefined, batch_id: undefined, created_from: undefined, created_to: undefined });
                  }}
                >
                  Clear filters
                </Button>
              ) : null}
            </div>
          </div>

          {list.isError && !rows?.length ? (
            <div className="p-5">
              <IntakeLoadError error={list.error} onRetry={() => void list.refetch()} />
            </div>
          ) : (
            <IntakeTable
              rows={rows}
              loading={list.isPending}
              fetching={list.isFetching && !list.isPending}
              page={state.page}
              pageSize={PAGE_SIZE}
              total={list.data?.total}
              onPageChange={(page) => update({}, page)}
              empty={
                extra ? (
                  <EmptyState size="sm" icon={<SearchX />} title="No intake interviews match" description="Try another search or clear the filters." />
                ) : (
                  <EmptyState
                    size="sm"
                    icon={<ClipboardCheck />}
                    title={state.filters.bucket === "all" ? "No intake interviews yet" : "Nothing here"}
                    description={BUCKET_EMPTY[state.filters.bucket]}
                    actions={
                      canInvite && (state.filters.bucket === "all" || state.filters.bucket === "drafts") ? (
                        <Button size="sm" leftIcon={<Plus />} asChild>
                          <Link to="/app/intake/new">Create intake interview</Link>
                        </Button>
                      ) : undefined
                    }
                  />
                )
              }
            />
          )}
        </Card>
        <p className="text-xs text-fg-muted">Weighted totals and bands are AI recommendations. A person reviews every interview and makes the decision.</p>
      </div>
    </PageContainer>
  );
}
