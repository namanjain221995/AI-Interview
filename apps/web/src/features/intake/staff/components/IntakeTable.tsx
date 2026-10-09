import { ChevronLeft, ChevronRight, ListChecks, ShieldAlert } from "lucide-react";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatDate, formatNumber, formatRelativeOrDate, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { TARGET_ROLE_LABELS } from "../labels";
import { formatTotal } from "../lib/decimals";
import type { IntakeSummary } from "../types";
import { BandBadge, BucketBadge, OutcomeBadge } from "./IntakeBadges";

const INTERACTIVE = "a,button,input,select,textarea,label,[role='menuitem'],[role='checkbox']";

/** Where a row is in its life, in a few words. */
export function progressLine(row: IntakeSummary): string {
  const s = row.session;
  if (row.status === "draft") return `Draft, saved ${formatRelativeOrDate(row.updated_at || row.created_at)}`;
  if (row.status === "cancelled") return "Cancelled";
  if (s?.ended_at && (s.status === "completed" || s.status === "completing")) return `Completed ${formatRelativeOrDate(s.ended_at)}`;
  if (s?.ended_at) return `Ended ${formatRelativeOrDate(s.ended_at)}`;
  if (s?.started_at) return `Started ${formatRelativeOrDate(s.started_at)}`;
  if (row.interview?.scheduled_for && Date.parse(row.interview.scheduled_for) > Date.now()) return `Scheduled for ${formatDate(row.interview.scheduled_for, { dateStyle: "medium", timeStyle: "short" })}`;
  if (row.interview?.invite_expires_at) {
    const expired = Date.parse(row.interview.invite_expires_at) <= Date.now();
    return expired ? `Link expired ${formatRelativeOrDate(row.interview.invite_expires_at)}` : `Link open until ${formatDate(row.interview.invite_expires_at)}`;
  }
  return `Created ${formatRelativeOrDate(row.created_at)}`;
}

export function rowHref(row: IntakeSummary): string {
  return row.status === "draft" ? `/app/intake/new?draft=${encodeURIComponent(row.id)}` : `/app/intake/${encodeURIComponent(row.id)}`;
}

export interface IntakeTableProps {
  rows: IntakeSummary[] | undefined;
  loading: boolean;
  fetching?: boolean;
  empty: ReactNode;
  page: number;
  pageSize: number;
  total: number | undefined;
  onPageChange: (page: number) => void;
}

export function IntakeTable({ rows, loading, fetching, empty, page, pageSize, total, onPageChange }: IntakeTableProps) {
  const navigate = useNavigate();
  const activate = (event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>, row: IntakeSummary) => {
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.closest(INTERACTIVE)) return;
    navigate(rowHref(row));
  };
  const totalRows = total ?? rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));

  return (
    <div className="flex flex-col">
      <Table aria-busy={loading || fetching || undefined} className={cn("transition-opacity duration-200", fetching && !loading && "opacity-60")}>
        <caption className="sr-only">Intake interviews</caption>
        <TableHeader>
          <tr>
            <TableHead className="sticky left-0 z-[2] bg-surface-subtle">Candidate</TableHead>
            <TableHead className="max-lg:hidden">Target role and job</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="max-md:hidden">Progress</TableHead>
            <TableHead align="right" className="max-sm:hidden">
              Weighted total
            </TableHead>
            <TableHead className="max-md:hidden">AI band</TableHead>
            <TableHead className="max-lg:hidden">Decision</TableHead>
            <TableHead className="max-xl:hidden">Reviewer</TableHead>
            <TableHead align="right" className="max-sm:hidden">
              <span className="sr-only">Open review items</span>
              <ListChecks className="ml-auto size-3.5" aria-hidden="true" />
            </TableHead>
          </tr>
        </TableHeader>
        <TableBody>
          {loading && !rows?.length
            ? Array.from({ length: 6 }, (_, i) => (
                <TableRow key={`s-${i}`}>
                  <TableCell className="sticky left-0 z-[1] bg-surface">
                    <span className="grid gap-1.5">
                      <Skeleton className="h-3.5 w-36" />
                      <Skeleton className="h-3 w-44" />
                    </span>
                  </TableCell>
                  {Array.from({ length: 8 }, (_, j) => (
                    <TableCell key={j} className={cn(j === 0 && "max-lg:hidden", j === 2 && "max-md:hidden", j === 3 && "max-sm:hidden", j === 4 && "max-md:hidden", j === 5 && "max-lg:hidden", j === 6 && "max-xl:hidden", j === 7 && "max-sm:hidden")}>
                      <Skeleton className="h-3.5 w-14" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : (rows ?? []).map((row) => {
                const total = row.evaluation?.weighted_total ?? null;
                return (
                  <TableRow
                    key={row.id}
                    data-testid="intake-row"
                    data-intake-id={row.id}
                    data-bucket={row.bucket}
                    interactive
                    tabIndex={0}
                    aria-label={`Open the intake interview of ${row.candidate.full_name}`}
                    onClick={(e) => activate(e, row)}
                    onKeyDown={(e) => {
                      if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                        e.preventDefault();
                        activate(e, row);
                      }
                    }}
                    className={cn(row.bucket === "in_progress" && "shadow-[inset_2px_0_0_var(--signal)]")}
                  >
                    <TableCell className="sticky left-0 z-[1] bg-surface">
                      <span className="grid min-w-[9rem] max-w-[17rem]">
                        <span className="truncate font-medium text-fg" title={row.candidate.full_name}>
                          {row.candidate.full_name}
                        </span>
                        <span className="truncate text-xs text-fg-muted" title={row.candidate.email}>
                          {row.candidate.email}
                        </span>
                        <span className="truncate text-xs text-fg-subtle md:hidden">{progressLine(row)}</span>
                      </span>
                    </TableCell>
                    <TableCell className="max-lg:hidden">
                      <span className="grid min-w-[8rem] max-w-[12rem]">
                        <span className="truncate text-fg">{row.target_role ? TARGET_ROLE_LABELS[row.target_role] : <span className="text-fg-subtle">Candidate chooses</span>}</span>
                        <span className="truncate text-xs text-fg-muted" title={row.job.title}>
                          {row.job.title}
                        </span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-col items-start gap-1">
                        <BucketBadge bucket={row.bucket} />
                        {total ? (
                          <span className="text-xs whitespace-nowrap text-fg-muted sm:hidden">
                            <span className="font-semibold text-fg tabular-nums">{formatTotal(total)}</span> / 5
                          </span>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell className="max-md:hidden">
                      <span className="block max-w-[13rem] truncate text-[13px] text-fg-muted" title={progressLine(row)}>
                        {progressLine(row)}
                      </span>
                    </TableCell>
                    <TableCell numeric className="max-sm:hidden">
                      {total ? (
                        <span className="whitespace-nowrap">
                          <span className="font-semibold text-fg tabular-nums">{formatTotal(total)}</span>
                          <span className="text-xs text-fg-subtle"> / 5</span>
                        </span>
                      ) : row.evaluation?.status === "pending" || row.evaluation?.status === "running" ? (
                        <span className="text-xs whitespace-nowrap text-fg-subtle">Evaluating</span>
                      ) : (
                        <span className="text-fg-subtle">—</span>
                      )}
                    </TableCell>
                    <TableCell className="max-md:hidden">
                      <BandBadge band={row.evaluation?.band} />
                    </TableCell>
                    <TableCell className="max-lg:hidden">
                      {row.decision ? (
                        <OutcomeBadge outcome={row.decision.outcome} />
                      ) : row.bucket === "awaiting_review" || (row.session && ["completed", "completing"].includes(row.session.status)) ? (
                        <span className="text-xs font-medium whitespace-nowrap text-warning-fg">Awaiting decision</span>
                      ) : (
                        <span className="text-fg-subtle">—</span>
                      )}
                    </TableCell>
                    <TableCell className="max-xl:hidden">
                      <span className="block max-w-[10rem] truncate text-[13px] text-fg-muted">{row.decision?.reviewer?.full_name ?? row.assigned_reviewer?.full_name ?? "—"}</span>
                    </TableCell>
                    <TableCell numeric className="max-sm:hidden">
                      <span className="inline-flex items-center justify-end gap-2">
                        {row.integrity_flags ? (
                          <Tooltip content={`${pluralize(row.integrity_flags, "integrity observation")}. Observations, not verdicts.`}>
                            <span tabIndex={0} className="inline-flex items-center gap-0.5 rounded-xs text-xs text-fg-subtle">
                              <ShieldAlert className="size-3" aria-hidden="true" />
                              <span className="sr-only">Integrity observations:</span>
                              {row.integrity_flags}
                            </span>
                          </Tooltip>
                        ) : null}
                        {row.review_items_open ? (
                          <span className="font-medium text-fg tabular-nums" aria-label={`${pluralize(row.review_items_open, "open review item")}`}>
                            {formatNumber(row.review_items_open)}
                          </span>
                        ) : (
                          <span className="text-fg-subtle">0</span>
                        )}
                      </span>
                    </TableCell>
                  </TableRow>
                );
              })}
        </TableBody>
      </Table>
      {!loading && !(rows ?? []).length ? <div className="border-t border-line-subtle">{empty}</div> : null}
      {totalRows > pageSize ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
          <p className="tabular-nums" aria-live="polite">
            {formatNumber(page * pageSize + 1)}–{formatNumber(Math.min((page + 1) * pageSize, totalRows))} of {formatNumber(totalRows)}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="mr-2 tabular-nums">
              Page {page + 1} of {pageCount}
            </span>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" data-testid="intake-page-prev" disabled={page === 0} onClick={() => onPageChange(page - 1)}>
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Next page" data-testid="intake-page-next" disabled={page >= pageCount - 1} onClick={() => onPageChange(page + 1)}>
              <ChevronRight />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
