/**
 * The effective monitoring policy for the interview room: the frozen policy
 * snapshot from the portal, refined by the live `session.state.integrity`
 * flags, gated by what the candidate consented to.
 */
import type { IntegrityPolicy, PolicySnapshot, RecordingPolicy } from "@/lib/types";

import type { MonitorPolicy } from "./monitor";

export const DEFAULT_INTEGRITY: IntegrityPolicy = {
  enabled: true,
  require_fullscreen: true,
  monitor_focus: true,
  monitor_clipboard: true,
  block_paste_in_coding: false,
  monitor_shortcuts: true,
  camera_required: true,
  camera_frame_analysis: true,
  // Fallback only: the server policy always supplies the interval (backend default 15 s).
  camera_frame_interval_seconds: 15,
  store_evidence_frames: true,
  face_presence_check: true,
  screen_share_required: false,
  screen_share_analysis: false,
  screen_frame_interval_seconds: 20,
  terminate_on_required_screen_share_stop: false,
  warn_candidate: true,
  allow_page_reload_resume: true,
  max_reconnects: 10,
  rules: [],
};

export const DEFAULT_RECORDING: RecordingPolicy = {
  record_answer_audio: true,
  // Matches the server default (RecordingPolicy.record_video); consent still gates it.
  record_video: true,
  record_screen: false,
  show_live_subtitles: true,
  allow_typed_answers: true,
};

export interface ConsentFlags {
  camera?: boolean;
  camera_frames?: boolean;
  face_check?: boolean;
  screen_share?: boolean;
  video_recording?: boolean;
  integrity_monitoring?: boolean;
  [key: string]: boolean | number | undefined;
}

export interface EffectivePolicy {
  integrity: IntegrityPolicy;
  recording: RecordingPolicy;
  /** Coding paste threshold (characters). */
  largePasteThreshold: number;
  monitor: MonitorPolicy;
  /** Camera frames may be uploaded for analysis. */
  uploadCameraFrames: boolean;
  uploadScreenFrames: boolean;
  recordVideo: boolean;
  /**
   * The recording may include the camera picture: recording is on and the candidate
   * did not decline the camera (an optional camera declined -> audio-only recording).
   */
  recordCamera: boolean;
  /** Record the shared screen (policy record_screen + screen-share or video consent, as the server checks). */
  recordScreen: boolean;
  /**
   * On-device face check: policy on while the camera is used, and the stored consent
   * covered it (item `face_check`). `null` = no local consent record: the runtime asks
   * the server (disclosure `consented`) before starting.
   */
  faceCheck: boolean | null;
}

/**
 * Whether the room runs the face check: off when the policy (or the server's live
 * flag, which also checks the consent) says so; on when the live flag says so or
 * the local consent record includes it; unknown (`null`) without either.
 */
function faceCheckDecision(integrity: IntegrityPolicy, live: Record<string, unknown> | null | undefined, consent: ConsentFlags | null | undefined): boolean | null {
  if (!faceCheckPolicyOn(integrity) || live?.face_presence_check === false) return false;
  if (live?.face_presence_check === true) return true;
  if (consent) return consent.face_check === true && consent.camera !== false;
  return null;
}

/** The face check applies under this policy (monitoring on, camera used, toggle not off). */
export function faceCheckPolicyOn(integrity: Partial<IntegrityPolicy> | null | undefined): boolean {
  if (!integrity || integrity.enabled === false) return false;
  if (integrity.face_presence_check === false) return false;
  return Boolean(integrity.camera_required ?? true) || Boolean(integrity.camera_frame_analysis ?? true);
}

/** Server bounds (apps/api/app/schemas/policies.py): out-of-range or missing values fall back to the defaults. */
const INTERVAL_BOUNDS = {
  camera_frame_interval_seconds: [3, 120],
  screen_frame_interval_seconds: [5, 300],
} as const;

function sanitizeIntervals(policy: IntegrityPolicy): IntegrityPolicy {
  const out = { ...policy };
  for (const [key, [min, max]] of Object.entries(INTERVAL_BOUNDS) as Array<[keyof typeof INTERVAL_BOUNDS, readonly [number, number]]>) {
    const value = out[key];
    out[key] = typeof value === "number" && Number.isFinite(value) && value >= min && value <= max ? Math.round(value) : DEFAULT_INTEGRITY[key];
  }
  return out;
}

function pickBooleans<T extends object>(base: T, overrides: Record<string, unknown> | null | undefined): T {
  if (!overrides) return base;
  const out = { ...base } as Record<string, unknown>;
  for (const [key, value] of Object.entries(overrides)) {
    if (!(key in out)) continue;
    const current = out[key];
    if (typeof current === "boolean" && typeof value === "boolean") out[key] = value;
    else if (typeof current === "number" && typeof value === "number" && Number.isFinite(value)) out[key] = value;
  }
  return out as T;
}

export function effectivePolicy(
  snapshot: Partial<PolicySnapshot> | null | undefined,
  live: Record<string, unknown> | null | undefined,
  consent: ConsentFlags | null | undefined,
): EffectivePolicy {
  const integrity = sanitizeIntervals(pickBooleans({ ...DEFAULT_INTEGRITY, ...(snapshot?.integrity ?? {}) }, live));
  const recording = pickBooleans({ ...DEFAULT_RECORDING, ...(snapshot?.recording ?? {}) }, live);
  const threshold = typeof live?.large_paste_threshold_chars === "number" ? live.large_paste_threshold_chars : 200;
  const consentedCamera = consent?.camera_frames ?? consent?.camera ?? true;
  const consentedVideo = consent?.video_recording ?? false;
  const consentedScreen = consent?.screen_share ?? true;
  return {
    integrity,
    recording,
    largePasteThreshold: threshold,
    monitor: {
      enabled: integrity.enabled,
      require_fullscreen: integrity.require_fullscreen,
      monitor_focus: integrity.monitor_focus,
      monitor_clipboard: integrity.monitor_clipboard,
      monitor_shortcuts: integrity.monitor_shortcuts,
      camera_required: integrity.camera_required,
      allow_page_reload_resume: integrity.allow_page_reload_resume,
    },
    uploadCameraFrames: integrity.enabled && integrity.camera_frame_analysis && consentedCamera,
    uploadScreenFrames: integrity.enabled && integrity.screen_share_required && integrity.screen_share_analysis && consentedScreen,
    recordVideo: recording.record_video && consentedVideo,
    recordCamera: recording.record_video && consentedVideo && consent?.camera !== false,
    recordScreen: recording.record_screen && (consent?.screen_share === true || consentedVideo),
    faceCheck: faceCheckDecision(integrity, live, consent),
  };
}

/** Plain-language list of what is monitored, for the instructions and the room. */
export function monitoringSummary(policy: IntegrityPolicy): string[] {
  if (!policy.enabled) return [];
  const items: string[] = [];
  if (policy.require_fullscreen) items.push("Whether the interview stays in full screen");
  if (policy.monitor_focus) items.push("When you switch to another tab or window");
  if (policy.monitor_clipboard) items.push("Copy and paste activity");
  if (policy.monitor_shortcuts) items.push("Shortcuts such as print, save or developer tools");
  if (policy.camera_required) items.push(policy.camera_frame_analysis ? "Your camera, with a still image checked every few seconds" : "Whether your camera stays on");
  if (faceCheckPolicyOn(policy)) items.push("Whether one face is visible, checked on your own device");
  if (policy.screen_share_required) items.push("Your shared screen");
  return items;
}
