import { describe, expect, it } from "vitest";

import type { IntegrityEventView } from "../../api/types";
import { normalizeRecordings, type RecordingPart } from "../../api/recordings";
import {
  activeCueIndex,
  integrityMarkers,
  locateSessionMs,
  mediaMsAtWall,
  mediaMsFor,
  sessionMsAt,
  syncedMediaMs,
  transcriptCues,
} from "../recording";
import type { TranscriptTurn } from "../transcript";

const START = Date.parse("2026-10-06T14:00:00Z");
const at = (seconds: number) => new Date(START + seconds * 1000).toISOString();

function part(over: Partial<RecordingPart> = {}): RecordingPart {
  return {
    partId: "a",
    kind: "camera",
    ordinal: 1,
    status: "ready",
    startedAt: at(0),
    endedAt: at(100),
    durationMs: 100_000,
    mediaId: "m1",
    contentType: "video/webm",
    sizeBytes: 1,
    hasVideo: true,
    hasAudio: true,
    audioOnly: false,
    seekable: true,
    version: 1,
    segments: 10,
    pendingSegments: 0,
    gaps: [],
    anchors: [],
    retentionUntil: null,
    error: null,
    ...over,
  };
}

function turn(id: string, startMs: number, speaker: TranscriptTurn["speaker"] = "candidate"): TranscriptTurn {
  return { id, speaker, text: id, segments: [], startMs, endMs: startMs + 1000, questionId: null, answerId: null, interrupted: false, partial: false, minConfidence: null, audioMediaId: null };
}

describe("recording clocks", () => {
  // The interview clock started 2 s after the recording; a 5 s disconnect paused it between 30 s and 40 s of media.
  const paused = part({
    anchors: [
      { mediaMs: 10_000, sessionMs: 8_000 },
      { mediaMs: 30_000, sessionMs: 28_000 },
      { mediaMs: 40_000, sessionMs: 33_000 },
      { mediaMs: 90_000, sessionMs: 83_000 },
    ],
  });

  it("maps media time to the interview clock through the nearest anchor", () => {
    expect(sessionMsAt(paused, 20_000, null)).toBe(18_000);
    expect(sessionMsAt(paused, 60_000, null)).toBe(53_000);
    expect(sessionMsAt(paused, 0, null)).toBe(0); // clamped
  });

  it("maps transcript times into the part, also after a pause", () => {
    expect(mediaMsFor(paused, 18_000, null)).toBe(20_000);
    expect(mediaMsFor(paused, 53_000, null)).toBe(60_000);
    expect(mediaMsFor(paused, 99_000, null)).toBeNull(); // after the part (media 106 s of 100 s)
  });

  it("falls back to wall time without anchors", () => {
    const plain = part({ startedAt: at(5) });
    // Session started at 0 s; the part 5 s later: interview 30 s = media 25 s.
    expect(mediaMsFor(plain, 30_000, at(0))).toBe(25_000);
    expect(sessionMsAt(plain, 25_000, at(0))).toBe(30_000);
    expect(mediaMsFor(plain, 30_000, null)).toBeNull();
  });

  it("places integrity observations by wall time as neutral markers", () => {
    const events = [
      { id: "e1", occurred_at: at(25), type: "tab_hidden" },
      { id: "e2", occurred_at: at(150), type: "tab_hidden" },
    ] as IntegrityEventView[];
    const markers = integrityMarkers(part(), events, () => "Tab hidden");
    expect(markers).toEqual([{ id: "e1", mediaMs: 25_000, leftPct: 25, label: "Tab hidden" }]);
    expect(mediaMsAtWall(part(), at(-5))).toBeNull();
  });

  it("puts each transcript line on the part that recorded it", () => {
    const first = part({ partId: "a", endedAt: at(50), durationMs: 50_000 });
    const second = part({ partId: "b", ordinal: 2, startedAt: at(60), endedAt: at(100), durationMs: 40_000 });
    const cues = transcriptCues([turn("t1", 10_000, "interviewer"), turn("t2", 55_000), turn("t3", 70_000), turn("sys", 1_000, "system")], [first, second], at(0));
    expect(cues.map((c) => [c.id, c.partId, c.mediaMs])).toEqual([
      ["t1", "a", 10_000],
      ["t2", null, null], // during the reload gap: not recorded
      ["t3", "b", 10_000],
    ]);
    expect(activeCueIndex(cues, "a", 12_000)).toBe(0);
    expect(activeCueIndex(cues, "b", 9_000)).toBe(-1);
    expect(activeCueIndex(cues, "b", 9_800)).toBe(2); // a moment of lead-in
    expect(locateSessionMs([first, { ...second, status: "processing" }], 70_000, at(0))).toBeNull();
  });

  it("keeps the shared screen in step on wall time", () => {
    const screen = part({ partId: "s", kind: "screen", startedAt: at(20), durationMs: 30_000 });
    expect(syncedMediaMs(part(), 25_000, screen)).toBe(5_000);
    expect(syncedMediaMs(part(), 60_000, screen)).toBeNull();
  });
});

describe("normalizeRecordings", () => {
  it("reads the API shape tolerantly", () => {
    const data = normalizeRecordings({
      session_id: "s1",
      status: "completed",
      session_started_at: at(0),
      policy: { record_video: true, record_screen: false, video_days: 30, consented: true },
      can_download: true,
      parts: [
        { part_id: "a", kind: "camera", ordinal: 1, status: "ready", anchors: [{ media_ms: 20, session_ms: 5 }, [10, 0]], seekable: true, media_id: "m" },
        { part_id: "b", kind: "screen", status: "weird", audio_only: true },
        { nothing: true },
      ],
    });
    expect(data.policy).toEqual({ recordVideo: true, recordScreen: false, videoDays: 30, consented: true });
    expect(data.canDownload).toBe(true);
    expect(data.parts.map((p) => [p.partId, p.kind, p.status])).toEqual([
      ["a", "camera", "ready"],
      ["b", "screen", "processing"],
    ]);
    expect(data.parts[0]!.anchors).toEqual([
      { mediaMs: 10, sessionMs: 0 },
      { mediaMs: 20, sessionMs: 5 },
    ]);
    expect(data.processing).toBe(true);
    expect(normalizeRecordings(null, "x")).toMatchObject({ sessionId: "x", parts: [], canDownload: false });
  });
});
