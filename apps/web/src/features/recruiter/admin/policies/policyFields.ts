/**
 * Plain-language descriptions of every policy field. Integrity signals are
 * observations for a human reviewer — the copy never implies a verdict.
 */

import type { IntegrityPolicy, RecordingPolicy, RetentionPolicy } from "@/lib/types";

export type PolicyFieldKind = { type: "boolean" } | { type: "integer"; min: number; max: number; unit: string; step?: number };

export interface PolicyFieldDef<K extends string> {
  key: K;
  label: string;
  help: string;
  kind: PolicyFieldKind;
  /** Only meaningful when this boolean field is on. */
  dependsOn?: K;
  group?: string;
}

type IntegrityKey = Exclude<keyof IntegrityPolicy, "rules">;

export const INTEGRITY_GROUPS: ReadonlyArray<{ key: string; label: string; description: string }> = [
  { key: "general", label: "Monitoring", description: "Whether signals are collected at all, and how candidates are told about them." },
  { key: "browser", label: "Browser and window", description: "Signals from the interview tab itself. They never stop an interview on their own." },
  { key: "camera", label: "Camera", description: "Periodic camera snapshots reviewed by a vision model. Results are observations for your team." },
  { key: "screen", label: "Screen sharing", description: "Optional screen sharing for the whole interview." },
  { key: "connection", label: "Connection", description: "How interruptions and reloads are handled." },
];

export const INTEGRITY_FIELDS: ReadonlyArray<PolicyFieldDef<IntegrityKey>> = [
  {
    key: "enabled",
    group: "general",
    label: "Collect integrity signals",
    help: "Turn off to run interviews without any focus, clipboard, camera or screen monitoring.",
    kind: { type: "boolean" },
  },
  {
    key: "warn_candidate",
    group: "general",
    label: "Remind candidates in the moment",
    help: "Shows a short, neutral reminder (for example, to return to the interview tab) when a rule asks for it.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "require_fullscreen",
    group: "browser",
    label: "Ask for full screen",
    help: "Candidates are asked to enter full screen when the interview starts. Leaving it is recorded.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "monitor_focus",
    group: "browser",
    label: "Notice tab and window switches",
    help: "Records when the interview tab is hidden or loses focus, and how long for.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "monitor_clipboard",
    group: "browser",
    label: "Notice copy and paste",
    help: "Records copy and paste events and their size (never the clipboard contents).",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "block_paste_in_coding",
    group: "browser",
    label: "Block pasting into the code editor",
    help: "Stops paste in the coding exercise entirely. Leave off to allow paste and record large pastes instead.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "monitor_shortcuts",
    group: "browser",
    label: "Notice restricted shortcuts",
    help: "Records shortcuts such as opening developer tools or printing the page.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "camera_required",
    group: "camera",
    label: "Require the camera",
    help: "Candidates must allow camera access to start. Turning the camera off later is recorded.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "camera_frame_analysis",
    group: "camera",
    label: "Review camera snapshots",
    help: "A vision model looks at snapshots for things like a second person or a phone. Findings are flagged for review, never acted on automatically.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "camera_frame_interval_seconds",
    group: "camera",
    label: "Snapshot interval",
    help: "How often a snapshot is taken while the camera is on.",
    kind: { type: "integer", min: 3, max: 120, unit: "seconds" },
    dependsOn: "camera_frame_analysis",
  },
  {
    key: "store_evidence_frames",
    group: "camera",
    label: "Keep snapshots that raised a flag",
    help: "Saves the frame behind each flag so a reviewer can see it. Kept for the frame retention period.",
    kind: { type: "boolean" },
    dependsOn: "camera_frame_analysis",
  },
  {
    key: "screen_share_required",
    group: "screen",
    label: "Require screen sharing",
    help: "Candidates share their entire screen for the whole interview.",
    kind: { type: "boolean" },
    dependsOn: "enabled",
  },
  {
    key: "screen_share_analysis",
    group: "screen",
    label: "Review shared-screen snapshots",
    help: "A vision model notes notable changes on the shared screen. Findings are observations for review.",
    kind: { type: "boolean" },
    dependsOn: "screen_share_required",
  },
  {
    key: "screen_frame_interval_seconds",
    group: "screen",
    label: "Screen snapshot interval",
    help: "How often the shared screen is sampled.",
    kind: { type: "integer", min: 5, max: 300, unit: "seconds" },
    dependsOn: "screen_share_analysis",
  },
  {
    key: "terminate_on_required_screen_share_stop",
    group: "screen",
    label: "End the interview if sharing stops",
    help: "An objective rule: if required sharing stops and isn't restarted, the interview ends. Candidates are told about this before they start.",
    kind: { type: "boolean" },
    dependsOn: "screen_share_required",
  },
  {
    key: "allow_page_reload_resume",
    group: "connection",
    label: "Let candidates resume after a reload",
    help: "A refreshed or reopened tab picks up where the interview left off.",
    kind: { type: "boolean" },
  },
  {
    key: "max_reconnects",
    group: "connection",
    label: "Reconnections allowed",
    help: "Dropped connections tolerated before the pattern is flagged for review.",
    kind: { type: "integer", min: 0, max: 100, unit: "times" },
  },
];

export const RECORDING_FIELDS: ReadonlyArray<PolicyFieldDef<keyof RecordingPolicy>> = [
  {
    key: "record_answer_audio",
    label: "Keep answer audio",
    help: "Stores each spoken answer so reviewers can listen alongside the transcript.",
    kind: { type: "boolean" },
  },
  {
    key: "record_video",
    label: "Record camera video",
    help: "Stores the camera feed in short segments. Candidates are told before they agree.",
    kind: { type: "boolean" },
  },
  {
    key: "record_screen",
    label: "Record the shared screen",
    help: "Stores the shared screen as video. Requires screen sharing.",
    kind: { type: "boolean" },
  },
  {
    key: "show_live_subtitles",
    label: "Show live subtitles",
    help: "Candidates see the interviewer's words and their own live transcript on screen.",
    kind: { type: "boolean" },
  },
  {
    key: "allow_typed_answers",
    label: "Allow typed answers",
    help: "Candidates can type an answer instead of speaking (accessibility, noisy rooms).",
    kind: { type: "boolean" },
  },
];

export const RETENTION_FIELDS: ReadonlyArray<PolicyFieldDef<keyof RetentionPolicy>> = [
  { key: "resume_days", label: "Resumes", help: "Uploaded files and the text extracted from them.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
  { key: "transcript_days", label: "Transcripts", help: "Questions, answers and evaluations.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
  { key: "audio_days", label: "Answer audio", help: "Recorded spoken answers.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
  { key: "video_days", label: "Video", help: "Camera and screen recordings.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
  { key: "frame_days", label: "Snapshots", help: "Camera and screen frames kept as evidence.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
  { key: "report_days", label: "Reports", help: "Recruiter and candidate reports.", kind: { type: "integer", min: 1, max: 3650, unit: "days" } },
];

/** Is `key` effectively disabled because a parent toggle is off? */
export function isDependencyOff<K extends string>(field: PolicyFieldDef<K>, fields: ReadonlyArray<PolicyFieldDef<K>>, values: Record<K, unknown>): boolean {
  let parent = field.dependsOn;
  const seen = new Set<string>();
  while (parent && !seen.has(parent)) {
    seen.add(parent);
    if (values[parent] === false) return true;
    parent = fields.find((f) => f.key === parent)?.dependsOn;
  }
  return false;
}
