/**
 * OrganizationSettings (app/schemas/policies.py) defaults, tolerant loading and
 * exact-key serialisation (`PUT /settings` uses `extra="forbid"`).
 */

import type {
  AvatarSettings,
  CandidateReportMode,
  IntegrityAction,
  IntegrityEventType,
  IntegrityPolicy,
  IntegrityRule,
  OrganizationSettings,
  RecordingPolicy,
  RetentionPolicy,
} from "@/lib/types";
import { IntegrityEventType as EventTypes } from "@/lib/types";

import { blankToNull } from "../lib/utils";

export function defaultIntegrityPolicy(): IntegrityPolicy {
  return {
    enabled: true,
    require_fullscreen: true,
    monitor_focus: true,
    monitor_clipboard: true,
    block_paste_in_coding: false,
    monitor_shortcuts: true,
    camera_required: true,
    camera_frame_analysis: true,
    camera_frame_interval_seconds: 15,
    store_evidence_frames: true,
    screen_share_required: false,
    screen_share_analysis: false,
    screen_frame_interval_seconds: 20,
    terminate_on_required_screen_share_stop: false,
    warn_candidate: true,
    allow_page_reload_resume: true,
    max_reconnects: 10,
    rules: [],
  };
}

export function defaultRecordingPolicy(): RecordingPolicy {
  return { record_answer_audio: true, record_video: false, record_screen: false, show_live_subtitles: true, allow_typed_answers: true };
}

export function defaultRetentionPolicy(): RetentionPolicy {
  return { resume_days: 365, audio_days: 90, video_days: 30, frame_days: 30, transcript_days: 365, report_days: 730 };
}

/** The interviewer presence orb is the default; the realistic 3D avatar stays selectable. */
export const ORB_URL_PATTERN = /^orb:(aurora|sunrise|ocean)$/;
export const REALISTIC_AVATAR_URL = "/avatars/interviewer.glb";

export function defaultAvatarSettings(): AvatarSettings {
  return { model_url: "orb:aurora", model_format: "orb", interviewer_name: "Maya", voice: "af_heart", background: "studio" };
}

/** Supported formats; the retired cartoon character (and anything unknown) becomes the orb. */
function normalizeAvatar(avatar: AvatarSettings): AvatarSettings {
  const known = (["orb", "glb", "vrm", "procedural"] as const).find((f) => f === avatar.model_format);
  if (known && known !== "orb") return { ...avatar, model_format: known };
  return { ...avatar, model_format: "orb", model_url: ORB_URL_PATTERN.test(avatar.model_url.trim()) ? avatar.model_url.trim() : "orb:aurora" };
}

export const DEFAULT_DATA_NOTICE =
  "Your interview is conducted by an AI interviewer. Audio is transcribed to evaluate your answers. " +
  "A human reviewer at the hiring organization makes every hiring decision.";

export function defaultOrganizationSettings(): OrganizationSettings {
  return {
    integrity: defaultIntegrityPolicy(),
    recording: defaultRecordingPolicy(),
    retention: defaultRetentionPolicy(),
    avatar: defaultAvatarSettings(),
    candidate_report_mode: "after_review",
    require_access_code: false,
    data_processing_notice: DEFAULT_DATA_NOTICE,
  };
}

/** Event types whose evidence comes from model guesses: never pause or end an interview. */
export const OBSERVATION_ONLY_EVENTS: ReadonlySet<IntegrityEventType> = new Set<IntegrityEventType>([
  "no_person_visible",
  "multiple_persons_visible",
  "person_left_frame",
  "another_person_entered",
  "possible_phone_visible",
  "possible_photo_taking",
  "looking_away_with_other_signals",
  "screen_context_change",
]);

export const POLICY_ACTIONS: ReadonlySet<IntegrityAction> = new Set<IntegrityAction>(["pause_interview", "terminate_interview"]);

export const ALL_EVENT_TYPES = Object.values(EventTypes) as IntegrityEventType[];

export function defaultRule(eventType: IntegrityEventType): IntegrityRule {
  return {
    event_type: eventType,
    enabled: true,
    min_confidence: 0.6,
    min_duration_seconds: 0,
    min_occurrences: 1,
    window_seconds: 300,
    action: "flag_for_human_review",
    candidate_message: null,
  };
}

function pick<T extends object>(defaults: T, source: unknown): T {
  const out = { ...defaults } as Record<string, unknown>;
  if (source && typeof source === "object") {
    for (const key of Object.keys(defaults)) {
      const value = (source as Record<string, unknown>)[key];
      const fallback = (defaults as Record<string, unknown>)[key];
      if (value === undefined || value === null) continue;
      if (typeof value === typeof fallback || (Array.isArray(fallback) && Array.isArray(value))) out[key] = value;
    }
  }
  return out as T;
}

export function normalizeRule(raw: unknown): IntegrityRule | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.event_type !== "string" || !ALL_EVENT_TYPES.includes(r.event_type as IntegrityEventType)) return null;
  const base = pick(defaultRule(r.event_type as IntegrityEventType), r);
  return { ...base, candidate_message: typeof r.candidate_message === "string" ? r.candidate_message : null };
}

export function withIntegrityDefaults(raw: unknown): IntegrityPolicy {
  const base = pick(defaultIntegrityPolicy(), raw);
  const rules = raw && typeof raw === "object" ? (raw as Record<string, unknown>).rules : undefined;
  return { ...base, rules: Array.isArray(rules) ? rules.map(normalizeRule).filter((r): r is IntegrityRule => r !== null) : [] };
}

const REPORT_MODES: CandidateReportMode[] = ["immediate", "after_review", "never"];

export function withSettingsDefaults(raw: Partial<OrganizationSettings> | null | undefined): OrganizationSettings {
  const base = defaultOrganizationSettings();
  const source = (raw ?? {}) as Record<string, unknown>;
  const avatar = pick(base.avatar, source.avatar);
  return {
    integrity: withIntegrityDefaults(source.integrity),
    recording: pick(base.recording, source.recording),
    retention: pick(base.retention, source.retention),
    avatar: normalizeAvatar(avatar),
    candidate_report_mode: REPORT_MODES.includes(source.candidate_report_mode as CandidateReportMode)
      ? (source.candidate_report_mode as CandidateReportMode)
      : base.candidate_report_mode,
    require_access_code: typeof source.require_access_code === "boolean" ? source.require_access_code : base.require_access_code,
    data_processing_notice: typeof source.data_processing_notice === "string" ? source.data_processing_notice : base.data_processing_notice,
  };
}

/** Rules ready for the API: trimmed message, observation-only events downgraded to a flag. */
export function toRules(rules: readonly IntegrityRule[]): IntegrityRule[] {
  return rules.map((rule) => ({
    event_type: rule.event_type,
    enabled: rule.enabled,
    min_confidence: rule.min_confidence,
    min_duration_seconds: rule.min_duration_seconds,
    min_occurrences: rule.min_occurrences,
    window_seconds: rule.window_seconds,
    action: OBSERVATION_ONLY_EVENTS.has(rule.event_type) && POLICY_ACTIONS.has(rule.action) ? "flag_for_human_review" : rule.action,
    candidate_message: blankToNull(rule.candidate_message),
  }));
}

/** Exactly the OrganizationSettings keys, cleaned. */
export function toOrganizationSettings(settings: OrganizationSettings): OrganizationSettings {
  const integrity = withIntegrityDefaults(settings.integrity);
  return {
    integrity: { ...integrity, rules: toRules(integrity.rules) },
    recording: pick(defaultRecordingPolicy(), settings.recording),
    retention: pick(defaultRetentionPolicy(), settings.retention),
    avatar: {
      ...pick(defaultAvatarSettings(), settings.avatar),
      interviewer_name: settings.avatar.interviewer_name.trim() || "Maya",
      model_url: settings.avatar.model_url.trim() || (settings.avatar.model_format === "orb" ? "orb:aurora" : REALISTIC_AVATAR_URL),
    },
    candidate_report_mode: settings.candidate_report_mode,
    require_access_code: settings.require_access_code,
    data_processing_notice: settings.data_processing_notice.trim() || DEFAULT_DATA_NOTICE,
  };
}

export interface SettingsIssue {
  path: string;
  message: string;
}

const RETENTION_KEYS: Array<keyof RetentionPolicy> = ["resume_days", "audio_days", "video_days", "frame_days", "transcript_days", "report_days"];

export function validateRules(rules: readonly IntegrityRule[], prefix = "integrity.rules"): SettingsIssue[] {
  const issues: SettingsIssue[] = [];
  const seen = new Set<string>();
  rules.forEach((rule, i) => {
    if (seen.has(rule.event_type)) issues.push({ path: `${prefix}.${i}.event_type`, message: "There is already a rule for this signal." });
    seen.add(rule.event_type);
    if (!(rule.min_confidence >= 0 && rule.min_confidence <= 1)) issues.push({ path: `${prefix}.${i}.min_confidence`, message: "Confidence must be between 0 and 1." });
    if (!(rule.min_duration_seconds >= 0)) issues.push({ path: `${prefix}.${i}.min_duration_seconds`, message: "Duration can't be negative." });
    if (!(Number.isInteger(rule.min_occurrences) && rule.min_occurrences >= 1)) issues.push({ path: `${prefix}.${i}.min_occurrences`, message: "At least 1 occurrence." });
    if (!(rule.window_seconds >= 1)) issues.push({ path: `${prefix}.${i}.window_seconds`, message: "The window must be at least 1 second." });
  });
  return issues;
}

export function validateIntegrity(policy: IntegrityPolicy, prefix = "integrity"): SettingsIssue[] {
  const issues: SettingsIssue[] = [];
  const intRange = (key: keyof IntegrityPolicy, min: number, max: number, label: string) => {
    const value = policy[key] as number;
    if (!Number.isInteger(value) || value < min || value > max) issues.push({ path: `${prefix}.${key}`, message: `${label} must be a whole number from ${min} to ${max}.` });
  };
  intRange("camera_frame_interval_seconds", 3, 120, "Camera check interval");
  intRange("screen_frame_interval_seconds", 5, 300, "Screen check interval");
  if (!Number.isInteger(policy.max_reconnects) || policy.max_reconnects < 0) issues.push({ path: `${prefix}.max_reconnects`, message: "Reconnects must be 0 or more." });
  issues.push(...validateRules(policy.rules, `${prefix}.rules`));
  return issues;
}

export function validateSettings(settings: OrganizationSettings): SettingsIssue[] {
  const issues = validateIntegrity(settings.integrity);
  for (const key of RETENTION_KEYS) {
    const value = settings.retention[key];
    if (!Number.isInteger(value) || value < 1) issues.push({ path: `retention.${key}`, message: "Keep data for at least 1 day." });
    else if (value > 3650) issues.push({ path: `retention.${key}`, message: "Ten years (3,650 days) is the maximum." });
  }
  if (!settings.avatar.interviewer_name.trim()) issues.push({ path: "avatar.interviewer_name", message: "Give the interviewer a name." });
  else if (settings.avatar.interviewer_name.length > 40) issues.push({ path: "avatar.interviewer_name", message: "Keep the name under 40 characters." });
  const url = settings.avatar.model_url.trim();
  if (settings.avatar.model_format === "orb") {
    if (!ORB_URL_PATTERN.test(url)) issues.push({ path: "avatar.model_url", message: "Choose one of the presence styles: Aurora, Sunrise or Ocean." });
  } else if (settings.avatar.model_format !== "procedural" && (!url.startsWith("/") || url.startsWith("//"))) {
    issues.push({ path: "avatar.model_url", message: "Use a path on this site, such as /avatars/interviewer.glb (external URLs are blocked)." });
  }
  if (settings.data_processing_notice.trim().length < 40) issues.push({ path: "data_processing_notice", message: "Explain to candidates how their data is used (at least a sentence)." });
  if (settings.data_processing_notice.length > 4000) issues.push({ path: "data_processing_notice", message: "Keep the notice under 4,000 characters." });
  if (settings.recording.record_screen && !settings.integrity.screen_share_required) {
    issues.push({ path: "recording.record_screen", message: "Screen recording needs required screen sharing (Integrity)." });
  }
  return issues;
}

/** "365 days" -> "1 year" style hint. */
export function describeDays(days: number): string {
  if (!Number.isFinite(days) || days < 1) return "";
  if (days % 365 === 0) return days === 365 ? "1 year" : `${days / 365} years`;
  if (days % 30 === 0) return days === 30 ? "About 1 month" : `About ${days / 30} months`;
  if (days % 7 === 0) return days === 7 ? "1 week" : `${days / 7} weeks`;
  return `${days} days`;
}
