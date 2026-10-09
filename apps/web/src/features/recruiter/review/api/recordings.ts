/**
 * Session recordings for staff: parts list (`GET /sessions/{id}/recordings`),
 * short-lived playback URLs (re-signed before they expire), "process now" and
 * the admin-only download. Every playback/download URL request is audited by
 * the API.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query";

import { isRecord } from "./normalize";

export type RecordingStatus = "ready" | "recording" | "processing" | "failed";

export interface RecordingAnchor {
  /** Media time within the part. */
  mediaMs: number;
  /** Interview clock (transcript `start_ms`) at that media time. */
  sessionMs: number;
}

export interface RecordingPart {
  partId: string;
  kind: "camera" | "screen";
  ordinal: number;
  status: RecordingStatus;
  /** Media time 0 on the server clock. */
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  mediaId: string | null;
  contentType: string | null;
  sizeBytes: number;
  hasVideo: boolean;
  hasAudio: boolean;
  audioOnly: boolean;
  /** Remuxed with an index: seeking works everywhere. */
  seekable: boolean;
  version: number | null;
  segments: number;
  pendingSegments: number;
  gaps: number[];
  anchors: RecordingAnchor[];
  retentionUntil: string | null;
  error: string | null;
}

export interface SessionRecordings {
  sessionId: string;
  status: string;
  sessionStartedAt: string | null;
  sessionEndedAt: string | null;
  policy: { recordVideo: boolean; recordScreen: boolean; videoDays: number; consented: boolean };
  canDownload: boolean;
  processing: boolean;
  parts: RecordingPart[];
}

export interface RecordingSource {
  url: string;
  expiresAt: string;
  contentType: string;
}

const STATUSES: readonly RecordingStatus[] = ["ready", "recording", "processing", "failed"];

const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

function normalizeAnchor(raw: unknown): RecordingAnchor | null {
  if (Array.isArray(raw) && raw.length === 2) {
    const [mediaMs, sessionMs] = raw.map(num);
    return mediaMs !== null && mediaMs !== undefined && sessionMs !== null && sessionMs !== undefined ? { mediaMs, sessionMs } : null;
  }
  if (!isRecord(raw)) return null;
  const mediaMs = num(raw.media_ms);
  const sessionMs = num(raw.session_ms);
  return mediaMs !== null && sessionMs !== null ? { mediaMs, sessionMs } : null;
}

export function normalizePart(raw: unknown): RecordingPart | null {
  if (!isRecord(raw)) return null;
  const partId = str(raw.part_id);
  if (!partId) return null;
  const status = STATUSES.includes(raw.status as RecordingStatus) ? (raw.status as RecordingStatus) : "processing";
  const anchors = (Array.isArray(raw.anchors) ? raw.anchors : []).map(normalizeAnchor).filter((a): a is RecordingAnchor => a !== null);
  anchors.sort((a, b) => a.mediaMs - b.mediaMs);
  return {
    partId,
    kind: raw.kind === "screen" ? "screen" : "camera",
    ordinal: num(raw.ordinal) ?? 1,
    status,
    startedAt: str(raw.started_at),
    endedAt: str(raw.ended_at),
    durationMs: num(raw.duration_ms),
    mediaId: str(raw.media_id),
    contentType: str(raw.content_type),
    sizeBytes: num(raw.size_bytes) ?? 0,
    hasVideo: raw.has_video !== false,
    hasAudio: raw.has_audio !== false,
    audioOnly: raw.audio_only === true,
    seekable: raw.seekable === true,
    version: num(raw.version),
    segments: num(raw.segments) ?? 0,
    pendingSegments: num(raw.pending_segments) ?? 0,
    gaps: (Array.isArray(raw.gaps) ? raw.gaps : []).filter((g): g is number => typeof g === "number"),
    anchors,
    retentionUntil: str(raw.retention_until),
    error: str(raw.error),
  };
}

export function normalizeRecordings(raw: unknown, sessionId = ""): SessionRecordings {
  const body = isRecord(raw) ? raw : {};
  const policy = isRecord(body.policy) ? body.policy : {};
  const parts = (Array.isArray(body.parts) ? body.parts : []).map(normalizePart).filter((p): p is RecordingPart => p !== null);
  return {
    sessionId: str(body.session_id) ?? sessionId,
    status: str(body.status) ?? "",
    sessionStartedAt: str(body.session_started_at),
    sessionEndedAt: str(body.session_ended_at),
    policy: {
      recordVideo: policy.record_video === true,
      recordScreen: policy.record_screen === true,
      videoDays: num(policy.video_days) ?? 0,
      consented: policy.consented === true,
    },
    canDownload: body.can_download === true,
    processing: body.processing === true || parts.some((p) => p.status === "recording" || p.status === "processing"),
    parts,
  };
}

export const recordingsKey = (sessionId: string) => [...queryKeys.sessions.detail(sessionId), "recordings"] as const;

export function useSessionRecordings(sessionId: string, { enabled = true, live = false }: { enabled?: boolean; live?: boolean } = {}) {
  return useQuery({
    queryKey: recordingsKey(sessionId),
    queryFn: async ({ signal }) => normalizeRecordings(await api.get<unknown>(`/sessions/${sessionId}/recordings`, { signal }), sessionId),
    enabled: !!sessionId && enabled,
    // Poll while a part is still being recorded or processed (usually well under a minute).
    refetchInterval: (query) => (live || query.state.data?.processing ? 5_000 : false),
  });
}

/** Re-sign this long before the URL expires (the player swaps URLs without losing its place). */
const RESIGN_MARGIN_MS = 60_000;

export function useRecordingSource(sessionId: string, partId: string | null, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: [...recordingsKey(sessionId), partId ?? "", "url"] as const,
    queryFn: async ({ signal }) => {
      const raw = await api.get<unknown>(`/sessions/${sessionId}/recordings/${encodeURIComponent(partId ?? "")}/url`, { signal });
      const body = isRecord(raw) ? raw : {};
      return { url: str(body.url) ?? "", expiresAt: str(body.expires_at) ?? "", contentType: str(body.content_type) ?? "video/webm" } satisfies RecordingSource;
    },
    enabled: !!sessionId && !!partId && enabled,
    staleTime: Infinity,
    gcTime: 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const expires = Date.parse(query.state.data?.expiresAt ?? "");
      return Number.isFinite(expires) ? Math.max(15_000, expires - Date.now() - RESIGN_MARGIN_MS) : false;
    },
    meta: { errorToast: false },
  });
}

export function useFinalizeRecordings(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const raw = await api.post<unknown>(`/sessions/${sessionId}/recordings/finalize`);
      const queued = isRecord(raw) && Array.isArray(raw.queued) ? raw.queued.filter((q): q is string => typeof q === "string") : [];
      return { queued };
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: recordingsKey(sessionId) }),
  });
}

/** Admin-only signed download URL (audited by the API). */
export async function requestRecordingDownload(sessionId: string, partId: string): Promise<{ url: string; filename: string | null }> {
  const raw = await api.get<unknown>(`/sessions/${sessionId}/recordings/${encodeURIComponent(partId)}/download`);
  const body = isRecord(raw) ? raw : {};
  return { url: str(body.url) ?? "", filename: str(body.filename) };
}
