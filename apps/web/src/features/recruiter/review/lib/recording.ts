/**
 * Recording timeline maths (pure).
 *
 * Three clocks meet here:
 * - media time: position inside one recording part (0 = the part started);
 * - the interview clock: transcript `start_ms`, which pauses while the
 *   candidate is disconnected (the recording may keep running);
 * - wall time: integrity `occurred_at`, part `started_at` (server clock).
 *
 * Parts carry anchors pairing media time with the interview clock (both ends
 * of every stretch with a constant offset). Mapping uses the nearest anchor; a
 * part without anchors falls back to wall time against the session start.
 */

import type { IntegrityEventView, TranscriptEntry } from "../api/types";
import type { RecordingAnchor, RecordingPart } from "../api/recordings";
import type { TranscriptTurn } from "./transcript";

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

function nearest(anchors: readonly RecordingAnchor[], value: number, key: keyof RecordingAnchor): RecordingAnchor | null {
  let best: RecordingAnchor | null = null;
  let distance = Number.POSITIVE_INFINITY;
  for (const anchor of anchors) {
    const d = Math.abs(anchor[key] - value);
    if (d < distance) {
      distance = d;
      best = anchor;
    }
  }
  return best;
}

export function partDurationMs(part: Pick<RecordingPart, "durationMs" | "anchors">): number {
  if (part.durationMs && part.durationMs > 0) return part.durationMs;
  return part.anchors.at(-1)?.mediaMs ?? 0;
}

/** Interview-clock ms at a media position of the part (null when unknown). */
export function sessionMsAt(part: RecordingPart, mediaMs: number, sessionStartedAt: string | null): number | null {
  const anchor = nearest(part.anchors, mediaMs, "mediaMs");
  if (anchor) return Math.max(0, anchor.sessionMs + (mediaMs - anchor.mediaMs));
  const partStart = parseMs(part.startedAt);
  const sessionStart = parseMs(sessionStartedAt);
  if (partStart === null || sessionStart === null) return null;
  return Math.max(0, partStart + mediaMs - sessionStart);
}

/** Media position of an interview-clock moment in this part, or null outside it. */
export function mediaMsFor(part: RecordingPart, sessionMs: number, sessionStartedAt: string | null, slackMs = 2_000): number | null {
  let media: number | null = null;
  const anchor = nearest(part.anchors, sessionMs, "sessionMs");
  if (anchor) media = anchor.mediaMs + (sessionMs - anchor.sessionMs);
  else {
    const partStart = parseMs(part.startedAt);
    const sessionStart = parseMs(sessionStartedAt);
    if (partStart !== null && sessionStart !== null) media = sessionStart + sessionMs - partStart;
  }
  if (media === null) return null;
  const duration = partDurationMs(part);
  if (media < -slackMs || (duration > 0 && media > duration + slackMs)) return null;
  return Math.min(Math.max(0, media), duration > 0 ? duration : Math.max(0, media));
}

/** Media position of a wall-clock instant (server time) in this part, or null outside it. */
export function mediaMsAtWall(part: RecordingPart, iso: string | null | undefined, slackMs = 1_000): number | null {
  const at = parseMs(iso);
  const start = parseMs(part.startedAt);
  if (at === null || start === null) return null;
  const media = at - start;
  const duration = partDurationMs(part);
  if (media < -slackMs || (duration > 0 && media > duration + slackMs)) return null;
  return Math.min(Math.max(0, media), duration > 0 ? duration : media);
}

/** The first part (of `kind`) that covers an interview-clock moment. */
export function locateSessionMs(
  parts: readonly RecordingPart[],
  sessionMs: number,
  sessionStartedAt: string | null,
  kind: RecordingPart["kind"] = "camera",
): { part: RecordingPart; mediaMs: number } | null {
  for (const part of parts) {
    if (part.kind !== kind || part.status !== "ready") continue;
    const mediaMs = mediaMsFor(part, sessionMs, sessionStartedAt);
    if (mediaMs !== null) return { part, mediaMs };
  }
  return null;
}

export interface TimelineMarker {
  id: string;
  mediaMs: number;
  /** 0-100 along the part. */
  leftPct: number;
  label: string;
}

/** Integrity observations inside the part, as neutral timeline markers. */
export function integrityMarkers(part: RecordingPart, events: readonly IntegrityEventView[], label: (e: IntegrityEventView) => string): TimelineMarker[] {
  const duration = partDurationMs(part);
  if (duration <= 0) return [];
  const markers: TimelineMarker[] = [];
  for (const event of events) {
    const mediaMs = mediaMsAtWall(part, event.occurred_at);
    if (mediaMs === null) continue;
    markers.push({ id: event.id, mediaMs, leftPct: Math.min(100, Math.max(0, (mediaMs / duration) * 100)), label: label(event) });
  }
  return markers.sort((a, b) => a.mediaMs - b.mediaMs);
}

export interface RecordingCue {
  id: string;
  speaker: TranscriptEntry["speaker"];
  text: string;
  /** Interview clock. */
  startMs: number;
  endMs: number | null;
  /** Where the line is in the recording (null: not recorded, e.g. during a reload). */
  partId: string | null;
  mediaMs: number | null;
}

/** Transcript turns placed on the recording parts. */
export function transcriptCues(turns: readonly TranscriptTurn[], parts: readonly RecordingPart[], sessionStartedAt: string | null): RecordingCue[] {
  return turns
    .filter((turn) => turn.startMs !== null && turn.speaker !== "system")
    .map((turn) => {
      const startMs = turn.startMs as number;
      const located = locateSessionMs(parts, startMs, sessionStartedAt);
      return {
        id: turn.id,
        speaker: turn.speaker,
        text: turn.text,
        startMs,
        endMs: turn.endMs,
        partId: located?.part.partId ?? null,
        mediaMs: located?.mediaMs ?? null,
      };
    });
}

/** Index of the line being heard at `mediaMs` of `partId` (-1 for none). */
export function activeCueIndex(cues: readonly RecordingCue[], partId: string | null, mediaMs: number): number {
  let active = -1;
  for (let i = 0; i < cues.length; i++) {
    const cue = cues[i]!;
    if (cue.partId !== partId || cue.mediaMs === null) continue;
    if (cue.mediaMs <= mediaMs + 250) active = i;
    else break;
  }
  return active;
}

/**
 * Where a second stream (the shared screen) should be at a moment of the main
 * part, matched on wall time; null when the screen part does not cover it.
 */
export function syncedMediaMs(main: RecordingPart, mainMediaMs: number, other: RecordingPart): number | null {
  const mainStart = parseMs(main.startedAt);
  if (mainStart === null) return null;
  return mediaMsAtWall(other, new Date(mainStart + mainMediaMs).toISOString(), 0);
}

export const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 2] as const;
