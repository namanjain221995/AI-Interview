import { ArrowLeft, Check, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import type { BreadcrumbItem } from "@/components/ui/Breadcrumbs";
import { useDocumentTitle } from "@/lib/hooks";

import { useCandidateChrome } from "./CandidateShell";
import { PageContainer } from "./StaffShell";

function PlannedList({ items }: { items: string[] }) {
  return (
    <ul className="mt-5 grid w-full max-w-md gap-2 text-left">
      {items.map((item) => (
        <li key={item} className="flex items-start gap-2.5 text-sm text-fg-muted">
          <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-neutral-soft text-fg-subtle">
            <Check className="size-3" strokeWidth={2.5} aria-hidden="true" />
          </span>
          {item}
        </li>
      ))}
    </ul>
  );
}

export interface StaffPagePlaceholderProps {
  title: string;
  description: string;
  icon: LucideIcon;
  /** What the finished page will let people do. */
  planned: string[];
  breadcrumbs?: BreadcrumbItem[];
  actions?: ReactNode;
}

/** Interim staff page: real header and navigation, honest body. */
export function StaffPagePlaceholder({ title, description, icon: Icon, planned, breadcrumbs, actions }: StaffPagePlaceholderProps) {
  useDocumentTitle(title);
  return (
    <PageContainer>
      <PageHeader title={title} description={description} breadcrumbs={breadcrumbs} actions={actions} />
      <section className="flex flex-col items-center rounded-lg border border-dashed border-line-strong bg-surface-subtle/60 px-6 py-14 text-center">
        <span className="mb-4 flex size-11 items-center justify-center rounded-md border border-line bg-surface text-fg-muted shadow-card" aria-hidden="true">
          <Icon className="size-5" />
        </span>
        <h2 className="text-[15px] font-semibold text-fg">This view is being built</h2>
        <p className="mt-1 max-w-md text-sm text-fg-muted">It ships with the next release of Interview Studio. It will let you:</p>
        <PlannedList items={planned} />
      </section>
    </PageContainer>
  );
}

export interface CandidateStepPlaceholderProps {
  title: string;
  description: string;
  icon: LucideIcon;
  planned: string[];
  /** Next step route segment, e.g. "instructions". */
  next?: { segment: string; label: string };
  back?: { segment: string; label: string };
}

/** Interim candidate step inside the candidate shell. */
export function CandidateStepPlaceholder({ title, description, icon: Icon, planned, next, back }: CandidateStepPlaceholderProps) {
  useDocumentTitle(title);
  useCandidateChrome({});
  const { interviewId } = useParams();
  const base = interviewId ? `/candidate/${interviewId}` : "/candidate";
  return (
    <div className="flex flex-col">
      <span className="mb-5 flex size-11 items-center justify-center rounded-md border border-line bg-surface text-fg-muted shadow-card" aria-hidden="true">
        <Icon className="size-5" />
      </span>
      <h1 className="text-title text-fg">{title}</h1>
      <p className="mt-2 max-w-xl text-fg-muted">{description}</p>
      <div className="mt-8 rounded-lg border border-line bg-surface p-6 shadow-card">
        <p className="text-sm font-medium text-fg">This step is being finished</p>
        <p className="mt-1 text-sm text-fg-muted">When it ships, you will be able to:</p>
        <PlannedList items={planned} />
      </div>
      {next || back ? (
        <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
          {back ? (
            <Button variant="ghost" asChild leftIcon={<ArrowLeft />}>
              <Link to={`${base}/${back.segment}`}>{back.label}</Link>
            </Button>
          ) : (
            <span />
          )}
          {next ? (
            <Button asChild size="lg">
              <Link to={`${base}/${next.segment}`}>{next.label}</Link>
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
