import { CalendarRange, ChevronLeft, ChevronRight, ExternalLink, Image as ImageIcon, ImageOff, ShieldCheck, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { IntegritySeverityBadge } from "@/components/domain/IntegritySeverityBadge";
import { StatusBadge } from "@/components/domain/StatusBadge";
import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Checkbox } from "@/components/ui/Checkbox";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { Sheet, SheetBody, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/Sheet";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Textarea } from "@/components/ui/Textarea";
import { toast } from "@/components/ui/Toaster";
import { errorMessage } from "@/lib/api";
import { formatClock, formatDateTime, formatNumber, formatPercent, formatRelativeOrDate, pluralize } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { INTEGRITY_ACTION_LABELS, INTEGRITY_EVENT_LABELS, INTEGRITY_SEVERITY_LABELS, INTEGRITY_SOURCE_LABELS, labelFor } from "@/lib/labels";
import type { IntegrityEventType, IntegrityReviewStatus, IntegritySeverity, IntegritySource } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useBulkReviewIntegrity, useIntegrityQueue } from "../api/hooks";
import type { IntegrityEventView, IntegrityQueueFilters } from "../api/types";
import { ObservationsBanner, ReviewControls } from "../components/IntegrityReview";
import { EvidenceFrame } from "../components/media";
import { QueryError, SegmentedControl } from "../components/primitives";
import { describeDetails, eventTitle, REVIEW_OPTIONS } from "../lib/integrity";

const PAGE_SIZE = 25;
const ALL = "__all";

const VIEWS = {
  needs_review: { label: "Needs review", status: "unreviewed" as const, actionable: true },
  all: { label: "All observations", status: undefined, actionable: false },
  reviewed: { label: "Reviewed", status: ["dismissed", "noted", "confirmed_concern"] as IntegrityReviewStatus[], actionable: false },
} as const;
type View = keyof typeof VIEWS;

const SEVERITIES: IntegritySeverity[] = ["high", "medium", "low", "info"];
const SOURCES: IntegritySource[] = ["browser", "vision", "screen", "server"];

function readState(params: URLSearchParams) {
  const rawView = params.get("view");
  const view: View = rawView && rawView in VIEWS ? (rawView as View) : "needs_review";
  return {
    view,
    page: Math.max(0, Number(params.get("page") ?? 0) || 0),
    type: (params.get("type") as IntegrityEventType | null) ?? undefined,
    severity: (params.get("severity") as IntegritySeverity | null) ?? undefined,
    source: (params.get("source") as IntegritySource | null) ?? undefined,
    from: params.get("from") ?? undefined,
    to: params.get("to") ?? undefined,
  };
}

type QueueState = ReturnType<typeof readState>;

function EvidenceSheet({ event, onOpenChange }: { event: IntegrityEventView | null; onOpenChange: (open: boolean) => void }) {
  const details = event ? describeDetails(event) : [];
  return (
    <Sheet open={!!event} onOpenChange={onOpenChange}>
      <SheetContent side="right" size="lg" aria-describedby={undefined}>
        {event ? (
          <>
            <SheetHeader>
              <SheetTitle>{eventTitle(event)}</SheetTitle>
              <SheetDescription>
                {event.candidate?.full_name ?? "Candidate"}
                {event.job ? `, ${event.job.title}` : ""}
              </SheetDescription>
            </SheetHeader>
            <SheetBody className="grid content-start gap-5">
              <div className="flex flex-wrap items-center gap-2">
                <IntegritySeverityBadge severity={event.severity} withNote={false} />
                <StatusBadge kind="integrity_review" value={event.review_status} />
              </div>
              <ObservationsBanner compact />
              {event.evidence_media_id || event.evidence_url ? (
                <div className="grid gap-2">
                  <p className="text-xs font-medium text-fg-muted">Evidence frame</p>
                  <EvidenceFrame event={event} className="h-auto w-full max-w-md [&_img]:aspect-[4/3]" />
                  {event.evidence_available === false ? <p className="text-xs text-fg-subtle">The frame was deleted under the retention policy.</p> : null}
                </div>
              ) : (
                <p className="flex items-center gap-2 text-[13px] text-fg-muted">
                  <ImageOff className="size-4 text-fg-subtle" aria-hidden="true" />
                  {event.source === "browser" || event.source === "server" ? "Browser and server signals have no image." : "No evidence frame was stored."}
                </p>
              )}
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-[13px]">
                <div className="grid gap-0.5">
                  <dt className="text-xs text-fg-muted">When</dt>
                  <dd className="font-medium text-fg">
                    {formatDateTime(event.occurred_at)}
                    {typeof event.elapsed_seconds === "number" ? <span className="block text-xs font-normal text-fg-muted">{formatClock(event.elapsed_seconds)} into the interview</span> : null}
                  </dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="text-xs text-fg-muted">Noticed by</dt>
                  <dd className="font-medium text-fg">{labelFor(INTEGRITY_SOURCE_LABELS, event.source)}</dd>
                </div>
                <div className="grid gap-0.5">
                  <dt className="text-xs text-fg-muted">Response at the time</dt>
                  <dd className="font-medium text-fg">{labelFor(INTEGRITY_ACTION_LABELS, event.action)}</dd>
                </div>
                {details.map((d) => (
                  <div key={d.label} className="grid gap-0.5">
                    <dt className="text-xs text-fg-muted">{d.label}</dt>
                    <dd className="font-medium text-fg">{d.value}</dd>
                  </div>
                ))}
              </dl>
              {event.review_note ? (
                <p className="rounded-md bg-surface-subtle px-3.5 py-3 text-[13px] text-fg ring-1 ring-line-subtle ring-inset">
                  <span className="block text-xs text-fg-muted">Earlier review note</span>“{event.review_note}”
                </p>
              ) : null}
              <div className="grid gap-2 border-t border-line-subtle pt-4">
                <p className="text-sm font-semibold text-fg">Your review</p>
                <ReviewControls event={event} onDone={() => onOpenChange(false)} />
              </div>
              <Button variant="outline" size="sm" className="w-fit" rightIcon={<ExternalLink />} asChild>
                <Link to={`/app/sessions/${event.session_id}?tab=integrity`}>Open the session for context</Link>
              </Button>
            </SheetBody>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function BulkBar({ ids, onDone }: { ids: string[]; onDone: () => void }) {
  const [choice, setChoice] = useState<Exclude<IntegrityReviewStatus, "unreviewed">>("noted");
  const [note, setNote] = useState("");
  const bulk = useBulkReviewIntegrity();
  const apply = () =>
    bulk.mutate(
      { ids, review_status: choice, review_note: note.trim() || null },
      {
        onSuccess: (result) => {
          toast.success(`${pluralize(result.updated, "observation")} reviewed`, {
            description: result.notFound.length ? `${result.notFound.length} could not be found (already removed or another organization).` : undefined,
          });
          setNote("");
          onDone();
        },
        onError: (error) => toast.error("Couldn't save the reviews", { description: errorMessage(error) }),
      },
    );
  return (
    <div className="sticky bottom-0 z-10 grid gap-3 rounded-b-lg border-t border-line bg-surface-raised px-5 py-3.5 shadow-[0_-6px_16px_-10px_rgb(16_24_40/0.18)] sm:grid-cols-[auto_auto_minmax(0,1fr)_auto] sm:items-center">
      <span className="text-sm font-medium text-fg tabular-nums">{pluralize(ids.length, "observation")} selected</span>
      <SegmentedControl label="Review outcome" value={choice} onChange={setChoice} options={REVIEW_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} />
      <Textarea rows={1} autoResize maxRows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for every selected observation (optional)" aria-label="Review note" />
      <span className="flex items-center gap-2">
        <Button onClick={apply} loading={bulk.isPending}>
          Save {ids.length} {ids.length === 1 ? "review" : "reviews"}
        </Button>
        <Button variant="ghost" onClick={onDone} disabled={bulk.isPending}>
          Clear
        </Button>
      </span>
    </div>
  );
}

export function IntegrityQueueView() {
  useDocumentTitle("Integrity review");
  const [params, setParams] = useSearchParams();
  const state = useMemo(() => readState(params), [params]);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [open, setOpen] = useState<IntegrityEventView | null>(null);

  const update = (patch: Partial<QueueState>) => {
    const next = { ...state, page: 0, ...patch };
    const out = new URLSearchParams();
    if (next.view !== "needs_review") out.set("view", next.view);
    if (next.type) out.set("type", next.type);
    if (next.severity) out.set("severity", next.severity);
    if (next.source) out.set("source", next.source);
    if (next.from) out.set("from", next.from);
    if (next.to) out.set("to", next.to);
    if (next.page) out.set("page", String(next.page));
    setParams(out, { replace: true });
    setSelected(new Set());
  };

  const view = VIEWS[state.view];
  const filters: IntegrityQueueFilters & { limit: number; offset: number } = {
    status: view.status,
    actionable: view.actionable,
    type: state.type,
    severity: state.severity,
    source: state.source,
    from: state.from,
    to: state.to,
    limit: PAGE_SIZE,
    offset: state.page * PAGE_SIZE,
  };
  const queue = useIntegrityQueue(filters);
  const items = queue.data?.items ?? [];
  const total = queue.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const allSelected = items.length > 0 && items.every((e) => selected.has(e.id));
  const someSelected = items.some((e) => selected.has(e.id));
  const extra = !!(state.type || state.severity || state.source || state.from || state.to);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <PageContainer>
      <PageHeader
        title="Integrity review"
        description="Observations from interviews that a person should look at, newest first. Review each one in context; none of them is proof on its own."
      />
      <ObservationsBanner className="mb-5" />

      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <SegmentedControl label="Show" value={state.view} onChange={(v) => update({ view: v })} options={(Object.keys(VIEWS) as View[]).map((v) => ({ value: v, label: VIEWS[v].label }))} />
        {queue.data ? <span className="text-[13px] text-fg-muted tabular-nums">{pluralize(total, "observation")}</span> : null}
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select
          size="sm"
          className="w-full sm:w-64"
          aria-label="Filter by observation"
          value={state.type ?? ALL}
          onValueChange={(v) => update({ type: v === ALL ? undefined : (v as IntegrityEventType) })}
          options={[{ value: ALL, label: "All observation types" }, ...(Object.keys(INTEGRITY_EVENT_LABELS) as IntegrityEventType[]).map((t) => ({ value: t, label: INTEGRITY_EVENT_LABELS[t] }))]}
        />
        <Select
          size="sm"
          className="w-full sm:w-40"
          aria-label="Filter by severity"
          value={state.severity ?? ALL}
          onValueChange={(v) => update({ severity: v === ALL ? undefined : (v as IntegritySeverity) })}
          options={[{ value: ALL, label: "Any severity" }, ...SEVERITIES.map((s) => ({ value: s, label: `${INTEGRITY_SEVERITY_LABELS[s]} severity` }))]}
        />
        <Select
          size="sm"
          className="w-full sm:w-40"
          aria-label="Filter by source"
          value={state.source ?? ALL}
          onValueChange={(v) => update({ source: v === ALL ? undefined : (v as IntegritySource) })}
          options={[{ value: ALL, label: "Any source" }, ...SOURCES.map((s) => ({ value: s, label: INTEGRITY_SOURCE_LABELS[s] }))]}
        />
        <div className="flex w-full min-w-0 items-center gap-1.5 sm:w-auto">
          <CalendarRange className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          <Input size="sm" type="date" className="min-w-0 flex-1 sm:w-[9.5rem] sm:flex-none" aria-label="Observed on or after" value={state.from ?? ""} max={state.to} onChange={(e) => update({ from: e.target.value || undefined })} />
          <span className="shrink-0 text-xs text-fg-subtle">to</span>
          <Input size="sm" type="date" className="min-w-0 flex-1 sm:w-[9.5rem] sm:flex-none" aria-label="Observed on or before" value={state.to ?? ""} min={state.from} onChange={(e) => update({ to: e.target.value || undefined })} />
        </div>
        {extra ? (
          <Button variant="ghost" size="sm" leftIcon={<X />} onClick={() => update({ type: undefined, severity: undefined, source: undefined, from: undefined, to: undefined })}>
            Clear filters
          </Button>
        ) : null}
      </div>

      <Card>
        {queue.isError && !queue.data ? (
          <div className="p-5">
            <QueryError error={queue.error} title="Couldn't load the review queue" onRetry={() => void queue.refetch()} />
          </div>
        ) : (
          <>
            <Table aria-busy={queue.isFetching || undefined} className={cn(queue.isFetching && !queue.isPending && "opacity-70")}>
              <caption className="sr-only">Integrity observations</caption>
              <TableHeader>
                <tr>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={allSelected ? true : someSelected ? "indeterminate" : false}
                      onCheckedChange={(checked) => setSelected(checked ? new Set(items.map((e) => e.id)) : new Set())}
                      aria-label="Select all observations on this page"
                      disabled={items.length === 0}
                    />
                  </TableHead>
                  <TableHead>Observation</TableHead>
                  <TableHead className="max-md:hidden">Candidate</TableHead>
                  <TableHead className="max-lg:hidden">Noticed by</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead align="right" className="max-sm:hidden">
                    When
                  </TableHead>
                </tr>
              </TableHeader>
              <TableBody>
                {queue.isPending
                  ? Array.from({ length: 6 }, (_, i) => (
                      <TableRow key={i}>
                        <TableCell>
                          <Skeleton className="size-4" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-48" />
                        </TableCell>
                        <TableCell className="max-md:hidden">
                          <Skeleton className="h-4 w-32" />
                        </TableCell>
                        <TableCell className="max-lg:hidden">
                          <Skeleton className="h-4 w-20" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-5 w-24" />
                        </TableCell>
                        <TableCell className="max-sm:hidden">
                          <Skeleton className="ml-auto h-4 w-20" />
                        </TableCell>
                      </TableRow>
                    ))
                  : items.map((event) => (
                      <TableRow
                        key={event.id}
                        data-testid="integrity-event"
                        data-event-id={event.id}
                        data-review-status={event.review_status}
                        interactive
                        selected={selected.has(event.id)}
                        tabIndex={0}
                        aria-label={`Open ${eventTitle(event).toLowerCase()} for ${event.candidate?.full_name ?? "candidate"}`}
                        onClick={(e) => {
                          if ((e.target as HTMLElement).closest("button,[role='checkbox'],a,label")) return;
                          setOpen(event);
                        }}
                        onKeyDown={(e) => {
                          if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                            e.preventDefault();
                            setOpen(event);
                          }
                        }}
                      >
                        <TableCell>
                          <Checkbox checked={selected.has(event.id)} onCheckedChange={() => toggle(event.id)} aria-label={`Select ${eventTitle(event).toLowerCase()}`} />
                        </TableCell>
                        <TableCell>
                          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                            <span className="font-medium text-fg">{event.label || eventTitle(event)}</span>
                            <IntegritySeverityBadge severity={event.severity} size="sm" withNote={false} />
                            {event.count > 1 ? <span className="text-xs text-fg-muted tabular-nums">×{event.count}</span> : null}
                            {event.evidence_media_id || event.evidence_url ? (
                              <span className="inline-flex items-center gap-1 text-xs text-fg-subtle">
                                <ImageIcon className="size-3.5" aria-hidden="true" />
                                Frame
                              </span>
                            ) : null}
                            {/* Phones: the When column is hidden, so the time sits under the observation. */}
                            <span className="w-full text-xs text-fg-muted sm:hidden">
                              {formatRelativeOrDate(event.occurred_at)}
                              {typeof event.elapsed_seconds === "number" ? `, ${formatClock(event.elapsed_seconds)} into the interview` : ""}
                            </span>
                          </span>
                        </TableCell>
                        <TableCell className="max-md:hidden">
                          <span className="grid min-w-0">
                            <span className="truncate text-fg">{event.candidate?.full_name ?? "—"}</span>
                            <span className="truncate text-xs text-fg-muted">{event.job?.title ?? ""}</span>
                          </span>
                        </TableCell>
                        <TableCell className="max-lg:hidden">
                          <span className="grid">
                            <span className="text-fg-muted">{labelFor(INTEGRITY_SOURCE_LABELS, event.source)}</span>
                            {typeof event.confidence === "number" ? <span className="text-xs text-fg-subtle tabular-nums">{formatPercent(event.confidence)} confidence</span> : null}
                          </span>
                        </TableCell>
                        <TableCell>
                          <StatusBadge kind="integrity_review" value={event.review_status} size="sm" />
                        </TableCell>
                        <TableCell numeric className="whitespace-nowrap max-sm:hidden">
                          <span className="grid justify-items-end">
                            <span className="text-fg-muted">{formatRelativeOrDate(event.occurred_at)}</span>
                            {typeof event.elapsed_seconds === "number" ? <span className="text-xs text-fg-subtle">{formatClock(event.elapsed_seconds)} into the interview</span> : null}
                          </span>
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
            {!queue.isPending && items.length === 0 ? (
              <div className="border-t border-line-subtle">
                <EmptyState
                  size="sm"
                  icon={<ShieldCheck />}
                  title={state.view === "needs_review" && !extra ? "Nothing needs review" : "No observations match"}
                  description={state.view === "needs_review" && !extra ? "Every observation that needed a person has been reviewed." : "Try another view or clear the filters."}
                />
              </div>
            ) : null}
            {total > PAGE_SIZE ? (
              <div className="flex items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
                <p className="tabular-nums">
                  {formatNumber(state.page * PAGE_SIZE + 1)}–{formatNumber(Math.min((state.page + 1) * PAGE_SIZE, total))} of {formatNumber(total)}
                </p>
                <div className="flex items-center gap-1.5">
                  <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" disabled={state.page === 0} onClick={() => update({ page: state.page - 1 })}>
                    <ChevronLeft />
                  </Button>
                  <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Next page" disabled={state.page >= pageCount - 1} onClick={() => update({ page: state.page + 1 })}>
                    <ChevronRight />
                  </Button>
                </div>
              </div>
            ) : null}
            {selected.size ? <BulkBar ids={[...selected]} onDone={() => setSelected(new Set())} /> : null}
          </>
        )}
      </Card>
      <EvidenceSheet event={open} onOpenChange={(v) => !v && setOpen(null)} />
    </PageContainer>
  );
}
