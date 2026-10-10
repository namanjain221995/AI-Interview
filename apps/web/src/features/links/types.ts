/**
 * Open interview links: wire types (docs/design/OPEN_LINKS.md section 9).
 *
 * The enums mirror `apps/api/app/services/open_links/constants.py`. They are kept
 * out of `src/lib/types.ts` on purpose (the enum-drift test parses only
 * `app/domain/enums.py`).
 */

export const LINK_STATUSES = ["active", "paused", "closed"] as const;
export type LinkStatus = (typeof LINK_STATUSES)[number];

export const LINK_STATES = ["open", "scheduled", "paused", "closed", "expired", "full"] as const;
export type LinkState = (typeof LINK_STATES)[number];

export const PROGRESSIONS = ["immediate", "after_review"] as const;
export type Progression = (typeof PROGRESSIONS)[number];

export const REGISTRATION_STATUSES = ["registered", "in_progress", "awaiting_review", "completed", "expired", "cancelled", "blocked"] as const;
export type RegistrationStatus = (typeof REGISTRATION_STATUSES)[number];

export const ROUND_STATES = ["locked", "invited", "in_progress", "completed", "expired", "cancelled"] as const;
export type RoundState = (typeof ROUND_STATES)[number];

export const FIELD_TYPES = ["text", "textarea", "number", "email", "phone", "url", "select", "multiselect", "checkbox", "date"] as const;
export type FieldType = (typeof FIELD_TYPES)[number];

export const FLAG_CODES = ["same_device", "same_resume", "same_network"] as const;
export type FlagCode = (typeof FLAG_CODES)[number];

export type FlagReview = "none" | "open" | "cleared" | "confirmed";

export interface FormField {
  key: string;
  builtin: boolean;
  type: FieldType;
  label: string;
  help_text: string | null;
  placeholder: string | null;
  required: boolean;
  enabled: boolean;
  options: string[];
  min: number | null;
  max: number | null;
  max_length: number | null;
}

/** `version` is server-owned and ignored on input. */
export interface FormSchema {
  version: number;
  fields: FormField[];
}

/** What the candidate's form renders: enabled fields only, no builtin/enabled flags. */
export type PublicFormField = Omit<FormField, "builtin" | "enabled">;

export interface DuplicatePolicy {
  scope: "job" | "link";
  device: "flag" | "block";
  network: "off" | "flag";
}

export interface LinkRound {
  id: string;
  name: string;
  round_type: string;
  sequence: number;
  duration_minutes: number;
  missing: boolean;
}

export interface PolicySummary {
  video_recording: boolean;
  audio_recording: boolean;
  screen_recording: boolean;
  camera_monitoring: boolean;
  screen_monitoring: boolean;
  candidate_report_mode: string;
  retention_days: { resume: number; audio: number; video: number; transcript: number; report: number };
}

export interface LinkCounts {
  registrations: number;
  in_progress: number;
  awaiting_review: number;
  completed: number;
  flagged: number;
  blocked: number;
}

export interface Flag {
  code: FlagCode;
  severity: "high" | "low";
  message: string;
  related_registration_id: string | null;
  at: string;
}

// ------------------------------------------------------------------ staff

export interface FormCatalog {
  builtins: FormField[];
  types: FieldType[];
  limits: { max_fields: number; max_options: number };
  default_schema: FormSchema;
}

export interface FormIssue {
  key: string;
  label: string;
  topic: string;
  message: string;
}

export interface FormWarning {
  key: string;
  code: string;
  message: string;
  acknowledged: boolean;
}

export interface FormValidation {
  ok: boolean;
  errors: FormIssue[];
  warnings: FormWarning[];
  normalized: FormSchema;
}

export interface OpenLinkCreate {
  job_id: string;
  name: string;
  description?: string | null;
  round_ids: string[];
  progression?: Progression;
  opens_at?: string | null;
  closes_at?: string | null;
  max_registrations?: number | null;
  round_window_hours?: number;
  require_resume?: boolean;
  require_access_code?: boolean;
  per_ip_hourly_limit?: number;
  duplicate_policy?: DuplicatePolicy;
  form_schema?: FormSchema;
  acknowledged_warnings?: string[];
}

export type OpenLinkUpdate = Partial<Omit<OpenLinkCreate, "job_id" | "require_access_code">>;

export interface OpenLinkSummary {
  id: string;
  name: string;
  job: { id: string; title: string; status: string };
  status: LinkStatus;
  state: LinkState;
  url: string;
  rounds: LinkRound[];
  progression: Progression;
  opens_at: string | null;
  closes_at: string | null;
  max_registrations: number | null;
  counts: LinkCounts;
  created_at: string;
  updated_at: string;
}

export interface OpenLink extends OpenLinkSummary {
  description: string | null;
  round_window_hours: number;
  require_resume: boolean;
  requires_access_code: boolean;
  per_ip_hourly_limit: number;
  duplicate_policy: DuplicatePolicy;
  form_schema: FormSchema;
  acknowledged_warnings: string[];
  policy_summary: PolicySummary;
  /** e.g. "round_missing", "video_recording_off" */
  warnings: string[];
  created_by: { id: string; full_name: string } | null;
}

export interface LinkWithCode {
  link: OpenLink;
  access_code: string | null;
}

export interface RegistrationRound {
  sequence: number;
  round_id: string;
  name: string;
  state: RoundState;
  attempt: number;
  interview_id: string | null;
  session_id: string | null;
  session_status: string | null;
  end_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
}

export interface Registration {
  id: string;
  link_id: string;
  candidate: { id: string; full_name: string; email: string } | null;
  full_name: string;
  email: string;
  phone: string | null;
  status: RegistrationStatus;
  status_reason: string | null;
  current_round_index: number;
  rounds: RegistrationRound[];
  flags: Flag[];
  flag_review: FlagReview;
  created_at: string;
  updated_at: string;
  /** Present on `GET /open-links/registrations?candidate_id=`. */
  link?: { id: string; name: string; job_title: string };
}

export interface RegistrationAnswer {
  key: string;
  label: string;
  type: FieldType;
  value: unknown;
}

export interface RegistrationDetail extends Registration {
  answers: RegistrationAnswer[];
  attempts: RegistrationRound[];
  privacy_ack_at: string | null;
  personal_link_issued_at: string | null;
  resume_id: string | null;
  flag_reviewed_at: string | null;
  flag_review_note: string | null;
}

export type AdvanceSkipCode = "not_awaiting_review" | "last_round" | "candidate_archived" | "already_invited" | "round_missing" | "not_found";

export interface AdvanceResult {
  advanced: Array<{ registration_id: string; interview_id: string; round_id: string }>;
  skipped: Array<{ registration_id: string; code: AdvanceSkipCode | string }>;
}

export interface RegistrationListQuery {
  status?: RegistrationStatus;
  round_index?: number;
  needs_review?: boolean;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface LinkListQuery {
  job_id?: string;
  status?: LinkStatus;
  q?: string;
  limit?: number;
  offset?: number;
}

// ----------------------------------------------------------------- public

export interface LinkInfoRound {
  name: string;
  round_type: string;
  duration_minutes: number;
  sequence: number;
}

export interface DataUse extends PolicySummary {
  notice_markdown: string;
  ai_assessment: true;
  human_decision: true;
}

export interface LinkInfo {
  organization_name: string;
  job_title: string;
  job_location: string | null;
  link_name: string;
  description: string | null;
  state: LinkState;
  opens_at: string | null;
  closes_at: string | null;
  rounds: LinkInfoRound[];
  progression: Progression;
  requires_access_code: boolean;
  require_resume: boolean;
  interviewer_name: string | null;
  form: { fields: PublicFormField[] } | null;
  data_use: DataUse;
  honeypot_field: "website";
}

export type AnswerValue = string | number | boolean | string[] | null;

export interface RegisterRequest {
  answers: Record<string, AnswerValue>;
  privacy_ack: boolean;
  access_code?: string | null;
  website?: string;
  /** Proof-of-work solution (S15). */
  pow?: string;
}

export interface RegisterResponse {
  outcome: "registered" | "resumed";
  interview_id: string;
  next_step: string;
  csrf_token: string;
  personal_url: string | null;
}

export interface PersonalLinkInfo {
  organization_name: string;
  job_title: string;
  link_name: string;
  state: "active" | "cancelled";
}

export type NextAction = "continue_current" | "start_next" | "wait_for_review" | "all_done" | "closed";

export interface LinkProgressRound {
  sequence: number;
  name: string;
  round_type: string;
  duration_minutes: number;
  state: RoundState;
  interview_id: string | null;
  is_current: boolean;
}

export interface LinkProgress {
  organization_name: string;
  job_title: string;
  link_name: string;
  progression: Progression;
  rounds: LinkProgressRound[];
  next_action: NextAction;
  next_interview_id: string | null;
  next_round_name: string | null;
  deadline: string | null;
}

export interface VerifyPersonalResponse {
  interview_id: string;
  next_step: string;
  csrf_token: string;
  progress: LinkProgress | null;
}

export interface ContinueResponse {
  interview_id: string;
  next_step: string;
  csrf_token: string;
}
