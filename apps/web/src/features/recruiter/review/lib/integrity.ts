/**
 * Integrity helpers. Integrity events are observations for a person to
 * review in context; nothing here ranks candidates or implies intent.
 */

import { formatDuration, formatPercent, humanize } from "@/lib/format";
import { INTEGRITY_EVENT_LABELS, labelFor } from "@/lib/labels";
import type { IntegrityReviewStatus, IntegritySeverity } from "@/lib/types";

import { isRecord, numberOrNull, stringOrNull } from "../api/normalize";
import type { IntegrityEventView } from "../api/types";

export const OBSERVATIONS_BANNER = "Signals are observations, not determinations.";
export const OBSERVATIONS_DETAIL =
  "Each signal is something the browser, camera or server noticed. Many have innocent explanations (a notification, a second monitor, a family member walking by). Review it in context before drawing any conclusion, and never treat it as proof on its own.";

export const SEVERITY_ORDER: readonly IntegritySeverity[] = ["high", "medium", "low", "info"];

export const SEVERITY_RANK: Record<IntegritySeverity, number> = { high: 3, medium: 2, low: 1, info: 0 };

export interface ReviewOption {
  value: Exclude<IntegrityReviewStatus, "unreviewed">;
  label: string;
  description: string;
}

/** Review outcomes, worded as the reviewer's judgement about the signal (not the person). */
export const REVIEW_OPTIONS: readonly ReviewOption[] = [
  { value: "dismissed", label: "Dismiss", description: "Reviewed in context; no concern." },
  { value: "noted", label: "Note", description: "Keep for context; no action needed now." },
  { value: "confirmed_concern", label: "Confirm concern", description: "Worth a conversation with the candidate before any decision." },
];

export function reviewOptionLabel(status: IntegrityReviewStatus): string {
  return REVIEW_OPTIONS.find((o) => o.value === status)?.label ?? "Not reviewed";
}

export function countBySeverity(events: readonly IntegrityEventView[]): Record<IntegritySeverity, number> {
  const out: Record<IntegritySeverity, number> = { high: 0, medium: 0, low: 0, info: 0 };
  for (const event of events) out[event.severity] = (out[event.severity] ?? 0) + 1;
  return out;
}

export function countByType(events: readonly IntegrityEventView[]): Array<{ type: IntegrityEventView["type"]; count: number }> {
  const map = new Map<IntegrityEventView["type"], number>();
  for (const event of events) map.set(event.type, (map.get(event.type) ?? 0) + Math.max(1, event.count ?? 1));
  return [...map.entries()].map(([type, count]) => ({ type, count })).sort((a, b) => b.count - a.count);
}

export function sortEvents(events: readonly IntegrityEventView[], by: "time" | "severity" = "time"): IntegrityEventView[] {
  return [...events].sort((a, b) => {
    if (by === "severity") {
      const diff = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
      if (diff !== 0) return diff;
    }
    return a.occurred_at < b.occurred_at ? (by === "severity" ? 1 : -1) : a.occurred_at > b.occurred_at ? (by === "severity" ? -1 : 1) : 0;
  });
}

/** Signed evidence URL when the API included one (else the caller signs `evidence_media_id`). */
export function inlineEvidenceUrl(event: IntegrityEventView): string | null {
  const direct = stringOrNull(event.evidence_url);
  if (direct && direct.startsWith("/api/")) return direct;
  const nested = isRecord(event.details) ? stringOrNull(event.details.evidence_url) : null;
  return nested && nested.startsWith("/api/") ? nested : null;
}

export function eventTitle(event: Pick<IntegrityEventView, "type">): string {
  return labelFor(INTEGRITY_EVENT_LABELS, event.type);
}

const DETAIL_LABELS: Record<string, string> = {
  duration_ms: "Duration",
  count: "Occurrences",
  occurrences: "Occurrences",
  chars: "Characters",
  char_count: "Characters",
  length: "Characters",
  displays: "Displays",
  key: "Shortcut",
  shortcut: "Shortcut",
  kind: "Kind",
  persons: "People visible",
  person_count: "People visible",
  window_seconds: "Within",
  reason: "Reason",
  surface: "Surface",
  observation: "Observation",
  note: "Note",
  frames: "Frames",
  faces: "Faces on camera",
  still_seconds: "Motionless for",
  position: "Position",
  basis: "Found by",
};

const HIDDEN_DETAIL_KEYS = new Set([
  "evidence_url",
  "evidence_media_id",
  "media_id",
  "raw",
  "model",
  "prompt_version",
  "frame_ids",
  // Face-check plumbing (interval / page-run ids, detector internals).
  "episode",
  "run",
  "phase",
  "detector",
  "fps",
  "frame_ms",
  "face_size",
]);

/** Face-check event types (on-device detector, server double-check). */
export const FACE_CHECK_TYPES: ReadonlySet<string> = new Set(["face_not_visible", "multiple_faces_visible", "static_face_ignored", "face_check_summary"]);
const FACE_INTERVAL_TYPES: ReadonlySet<string> = new Set(["face_not_visible", "multiple_faces_visible"]);

const BASIS_LABELS: Record<string, string> = {
  face_detector: "Face detector",
  vision: "Camera analysis",
  "face_detector+vision": "Face detector and camera analysis",
};

/** "Confirmed: 2 faces found" for a face-check interval's server check, or null. */
export function serverCheckText(details: Record<string, unknown> | null | undefined): string | null {
  const check = isRecord(details) && isRecord(details.server_check) ? details.server_check : null;
  if (!check) return null;
  const faces = numberOrNull(check.faces);
  const found = faces === null ? "" : `: ${faces === 1 ? "1 face" : `${faces} faces`} found`;
  if (check.status === "confirmed") return `Confirmed${found}`;
  if (check.status === "not_confirmed") return `Not confirmed${found}`;
  if (check.status === "unavailable") return "Could not run";
  return null;
}

/** Human-readable key facts from an event's details (never raw model output). */
export function describeDetails(event: IntegrityEventView): Array<{ label: string; value: string }> {
  const out: Array<{ label: string; value: string }> = [];
  const faceInterval = FACE_INTERVAL_TYPES.has(event.type);
  if (event.count > 1) out.push({ label: faceInterval ? "Intervals" : "Occurrences", value: String(event.count) });
  if (event.duration_ms) out.push({ label: "Duration", value: formatDuration(event.duration_ms / 1000) });
  if (typeof event.confidence === "number") out.push({ label: "Model confidence", value: formatPercent(event.confidence) });
  const details = isRecord(event.details) ? event.details : {};
  const check = serverCheckText(details);
  if (check) out.push({ label: "Server check", value: check });
  for (const [key, raw] of Object.entries(details)) {
    if (HIDDEN_DETAIL_KEYS.has(key) || key === "count" || key === "duration_ms" || key === "confidence") continue;
    let value: string | null = null;
    if (key === "basis" && typeof raw === "string") value = BASIS_LABELS[raw] ?? humanize(raw);
    else if (key === "still_seconds" && numberOrNull(raw) !== null) value = formatDuration(raw as number);
    else if (typeof raw === "string") value = raw.length > 160 ? `${raw.slice(0, 159)}…` : raw;
    else if (typeof raw === "boolean") value = raw ? "Yes" : "No";
    else if (numberOrNull(raw) !== null) value = key.endsWith("_ms") ? formatDuration((raw as number) / 1000) : String(raw);
    else if (Array.isArray(raw) && raw.every((v) => typeof v === "string")) value = raw.join(", ");
    if (value) out.push({ label: DETAIL_LABELS[key] ?? humanize(key), value });
  }
  return out.slice(0, 8);
}

// ---------------------------------------------------------------- face presence

export interface FaceIntervalStats {
  intervals: number;
  totalMs: number;
  longestMs: number;
  /** Intervals the server's face detector confirmed from the snapshot. */
  confirmed: number;
}

export interface FacePresenceSummary {
  monitoredMs: number;
  oneFaceMs: number;
  noFaceMs: number;
  multiFaceMs: number;
  /** Share of monitored time with exactly one face visible (null without monitored time). */
  oneFaceShare: number | null;
  noFace: FaceIntervalStats;
  multipleFaces: FaceIntervalStats;
  staticFacesIgnored: number;
  pageRuns: number;
}

const SUMMARY_COUNTERS = ["monitored_ms", "one_face_ms", "no_face_ms", "multi_face_ms", "static_faces"] as const;

function intervalStats(events: readonly IntegrityEventView[]): FaceIntervalStats {
  const episodes = new Map<string, number>();
  let confirmed = 0;
  for (const event of events) {
    const details = isRecord(event.details) ? event.details : {};
    const bucket = isRecord(details.episodes) ? details.episodes : null;
    if (bucket && Object.keys(bucket).length) {
      for (const [key, value] of Object.entries(bucket)) {
        const ms = numberOrNull(value);
        if (ms !== null) episodes.set(key, Math.max(episodes.get(key) ?? 0, ms));
      }
    } else {
      episodes.set(event.id, event.duration_ms ?? 0);
    }
    const checks = isRecord(details.server_checks) ? details.server_checks : {};
    confirmed += Object.values(checks).filter((status) => status === "confirmed").length;
  }
  const values = [...episodes.values()];
  return { intervals: values.length, totalMs: values.reduce((a, b) => a + b, 0), longestMs: values.length ? Math.max(...values) : 0, confirmed };
}

/**
 * What the on-device face check observed over the session: how much of the
 * monitored time showed exactly one face, the intervals with no face or more
 * than one face, and motionless pictures set aside. Null when the session has
 * no face-check observations.
 */
export function facePresenceSummary(events: readonly IntegrityEventView[]): FacePresenceSummary | null {
  const face = events.filter((e) => FACE_CHECK_TYPES.has(e.type));
  if (!face.length) return null;
  const runs = new Map<string, Record<string, number>>();
  let staticEvents = 0;
  for (const event of face) {
    const details = isRecord(event.details) ? event.details : {};
    if (event.type === "face_check_summary") {
      const bucket = isRecord(details.runs) ? details.runs : {};
      for (const [run, counters] of Object.entries(bucket)) {
        if (!isRecord(counters)) continue;
        const merged = runs.get(run) ?? {};
        for (const name of SUMMARY_COUNTERS) {
          const value = numberOrNull(counters[name]);
          if (value !== null) merged[name] = Math.max(merged[name] ?? 0, value);
        }
        runs.set(run, merged);
      }
    } else if (event.type === "static_face_ignored") {
      staticEvents += Math.max(1, event.count ?? 1);
    }
  }
  const total = (name: (typeof SUMMARY_COUNTERS)[number]) => [...runs.values()].reduce((sum, run) => sum + (run[name] ?? 0), 0);
  const monitoredMs = total("monitored_ms");
  return {
    monitoredMs,
    oneFaceMs: total("one_face_ms"),
    noFaceMs: total("no_face_ms"),
    multiFaceMs: total("multi_face_ms"),
    oneFaceShare: monitoredMs > 0 ? total("one_face_ms") / monitoredMs : null,
    noFace: intervalStats(face.filter((e) => e.type === "face_not_visible")),
    multipleFaces: intervalStats(face.filter((e) => e.type === "multiple_faces_visible")),
    staticFacesIgnored: Math.max(staticEvents, total("static_faces")),
    pageRuns: runs.size,
  };
}
