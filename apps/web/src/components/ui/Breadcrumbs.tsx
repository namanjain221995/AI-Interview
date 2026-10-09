import { ChevronRight } from "lucide-react";
import { Fragment } from "react";
import { Link } from "react-router";

import { cn } from "@/lib/utils";

export interface BreadcrumbItem {
  label: string;
  to?: string;
}

export function Breadcrumbs({ items, className }: { items: BreadcrumbItem[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className={cn("min-w-0", className)}>
      <ol className="flex min-w-0 items-center gap-1 text-[13px] text-fg-muted">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <Fragment key={`${item.label}-${index}`}>
              <li className={cn("min-w-0", last ? "truncate" : "shrink-0")}>
                {item.to && !last ? (
                  <Link to={item.to} className="rounded-xs transition-colors hover:text-fg">
                    {item.label}
                  </Link>
                ) : (
                  <span aria-current={last ? "page" : undefined} className={cn(last && "text-fg")}>
                    {item.label}
                  </span>
                )}
              </li>
              {!last ? (
                <li aria-hidden="true" className="shrink-0 text-fg-subtle">
                  <ChevronRight className="size-3.5" />
                </li>
              ) : null}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}
