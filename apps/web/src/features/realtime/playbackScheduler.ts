/**
 * Pure jitter-buffer scheduler for interviewer speech. It pairs `tts.chunk`
 * metadata with the binary 0x02 audio frame carrying the same chunk_id,
 * releases chunks strictly in `index` order per utterance, assigns each a
 * start time on the AudioContext clock (back to back, with a small lead after
 * an underrun), and derives playback events from the clock. It owns no audio
 * objects, so ordering, gaps and barge-in are unit-testable with a fake clock.
 */
import type { TtsChunkMsg, Viseme } from "@/lib/realtime/protocol";

export interface ChunkMeta {
  utteranceId: string;
  chunkId: number;
  index: number;
  text: string;
  sampleRate: number;
  durationMs: number;
  visemes: Viseme[];
}

export interface ScheduledChunk extends ChunkMeta {
  samples: Int16Array;
  /** AudioContext seconds. */
  startTime: number;
  endTime: number;
  /** Actual duration from the sample count (ms). */
  playMs: number;
  started: boolean;
}

export type PlaybackEvent =
  | { type: "chunk_start"; utteranceId: string; chunk: ScheduledChunk }
  | { type: "started"; utteranceId: string; playedMs: number; chunkId: number }
  | { type: "progress"; utteranceId: string; playedMs: number; chunkId: number }
  | { type: "finished"; utteranceId: string; playedMs: number; chunkId: number };

export interface InterruptResult {
  utteranceId: string;
  playedMs: number;
  chunkId: number | null;
  /** Chunks whose audio sources must be stopped. */
  chunks: ScheduledChunk[];
  hadAudio: boolean;
}

interface PendingAudio {
  meta: ChunkMeta;
  samples: Int16Array;
  receivedAt: number;
}

interface Track {
  id: string;
  metas: Map<number, ChunkMeta>;
  waiting: Map<number, PendingAudio>;
  nextIndex: number;
  scheduled: ScheduledChunk[];
  ended: boolean;
  finalIndex: number | null;
  startedReported: boolean;
  lastProgressAt: number;
}

export interface PlaybackSchedulerOptions {
  /** Delay before the first chunk (and after an underrun), seconds. */
  leadS?: number;
  progressIntervalS?: number;
  /** Give up waiting for a missing chunk index after this long, seconds. */
  skipMissingAfterS?: number;
}

export function metaFromMessage(msg: TtsChunkMsg): ChunkMeta {
  return {
    utteranceId: msg.utterance_id,
    chunkId: msg.chunk_id,
    index: msg.index ?? 0,
    text: msg.text ?? "",
    sampleRate: msg.sample_rate > 0 ? msg.sample_rate : 24000,
    durationMs: msg.duration_ms,
    visemes: Array.isArray(msg.visemes) ? msg.visemes : [],
  };
}

export class PlaybackScheduler {
  private readonly leadS: number;
  private readonly progressIntervalS: number;
  private readonly skipMissingAfterS: number;
  private tracks = new Map<string, Track>();
  private chunkOwner = new Map<number, string>();
  private cancelled = new Set<string>();
  private cursor = 0;

  constructor(options: PlaybackSchedulerOptions = {}) {
    this.leadS = options.leadS ?? 0.15;
    this.progressIntervalS = options.progressIntervalS ?? 1;
    this.skipMissingAfterS = options.skipMissingAfterS ?? 1.5;
  }

  private track(utteranceId: string): Track | null {
    if (this.cancelled.has(utteranceId)) return null;
    let track = this.tracks.get(utteranceId);
    if (!track) {
      track = {
        id: utteranceId,
        metas: new Map(),
        waiting: new Map(),
        nextIndex: 0,
        scheduled: [],
        ended: false,
        finalIndex: null,
        startedReported: false,
        lastProgressAt: 0,
      };
      this.tracks.set(utteranceId, track);
    }
    return track;
  }

  begin(utteranceId: string): void {
    this.track(utteranceId);
  }

  isCancelled(utteranceId: string): boolean {
    return this.cancelled.has(utteranceId);
  }

  hasAudio(utteranceId: string): boolean {
    const t = this.tracks.get(utteranceId);
    return Boolean(t && (t.scheduled.length > 0 || t.waiting.size > 0 || t.metas.size > 0));
  }

  addMeta(meta: ChunkMeta): void {
    const track = this.track(meta.utteranceId);
    if (!track) return;
    track.metas.set(meta.chunkId, meta);
    this.chunkOwner.set(meta.chunkId, meta.utteranceId);
  }

  /** Audio for a chunk arrived; returns chunks newly scheduled (in play order). */
  addAudio(chunkId: number, samples: Int16Array, final: boolean, now: number): ScheduledChunk[] {
    const owner = this.chunkOwner.get(chunkId);
    if (!owner) return [];
    this.chunkOwner.delete(chunkId);
    const track = this.track(owner);
    if (!track) return [];
    const meta = track.metas.get(chunkId);
    if (!meta) return [];
    track.metas.delete(chunkId);
    if (meta.index < track.nextIndex) return []; // late duplicate / skipped
    track.waiting.set(meta.index, { meta, samples, receivedAt: now });
    if (final) track.finalIndex = meta.index;
    return this.drain(track, now);
  }

  private drain(track: Track, now: number): ScheduledChunk[] {
    const out: ScheduledChunk[] = [];
    for (;;) {
      const next = track.waiting.get(track.nextIndex);
      if (!next) break;
      track.waiting.delete(track.nextIndex);
      track.nextIndex += 1;
      out.push(this.schedule(track, next, now));
    }
    return out;
  }

  private schedule(track: Track, pending: PendingAudio, now: number): ScheduledChunk {
    const { meta, samples } = pending;
    const seconds = samples.length / meta.sampleRate;
    const start = this.cursor < now + 0.02 ? now + this.leadS : this.cursor;
    const chunk: ScheduledChunk = {
      ...meta,
      samples,
      startTime: start,
      endTime: start + seconds,
      playMs: seconds * 1000,
      started: false,
    };
    this.cursor = chunk.endTime;
    track.scheduled.push(chunk);
    return chunk;
  }

  /** ai.utterance.end received: no more chunks will come for this utterance. */
  end(utteranceId: string): void {
    const track = this.tracks.get(utteranceId);
    if (track) track.ended = true;
  }

  playedMs(utteranceId: string, now: number): number {
    const track = this.tracks.get(utteranceId);
    if (!track) return 0;
    return Math.round(track.scheduled.reduce((sum, c) => sum + Math.max(0, Math.min(c.playMs, (now - c.startTime) * 1000)), 0));
  }

  private lastChunkId(track: Track, now: number): number | null {
    let id: number | null = null;
    for (const c of track.scheduled) if (c.startTime <= now) id = c.chunkId;
    return id ?? track.scheduled[0]?.chunkId ?? null;
  }

  /**
   * Stop an utterance (barge-in) or everything. Cancelled utterances ignore
   * any chunks still in flight.
   */
  interrupt(now: number, utteranceId?: string): InterruptResult[] {
    const ids = utteranceId ? [utteranceId] : [...this.tracks.keys()];
    const results: InterruptResult[] = [];
    for (const id of ids) {
      this.cancelled.add(id);
      const track = this.tracks.get(id);
      if (!track) continue;
      results.push({
        utteranceId: id,
        playedMs: this.playedMs(id, now),
        chunkId: this.lastChunkId(track, now),
        chunks: track.scheduled.filter((c) => c.endTime > now),
        hadAudio: track.scheduled.length > 0,
      });
      this.tracks.delete(id);
    }
    for (const [chunkId, owner] of this.chunkOwner) if (this.cancelled.has(owner)) this.chunkOwner.delete(chunkId);
    // Nothing left queued: the next utterance starts with a fresh lead.
    const latest = Math.max(now, ...[...this.tracks.values()].flatMap((t) => t.scheduled.map((c) => c.endTime)));
    this.cursor = this.tracks.size ? latest : now;
    if (this.cancelled.size > 64) this.cancelled = new Set([...this.cancelled].slice(-32));
    return results;
  }

  /** Is any audio audible at `now`? */
  isPlaying(now: number): boolean {
    for (const t of this.tracks.values()) for (const c of t.scheduled) if (c.startTime <= now && now < c.endTime) return true;
    return false;
  }

  /** Utterance whose audio is audible (or about to be). */
  activeUtterance(now: number): string | null {
    for (const t of this.tracks.values()) if (t.scheduled.some((c) => c.endTime > now)) return t.id;
    return null;
  }

  /** Advance the clock: returns playback events in order. */
  tick(now: number): PlaybackEvent[] {
    const events: PlaybackEvent[] = [];
    for (const track of [...this.tracks.values()]) {
      // A chunk index never arrived (e.g. dropped during a reconnect): skip ahead.
      if (track.waiting.size > 0 && !track.waiting.has(track.nextIndex)) {
        const oldest = Math.min(...[...track.waiting.values()].map((p) => p.receivedAt));
        if (now - oldest >= this.skipMissingAfterS) {
          track.nextIndex = Math.min(...track.waiting.keys());
          this.drain(track, now);
        }
      }
      for (const chunk of track.scheduled) {
        if (!chunk.started && now >= chunk.startTime) {
          chunk.started = true;
          if (!track.startedReported) {
            track.startedReported = true;
            track.lastProgressAt = now;
            events.push({ type: "started", utteranceId: track.id, playedMs: 0, chunkId: chunk.chunkId });
          }
          events.push({ type: "chunk_start", utteranceId: track.id, chunk });
        }
      }
      const last = track.scheduled[track.scheduled.length - 1];
      // Announced chunks whose audio never arrived stop blocking completion after a grace period.
      const complete =
        (track.ended || (track.finalIndex !== null && track.nextIndex > track.finalIndex)) &&
        track.waiting.size === 0 &&
        last !== undefined &&
        (track.metas.size === 0 || now >= last.endTime + this.skipMissingAfterS);
      if (complete && now >= last.endTime) {
        events.push({ type: "finished", utteranceId: track.id, playedMs: this.playedMs(track.id, now), chunkId: last.chunkId });
        this.tracks.delete(track.id);
        continue;
      }
      if (track.startedReported && now - track.lastProgressAt >= this.progressIntervalS && this.isTrackPlaying(track, now)) {
        track.lastProgressAt = now;
        events.push({ type: "progress", utteranceId: track.id, playedMs: this.playedMs(track.id, now), chunkId: this.lastChunkId(track, now) ?? 0 });
      }
    }
    return events;
  }

  private isTrackPlaying(track: Track, now: number): boolean {
    return track.scheduled.some((c) => c.startTime <= now && now < c.endTime);
  }

  reset(now = 0): void {
    this.tracks.clear();
    this.chunkOwner.clear();
    this.cursor = now;
  }
}
