import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { simulateFixture } from "../dev/lipSyncSim";
import { pcm16FromBytes, type TtsFixture, type TtsFixtureIndexEntry } from "../dev/ttsFixtures";
import { AvatarEngine } from "../engine";
import { AmplitudeFollower } from "../lipsync/amplitude";
import { audibleClock } from "../lipsync/audioClock";
import { LipSync, VISUAL_LEAD } from "../lipsync/lipSync";

const DIR = path.resolve(__dirname, "../dev/fixtures");
const INDEX = JSON.parse(readFileSync(path.join(DIR, "index.json"), "utf8")) as { fixtures: TtsFixtureIndexEntry[] };

function loadFixture(id: string): { fixture: TtsFixture; audio: Int16Array[] } {
  const fixture = JSON.parse(readFileSync(path.join(DIR, `${id}.json`), "utf8")) as TtsFixture;
  const audio = fixture.chunks.map((c) => pcm16FromBytes(readFileSync(path.join(DIR, c.audio))));
  return { fixture, audio };
}

describe("lip-sync against real Kokoro TTS utterances (room scheduling path)", () => {
  it("has the captured fixtures with audio matching the declared durations", () => {
    expect(INDEX.fixtures.length).toBeGreaterThanOrEqual(6);
    for (const entry of INDEX.fixtures) {
      const { fixture, audio } = loadFixture(entry.id);
      fixture.chunks.forEach((chunk, i) => {
        expect(Math.abs((audio[i]!.length / chunk.sample_rate) * 1000 - chunk.duration_ms)).toBeLessThanOrEqual(1);
        expect(chunk.visemes.length).toBeGreaterThan(3);
      });
    }
  });

  for (const entry of INDEX.fixtures) {
    it(`${entry.id}: seals every p/b/m, reaches every f/v, rests in pauses, no jitter, in sync`, () => {
      const { fixture, audio } = loadFixture(entry.id);
      const { report } = simulateFixture(fixture, audio);
      expect(report.closures.total).toBeGreaterThan(0);
      expect(report.closures.events.filter((e) => !e.ok)).toEqual([]);
      expect(report.labiodentals.events.filter((e) => !e.ok)).toEqual([]);
      expect(report.pauses.events.filter((p) => !p.ok)).toEqual([]);
      expect(report.jitter.microReversalsPerSecond).toBeLessThan(6);
      expect(report.jitter.p95Step).toBeLessThan(0.18);
      expect(report.sync.lagMs).not.toBeNull();
      expect(Math.abs(report.sync.lagMs!)).toBeLessThanOrEqual(34); // within two 60 fps frames
      expect(report.sync.correlation!).toBeGreaterThan(0.4);
    });
  }

  it("seals closures without an analyser too (they never depend on loudness)", () => {
    const { fixture, audio } = loadFixture("transition");
    const { report } = simulateFixture(fixture, audio, { analyser: false });
    expect(report.closures.sealed).toBe(report.closures.total);
  });

  it("closes the mouth through an underrun gap and resumes cleanly", () => {
    const { fixture, audio } = loadFixture("transition");
    // The last chunk arrives a second after the others have played out.
    const late = fixture.chunks.slice(0, -1).reduce((s, c) => s + c.duration_ms / 1000, 0) + 1;
    const { report, cues } = simulateFixture(fixture, audio, { arrivals: [0, 0.25, late] });
    const gap = cues[2]!.startTime - (cues[1]!.startTime + cues[1]!.durationMs / 1000);
    expect(gap).toBeGreaterThan(0.8);
    const gapPause = report.pauses.events.find((p) => p.durationMs >= 800);
    expect(gapPause?.ok).toBe(true);
    expect(report.closures.sealed).toBe(report.closures.total);
  });
});

describe("closure planning", () => {
  /** Sample a LipSync at 120 Hz from t0 to t1 on a fake clock, collecting frames. */
  function sweep(lip: LipSync, clock: { t: number }, t0: number, t1: number) {
    const frames: Array<{ t: number; PP: number; aa: number }> = [];
    for (let t = t0; t <= t1 + 1e-9; t += 1 / 120) {
      clock.t = t;
      const f = lip.sampleAt(t);
      frames.push({ t, PP: f.weights.PP, aa: f.weights.aa });
    }
    return frames;
  }

  it("turns a 25 ms bilabial between open vowels into a sealed closure", () => {
    const clock = { t: 0 };
    const lip = new LipSync(() => clock.t);
    lip.enqueue({
      utteranceId: "u",
      chunkId: 1,
      startTime: 1,
      durationMs: 500,
      visemes: [
        { t: 0, v: "aa", w: 0.9 },
        { t: 200, v: "PP", w: 1 },
        { t: 225, v: "aa", w: 0.9 },
        { t: 420, v: "sil", w: 1 },
      ],
      text: "aba",
    });
    const frames = sweep(lip, clock, 0.9, 1.5);
    const peak = frames.reduce((best, f) => (f.PP > best.PP ? f : best), frames[0]!);
    expect(peak.PP).toBeGreaterThan(0.9);
    expect(peak.aa).toBeLessThan(0.2); // the open shape yields while the lips are sealed
    // The closure is centred on the cue, not after it (anticipation + visual lead).
    expect(peak.t).toBeGreaterThan(1.17);
    expect(peak.t).toBeLessThan(1.23);
  });

  it("samples the timeline slightly ahead so the smoothed mouth lands on the sound", () => {
    expect(VISUAL_LEAD).toBeGreaterThan(0);
    expect(VISUAL_LEAD).toBeLessThanOrEqual(0.04);
  });

  it("keeps lip closures at full strength when the audio is quiet", () => {
    let t = 10;
    const engine = new AvatarEngine({ seed: 1, wallClock: () => t });
    const context = {
      get currentTime() {
        return t;
      },
    } as unknown as AudioContext;
    const silent = { fftSize: 1024, getFloatTimeDomainData: (b: Float32Array) => b.fill(0) } as unknown as AnalyserNode;
    engine.attachAudio(context, silent);
    engine.lipSync.enqueue({ utteranceId: "u", chunkId: 1, startTime: 10.05, durationMs: 400, visemes: [{ t: 0, v: "PP", w: 1 }, { t: 200, v: "aa", w: 1 }], text: "pa" });
    let pp = 0;
    let aa = 0;
    for (let i = 0; i < 40; i++) {
      t += 1 / 60;
      const f = engine.update();
      pp = Math.max(pp, f.face.viseme_PP ?? 0);
      aa = Math.max(aa, f.face.viseme_aa ?? 0);
    }
    expect(pp).toBeGreaterThan(0.9); // closures ignore loudness
    expect(aa).toBeLessThan(0.5); // open shapes follow it (quiet -> smaller)
  });
});

describe("audible clock", () => {
  it("uses getOutputTimestamp extrapolated to now", () => {
    const clock = audibleClock(
      { currentTime: 10.06, getOutputTimestamp: () => ({ contextTime: 10, performanceTime: 1000 }) },
      () => 1020,
    );
    expect(clock()).toBeCloseTo(10.02, 6);
  });

  it("falls back to outputLatency + baseLatency, and to currentTime when implausible", () => {
    expect(audibleClock({ currentTime: 5, outputLatency: 0.03, baseLatency: 0.01, getOutputTimestamp: () => ({ contextTime: 0, performanceTime: 0 }) })()).toBeCloseTo(4.96, 6);
    expect(audibleClock({ currentTime: 5, outputLatency: 2 })()).toBe(5);
    expect(audibleClock({ currentTime: 5 })()).toBe(5);
    // An estimate ahead of the scheduling clock is impossible: currentTime wins.
    expect(audibleClock({ currentTime: 5, getOutputTimestamp: () => ({ contextTime: 5.2, performanceTime: 1 }) }, () => 1)()).toBe(5);
  });

  it("reads the loudness envelope at the audible time, one output latency behind the analyser", () => {
    let loud = false;
    const analyser = { fftSize: 256, getFloatTimeDomainData: (b: Float32Array) => b.fill(loud ? 0.3 : 0) } as unknown as AnalyserNode;
    const follower = new AmplitudeFollower();
    follower.attach(analyser);
    const latency = 0.2; // a Bluetooth headset
    let heard = 0;
    let seen = 0;
    for (let i = 0; i < 60; i++) {
      const now = 1 + i / 60;
      loud = now >= 1.4; // the analyser sees speech start at 1.4 s
      const atNow = follower.sample(1 / 60, now, now);
      heard = follower.sample(0, now, now - latency);
      if (now < 1.55) seen = atNow;
      if (now >= 1.45 && now < 1.55) expect(heard).toBeLessThan(0.05); // not audible yet
    }
    expect(seen).toBeGreaterThan(0.5);
    expect(heard).toBeGreaterThan(0.5); // ...and heard 0.2 s later
  });

  it("never runs backwards on small timestamp corrections", () => {
    let contextTime = 10;
    const clock = audibleClock({ currentTime: 10.05, getOutputTimestamp: () => ({ contextTime, performanceTime: 1000 }) }, () => 1000);
    expect(clock()).toBeCloseTo(10, 6);
    contextTime = 9.99;
    expect(clock()).toBeCloseTo(10, 6);
  });
});
