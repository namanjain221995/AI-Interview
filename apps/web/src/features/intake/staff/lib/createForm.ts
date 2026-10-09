/**
 * Create/edit form model for intake invitations (design 11.1 "Create page"): state,
 * defaults from the organisation, validation mirroring the server ranges, and the
 * request payloads (single create, batch create, draft update).
 */

import type {
  IntakeBatchCreate,
  IntakeCreate,
  IntakeDefaults,
  IntakeDetail,
  IntakeInviteSettingsIn,
  IntakeUpdate,
  PresenceMode,
  TargetRole,
  WorkAuthorizationPolicy,
} from "../types";
import { batchPayload, EMAIL_RX, type BatchRow } from "./batch";

export const DURATION_MIN = 35;
export const DURATION_MAX = 50;
export const RECONNECTS_MAX = 20;
export const EXPIRY_MAX_HOURS = 1440;
export const NOTES_MAX = 4000;
export const COUNTRY_MAX = 80;

export const EXPIRY_CHOICES: ReadonlyArray<{ value: number; label: string }> = [
  { value: 24, label: "24 hours" },
  { value: 72, label: "3 days" },
  { value: 168, label: "7 days" },
  { value: 336, label: "14 days" },
  { value: 720, label: "30 days" },
  { value: 1440, label: "60 days" },
];

export type Mq1Mode = "inherit" | "ask" | "off";

export interface InviteSettingsForm {
  duration_minutes: number;
  presence: PresenceMode;
  max_reconnects: number;
  expires_in_hours: number;
  /** `datetime-local` value (local time) or "". */
  scheduled_for: string;
  require_access_code: boolean;
  resume_required: boolean;
  notify_on_completion: boolean;
  assigned_reviewer_id: string | null;
  mq1_mode: Mq1Mode;
  mq1_country: string;
  mq1_sponsorship: boolean;
}

export interface SingleForm {
  candidate_mode: "existing" | "new";
  candidate: { id: string; full_name: string; email: string } | null;
  new_candidate: { full_name: string; email: string; phone: string };
  job_id: string | null;
  round_id: string | null;
  /** "candidate" = let the candidate choose (null on the wire). */
  target_role: TargetRole | "candidate";
  notes: string;
  /** False when editing a draft whose notes the server didn't return: the PATCH then leaves notes alone unless typed. */
  notes_known: boolean;
  settings: InviteSettingsForm;
}

export function defaultSettingsForm(defaults: IntakeDefaults, options: { requireAccessCode?: boolean } = {}): InviteSettingsForm {
  return {
    duration_minutes: clampDuration(defaults.duration_minutes),
    presence: defaults.presence,
    max_reconnects: Math.max(0, Math.min(RECONNECTS_MAX, defaults.max_reconnects)),
    expires_in_hours: Math.max(1, Math.min(EXPIRY_MAX_HOURS, defaults.expires_in_hours)),
    scheduled_for: "",
    require_access_code: options.requireAccessCode ?? false,
    resume_required: defaults.resume_required,
    notify_on_completion: true,
    assigned_reviewer_id: null,
    mq1_mode: "inherit",
    mq1_country: "",
    mq1_sponsorship: true,
  };
}

export function defaultSingleForm(defaults: IntakeDefaults, options: { requireAccessCode?: boolean; jobId?: string | null; roundId?: string | null } = {}): SingleForm {
  return {
    candidate_mode: "existing",
    candidate: null,
    new_candidate: { full_name: "", email: "", phone: "" },
    job_id: options.jobId ?? null,
    round_id: options.roundId ?? null,
    target_role: "candidate",
    notes: "",
    notes_known: true,
    settings: defaultSettingsForm(defaults, options),
  };
}

function clampDuration(value: number): number {
  if (!Number.isFinite(value)) return 45;
  return Math.max(DURATION_MIN, Math.min(DURATION_MAX, Math.round(value)));
}

/** Local "YYYY-MM-DDTHH:mm" for an ISO instant (datetime-local inputs work in local time). */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

/** Form values from a saved draft. */
export function formFromDraft(detail: IntakeDetail, defaults: IntakeDefaults): SingleForm {
  const s = detail.settings;
  const wa = s.work_authorization;
  return {
    candidate_mode: "existing",
    candidate: detail.candidate.id ? { id: detail.candidate.id, full_name: detail.candidate.full_name, email: detail.candidate.email } : null,
    new_candidate: { full_name: "", email: "", phone: "" },
    job_id: detail.job.id || null,
    round_id: detail.round?.id ?? null,
    target_role: detail.target_role ?? "candidate",
    notes: detail.notes ?? "",
    notes_known: detail.notes !== undefined,
    settings: {
      ...defaultSettingsForm(defaults),
      duration_minutes: clampDuration(s.duration_minutes),
      presence: s.presence,
      max_reconnects: s.max_reconnects,
      expires_in_hours: s.expires_in_hours,
      scheduled_for: toLocalInput(s.scheduled_for),
      require_access_code: s.require_access_code,
      resume_required: s.resume_required,
      notify_on_completion: s.notify_on_completion,
      assigned_reviewer_id: s.assigned_reviewer_id ?? detail.assigned_reviewer?.id ?? null,
      mq1_mode: wa === null ? "inherit" : wa.enabled ? "ask" : "off",
      mq1_country: wa?.country ?? "",
      mq1_sponsorship: wa?.ask_sponsorship ?? true,
    },
  };
}

export type FormErrors = Record<string, string>;

export function validateSettings(settings: InviteSettingsForm, now: Date = new Date()): FormErrors {
  const errors: FormErrors = {};
  if (!Number.isInteger(settings.duration_minutes) || settings.duration_minutes < DURATION_MIN || settings.duration_minutes > DURATION_MAX) {
    errors.duration_minutes = `Intake interviews last ${DURATION_MIN} to ${DURATION_MAX} minutes.`;
  }
  if (!Number.isInteger(settings.max_reconnects) || settings.max_reconnects < 0 || settings.max_reconnects > RECONNECTS_MAX) {
    errors.max_reconnects = `Allow between 0 and ${RECONNECTS_MAX} reconnections.`;
  }
  if (!Number.isInteger(settings.expires_in_hours) || settings.expires_in_hours < 1 || settings.expires_in_hours > EXPIRY_MAX_HOURS) {
    errors.expires_in_hours = "Choose how long the link stays valid.";
  }
  if (settings.scheduled_for) {
    const at = fromLocalInput(settings.scheduled_for);
    if (!at) errors.scheduled_for = "Enter a valid date and time.";
    else if (Date.parse(at) <= now.getTime()) errors.scheduled_for = "Pick a time in the future, or leave it empty to allow starting right away.";
    else if (Date.parse(at) >= now.getTime() + settings.expires_in_hours * 3600_000) errors.scheduled_for = "The link expires before this time. Choose a later expiry or an earlier start.";
  }
  if (settings.mq1_mode === "ask") {
    const country = settings.mq1_country.trim();
    if (!country) errors.mq1_country = "Name the country, as it should read in the question (for example, the United States).";
    else if (country.length > COUNTRY_MAX) errors.mq1_country = `Keep it under ${COUNTRY_MAX} characters.`;
  }
  return errors;
}

export function validateSingle(form: SingleForm, options: { draft?: boolean; now?: Date } = {}): FormErrors {
  const errors: FormErrors = {};
  if (form.candidate_mode === "existing" && !form.candidate) errors.candidate = "Choose a candidate, or add a new one.";
  if (form.candidate_mode === "new") {
    const name = form.new_candidate.full_name.trim();
    if (name.length < 2) errors.full_name = "Enter the candidate's full name.";
    else if (name.length > 200) errors.full_name = "Keep the name under 200 characters.";
    if (!EMAIL_RX.test(form.new_candidate.email.trim())) errors.email = "Enter a valid email address. The candidate confirms it to open the link.";
  }
  if (!form.job_id) errors.job_id = "Choose the program's job.";
  if (form.notes.length > NOTES_MAX) errors.notes = `Keep notes under ${NOTES_MAX.toLocaleString()} characters.`;
  // A draft only needs a candidate and a job; the settings are checked when it is sent.
  if (!options.draft) Object.assign(errors, validateSettings(form.settings, options.now));
  else if (form.settings.mq1_mode === "ask" && form.settings.mq1_country.trim().length > COUNTRY_MAX) errors.mq1_country = `Keep it under ${COUNTRY_MAX} characters.`;
  return errors;
}

export function workAuthorizationOf(settings: InviteSettingsForm): WorkAuthorizationPolicy | null {
  if (settings.mq1_mode === "inherit") return null;
  if (settings.mq1_mode === "off") return { enabled: false, country: null, ask_sponsorship: true, variant: "default" };
  return { enabled: true, country: settings.mq1_country.trim(), ask_sponsorship: settings.mq1_sponsorship, variant: "default" };
}

export function settingsPayload(settings: InviteSettingsForm): IntakeInviteSettingsIn {
  return {
    duration_minutes: settings.duration_minutes,
    presence: settings.presence,
    max_reconnects: settings.max_reconnects,
    expires_in_hours: settings.expires_in_hours,
    scheduled_for: fromLocalInput(settings.scheduled_for),
    require_access_code: settings.require_access_code,
    resume_required: settings.resume_required,
    assigned_reviewer_id: settings.assigned_reviewer_id,
    notify_on_completion: settings.notify_on_completion,
    work_authorization: workAuthorizationOf(settings),
  };
}

export function createPayload(form: SingleForm, send: boolean): IntakeCreate {
  const existing = form.candidate_mode === "existing" && form.candidate;
  return {
    candidate_id: existing ? form.candidate!.id : null,
    candidate: existing
      ? null
      : { full_name: form.new_candidate.full_name.trim(), email: form.new_candidate.email.trim().toLowerCase(), phone: form.new_candidate.phone.trim() || null },
    job_id: form.job_id as string,
    round_id: form.round_id,
    target_role: form.target_role === "candidate" ? null : form.target_role,
    title: null,
    settings: settingsPayload(form.settings),
    notes: form.notes.trim() || null,
    send,
  };
}

/** PATCH body for a draft (every field may change while it is a draft). */
export function draftUpdatePayload(form: SingleForm): IntakeUpdate {
  // PATCH applies only the fields it receives (server `model_fields_set`): never send notes nobody saw.
  const sendNotes = form.notes_known || form.notes.trim() !== "";
  return {
    target_role: form.target_role === "candidate" ? null : form.target_role,
    settings: settingsPayload(form.settings),
    assigned_reviewer_id: form.settings.assigned_reviewer_id,
    notify_on_completion: form.settings.notify_on_completion,
    ...(sendNotes ? { notes: form.notes.trim() || null } : {}),
    ...(form.job_id ? { job_id: form.job_id } : {}),
    round_id: form.round_id,
  };
}

export function batchCreatePayload(jobId: string, roundId: string | null, settings: InviteSettingsForm, rows: readonly BatchRow[], send: boolean): IntakeBatchCreate {
  return { job_id: jobId, round_id: roundId, settings: settingsPayload(settings), send, rows: batchPayload(rows) };
}
