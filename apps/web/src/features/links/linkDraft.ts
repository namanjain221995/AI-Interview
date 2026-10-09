/** Link editor state: conversion from/to the API and local validation (pure). */

import type { Job, OrganizationSettings, Round } from "@/lib/types";

import { checkForm } from "./fairness";
import { checkSchema, defaultSchema } from "./formSchema";
import type { DuplicatePolicy, FormSchema, OpenLink, OpenLinkCreate, OpenLinkUpdate, PolicySummary, Progression } from "./types";

export const CAMPUS_IP_LIMIT = 1000;
export const DEFAULT_IP_LIMIT = 120;
export const DEFAULT_ROUND_WINDOW = 72;

export interface LinkDraft {
  job_id: string;
  name: string;
  description: string;
  roundMode: "single" | "pipeline";
  round_ids: string[];
  progression: Progression;
  /** `<input type="datetime-local">` values ("" = none), local time. */
  opens_at: string;
  closes_at: string;
  max_registrations: number | null;
  round_window_hours: number | null;
  per_ip_hourly_limit: number | null;
  require_resume: boolean;
  require_access_code: boolean;
  duplicate_policy: DuplicatePolicy;
  form_schema: FormSchema;
  acknowledged_warnings: string[];
}

export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export function activeRounds(rounds: readonly Round[] | undefined): Round[] {
  return [...(rounds ?? [])].filter((r) => r.is_active !== false).sort((a, b) => a.sequence - b.sequence);
}

export function newDraft(jobId = "", schema: FormSchema = defaultSchema()): LinkDraft {
  return {
    job_id: jobId,
    name: "",
    description: "",
    roundMode: "single",
    round_ids: [],
    progression: "immediate",
    opens_at: "",
    closes_at: "",
    max_registrations: null,
    round_window_hours: DEFAULT_ROUND_WINDOW,
    per_ip_hourly_limit: DEFAULT_IP_LIMIT,
    require_resume: true,
    require_access_code: false,
    duplicate_policy: { scope: "job", device: "flag", network: "flag" },
    form_schema: schema,
    acknowledged_warnings: [],
  };
}

export function draftFromLink(link: OpenLink): LinkDraft {
  return {
    job_id: link.job.id,
    name: link.name,
    description: link.description ?? "",
    roundMode: link.rounds.length > 1 ? "pipeline" : "single",
    round_ids: link.rounds.map((r) => r.id),
    progression: link.progression,
    opens_at: toLocalInput(link.opens_at),
    closes_at: toLocalInput(link.closes_at),
    max_registrations: link.max_registrations,
    round_window_hours: link.round_window_hours,
    per_ip_hourly_limit: link.per_ip_hourly_limit,
    require_resume: link.require_resume,
    require_access_code: link.requires_access_code,
    duplicate_policy: { ...link.duplicate_policy },
    form_schema: link.form_schema,
    acknowledged_warnings: [...link.acknowledged_warnings],
  };
}

/** Round ids in the job's order (the server stores them in job sequence order too). */
export function orderedRoundIds(ids: readonly string[], rounds: readonly Round[]): string[] {
  const order = new Map(rounds.map((r, i) => [r.id, r.sequence ?? i]));
  return [...new Set(ids)].sort((a, b) => (order.get(a) ?? 1e9) - (order.get(b) ?? 1e9));
}

function common(draft: LinkDraft): OpenLinkUpdate {
  const pipeline = draft.round_ids.length > 1;
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    round_ids: draft.round_ids,
    progression: pipeline ? draft.progression : "immediate",
    opens_at: fromLocalInput(draft.opens_at),
    closes_at: fromLocalInput(draft.closes_at),
    max_registrations: draft.max_registrations,
    round_window_hours: draft.round_window_hours ?? DEFAULT_ROUND_WINDOW,
    require_resume: draft.require_resume,
    per_ip_hourly_limit: draft.per_ip_hourly_limit ?? DEFAULT_IP_LIMIT,
    duplicate_policy: draft.duplicate_policy,
    form_schema: draft.form_schema,
    acknowledged_warnings: draft.acknowledged_warnings,
  };
}

export function toCreateBody(draft: LinkDraft): OpenLinkCreate {
  return { job_id: draft.job_id, ...common(draft), name: draft.name.trim(), round_ids: draft.round_ids, require_access_code: draft.require_access_code };
}

export function toUpdateBody(draft: LinkDraft): OpenLinkUpdate {
  return common(draft);
}

export type DraftErrors = Partial<Record<"job_id" | "name" | "description" | "round_ids" | "opens_at" | "closes_at" | "max_registrations" | "round_window_hours" | "per_ip_hourly_limit" | "form", string>>;

/** Local checks mirroring the server limits (OPEN_LINKS.md section 9.2). */
export function validateDraft(draft: LinkDraft): DraftErrors {
  const errors: DraftErrors = {};
  if (!draft.job_id) errors.job_id = "Choose the job this link interviews for.";
  const name = draft.name.trim();
  if (!name) errors.name = "Give the link a name candidates will recognise, e.g. “Campus drive, Pune”.";
  else if (name.length > 120) errors.name = "Keep the name under 120 characters.";
  if (draft.description.length > 4000) errors.description = "Keep the description under 4,000 characters.";
  if (draft.round_ids.length === 0) errors.round_ids = draft.roundMode === "single" ? "Choose the round." : "Choose at least one round.";
  else if (draft.round_ids.length > 10) errors.round_ids = "A link can have at most 10 rounds.";
  const opens = fromLocalInput(draft.opens_at);
  const closes = fromLocalInput(draft.closes_at);
  if (draft.opens_at && !opens) errors.opens_at = "Enter a valid date and time.";
  if (draft.closes_at && !closes) errors.closes_at = "Enter a valid date and time.";
  if (opens && closes && Date.parse(closes) <= Date.parse(opens)) errors.closes_at = "Closing must be after opening.";
  if (draft.max_registrations !== null && (!Number.isInteger(draft.max_registrations) || draft.max_registrations < 1 || draft.max_registrations > 10_000)) {
    errors.max_registrations = "Use a whole number from 1 to 10,000, or leave empty for no limit.";
  }
  const window = draft.round_window_hours;
  if (window === null || !Number.isInteger(window) || window < 1 || window > 720) errors.round_window_hours = "Use a whole number of hours from 1 to 720.";
  const ip = draft.per_ip_hourly_limit;
  if (ip === null || !Number.isInteger(ip) || ip < 5 || ip > 2000) errors.per_ip_hourly_limit = "Use a whole number from 5 to 2,000.";
  const structural = checkSchema(draft.form_schema);
  const fairness = checkForm(draft.form_schema, draft.acknowledged_warnings);
  if (fairness.errors.length) errors.form = "Remove the questions that ask for information we never collect.";
  else if (Object.keys(structural).length) errors.form = "Fix the highlighted form fields.";
  else if (fairness.warnings.some((w) => !w.acknowledged)) errors.form = "Confirm or remove the questions with a warning.";
  return errors;
}

/** Best local guess of the job's effective policy, used before the link exists (the server summary wins afterwards). */
export function policyFromJob(job: Job | undefined, settings: OrganizationSettings | undefined): PolicySummary | null {
  if (!job) return null;
  const recording = { ...(settings?.recording ?? {}), ...(job.recording_settings ?? {}) };
  const integrity = { ...(settings?.integrity ?? {}), ...(job.integrity_settings ?? {}) };
  const retention = { ...(settings?.retention ?? {}), ...(job.recording_settings?.retention ?? {}) };
  const monitored = integrity.enabled !== false;
  return {
    video_recording: recording.record_video === true,
    audio_recording: recording.record_answer_audio !== false,
    screen_recording: recording.record_screen === true,
    camera_monitoring: monitored && (integrity.camera_frame_analysis === true || integrity.camera_required === true),
    screen_monitoring: monitored && integrity.monitor_focus !== false,
    candidate_report_mode: job.candidate_report_mode ?? settings?.candidate_report_mode ?? "after_review",
    retention_days: {
      resume: retention.resume_days ?? 0,
      audio: retention.audio_days ?? 0,
      video: retention.video_days ?? 0,
      transcript: retention.transcript_days ?? 0,
      report: retention.report_days ?? 0,
    },
  };
}
