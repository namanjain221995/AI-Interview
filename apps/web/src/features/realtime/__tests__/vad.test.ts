import { describe, expect, it } from "vitest";

import { MicFramer } from "../audioCapture";
import { estimateReadingMs, splitSentences } from "../speechFallback";
import { EnergyVad } from "../vad";

const dbToRms = (db: number) => Math.pow(10, db / 20);

describe("EnergyVad", () => {
  it("stays silent on a quiet room", () => {
    const vad = new EnergyVad();
    for (let i = 0; i < 100; i++) expect(vad.push(dbToRms(-62)).speaking).toBe(false);
  });

  it("detects speech after a few frames and releases after the hangover", () => {
    const vad = new EnergyVad({ startFrames: 3, hangMs: 200 });
    for (let i = 0; i < 20; i++) vad.push(dbToRms(-60));
    expect(vad.push(dbToRms(-25)).speaking).toBe(false);
    expect(vad.push(dbToRms(-25)).speaking).toBe(false);
    const third = vad.push(dbToRms(-25));
    expect(third).toMatchObject({ speaking: true, changed: true });
    // Short pauses inside speech keep the state.
    for (let i = 0; i < 9; i++) expect(vad.push(dbToRms(-60)).speaking).toBe(true);
    const off = vad.push(dbToRms(-60));
    expect(off).toMatchObject({ speaking: false, changed: true });
  });

  it("adapts to steady background noise", () => {
    const vad = new EnergyVad({ floorRiseDbPerSec: 20 });
    // Fan noise at -40 dBFS: initially loud enough to count, then absorbed into the floor.
    let speakingFrames = 0;
    for (let i = 0; i < 400; i++) if (vad.push(dbToRms(-40)).speaking) speakingFrames++;
    expect(vad.speaking).toBe(false);
    expect(vad.noiseFloorDb).toBeGreaterThan(-45);
    expect(speakingFrames).toBeLessThan(400);
    // Speech well above the noise is still detected.
    let detected = false;
    for (let i = 0; i < 5; i++) detected = vad.push(dbToRms(-15)).speaking || detected;
    expect(detected).toBe(true);
  });

  it("reports a 0-1 level", () => {
    const vad = new EnergyVad();
    expect(vad.push(0).level).toBe(0);
    expect(vad.push(1).level).toBe(1);
  });
});

describe("MicFramer", () => {
  it("numbers each 100 ms binary frame", () => {
    const framer = new MicFramer();
    const out = [];
    for (let i = 0; i < 10; i++) out.push(...framer.push(new Int16Array(320).fill(i)));
    expect(out.map((f) => f.seq)).toEqual([1, 2]);
    const view = new DataView(out[1]!.data);
    expect(view.getUint8(0)).toBe(1);
    expect(view.getUint32(4, true)).toBe(2);
    expect(out[1]!.data.byteLength).toBe(8 + 1600 * 2);
    expect(new Int16Array(out[1]!.data, 8)[0]).toBe(5);
  });
});

describe("speech fallback helpers", () => {
  it("splits sentences and estimates reading time", () => {
    expect(splitSentences("Thanks. What was the baseline? And after the change!")).toEqual(["Thanks.", "What was the baseline?", "And after the change!"]);
    expect(splitSentences("No punctuation here")).toEqual(["No punctuation here"]);
    expect(estimateReadingMs("short")).toBe(1500);
    expect(estimateReadingMs(Array(170).fill("word").join(" "))).toBe(60_000);
  });
});
