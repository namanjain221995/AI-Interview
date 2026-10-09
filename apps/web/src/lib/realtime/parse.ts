/**
 * Runtime guards for server -> client messages. The server is trusted, but a
 * malformed or newer-protocol message must never crash the interview room:
 * unknown types are ignored, required fields are checked for the messages the
 * UI acts on.
 */
import type { ServerMessage } from "./protocol";

const SERVER_TYPES = new Set<ServerMessage["type"]>([
  "avatar.behavior",
  "session.state",
  "turn.state",
  "transcript.partial",
  "transcript.final",
  "ai.utterance.start",
  "ai.text.delta",
  "tts.chunk",
  "tts.fallback",
  "ai.utterance.end",
  "barge_in",
  "coding.start",
  "coding.run_result",
  "coding.hint",
  "coding.end",
  "integrity.warning",
  "notice",
  "error",
  "session.ended",
  "heartbeat.ack",
  // Intake activities (non-replayable; INTAKE_INTERVIEW.md §7.1).
  "activity.start",
  "activity.phase",
  "activity.end",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const REQUIRED: Partial<Record<ServerMessage["type"], string[]>> = {
  "session.state": ["session_id", "status", "turn_state"],
  "turn.state": ["state"],
  "transcript.partial": ["segment_id", "text"],
  "transcript.final": ["segment_id", "text"],
  "ai.utterance.start": ["utterance_id"],
  "ai.text.delta": ["utterance_id", "text"],
  "tts.chunk": ["utterance_id", "chunk_id", "duration_ms"],
  "tts.fallback": ["utterance_id", "text"],
  "ai.utterance.end": ["utterance_id"],
  barge_in: ["utterance_id"],
  "coding.start": ["coding"],
  "coding.run_result": ["coding_session_id", "status"],
  "coding.hint": ["coding_session_id", "level", "text"],
  "coding.end": ["coding_session_id"],
  "integrity.warning": ["message"],
  notice: ["code", "message"],
  error: ["code", "message"],
  "session.ended": ["reason"],
  "heartbeat.ack": ["t"],
  "activity.start": ["activity_id", "kind", "phases"],
  "activity.phase": ["activity_id", "phase", "status", "remaining_ms"],
  "activity.end": ["activity_id"],
};

/** Parse a text frame; returns null for anything the client does not understand. */
export function parseServerMessage(raw: string): ServerMessage | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  return asServerMessage(value);
}

export function asServerMessage(value: unknown): ServerMessage | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  const type = value.type as ServerMessage["type"];
  if (!SERVER_TYPES.has(type)) return null;
  for (const key of REQUIRED[type] ?? []) {
    if (value[key] === undefined || value[key] === null) return null;
  }
  if (typeof value.seq !== "number" || !Number.isFinite(value.seq)) value.seq = 0;
  return value as unknown as ServerMessage;
}
