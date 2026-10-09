import { describe, expect, it } from "vitest";
import { AvatarEngine } from "../engine";

describe("AvatarEngine face composition", () => {
  it("combines expression, blinks and visemes into bounded morph weights", () => {
    let t = 50;
    const engine = new AvatarEngine({ seed: 3, wallClock: () => t });
    engine.behavior.applyBehavior({ state: "LISTENING", expression: "pleased" }, t);
    let maxBlink = 0;
    let smileQuiet = 0;
    for (let i = 0; i < 60 * 8; i++) {
      t += 1 / 60;
      const f = engine.update();
      maxBlink = Math.max(maxBlink, f.face.eyeBlinkLeft ?? 0);
      smileQuiet = f.face.mouthSmileLeft ?? 0;
      for (const v of Object.values(f.face)) {
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThanOrEqual(1);
      }
    }
    expect(maxBlink).toBeGreaterThan(0.9); // at least one blink in 8 s
    expect(smileQuiet).toBeGreaterThan(0.4); // "pleased" preset settled

    engine.lipSync.enqueue({
      utteranceId: "u",
      chunkId: 0,
      startTime: t + 0.05,
      durationMs: 1200,
      visemes: [
        { t: 0, v: "aa", w: 1 },
        { t: 300, v: "O", w: 1 },
        { t: 600, v: "aa", w: 1 },
        { t: 900, v: "E", w: 1 },
      ],
      text: "demo",
    });
    let speakingFrames = 0;
    let maxMouth = 0;
    let smileSpeaking = 1;
    for (let i = 0; i < 60; i++) {
      t += 1 / 60;
      const f = engine.update();
      if (f.speech.speaking) speakingFrames++;
      maxMouth = Math.max(maxMouth, f.mouthOpen, f.face.viseme_aa ?? 0);
      if (i > 20) smileSpeaking = Math.min(smileSpeaking, f.face.mouthSmileLeft ?? 0);
    }
    expect(speakingFrames).toBeGreaterThan(40);
    expect(maxMouth).toBeGreaterThan(0.5);
    expect(smileSpeaking).toBeLessThan(smileQuiet); // smile yields to articulation
    expect(engine.behavior.getState()).toBe("SPEAKING"); // audio activity entered SPEAKING
  });
});
