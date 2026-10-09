/**
 * Score timeline bucketing. The scoring engine writes a snapshot per
 * competency after every applied evaluation; reviewers read them in windows
 * ("Minute 0–10: AWS 7.2, Kubernetes 6.5") using the last value per window.
 * A competency with no new evidence in a window keeps its previous estimate,
 * marked as carried so the display can tell "unchanged" from "re-scored".
 */

import type { ScoreSnapshot } from "@/lib/types";

import { isRecord, numberOrNull, stringOrNull } from "../api/normalize";
import type { TimelineWindowDTO } from "../api/types";

export const WINDOW_OPTIONS = [5, 10, 15] as const;
export const DEFAULT_WINDOW_MINUTES = 10;

export interface WindowValue {
  score: number;
  confidence: number | null;
  evidenceCount: number | null;
  /** True when the value was carried over from an earlier window (no new evidence here). */
  carried: boolean;
  /** Change against the previous window's value (null for the first value). */
  delta: number | null;
}

export interface TimelineWindow {
  index: number;
  startMinute: number;
  endMinute: number;
  label: string;
  values: Record<string, WindowValue>;
  /** Competencies re-scored inside this window, in first-seen order. */
  updatedKeys: string[];
}

type SnapshotLike = Pick<ScoreSnapshot, "elapsed_seconds" | "competency_key" | "score"> & Partial<Pick<ScoreSnapshot, "confidence" | "evidence_count">>;

export function windowLabel(startMinute: number, endMinute: number): string {
  return `Minute ${startMinute}–${endMinute}`;
}

/** Competency keys in the order they first appear. */
export function competencyKeysInOrder(snapshots: readonly SnapshotLike[]): string[] {
  const seen = new Set<string>();
  const ordered = [...snapshots].sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  for (const snap of ordered) seen.add(snap.competency_key);
  return [...seen];
}

export interface BucketOptions {
  /** Interview length; extends the windows to cover the whole interview. */
  durationSeconds?: number | null;
  /** Keep earlier estimates in windows without new evidence (default true). */
  carryForward?: boolean;
}

/**
 * Buckets snapshots into fixed windows keeping the last value per competency
 * per window. Snapshots are ordered by elapsed time (stable for ties, so the
 * later array element wins at the same second, like the backend's insert
 * order). A snapshot exactly on a boundary belongs to the window it starts,
 * including the last boundary (which opens one more window), matching the
 * timeline API's bucketing.
 */
export function bucketSnapshots(snapshots: readonly SnapshotLike[], windowMinutes: number, options: BucketOptions = {}): TimelineWindow[] {
  const minutes = Math.max(1, Math.round(windowMinutes));
  const windowSeconds = minutes * 60;
  const carryForward = options.carryForward ?? true;
  const ordered = snapshots
    .map((snap, order) => ({ snap, order }))
    .filter(({ snap }) => Number.isFinite(snap.elapsed_seconds) && Number.isFinite(snap.score))
    .sort((a, b) => a.snap.elapsed_seconds - b.snap.elapsed_seconds || a.order - b.order)
    .map(({ snap }) => snap);

  const lastElapsed = ordered.length ? Math.max(0, ordered[ordered.length - 1]!.elapsed_seconds) : 0;
  const span = Math.max(lastElapsed, options.durationSeconds ?? 0);
  if (ordered.length === 0 && !options.durationSeconds) return [];
  let count = Math.max(1, Math.ceil(span / windowSeconds) || 1);
  if (ordered.length && ordered[ordered.length - 1]!.elapsed_seconds >= count * windowSeconds) count += 1;
  const indexOf = (elapsed: number) => Math.min(count - 1, Math.max(0, Math.floor(Math.max(0, elapsed) / windowSeconds)));

  const perWindow: Array<Map<string, SnapshotLike>> = Array.from({ length: count }, () => new Map());
  const firstSeen: string[][] = Array.from({ length: count }, () => []);
  for (const snap of ordered) {
    const index = indexOf(snap.elapsed_seconds);
    const bucket = perWindow[index]!;
    if (!bucket.has(snap.competency_key)) firstSeen[index]!.push(snap.competency_key);
    bucket.set(snap.competency_key, snap);
  }

  const windows: TimelineWindow[] = [];
  const previous = new Map<string, number>();
  const carried = new Map<string, WindowValue>();
  for (let index = 0; index < count; index += 1) {
    const values: Record<string, WindowValue> = {};
    if (carryForward) {
      for (const [key, value] of carried) values[key] = { ...value, carried: true, delta: 0 };
    }
    for (const [key, snap] of perWindow[index]!) {
      const prior = previous.get(key);
      const value: WindowValue = {
        score: snap.score,
        confidence: snap.confidence ?? null,
        evidenceCount: snap.evidence_count ?? null,
        carried: false,
        delta: prior === undefined ? null : round1(snap.score - prior),
      };
      values[key] = value;
      previous.set(key, snap.score);
      carried.set(key, value);
    }
    const startMinute = index * minutes;
    const endMinute = (index + 1) * minutes;
    windows.push({ index, startMinute, endMinute, label: windowLabel(startMinute, endMinute), values, updatedKeys: firstSeen[index]! });
  }
  return windows;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

/**
 * Normalises the timeline API's windows into the same shape (labels are
 * re-rendered locally for consistent typography). With `carryForward`,
 * competencies without a new value keep their previous one, marked carried.
 */
export function windowsFromServer(windows: readonly TimelineWindowDTO[], windowMinutes: number, { carryForward = true }: { carryForward?: boolean } = {}): TimelineWindow[] {
  const minutes = Math.max(1, Math.round(windowMinutes));
  const previous = new Map<string, number>();
  const carried = new Map<string, WindowValue>();
  return windows.map((raw, position) => {
    const index = numberOrNull(raw.index) ?? position;
    const startMinute =
      numberOrNull(raw.start_minute) ?? (numberOrNull(raw.start_seconds) !== null ? Math.round(numberOrNull(raw.start_seconds)! / 60) : index * minutes);
    const endMinute =
      numberOrNull(raw.end_minute) ?? (numberOrNull(raw.end_seconds) !== null ? Math.round(numberOrNull(raw.end_seconds)! / 60) : startMinute + minutes);
    const values: Record<string, WindowValue> = {};
    if (carryForward) for (const [key, value] of carried) values[key] = { ...value, carried: true, delta: 0 };
    const updatedKeys: string[] = [];
    const details = isRecord((raw as { details?: unknown }).details) ? ((raw as { details: Record<string, unknown> }).details) : {};
    const add = (key: string, score: number | null, confidence: number | null, evidenceCount: number | null) => {
      if (score === null) return;
      const prior = previous.get(key);
      const detail = isRecord(details[key]) ? (details[key] as Record<string, unknown>) : {};
      const value: WindowValue = {
        score,
        confidence: confidence ?? numberOrNull(detail.confidence),
        evidenceCount: evidenceCount ?? numberOrNull(detail.evidence_count),
        carried: false,
        delta: prior === undefined ? null : round1(score - prior),
      };
      values[key] = value;
      previous.set(key, score);
      carried.set(key, value);
      updatedKeys.push(key);
    };
    const source = raw.competencies ?? raw.scores;
    if (Array.isArray(source)) {
      for (const item of source) {
        const key = stringOrNull(item.competency_key) ?? stringOrNull(item.key);
        if (key) add(key, numberOrNull(item.score), numberOrNull(item.confidence), numberOrNull(item.evidence_count));
      }
    } else if (isRecord(source)) {
      for (const [key, value] of Object.entries(source)) {
        if (isRecord(value)) add(key, numberOrNull(value.score), numberOrNull(value.confidence), numberOrNull(value.evidence_count));
        else add(key, numberOrNull(value), null, null);
      }
    }
    return { index, startMinute, endMinute, label: windowLabel(startMinute, endMinute), values, updatedKeys };
  });
}

/** "Minute 0–10: AWS 7.2, Kubernetes 6.5" (re-scored competencies only unless `includeCarried`). */
export function describeWindow(window: TimelineWindow, nameOf: (key: string) => string, includeCarried = false): string {
  const parts = Object.entries(window.values)
    .filter(([, value]) => includeCarried || !value.carried)
    .map(([key, value]) => `${nameOf(key)} ${value.score.toFixed(1)}`);
  return parts.length ? `${window.label}: ${parts.join(", ")}` : `${window.label}: no new evidence`;
}

/** Latest snapshot per competency. */
export function latestByCompetency(snapshots: readonly SnapshotLike[]): Map<string, SnapshotLike> {
  const out = new Map<string, SnapshotLike>();
  const ordered = [...snapshots].sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  for (const snap of ordered) out.set(snap.competency_key, snap);
  return out;
}
