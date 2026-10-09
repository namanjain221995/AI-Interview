import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Breadcrumbs, type BreadcrumbItem } from "./Breadcrumbs";

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** Primary and secondary page actions, right-aligned. */
  actions?: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  /** Status badges or key facts shown beside the title. */
  titleAddon?: ReactNode;
  /** Content under the description (meta facts, tabs). */
  children?: ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, breadcrumbs, titleAddon, children, className }: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-3 pb-6", className)}>
      {breadcrumbs && breadcrumbs.length > 0 ? <Breadcrumbs items={breadcrumbs} /> : null}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="grid min-w-0 gap-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
            <h1 className="min-w-0 text-title text-fg">{title}</h1>
            {titleAddon}
          </div>
          {description ? <p className="max-w-2xl text-sm text-fg-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </header>
  );
}
