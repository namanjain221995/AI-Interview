import { AlertCircle, ArrowDown, ArrowUp, Copy, MoreHorizontal, Pencil, Trash2, UserCheck } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/DropdownMenu";
import { ROUND_TYPE_LABELS } from "@/lib/labels";
import type { Competency, RoundConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

import { RoundBlueprint } from "./RoundBlueprint";

export interface RoundCardProps {
  index: number;
  config: RoundConfig;
  catalog?: Competency[];
  errorCount?: number;
  warningCount?: number;
  inactive?: boolean;
  onEdit?: () => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
  onDuplicate?: () => void;
  onRemove?: () => void;
  footer?: ReactNode;
  className?: string;
}

/** A round in a job: sequence number, name, type and its blueprint. */
export function RoundCard({ index, config, catalog, errorCount = 0, warningCount = 0, inactive, onEdit, onMoveUp, onMoveDown, onDuplicate, onRemove, footer, className }: RoundCardProps) {
  const editable = Boolean(onEdit);
  return (
    <article
      className={cn(
        "group relative grid gap-4 rounded-lg border bg-surface p-4 shadow-[0_1px_2px_rgb(16_24_40/0.04)] transition-colors",
        errorCount > 0 ? "border-danger-line" : "border-line",
        editable && "hover:border-line-strong",
        inactive && "opacity-60",
        className,
      )}
      aria-label={`Round ${index + 1}: ${config.name}`}
    >
      <header className="flex items-start gap-3">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-line-strong text-xs font-semibold text-fg-muted tabular-nums" aria-hidden="true">
          {index + 1}
        </span>
        <div className="grid min-w-0 flex-1 gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {editable ? (
              <button type="button" onClick={onEdit} className="min-w-0 truncate text-left text-[15px] font-semibold text-fg after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:outline-none">
                {config.name || "Untitled round"}
              </button>
            ) : (
              <h3 className="min-w-0 truncate text-[15px] font-semibold text-fg">{config.name || "Untitled round"}</h3>
            )}
            {config.candidate_selectable ? (
              <Badge size="sm" tone="outline" icon={<UserCheck />}>
                Candidate can pick
              </Badge>
            ) : null}
            {inactive ? (
              <Badge size="sm" tone="neutral">
                Inactive
              </Badge>
            ) : null}
          </div>
          {(ROUND_TYPE_LABELS[config.round_type] ?? config.round_type).toLowerCase() !== config.name.trim().toLowerCase() ? (
            <p className="text-xs text-fg-muted">{ROUND_TYPE_LABELS[config.round_type] ?? config.round_type}</p>
          ) : null}
        </div>
        <div className="relative z-10 flex shrink-0 items-center gap-1">
          {errorCount > 0 ? (
            <Badge tone="danger" size="sm" icon={<AlertCircle />}>
              {errorCount} to fix
            </Badge>
          ) : warningCount > 0 ? (
            <Badge tone="warning" size="sm">
              {warningCount} {warningCount === 1 ? "suggestion" : "suggestions"}
            </Badge>
          ) : null}
          {editable ? (
            <>
              <Button variant="ghost" size="sm" leftIcon={<Pencil />} onClick={onEdit} className="max-sm:hidden">
                Edit
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className="size-8 px-0" aria-label={`More actions for ${config.name}`}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={onEdit}>
                    <Pencil /> Edit round
                  </DropdownMenuItem>
                  {onMoveUp ? (
                    <DropdownMenuItem onSelect={onMoveUp}>
                      <ArrowUp /> Move up
                    </DropdownMenuItem>
                  ) : null}
                  {onMoveDown ? (
                    <DropdownMenuItem onSelect={onMoveDown}>
                      <ArrowDown /> Move down
                    </DropdownMenuItem>
                  ) : null}
                  {onDuplicate ? (
                    <DropdownMenuItem onSelect={onDuplicate}>
                      <Copy /> Duplicate
                    </DropdownMenuItem>
                  ) : null}
                  {onRemove ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={onRemove} destructive>
                        <Trash2 /> Remove round
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          ) : null}
        </div>
      </header>
      <RoundBlueprint config={config} catalog={catalog} />
      {footer}
    </article>
  );
}
