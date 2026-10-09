import { useMemo } from "react";

import { cn } from "@/lib/utils";

import { publicFields } from "../formSchema";
import type { DataUse, FormSchema, PolicySummary } from "../types";
import { RegistrationForm } from "./RegistrationForm";

export interface FormPreviewProps {
  schema: FormSchema;
  organizationName: string;
  jobTitle: string;
  linkName: string;
  policy: PolicySummary | null;
  notice?: string;
  requiresAccessCode: boolean;
  className?: string;
}

/** Live preview of the candidate's form: the exact RegistrationForm, in preview mode. */
export function FormPreview({ schema, organizationName, jobTitle, linkName, policy, notice = "", requiresAccessCode, className }: FormPreviewProps) {
  const fields = useMemo(() => publicFields(schema), [schema]);
  const dataUse: DataUse | null = policy ? { ...policy, notice_markdown: notice, ai_assessment: true, human_decision: true } : null;
  return (
    <aside data-testid="form-preview" aria-label="What candidates see" className={cn("grid grid-cols-1 content-start gap-2", className)}>
      <p className="text-[13px] font-medium text-fg-muted">What candidates see</p>
      <div className="overflow-hidden rounded-2xl border border-line-strong bg-surface-muted shadow-card">
        <div className="border-b border-line bg-surface px-4 py-3">
          <p className="truncate text-sm font-semibold text-fg">{organizationName}</p>
          <p className="truncate text-xs text-fg-muted">{jobTitle || "Job title"}</p>
        </div>
        <div className="grid max-h-[70dvh] grid-cols-1 gap-4 overflow-y-auto px-4 py-5 scrollbar-thin">
          <div>
            {linkName ? <p className="text-xs font-medium text-fg-muted">{linkName}</p> : null}
            <p className="text-heading text-fg">Register</p>
          </div>
          <RegistrationForm fields={fields} dataUse={dataUse} organizationName={organizationName} requiresAccessCode={requiresAccessCode} mode="preview" />
        </div>
      </div>
    </aside>
  );
}
