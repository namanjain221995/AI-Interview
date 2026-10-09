import { ChevronLeft, ChevronRight, ShieldAlert } from "lucide-react";
import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import { useNavigate } from "react-router";

import { StatusBadge } from "@/components/domain/StatusBadge";
import { scoreTone, TONE_BG } from "@/components/domain/scoreBands";
import { Avatar } from "@/components/ui/Avatar";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Tooltip } from "@/components/ui/Tooltip";
import { errorMessage, isApiError } from "@/lib/api";
import { formatDuration, formatMinutes, formatNumber, formatRelativeOrDate, formatScore } from "@/lib/format";
import { labelFor, RECOMMENDATION_SHORT_LABELS, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { Recommendation, SessionSummary } from "@/lib/types";
import { INSUFFICIENT_EVIDENCE_HEADLINE, INSUFFICIENT_EVIDENCE_SHORT } from "@/components/domain/evidenceCopy";
import { cn } from "@/lib/utils";

import { isInProgress, isLive } from "../api/hooks";
import { DecisionBadge } from "../components/decisions";

export const RECOMMENDATION_TONES: Record<Recommendation, BadgeTone> = {
  strong_proceed: "success",
  proceed: "accent",
  human_review: "neutral",
  borderline_evidence: "warning",
  insufficient_evidence: "outline",
};

export function ScoreCell({ score, compact = false, insufficient = false }: { score: number | null | undefined; compact?: boolean; insufficient?: boolean }) {
  if (typeof score !== "number") {
    return insufficient ? (
      <span className="text-xs whitespace-nowrap text-warning-fg" title={INSUFFICIENT_EVIDENCE_HEADLINE} data-testid="score-insufficient">
        {compact ? "No score" : INSUFFICIENT_EVIDENCE_SHORT}
      </span>
    ) : (
      <span className="text-fg-subtle">—</span>
    );
  }
  const tone = scoreTone(score, 100);
  return (
    <span className="inline-flex items-center justify-end gap-2.5">
      <span className={cn("h-1 w-12 overflow-hidden rounded-full bg-track", compact ? "max-2xl:hidden" : "max-sm:hidden")} aria-hidden="true">
        <span className={cn("block h-full rounded-full", TONE_BG[tone])} style={{ width: `${Math.max(2, Math.min(100, score))}%` }} />
      </span>
      <span className="w-7 text-right font-medium text-fg tabular-nums">{formatScore(score)}</span>
    </span>
  );
}

function StatusCell({ session }: { session: SessionSummary }) {
  if (isLive(session.status)) {
    return (
      <span className="flex flex-col items-start gap-1">
        <StatusBadge kind="session" value={session.status} size="sm" />
        <span className="text-xs text-fg-muted tabular-nums">{formatMinutes(session.elapsed_seconds)} in</span>
      </span>
    );
  }
  return <StatusBadge kind="session" value={session.status} size="sm" />;
}

export interface SessionsTableProps {
  rows: SessionSummary[] | undefined;
  loading: boolean;
  fetching?: boolean;
  error?: unknown;
  onRetry?: () => void;
  empty: ReactNode;
  variant?: "full" | "compact";
  /** Server pagination. */
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (page: number) => void;
  caption: string;
}

const INTERACTIVE = "a,button,input,select,textarea,label,[role='menuitem'],[role='checkbox']";

export function SessionsTable({ rows, loading, fetching, error, onRetry, empty, variant = "full", page = 0, pageSize = 25, total, onPageChange, caption }: SessionsTableProps) {
  const navigate = useNavigate();
  const full = variant === "full";

  if (error && !rows?.length) {
    return (
      <div className="p-5">
        <Callout
          tone="danger"
          title="Couldn't load this list"
          actions={
            onRetry ? (
              <Button size="sm" variant="outline" onClick={onRetry}>
                Try again
              </Button>
            ) : undefined
          }
        >
          {errorMessage(error)}
          {isApiError(error) && error.requestId ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
        </Callout>
      </div>
    );
  }

  const open = (session: SessionSummary) => navigate(`/app/sessions/${session.id}`);
  const activate = (event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>, session: SessionSummary) => {
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.closest(INTERACTIVE)) return;
    open(session);
  };
  const totalRows = total ?? rows?.length ?? 0;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));

  return (
    <div className="flex flex-col">
      <Table aria-busy={loading || fetching || undefined} className={cn("transition-opacity duration-200", fetching && !loading && "opacity-60")}>
        <caption className="sr-only">{caption}</caption>
        <TableHeader>
          <tr>
            <TableHead>Candidate</TableHead>
            {full ? <TableHead className="max-md:hidden">Round</TableHead> : null}
            <TableHead>Status</TableHead>
            <TableHead align="right">Score</TableHead>
            <TableHead className="max-lg:hidden">AI suggestion</TableHead>
            {full ? <TableHead className="max-xl:hidden">Decision</TableHead> : null}
            {full ? (
              <TableHead align="right" className="max-sm:hidden">
                <span className="sr-only">Integrity observations</span>
                <ShieldAlert className="ml-auto size-3.5" aria-hidden="true" />
              </TableHead>
            ) : null}
            <TableHead align="right" className="max-sm:hidden">
              Started
            </TableHead>
          </tr>
        </TableHeader>
        <TableBody>
          {loading && !rows?.length
            ? Array.from({ length: full ? 8 : 5 }, (_, i) => (
                <TableRow key={`skeleton-${i}`}>
                  <TableCell>
                    <span className="flex items-center gap-3">
                      <Skeleton className="size-8 rounded-full" />
                      <span className="grid gap-1.5">
                        <Skeleton className="h-3.5 w-32" />
                        <Skeleton className="h-3 w-24" />
                      </span>
                    </span>
                  </TableCell>
                  {Array.from({ length: full ? 7 : 4 }, (_, j) => (
                    <TableCell key={j} className={cn(j === 0 && full && "max-md:hidden")}>
                      <Skeleton className="h-3.5 w-16" style={{ opacity: 1 - i * 0.1 }} />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : (rows ?? []).map((s) => (
                <TableRow
                  key={s.id}
                  data-testid="session-row"
                  data-session-id={s.id}
                  data-status={s.status}
                  interactive
                  tabIndex={0}
                  aria-label={`Open the session for ${s.candidate?.full_name ?? "candidate"}`}
                  onClick={(e) => activate(e, s)}
                  onKeyDown={(e) => {
                    if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) {
                      e.preventDefault();
                      activate(e, s);
                    }
                  }}
                  className={cn(isLive(s.status) && "bg-signal-soft/40 shadow-[inset_2px_0_0_var(--signal)]")}
                >
                  <TableCell>
                    <span className={cn("flex min-w-[7.5rem] items-center gap-3", full ? "sm:min-w-[13.5rem]" : "sm:min-w-[10.5rem]")}>
                      {/* No avatar on phones, so candidate, status and score fit without sideways scrolling. */}
                      <Avatar name={s.candidate?.full_name ?? "Candidate"} size="md" className="max-sm:hidden" />
                      <span className="grid min-w-0">
                        <span className="truncate font-medium text-fg">{s.candidate?.full_name ?? "Unknown candidate"}</span>
                        <span className="truncate text-xs text-fg-muted">{s.job?.title ?? "—"}</span>
                      </span>
                    </span>
                  </TableCell>
                  {full ? (
                    <TableCell className="max-md:hidden">
                      <span className="grid min-w-[9.5rem]">
                        <span className="text-fg">{s.round?.name ?? "—"}</span>
                        {s.round && labelFor(ROUND_TYPE_LABELS, s.round.round_type).toLowerCase() !== s.round.name.toLowerCase() ? (
                          <span className="truncate text-xs text-fg-muted">{labelFor(ROUND_TYPE_LABELS, s.round.round_type)}</span>
                        ) : null}
                      </span>
                    </TableCell>
                  ) : null}
                  <TableCell>
                    <StatusCell session={s} />
                  </TableCell>
                  <TableCell numeric>
                    {isInProgress(s.status) ? <span className="text-xs whitespace-nowrap text-fg-subtle">In progress</span> : <ScoreCell score={s.overall_score} compact={!full} insufficient={s.recommendation === "insufficient_evidence"} />}
                  </TableCell>
                  <TableCell className="max-lg:hidden">
                    {s.recommendation ? (
                      <Badge size="sm" tone={RECOMMENDATION_TONES[s.recommendation]}>
                        {RECOMMENDATION_SHORT_LABELS[s.recommendation]}
                      </Badge>
                    ) : (
                      <span className="text-fg-subtle">—</span>
                    )}
                  </TableCell>
                  {full ? (
                    <TableCell className="max-xl:hidden">
                      {s.review_decision ? (
                        <DecisionBadge value={s.review_decision} size="sm" />
                      ) : s.status === "completed" ? (
                        <span className="text-xs font-medium text-warning-fg">Awaiting decision</span>
                      ) : (
                        <span className="text-fg-subtle">—</span>
                      )}
                    </TableCell>
                  ) : null}
                  {full ? (
                    <TableCell numeric className="max-sm:hidden">
                      {s.integrity_flags ? (
                        <Tooltip content={`${s.integrity_flags} integrity ${s.integrity_flags === 1 ? "observation" : "observations"} to review. Observations, not verdicts.`}>
                          <span tabIndex={0} className="rounded-xs text-[13px] font-medium text-fg-muted">
                            {s.integrity_flags}
                          </span>
                        </Tooltip>
                      ) : (
                        <span className="text-fg-subtle">0</span>
                      )}
                    </TableCell>
                  ) : null}
                  <TableCell numeric className="whitespace-nowrap text-fg-muted max-sm:hidden">
                    <span className="grid justify-items-end">
                      <span>{formatRelativeOrDate(s.started_at ?? s.created_at)}</span>
                      {full && !isInProgress(s.status) && s.elapsed_seconds ? <span className="text-xs text-fg-subtle">{formatDuration(s.elapsed_seconds)}</span> : null}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
      {!loading && !(rows ?? []).length ? <div className="border-t border-line-subtle">{empty}</div> : null}
      {onPageChange && totalRows > pageSize ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
          <p className="tabular-nums" aria-live="polite">
            {formatNumber(page * pageSize + 1)}–{formatNumber(Math.min((page + 1) * pageSize, totalRows))} of {formatNumber(totalRows)}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="mr-2 tabular-nums">
              Page {page + 1} of {pageCount}
            </span>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" disabled={page === 0} onClick={() => onPageChange(page - 1)}>
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Next page" disabled={page >= pageCount - 1} onClick={() => onPageChange(page + 1)}>
              <ChevronRight />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
