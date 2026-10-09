import { CalendarRange, MonitorPlay, Search, SearchX, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { HUMAN_DECISION_NOTE } from "@/components/domain/RecommendationBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { useDebounce, useDocumentTitle } from "@/lib/hooks";
import { labelFor, RECOMMENDATION_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { Recommendation } from "@/lib/types";

import { useJobOptions, useJobRounds, useSessionList } from "../api/hooks";
import type { SessionFilters } from "../api/types";
import { SegmentedControl } from "../components/primitives";
import { applyClientFilters, hasExtraFilters, readSessionListState, VIEW_LABELS, VIEW_PRESETS, writeSessionListState, type ViewPreset } from "./filters";
import { SessionsTable } from "./SessionsTable";

const PAGE_SIZE = 25;
const ALL = "__all";

export function SessionsListView() {
  useDocumentTitle("Sessions");
  const [params, setParams] = useSearchParams();
  const state = useMemo(() => readSessionListState(params), [params]);
  const [search, setSearch] = useState(state.filters.q ?? "");
  const debouncedSearch = useDebounce(search, 300);

  const update = (patch: Partial<{ view: ViewPreset; filters: Partial<SessionFilters>; page: number }>) => {
    const next = writeSessionListState({
      view: patch.view ?? state.view,
      filters: { ...state.filters, ...patch.filters },
      page: patch.page ?? 0,
    });
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if ((debouncedSearch || undefined) !== state.filters.q) update({ filters: { q: debouncedSearch || undefined } });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to the debounced text
  }, [debouncedSearch]);

  // Every view, including "Needs a decision" (needs_review=true), is filtered and paged by the server.
  const list = useSessionList({ ...state.filters, limit: PAGE_SIZE, offset: state.page * PAGE_SIZE }, { refetchInterval: state.view === "live" ? 10_000 : 30_000 });
  const liveCount = useSessionList({ status: ["live", "paused"], limit: 1, offset: 0 }, { refetchInterval: 15_000 });
  const jobs = useJobOptions();
  const rounds = useJobRounds(state.filters.job_id);

  // Safety net only (harmless when the server already filtered).
  const rows = useMemo(() => (list.data ? applyClientFilters(list.data.items, state.filters) : undefined), [list.data, state.filters]);
  const total = list.data?.total;
  const roundOptions = useMemo(() => {
    if (rounds.data?.length) return rounds.data.map((r) => ({ value: r.id, label: r.name }));
    // Without a job: rounds seen in the current results.
    const seen = new Map<string, string>();
    for (const s of list.data?.items ?? []) if (s.round) seen.set(s.round.id, `${s.round.name}${s.job ? `, ${s.job.title}` : ""}`);
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [rounds.data, list.data?.items]);

  const live = liveCount.data?.total ?? 0;
  const extra = hasExtraFilters(state.filters);

  return (
    <PageContainer>
      <PageHeader
        title="Sessions"
        description="Every interview, live or finished. Open one to see the evidence behind its scores."
        titleAddon={
          live > 0 ? (
            <Badge tone="signal" dot pulse>
              {live} live now
            </Badge>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl
          label="Show sessions"
          value={state.view}
          onChange={(view) => update({ view })}
          options={VIEW_PRESETS.map((v) => ({ value: v, label: v === "live" && live ? `${VIEW_LABELS[v]} (${live})` : VIEW_LABELS[v] }))}
          className="max-w-full overflow-x-auto"
        />
        <Input
          size="sm"
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search candidates or jobs"
          aria-label="Search candidates or jobs"
          leading={<Search />}
          wrapperClassName="w-full sm:w-64"
        />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select
          size="sm"
          className="w-full sm:w-56"
          aria-label="Filter by job"
          value={state.filters.job_id ?? ALL}
          onValueChange={(v) => update({ filters: { job_id: v === ALL ? undefined : v, round_id: undefined } })}
          options={[{ value: ALL, label: "All jobs" }, ...(jobs.data ?? []).map((j) => ({ value: j.id, label: j.title }))]}
        />
        <Select
          size="sm"
          className="w-full sm:w-56"
          aria-label="Filter by round"
          value={state.filters.round_id ?? ALL}
          onValueChange={(v) => update({ filters: { round_id: v === ALL ? undefined : v } })}
          options={[
            { value: ALL, label: "All rounds" },
            ...roundOptions.map((r) => ({ ...r, description: rounds.data?.find((x) => x.id === r.value) ? labelFor(ROUND_TYPE_LABELS, rounds.data.find((x) => x.id === r.value)!.round_type) : undefined })),
          ]}
        />
        <Select
          size="sm"
          className="w-full sm:w-60"
          aria-label="Filter by AI suggestion"
          value={state.filters.recommendation ?? ALL}
          onValueChange={(v) => update({ filters: { recommendation: v === ALL ? undefined : (v as Recommendation) } })}
          options={[{ value: ALL, label: "Any AI suggestion" }, ...(Object.keys(RECOMMENDATION_LABELS) as Recommendation[]).map((r) => ({ value: r, label: RECOMMENDATION_LABELS[r] }))]}
        />
        <div className="flex w-full min-w-0 items-center gap-1.5 sm:w-auto">
          <CalendarRange className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          <Input size="sm" type="date" className="min-w-0 flex-1 sm:w-[9.5rem] sm:flex-none" aria-label="Started on or after" value={state.filters.from ?? ""} max={state.filters.to} onChange={(e) => update({ filters: { from: e.target.value || undefined } })} />
          <span className="shrink-0 text-xs text-fg-subtle">to</span>
          <Input size="sm" type="date" className="min-w-0 flex-1 sm:w-[9.5rem] sm:flex-none" aria-label="Started on or before" value={state.filters.to ?? ""} min={state.filters.from} onChange={(e) => update({ filters: { to: e.target.value || undefined } })} />
        </div>
        {extra ? (
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<X />}
            onClick={() => {
              setSearch("");
              update({ filters: { job_id: undefined, round_id: undefined, recommendation: undefined, from: undefined, to: undefined, q: undefined } });
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </div>

      <Card>
        <SessionsTable
          rows={rows}
          loading={list.isPending}
          fetching={list.isFetching && !list.isPending}
          error={list.error}
          onRetry={() => void list.refetch()}
          page={state.page}
          pageSize={PAGE_SIZE}
          total={total}
          onPageChange={(page) => update({ page })}
          caption="Interview sessions"
          empty={
            extra || state.view !== "all" ? (
              <EmptyState size="sm" icon={<SearchX />} title="No sessions match" description="Try a different view or clear the filters." />
            ) : (
              <EmptyState size="sm" icon={<MonitorPlay />} title="No interviews yet" description="When a candidate starts an interview it appears here, live, with its status and score." />
            )
          }
        />
      </Card>
      <p className="mt-3 text-xs text-fg-muted">{HUMAN_DECISION_NOTE}</p>
    </PageContainer>
  );
}
