/**
 * Transcript shaping: consecutive segments from the same speaker for the same
 * answer become one turn, question markers are interleaved so reviewers can
 * jump to a question, and search works on whole turns.
 */

import type { SessionQuestion, TranscriptSpeaker } from "@/lib/types";

import type { TranscriptEntry } from "../api/types";

export interface TranscriptTurn {
  id: string;
  speaker: TranscriptSpeaker;
  text: string;
  segments: TranscriptEntry[];
  startMs: number | null;
  endMs: number | null;
  questionId: string | null;
  answerId: string | null;
  interrupted: boolean;
  partial: boolean;
  /** Lowest STT confidence among the turn's segments (candidate speech). */
  minConfidence: number | null;
  audioMediaId: string | null;
}

export type TranscriptBlock = { kind: "question"; id: string; question: SessionQuestion } | { kind: "turn"; id: string; turn: TranscriptTurn };

function compareSegments(a: TranscriptEntry, b: TranscriptEntry): number {
  if (a.seq !== b.seq) return a.seq - b.seq;
  return (a.start_ms ?? 0) - (b.start_ms ?? 0);
}

function groupKey(segment: TranscriptEntry): string {
  return `${segment.speaker}|${segment.answer_id ?? (segment.speaker === "interviewer" ? `q:${segment.question_id ?? ""}` : "")}`;
}

/**
 * Merges consecutive segments of one speaker that belong to the same answer
 * (candidate) or question (interviewer). Partial segments are dropped unless
 * requested (finals supersede them).
 */
export function buildTranscriptTurns(segments: readonly TranscriptEntry[], { includePartials = false }: { includePartials?: boolean } = {}): TranscriptTurn[] {
  const ordered = segments.filter((s) => includePartials || s.kind !== "partial").sort(compareSegments);
  const turns: TranscriptTurn[] = [];
  let current: TranscriptTurn | null = null;
  let currentKey = "";
  for (const segment of ordered) {
    const text = segment.text.trim();
    if (!text) continue;
    const key = groupKey(segment);
    if (current && key === currentKey && segment.speaker !== "system" && !current.interrupted) {
      current.segments.push(segment);
      current.text = `${current.text} ${text}`;
      current.endMs = segment.end_ms ?? current.endMs;
      current.interrupted = current.interrupted || segment.interrupted;
      current.partial = current.partial && segment.kind === "partial";
      current.questionId = current.questionId ?? segment.question_id;
      current.audioMediaId = current.audioMediaId ?? segment.audio_media_id ?? null;
      if (segment.confidence !== null && segment.confidence !== undefined) {
        current.minConfidence = current.minConfidence === null ? segment.confidence : Math.min(current.minConfidence, segment.confidence);
      }
      continue;
    }
    current = {
      id: segment.id,
      speaker: segment.speaker,
      text,
      segments: [segment],
      startMs: segment.start_ms,
      endMs: segment.end_ms,
      questionId: segment.question_id,
      answerId: segment.answer_id,
      interrupted: segment.interrupted,
      partial: segment.kind === "partial",
      minConfidence: segment.confidence ?? null,
      audioMediaId: segment.audio_media_id ?? null,
    };
    currentKey = key;
    turns.push(current);
  }
  return turns;
}

/**
 * Inserts a marker before the first turn of each question: by question id
 * when the transcript carries it, otherwise by time (asked at or before the
 * turn's start). Questions without any transcript turn are appended in place
 * by time so the index stays complete.
 */
export function interleaveQuestions(turns: readonly TranscriptTurn[], questions: readonly SessionQuestion[]): TranscriptBlock[] {
  const ordered = [...questions].sort((a, b) => a.sequence - b.sequence);
  const placed = new Set<string>();
  const blocks: TranscriptBlock[] = [];
  const byId = new Map(ordered.map((q) => [q.id, q]));

  const placeUpTo = (ms: number | null) => {
    if (ms === null) return;
    for (const question of ordered) {
      if (placed.has(question.id)) continue;
      if (question.elapsed_seconds * 1000 <= ms) {
        // Only place by time when no turn references this question explicitly.
        if (turns.some((t) => t.questionId === question.id)) continue;
        placed.add(question.id);
        blocks.push({ kind: "question", id: `question-${question.id}`, question });
      }
    }
  };

  for (const turn of turns) {
    if (turn.questionId && !placed.has(turn.questionId) && byId.has(turn.questionId)) {
      placeUpTo(turn.startMs);
      placed.add(turn.questionId);
      blocks.push({ kind: "question", id: `question-${turn.questionId}`, question: byId.get(turn.questionId)! });
    } else {
      placeUpTo(turn.startMs);
    }
    blocks.push({ kind: "turn", id: turn.id, turn });
  }
  for (const question of ordered) {
    if (!placed.has(question.id) && !turns.some((t) => t.questionId === question.id)) {
      blocks.push({ kind: "question", id: `question-${question.id}`, question });
      placed.add(question.id);
    }
  }
  return blocks;
}

function fold(text: string): string {
  return text.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Ids of turns containing the query (case- and accent-insensitive) and the total match count. */
export function searchTurns(turns: readonly TranscriptTurn[], query: string): { turnIds: string[]; count: number } {
  const needle = fold(query.trim());
  if (!needle) return { turnIds: [], count: 0 };
  const turnIds: string[] = [];
  let count = 0;
  for (const turn of turns) {
    const hay = fold(turn.text);
    let index = hay.indexOf(needle);
    if (index === -1) continue;
    turnIds.push(turn.id);
    while (index !== -1) {
      count += 1;
      index = hay.indexOf(needle, index + needle.length);
    }
  }
  return { turnIds, count };
}

/** Splits text into matching / non-matching parts for highlighting. */
export function highlightParts(text: string, query: string): Array<{ text: string; match: boolean }> {
  const needle = fold(query.trim());
  if (!needle) return [{ text, match: false }];
  const hay = fold(text);
  // NFKD folding can change length for some characters; fall back to no highlight when it does.
  if (hay.length !== text.length) return [{ text, match: hay.includes(needle) }];
  const parts: Array<{ text: string; match: boolean }> = [];
  let cursor = 0;
  let index = hay.indexOf(needle);
  while (index !== -1) {
    if (index > cursor) parts.push({ text: text.slice(cursor, index), match: false });
    parts.push({ text: text.slice(index, index + needle.length), match: true });
    cursor = index + needle.length;
    index = hay.indexOf(needle, cursor);
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false });
  return parts;
}

/** The turn being spoken at (or last started before) `ms`. */
export function turnAt(turns: readonly TranscriptTurn[], ms: number): TranscriptTurn | null {
  let best: TranscriptTurn | null = null;
  for (const turn of turns) {
    if (turn.startMs === null) continue;
    if (turn.startMs <= ms) best = turn;
    else break;
  }
  return best ?? turns[0] ?? null;
}

export const SPEAKER_LABELS: Record<TranscriptSpeaker, string> = {
  interviewer: "Interviewer",
  candidate: "Candidate",
  system: "System",
};
