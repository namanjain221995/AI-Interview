import { forwardRef, type HTMLAttributes, type TdHTMLAttributes, type ThHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Align = "left" | "center" | "right";
const ALIGN: Record<Align, string> = { left: "text-left", center: "text-center", right: "text-right" };

export interface TableProps extends HTMLAttributes<HTMLTableElement> {
  /** Class for the horizontal-scroll wrapper. */
  containerClassName?: string;
}

/**
 * Horizontal-scroll wrapper + table. `contain-inline` makes the wrapper take its width
 * from the parent only, so a wide table scrolls inside its card instead of widening a
 * grid/flex column (the classic "table blows the column out" bug). For a sticky first
 * column on narrow screens add `sticky left-0 z-[1] bg-surface` to that column's cells.
 */
export const Table = forwardRef<HTMLTableElement, TableProps>(function Table({ className, containerClassName, ...props }, ref) {
  return (
    <div className={cn("relative w-full overflow-x-auto scrollbar-thin contain-inline", containerClassName)}>
      <table ref={ref} className={cn("w-full caption-bottom border-separate border-spacing-0 text-sm", className)} {...props} />
    </div>
  );
});

export const TableHeader = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(function TableHeader(
  { className, ...props },
  ref,
) {
  return <thead ref={ref} className={cn("[&_th]:border-b [&_th]:border-line", className)} {...props} />;
});

export const TableBody = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(function TableBody(
  { className, ...props },
  ref,
) {
  return <tbody ref={ref} className={cn("[&_tr:last-child_td]:border-b-0", className)} {...props} />;
});

export const TableFooter = forwardRef<HTMLTableSectionElement, HTMLAttributes<HTMLTableSectionElement>>(function TableFooter(
  { className, ...props },
  ref,
) {
  return <tfoot ref={ref} className={cn("bg-surface-subtle font-medium [&_td]:border-t [&_td]:border-line", className)} {...props} />;
});

export interface TableRowProps extends HTMLAttributes<HTMLTableRowElement> {
  interactive?: boolean;
  selected?: boolean;
}

export const TableRow = forwardRef<HTMLTableRowElement, TableRowProps>(function TableRow({ className, interactive, selected, ...props }, ref) {
  return (
    <tr
      ref={ref}
      data-selected={selected || undefined}
      className={cn(
        "group/row transition-colors duration-75 data-[selected]:bg-accent-soft/60",
        interactive && "cursor-pointer hover:bg-surface-hover focus-visible:bg-surface-hover focus-visible:outline-2 focus-visible:-outline-offset-2",
        className,
      )}
      {...props}
    />
  );
});

export interface TableHeadProps extends ThHTMLAttributes<HTMLTableCellElement> {
  align?: Align;
}

export const TableHead = forwardRef<HTMLTableCellElement, TableHeadProps>(function TableHead({ className, align = "left", ...props }, ref) {
  return (
    <th
      ref={ref}
      scope="col"
      className={cn(
        "h-9 bg-surface-subtle px-4 text-xs font-medium whitespace-nowrap text-fg-muted first:pl-5 last:pr-5",
        ALIGN[align],
        className,
      )}
      {...props}
    />
  );
});

export interface TableCellProps extends TdHTMLAttributes<HTMLTableCellElement> {
  align?: Align;
  /** Tabular figures for numbers that align in a column. */
  numeric?: boolean;
}

export const TableCell = forwardRef<HTMLTableCellElement, TableCellProps>(function TableCell(
  { className, align, numeric, ...props },
  ref,
) {
  return (
    <td
      ref={ref}
      className={cn(
        "border-b border-line-subtle px-4 py-3 align-middle text-fg first:pl-5 last:pr-5",
        ALIGN[align ?? (numeric ? "right" : "left")],
        numeric && "tabular-nums",
        className,
      )}
      {...props}
    />
  );
});

export const TableCaption = forwardRef<HTMLTableCaptionElement, HTMLAttributes<HTMLTableCaptionElement>>(function TableCaption(
  { className, ...props },
  ref,
) {
  return <caption ref={ref} className={cn("mt-3 text-xs text-fg-muted", className)} {...props} />;
});
