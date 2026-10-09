/**
 * Data table with sorting, pagination, loading/empty/error states and
 * keyboard-accessible row clicks.
 *
 * Client mode (default): pass all rows; sorting uses `column.sortValue` and
 * pagination slices locally. Server mode: pass `total` + `onPageChange`
 * (and optionally `sort` + `onSortChange`); `data` is the current page.
 */

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from "lucide-react";
import { useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";

import { errorMessage, isApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Button } from "./Button";
import { Callout } from "./Callout";
import { Skeleton } from "./Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./Table";

export type SortDirection = "asc" | "desc";
export interface SortState {
  columnId: string;
  direction: SortDirection;
}

type SortPrimitive = string | number | boolean | Date | null | undefined;

export interface DataTableColumn<T> {
  id: string;
  header: ReactNode;
  cell: (row: T, index: number) => ReactNode;
  /** Enables sorting on this column (client mode uses the returned value). */
  sortValue?: (row: T) => SortPrimitive;
  /** Server mode: mark sortable without a client accessor. */
  sortable?: boolean;
  align?: "left" | "center" | "right";
  /** Tabular figures + right alignment. */
  numeric?: boolean;
  width?: string;
  /** Hide below a breakpoint to keep small screens readable. */
  hideBelow?: "sm" | "md" | "lg" | "xl";
  className?: string;
  headerClassName?: string;
}

const HIDE_BELOW = { sm: "max-sm:hidden", md: "max-md:hidden", lg: "max-lg:hidden", xl: "max-xl:hidden" } as const;

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  data: T[] | undefined;
  getRowId: (row: T) => string;
  /** First load: renders skeleton rows. */
  loading?: boolean;
  /** Background refetch: keeps the current rows, dimmed. */
  fetching?: boolean;
  error?: unknown;
  onRetry?: () => void;
  /** Rendered when there are no rows. */
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  /** Accessible label for clickable rows, e.g. (row) => `Open ${row.name}`. */
  rowLabel?: (row: T) => string;
  isRowSelected?: (row: T) => boolean;
  sort?: SortState | null;
  defaultSort?: SortState | null;
  onSortChange?: (sort: SortState | null) => void;
  pageSize?: number;
  /** Server mode: zero-based page index. */
  page?: number;
  total?: number;
  onPageChange?: (page: number) => void;
  skeletonRows?: number;
  caption?: string;
  density?: "comfortable" | "compact";
  /**
   * Keep the first column visible while the table scrolls sideways (narrow screens).
   * Pair it with `hideBelow` on low-value columns; see THEME_GUIDE.md, responsive tables.
   */
  stickyFirstColumn?: boolean;
  className?: string;
}

// The sticky cell is opaque (content scrolls under it), so row hover/selection tints are
// painted as an inset overlay; the inset right hairline marks the scroll edge.
const STICKY_HEAD = "sticky left-0 z-[2] bg-surface-subtle shadow-[inset_-1px_0_0_var(--border)]";
const STICKY_CELL = cn(
  "sticky left-0 z-[1] bg-surface shadow-[inset_-1px_0_0_var(--border)]",
  "group-hover/row:shadow-[inset_-1px_0_0_var(--border),inset_0_0_0_9999px_var(--surface-hover)]",
  "group-data-[selected]/row:shadow-[inset_-1px_0_0_var(--border),inset_0_0_0_9999px_var(--accent-soft)]",
);

function compareValues(a: SortPrimitive, b: SortPrimitive): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  const av = a instanceof Date ? a.getTime() : a;
  const bv = b instanceof Date ? b.getTime() : b;
  if (typeof av === "number" && typeof bv === "number") return av - bv;
  if (typeof av === "boolean" && typeof bv === "boolean") return Number(av) - Number(bv);
  return String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
}

const INTERACTIVE_SELECTOR = "a,button,input,select,textarea,label,[role='menuitem'],[role='checkbox'],[data-row-action]";

export function DataTable<T>({
  columns,
  data,
  getRowId,
  loading = false,
  fetching = false,
  error,
  onRetry,
  empty,
  onRowClick,
  rowLabel,
  isRowSelected,
  sort: controlledSort,
  defaultSort = null,
  onSortChange,
  pageSize = 20,
  page: controlledPage,
  total,
  onPageChange,
  skeletonRows = 6,
  caption,
  density = "comfortable",
    stickyFirstColumn = false,
  className,
}: DataTableProps<T>) {
  const serverPaging = total !== undefined && onPageChange !== undefined;
  const serverSorting = controlledSort !== undefined && onSortChange !== undefined;
  const [localSort, setLocalSort] = useState<SortState | null>(defaultSort);
  const [localPage, setLocalPage] = useState(0);
  const sort = serverSorting ? controlledSort : localSort;

  const rows = data ?? [];
  const sorted = useMemo(() => {
    if (serverSorting || !sort) return rows;
    const column = columns.find((c) => c.id === sort.columnId);
    if (!column?.sortValue) return rows;
    const accessor = column.sortValue;
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      // Missing values stay at the bottom in both directions.
      const aMissing = av === null || av === undefined;
      const bMissing = bv === null || bv === undefined;
      if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
      const result = compareValues(av, bv);
      return sort.direction === "asc" ? result : -result;
    });
    return copy;
  }, [rows, sort, columns, serverSorting]);

  const totalRows = serverPaging ? total : sorted.length;
  const pageCount = Math.max(1, Math.ceil(totalRows / pageSize));
  const page = Math.min(serverPaging ? (controlledPage ?? 0) : localPage, pageCount - 1);
  const visible = serverPaging ? sorted : sorted.slice(page * pageSize, page * pageSize + pageSize);

  const setPage = (next: number) => {
    const clamped = Math.max(0, Math.min(next, pageCount - 1));
    if (serverPaging) onPageChange?.(clamped);
    else setLocalPage(clamped);
  };

  const toggleSort = (column: DataTableColumn<T>) => {
    let next: SortState | null;
    if (!sort || sort.columnId !== column.id) next = { columnId: column.id, direction: "asc" };
    else if (sort.direction === "asc") next = { columnId: column.id, direction: "desc" };
    else next = null;
    if (serverSorting) onSortChange?.(next);
    else {
      setLocalSort(next);
      onSortChange?.(next);
    }
    setPage(0);
  };

  const handleRowActivate = (event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (!onRowClick) return;
    const target = event.target as HTMLElement;
    if (target !== event.currentTarget && target.closest(INTERACTIVE_SELECTOR)) return;
    onRowClick(row);
  };

  const cellPadding = density === "compact" ? "py-2" : "py-3";
  const showSkeleton = loading && rows.length === 0;
  const showEmpty = !loading && !error && rows.length === 0;

  if (error && rows.length === 0) {
    return (
      <div className={cn("p-5", className)}>
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

  return (
    <div className={cn("flex flex-col", className)}>
      <Table aria-busy={loading || fetching || undefined} className={cn("transition-opacity duration-200", fetching && !loading && "opacity-60")}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <TableHeader>
          <tr>
            {columns.map((column, colIndex) => {
              const sortable = Boolean(column.sortValue) || column.sortable;
              const active = sort?.columnId === column.id;
              const ariaSort = active ? (sort?.direction === "asc" ? "ascending" : "descending") : sortable ? "none" : undefined;
              const align = column.align ?? (column.numeric ? "right" : "left");
              return (
                <TableHead
                  key={column.id}
                  align={align}
                  aria-sort={ariaSort}
                  style={column.width ? { width: column.width } : undefined}
                  className={cn(column.hideBelow && HIDE_BELOW[column.hideBelow], stickyFirstColumn && colIndex === 0 && STICKY_HEAD, column.headerClassName)}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      className={cn(
                        "-mx-1.5 inline-flex h-7 items-center gap-1 rounded-sm px-1.5 hover:bg-surface-hover hover:text-fg",
                        active && "text-fg",
                        align === "right" && "flex-row-reverse",
                      )}
                    >
                      {column.header}
                      {active ? (
                        sort?.direction === "asc" ? (
                          <ArrowUp className="size-3.5" aria-hidden="true" />
                        ) : (
                          <ArrowDown className="size-3.5" aria-hidden="true" />
                        )
                      ) : (
                        <ChevronsUpDown className="size-3.5 opacity-50" aria-hidden="true" />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </TableHead>
              );
            })}
          </tr>
        </TableHeader>
        <TableBody>
          {showSkeleton
            ? Array.from({ length: skeletonRows }, (_, rowIndex) => (
                <TableRow key={`skeleton-${rowIndex}`}>
                  {columns.map((column, colIndex) => (
                    <TableCell key={column.id} className={cn(cellPadding, column.hideBelow && HIDE_BELOW[column.hideBelow])}>
                      <Skeleton
                        className={cn("h-4", colIndex === 0 ? "w-40" : "w-20", column.numeric && "ml-auto w-12")}
                        style={{ opacity: 1 - rowIndex * 0.12 }}
                      />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : visible.map((row, index) => (
                <TableRow
                  key={getRowId(row)}
                  interactive={Boolean(onRowClick)}
                  selected={isRowSelected?.(row)}
                  tabIndex={onRowClick ? 0 : undefined}
                  aria-label={onRowClick && rowLabel ? rowLabel(row) : undefined}
                  onClick={onRowClick ? (event) => handleRowActivate(event, row) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if ((event.key === "Enter" || event.key === " ") && event.target === event.currentTarget) {
                            event.preventDefault();
                            handleRowActivate(event, row);
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((column, colIndex) => (
                    <TableCell
                      key={column.id}
                      align={column.align}
                      numeric={column.numeric}
                      className={cn(cellPadding, column.hideBelow && HIDE_BELOW[column.hideBelow], stickyFirstColumn && colIndex === 0 && STICKY_CELL, column.className)}
                    >
                      {column.cell(row, page * pageSize + index)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
        </TableBody>
      </Table>

      {showEmpty ? <div className="border-t border-line-subtle">{empty ?? <p className="px-5 py-10 text-center text-sm text-fg-muted">No results</p>}</div> : null}

      {!showSkeleton && totalRows > pageSize ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle px-5 py-3 text-[13px] text-fg-muted">
          <p className="tabular-nums" aria-live="polite">
            {formatNumber(page * pageSize + 1)}–{formatNumber(Math.min((page + 1) * pageSize, totalRows))} of {formatNumber(totalRows)}
          </p>
          <div className="flex items-center gap-1.5">
            <span className="mr-2 tabular-nums">
              Page {page + 1} of {pageCount}
            </span>
            <Button variant="outline" size="sm" className="size-8 px-0" aria-label="Previous page" disabled={page === 0} onClick={() => setPage(page - 1)}>
              <ChevronLeft />
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="size-8 px-0"
              aria-label="Next page"
              disabled={page >= pageCount - 1}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
