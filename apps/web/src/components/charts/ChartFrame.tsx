import { BarChart3, Table2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface ChartTableColumn {
  key: string;
  label: string;
  numeric?: boolean;
}

export interface ChartTable {
  caption: string;
  columns: ChartTableColumn[];
  rows: Array<Record<string, ReactNode>>;
}

export interface ChartFrameProps {
  title?: ReactNode;
  description?: ReactNode;
  /** Legend rendered above the plot. */
  legend?: ReactNode;
  /** Data twin of the chart: always available to screen readers, optionally visible. */
  table: ChartTable;
  /** Plain-language summary announced instead of the SVG. */
  summary: string;
  /** Offer a visible Chart / Table switch. */
  tableToggle?: boolean;
  /** Right-aligned header controls. */
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

function DataTableView({ table, visible }: { table: ChartTable; visible: boolean }) {
  return (
    <div className={cn(visible ? "max-h-80 overflow-auto rounded-md border border-line scrollbar-thin" : "sr-only")}>
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">{table.caption}</caption>
        <thead>
          <tr>
            {table.columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={cn(
                  "sticky top-0 border-b border-line bg-surface-subtle px-3 py-2 text-xs font-medium text-fg-muted",
                  column.numeric ? "text-right" : "text-left",
                )}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, index) => (
            <tr key={index}>
              {table.columns.map((column) => (
                <td
                  key={column.key}
                  className={cn("border-b border-line-subtle px-3 py-2 text-fg", column.numeric ? "text-right tabular-nums" : "text-left")}
                >
                  {row[column.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Wraps a chart with its title, legend and an accessible data-table twin.
 * Tooltips enhance; the table guarantees every value is reachable.
 */
export function ChartFrame({ title, description, legend, table, summary, tableToggle = true, actions, children, className }: ChartFrameProps) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const summaryId = useId();
  return (
    <figure className={cn("m-0 flex min-w-0 flex-col gap-3", className)}>
      {title || description || tableToggle || actions ? (
        <div className="flex items-start justify-between gap-3">
          <div className="grid min-w-0 gap-0.5">
            {title ? <figcaption className="text-sm font-semibold text-fg">{title}</figcaption> : null}
            {description ? <p className="text-xs text-fg-muted">{description}</p> : null}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            {tableToggle ? (
              <div className="inline-flex rounded-control bg-surface-sunken/70 p-0.5" role="group" aria-label="Chart view">
                {(["chart", "table"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={view === mode}
                    aria-label={mode === "chart" ? "Show chart" : "Show table"}
                    onClick={() => setView(mode)}
                    className={cn(
                      "flex h-6 w-7 items-center justify-center rounded-[6px] text-fg-muted transition-colors hover:text-fg",
                      view === mode && "bg-surface text-fg shadow-[0_1px_2px_rgb(16_24_40/0.08),0_0_0_1px_var(--border)]",
                    )}
                  >
                    {mode === "chart" ? <BarChart3 className="size-3.5" /> : <Table2 className="size-3.5" />}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
      {view === "chart" ? (
        <>
          {legend}
          <div role="img" aria-describedby={summaryId} aria-label={typeof title === "string" ? title : "Chart"} className="min-w-0">
            {children}
          </div>
          <p id={summaryId} className="sr-only">
            {summary}
          </p>
          <DataTableView table={table} visible={false} />
        </>
      ) : (
        <DataTableView table={table} visible />
      )}
    </figure>
  );
}

export interface LegendItem {
  key: string;
  label: string;
  color: string;
  /** Line key (lines) or square swatch (bars/areas). */
  shape?: "line" | "square" | "dashed";
  hidden?: boolean;
}

export function ChartLegend({ items, onToggle, className }: { items: LegendItem[]; onToggle?: (key: string) => void; className?: string }) {
  return (
    <ul className={cn("flex flex-wrap items-center gap-x-4 gap-y-1.5", className)} aria-label="Legend">
      {items.map((item) => {
        const swatch =
          item.shape === "square" ? (
            <span className="size-2.5 rounded-[3px]" style={{ background: item.color }} />
          ) : (
            <span
              className="h-0.5 w-3.5 rounded-full"
              style={
                item.shape === "dashed"
                  ? { backgroundImage: `linear-gradient(90deg, ${item.color} 60%, transparent 60%)`, backgroundSize: "6px 2px" }
                  : { background: item.color }
              }
            />
          );
        const body = (
          <>
            {swatch}
            <span className={cn("text-xs", item.hidden ? "text-fg-subtle line-through" : "text-fg-muted")}>{item.label}</span>
          </>
        );
        return (
          <li key={item.key}>
            {onToggle ? (
              <button
                type="button"
                aria-pressed={!item.hidden}
                onClick={() => onToggle(item.key)}
                className="flex items-center gap-1.5 rounded-xs py-0.5 hover:opacity-80"
              >
                {body}
              </button>
            ) : (
              <span className="flex items-center gap-1.5">{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Tooltip body shared by charts: values lead, labels follow, line keys mark series. */
export function ChartTooltipCard({ title, rows }: { title?: ReactNode; rows: Array<{ key: string; label: string; value: ReactNode; color: string }> }) {
  return (
    <div className="min-w-40 rounded-md border border-line bg-surface-raised px-3 py-2 text-xs shadow-overlay">
      {title ? <p className="mb-1.5 font-medium text-fg-muted">{title}</p> : null}
      <ul className="grid gap-1">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center gap-2">
            <span className="h-0.5 w-3 shrink-0 rounded-full" style={{ background: row.color }} aria-hidden="true" />
            <span className="font-semibold text-fg tabular-nums">{row.value}</span>
            <span className="truncate text-fg-muted">{row.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
