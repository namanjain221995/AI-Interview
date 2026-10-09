import { Loader2 } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import {
  INTEGRITY_REVIEW_STATUS_LABELS,
  INTERVIEW_STATUS_LABELS,
  JOB_STATUS_LABELS,
  labelFor,
  REPORT_STATUS_LABELS,
  RESUME_STATUS_LABELS,
  RUN_STATUS_LABELS,
  SESSION_STATUS_LABELS,
} from "@/lib/labels";
import type {
  IntegrityReviewStatus,
  InterviewStatus,
  JobStatus,
  ReportStatus,
  ResumeStatus,
  RunStatus,
  SessionStatus,
} from "@/lib/types";

interface Style {
  tone: BadgeTone;
  /** Animated dot for live/working states. */
  pulse?: boolean;
  /** Spinner icon for background processing. */
  working?: boolean;
}

const SESSION: Record<SessionStatus, Style> = {
  created: { tone: "neutral" },
  preparing: { tone: "accent", working: true },
  ready: { tone: "accent" },
  live: { tone: "signal", pulse: true },
  paused: { tone: "warning" },
  completing: { tone: "accent", working: true },
  completed: { tone: "success" },
  aborted: { tone: "neutral" },
  failed: { tone: "danger" },
};

const INTERVIEW: Record<InterviewStatus, Style> = {
  invited: { tone: "accent" },
  in_progress: { tone: "signal" },
  completed: { tone: "success" },
  expired: { tone: "neutral" },
  cancelled: { tone: "neutral" },
};

const RESUME: Record<ResumeStatus, Style> = {
  uploaded: { tone: "neutral" },
  validating: { tone: "accent", working: true },
  extracting: { tone: "accent", working: true },
  ocr: { tone: "accent", working: true },
  analyzing: { tone: "accent", working: true },
  ready: { tone: "success" },
  failed: { tone: "danger" },
  rejected: { tone: "danger" },
};

const REPORT: Record<ReportStatus, Style> = {
  pending: { tone: "neutral" },
  generating: { tone: "accent", working: true },
  ready: { tone: "success" },
  failed: { tone: "danger" },
};

const JOB: Record<JobStatus, Style> = {
  draft: { tone: "neutral" },
  open: { tone: "success" },
  closed: { tone: "neutral" },
  archived: { tone: "outline" },
};

const INTEGRITY_REVIEW: Record<IntegrityReviewStatus, Style> = {
  unreviewed: { tone: "warning" },
  dismissed: { tone: "neutral" },
  noted: { tone: "accent" },
  confirmed_concern: { tone: "danger" },
};

const RUN: Record<RunStatus, Style> = {
  queued: { tone: "neutral" },
  running: { tone: "accent", working: true },
  passed: { tone: "success" },
  failed: { tone: "danger" },
  error: { tone: "danger" },
  compile_error: { tone: "danger" },
  timeout: { tone: "warning" },
  memory_exceeded: { tone: "warning" },
  rejected: { tone: "danger" },
};

export type StatusBadgeProps =
  | { kind: "session"; value: SessionStatus }
  | { kind: "interview"; value: InterviewStatus }
  | { kind: "resume"; value: ResumeStatus }
  | { kind: "report"; value: ReportStatus }
  | { kind: "job"; value: JobStatus }
  | { kind: "integrity_review"; value: IntegrityReviewStatus }
  | { kind: "run"; value: RunStatus };

type CommonProps = { size?: "sm" | "md"; className?: string };

function resolve(props: StatusBadgeProps): { style: Style; label: string } {
  switch (props.kind) {
    case "session":
      return { style: SESSION[props.value] ?? { tone: "neutral" }, label: labelFor(SESSION_STATUS_LABELS, props.value) };
    case "interview":
      return { style: INTERVIEW[props.value] ?? { tone: "neutral" }, label: labelFor(INTERVIEW_STATUS_LABELS, props.value) };
    case "resume":
      return { style: RESUME[props.value] ?? { tone: "neutral" }, label: labelFor(RESUME_STATUS_LABELS, props.value) };
    case "report":
      return { style: REPORT[props.value] ?? { tone: "neutral" }, label: labelFor(REPORT_STATUS_LABELS, props.value) };
    case "job":
      return { style: JOB[props.value] ?? { tone: "neutral" }, label: labelFor(JOB_STATUS_LABELS, props.value) };
    case "integrity_review":
      return { style: INTEGRITY_REVIEW[props.value] ?? { tone: "neutral" }, label: labelFor(INTEGRITY_REVIEW_STATUS_LABELS, props.value) };
    case "run":
      return { style: RUN[props.value] ?? { tone: "neutral" }, label: labelFor(RUN_STATUS_LABELS, props.value) };
  }
}

/** Lifecycle status for sessions, interviews, resumes, reports, jobs, integrity reviews and code runs. */
export function StatusBadge(props: StatusBadgeProps & CommonProps) {
  const { style, label } = resolve(props);
  return (
    <Badge
      tone={style.tone}
      size={props.size}
      dot={!style.working}
      pulse={style.pulse}
      icon={style.working ? <Loader2 className="animate-spin" aria-hidden="true" /> : undefined}
      className={props.className}
    >
      {label}
    </Badge>
  );
}
