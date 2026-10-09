import { describe, expect, it } from "vitest";

import { PlaybackScheduler, type ChunkMeta, type PlaybackEvent } from "../playbackScheduler";

const RATE = 24000;

function meta(utteranceId: string, chunkId: number, index: number, text = `chunk ${index}`): ChunkMeta {
  return { utteranceId, chunkId, index, text, sampleRate: RATE, durationMs: 500, visemes: [] };
}

/** `seconds` of PCM at 24 kHz. */
function pcm(seconds: number): Int16Array {
  return new Int16Array(Math.round(seconds * RATE));
}

function types(events: PlaybackEvent[]): string[] {
  return events.map((e) => e.type);
}

describe("PlaybackScheduler", () => {
  it("schedules chunks back to back after an initial jitter lead", () => {
    const s = new PlaybackScheduler({ leadS: 0.15 });
    s.begin("u1");
    s.addMeta(meta("u1", 10, 0));
    s.addMeta(meta("u1", 11, 1));
    const a = s.addAudio(10, pcm(0.5), false, 1.0);
    const b = s.addAudio(11, pcm(0.25), false, 1.1);
    expect(a[0]!.startTime).toBeCloseTo(1.15, 6);
    expect(a[0]!.endTime).toBeCloseTo(1.65, 6);
    expect(b[0]!.startTime).toBeCloseTo(1.65, 6);
    expect(b[0]!.playMs).toBeCloseTo(250, 3);
  });

  it("releases chunks strictly in index order even when audio arrives out of order", () => {
    const s = new PlaybackScheduler();
    s.addMeta(meta("u1", 1, 0));
    s.addMeta(meta("u1", 2, 1));
    s.addMeta(meta("u1", 3, 2));
    expect(s.addAudio(3, pcm(0.2), false, 0)).toEqual([]);
    expect(s.addAudio(2, pcm(0.2), false, 0)).toEqual([]);
    const released = s.addAudio(1, pcm(0.2), false, 0);
    expect(released.map((c) => c.index)).toEqual([0, 1, 2]);
    expect(released[1]!.startTime).toBeCloseTo(released[0]!.endTime, 9);
    expect(released[2]!.startTime).toBeCloseTo(released[1]!.endTime, 9);
  });

  it("drops audio without matching metadata and duplicates", () => {
    const s = new PlaybackScheduler();
    expect(s.addAudio(99, pcm(0.1), false, 0)).toEqual([]);
    s.addMeta(meta("u1", 1, 0));
    expect(s.addAudio(1, pcm(0.1), false, 0)).toHaveLength(1);
    expect(s.addAudio(1, pcm(0.1), false, 0)).toEqual([]);
  });

  it("restarts with a fresh lead after an underrun", () => {
    const s = new PlaybackScheduler({ leadS: 0.1 });
    s.addMeta(meta("u1", 1, 0));
    s.addMeta(meta("u1", 2, 1));
    s.addAudio(1, pcm(0.2), false, 0); // plays 0.1 - 0.3
    const late = s.addAudio(2, pcm(0.2), false, 0.9); // arrives after the first ended
    expect(late[0]!.startTime).toBeCloseTo(1.0, 6);
  });

  it("reports started, chunk starts, progress and finished from the clock", () => {
    const s = new PlaybackScheduler({ leadS: 0.1, progressIntervalS: 0.5 });
    s.begin("u1");
    s.addMeta(meta("u1", 1, 0, "Tell me about"));
    s.addMeta(meta("u1", 2, 1, "your last project."));
    s.addAudio(1, pcm(1), false, 0); // 0.1 - 1.1
    s.addAudio(2, pcm(1), true, 0); // 1.1 - 2.1, final
    expect(s.tick(0.05)).toEqual([]);
    expect(types(s.tick(0.1))).toEqual(["started", "chunk_start"]);
    expect(types(s.tick(0.7))).toEqual(["progress"]);
    const second = s.tick(1.25);
    expect(types(second)).toEqual(["chunk_start", "progress"]);
    expect(s.isPlaying(1.5)).toBe(true);
    const done = s.tick(2.2);
    expect(types(done)).toEqual(["finished"]);
    expect(done[0]).toMatchObject({ utteranceId: "u1", playedMs: 2000, chunkId: 2 });
    expect(s.tick(3)).toEqual([]);
  });

  it("finishes only after ai.utterance.end when no final flag is set", () => {
    const s = new PlaybackScheduler({ leadS: 0 });
    s.addMeta(meta("u1", 1, 0));
    s.addAudio(1, pcm(0.5), false, 0);
    expect(types(s.tick(1))).toEqual(["started", "chunk_start"]);
    expect(s.tick(1.1)).toEqual([]);
    s.end("u1");
    expect(types(s.tick(1.2))).toEqual(["finished"]);
  });

  it("barge-in stops the utterance, reports the played portion and ignores late chunks", () => {
    const s = new PlaybackScheduler({ leadS: 0 });
    s.addMeta(meta("u1", 1, 0));
    s.addMeta(meta("u1", 2, 1));
    s.addAudio(1, pcm(1), false, 0); // 0.02? lead 0 => starts at now
    s.addAudio(2, pcm(1), false, 0);
    s.tick(0.4);
    const [result] = s.interrupt(0.4, "u1");
    expect(result).toMatchObject({ utteranceId: "u1", hadAudio: true, chunkId: 1 });
    expect(result!.playedMs).toBe(400);
    expect(result!.chunks.map((c) => c.chunkId)).toEqual([1, 2]);
    // Chunks still in flight for the interrupted utterance are ignored.
    s.addMeta(meta("u1", 3, 2));
    expect(s.addAudio(3, pcm(0.5), false, 0.5)).toEqual([]);
    expect(s.isPlaying(0.6)).toBe(false);
    expect(s.tick(5)).toEqual([]);
    // The next utterance starts promptly instead of after the cancelled audio.
    s.addMeta(meta("u2", 4, 0));
    const next = s.addAudio(4, pcm(0.3), false, 0.6);
    expect(next[0]!.startTime).toBeCloseTo(0.6, 6);
  });

  it("plays consecutive utterances in sequence (acknowledgement, then question)", () => {
    const s = new PlaybackScheduler({ leadS: 0.1 });
    s.addMeta(meta("ack", 1, 0, "Okay."));
    s.addAudio(1, pcm(0.4), true, 0);
    s.addMeta(meta("q", 2, 0, "What did you measure?"));
    const q = s.addAudio(2, pcm(1), true, 0.05);
    expect(q[0]!.startTime).toBeCloseTo(0.5, 6);
    const events = [...s.tick(0.1), ...s.tick(0.5), ...s.tick(1.6)];
    expect(events.filter((e) => e.type === "finished").map((e) => e.utteranceId)).toEqual(["ack", "q"]);
  });

  it("skips a chunk index that never arrives", () => {
    const s = new PlaybackScheduler({ leadS: 0, skipMissingAfterS: 1 });
    s.addMeta(meta("u1", 1, 0));
    s.addMeta(meta("u1", 2, 1));
    s.addMeta(meta("u1", 3, 2));
    s.addAudio(1, pcm(0.2), false, 0);
    s.addAudio(3, pcm(0.2), true, 0.1); // index 1 lost
    expect(s.tick(0.5).some((e) => e.type === "chunk_start" && e.chunk.index === 2)).toBe(false);
    const events = s.tick(1.2);
    expect(events.some((e) => e.type === "chunk_start" && e.chunk.index === 2)).toBe(true);
  });

  it("computes played time across gaps", () => {
    const s = new PlaybackScheduler({ leadS: 0 });
    s.addMeta(meta("u1", 1, 0));
    s.addMeta(meta("u1", 2, 1));
    s.addAudio(1, pcm(0.5), false, 0);
    s.addAudio(2, pcm(0.5), false, 2); // gap 0.5 - 2.0
    expect(s.playedMs("u1", 1.5)).toBe(500);
    expect(s.playedMs("u1", 2.25)).toBe(750);
  });
});
