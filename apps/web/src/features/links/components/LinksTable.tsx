import { ChevronLeft, ChevronRight, Flag } from "lucide-react";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useNavigate } from "react-router";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { formatDate, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Callout } from "@/components/ui/Callout";
import { errorMessage, isApiError } from "@/lib/api";

import { LINK_STATE_LABELS, LINK_STATE_TONES } from "../status";
import type { OpenLinkSummary } from "../types";

const INTERACTIVE = "a,button,input,select,textarea,label,[role='menuitem'],[role='checkbox']";

export interface LinksTableProps {
  rows: OpenLinkSummary[] | undefined;
  loading: boolean;
  fetching?: boolean;
  error?: unknown;
  onRetry?: () => void;
  empty: ReactNode;
  /** Hide the job column (the job page lists its own links). */
  hideJob?: boolean;
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
}

function roundsLabel(link: OpenLinkSummary): string {
  if (link.rounds.length === 0) return "No rounds";
  if (link.rounds.length === 1) return link.rounds[0]!.name;
  return `${link.rounds.length} rounds in order`;
}

/** List-level failure (a 404 here means the list isn't available, not that an item was removed). */
export function ListLoadError({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title: string }) {
  const unavailable = isApiError(error) && error.status === 404;
  return (
    <Callout
      tone="danger"
      title={title}
      actions={
        onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {unavailable ? "The server didn't recognise this request. Interview links may not be available on this server yet." : errorMessage(error)}
      {isApiError(error) && error.requestId ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
    </Callout>
  );
}

/** Links of the organization or of one job; rows open the link's page. */
export function LinksTable({ rows, loading, fetching, error, onRetry, empty, hideJob = false, page = 0, pageSize = 25, total, onPageChange }: LinksTableProps) {
  const navigate = useNavigate();
  if (error && !rows?.length) {
    return (
      <div className="p-5">
        <ListLoadError error={error} onRetry={onRetry} title="Couldn't load the links" />
      </div>
    );
  }
  const open = (link: OpenLinkSummary) => navigate(`/app/links/${link.id}`);
  const activate = (event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>, link: OpenLinkSummary) => {
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.closest(INTERACTIVE)) return;
    open(link);
  };
  const totalRows = total ?? rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));

  return (
    <div className="flex flex-col">
      <Table aria-busy={loading || fetching || undefined} className={cn("transition-opacity duration-200", fetching && !loading && "opacity-60")}>
        <caption className="sr-only">Interview links</caption>
        <TableHeader>
          <tr>
            <TableHead className="sticky left-0 z-[2] bg-surface-subtle">Link</TableHead>
            <TableHead>State</TableHead>
            <TableHead className="max-md:hidden">Rounds</TableHead>
            <TableHead align="right">Registered</TableHead>
            <TableHead align="right" className="max-sm:hidden">
              Awaiting review
            </TableHead>
            <TableHead align="right" className="max-lg:hidden">
              <span className="sr-only">Flagged for review</span>
              <Flag className="ml-auto size-3.5" aria-hidden="true" />
            </TableHead>
            <TableHead align="right" className="max-lg:hidden">
              Closes
            </TableHead>
          </tr>
        </TableHeader>
        <TableBody>
          {loading && !rows?.length
            ? Array.from({ length: 4 }, (_, i) => (
                <TableRow key={`s-${i}`}>
                  <TableCell>
                    <span className="grid gap-1.5">
                      <Skeleton className="h-3.5 w-40" />
                      <Skeleton className="h-3 w-24" />
                    </span>
                  </TableCell>
                  {Array.from({ length: 6 }, (_, j) => (
                    <TableCell key={j} className={cn(j === 1 && "max-md:hidden", j === 3 && "max-sm:hidden", j >= 4 && "max-lg:hidden")}>
                      <Skeleton className="h-3.5 w-12" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : (rows ?? []).map((link) => (
                <TableRow
                  key={link.id}
                  data-testid="link-row"
                  data-link-id={link.id}
                  data-state={link.state}
                  interactive
                  tabIndex={0}
                  aria-label={`Open ${link.name}`}
                  onClick={(e) => activate(e, link)}
                  onKeyDown={(e) => {
                    if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                      e.preventDefault();
                      activate(e, link);
                    }
                  }}
                >
                  <TableCell className="sticky left-0 z-[1] bg-surface">
                    <span className="grid min-w-[11rem] max-w-[22rem]">
                      <span className="truncate font-medium text-fg">{link.name}</span>
                      {!hideJob ? <span className="truncate text-xs text-fg-muted">{link.job.title}</span> : null}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge tone={LINK_STATE_TONES[link.state]} size="sm" dot>
                      {LINK_STATE_LABELS[link.state]}
                    </Badge>
                  </TableCell>
                  <TableCell className="max-md:hidden">
                    <span className="block max-w-[14rem] truncate text-fg-muted" title={link.rounds.map((r) => r.name).join(", ")}>
                      {roundsLabel(link)}
                    </span>
                  </TableCell>
                  <TableCell numeric className="tabular-nums">
                    {formatNumber(link.counts.registrations)}
                    {link.max_registrations ? <span className="text-fg-subtle"> / {formatNumber(link.max_registrations)}</span> : null}
                  </TableCell>
                  <TableCell numeric className="max-sm:hidden">
                    {link.counts.awaiting_review ? <span className="font-medium text-warning-fg">{formatNumber(link.counts.awaiting_review)}</span> : <span className="text-fg-subtle">0</span>}
                  </TableCell>
                  <TableCell numeric className="max-lg:hidden">
                    {link.counts.flagged ? <span className="font-medium text-fg">{formatNumber(link.counts.flagged)}</span> : <span className="text-fg-subtle">0</span>}
                  </TableCell>
                  <TableCell numeric className="whitespace-nowrap text-fg-muted max-lg:hidden">
                    {link.closes_at ? formatDate(link.closes_at) : "No end date"}
                  </TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
      {!loading && !(rows ?? []).length ? <div className="border-t border-line-subtle">{empty}</div> : null}
      {onPageChange && totalRows > pageSize ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
          <p className="tabular-nums">
            {formatNumber(page * pageSize + 1)}–{formatNumber(Math.min((page + 1) * pageSize, totalRows))} of {formatNumber(totalRows)}
          </p>
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" disabled={page === 0} onClick={() => onPageChange(page - 1)}>
              <ChevronLeft />
            </Button>
            <span className="tabular-nums">
              {page + 1} / {pageCount}
            </span>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Next page" disabled={page >= pageCount - 1} onClick={() => onPageChange(page + 1)}>
              <ChevronRight />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
