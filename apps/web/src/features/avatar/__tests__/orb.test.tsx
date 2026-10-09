import { cleanup, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { AvatarState } from "@/lib/realtime/protocol";
import { AvatarStage } from "../AvatarStage";
import { createAvatarController, getAvatarInternals } from "../controller";
import type { AvatarFrame } from "../engine";
import { DEFAULT_PRESENCE, preloadAvatar, resolveAvatarModel } from "../index";
import { drawOrb } from "../orb/Orb2D";
import { BREATH_DEPTH, OrbDriver, RIPPLE_LIFE, VOICE_SILENCE, voiceTarget } from "../orb/orbSignals";
import { DEFAULT_ORB_PALETTE, ORB_PALETTE_SPECS, ORB_PALETTES, orbModelUrl, parseOrbModel } from "../orb/palettes";

// WebGL availability is switchable; the 3D orb is replaced by a stand-in.
const webgl = vi.hoisted(() => ({ available: true }));
vi.mock("../render/environment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../render/environment")>();
  return { ...actual, isWebGLAvailable: () => webgl.available };
});
vi.mock("../orb/Orb3D", () => ({ default: () => <div data-testid="orb-3d" /> }));

// jsdom has no canvas backend: the 2D orb then renders nothing (quietly).
const originalGetContext = HTMLCanvasElement.prototype.getContext;
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof originalGetContext;
});
afterAll(() => {
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

afterEach(() => {
  cleanup();
  webgl.available = true;
});

interface FrameOptions {
  now: number;
  dt?: number;
  state?: AvatarState;
  expression?: AvatarFrame["behavior"]["expression"];
  amplitude?: number;
  speaking?: boolean;
  mouthOpen?: number;
  lipsPressed?: number;
  listen?: { speaking: boolean; level: number };
  motionScale?: number;
  headGesture?: { id: string; serial: number } | null;
}

/** A minimal engine frame for the driver. */
function frame(o: FrameOptions): AvatarFrame {
  return {
    now: o.now,
    dt: o.dt ?? 1 / 60,
    behavior: {
      state: o.state ?? "IDLE",
      stateSince: 0,
      base: { pose: "idle_neutral", serial: 1, fade: 0, since: 0 },
      gesture: null,
      headGesture: o.headGesture ? ({ ...o.headGesture, intensity: 1, speed: 1, startedAt: o.now, duration: 1 } as AvatarFrame["behavior"]["headGesture"]) : null,
      cancelSerial: 0,
      expression: o.expression ?? "neutral",
      expressionIntensity: 1,
      tone: "professional",
      look: "camera",
      lean: 0,
      pending: null,
    },
    listen: o.listen ?? { speaking: false, level: 0 },
    gaze: { eyeYaw: 0, eyePitch: 0, headYaw: 0, headPitch: 0, bigShift: false },
    face: {},
    blink: 0,
    // The tests drive the raw loudness (level); the envelope is not used by the orb.
    speech: { activity: o.speaking ? 0.5 : 0, amplitude: o.amplitude ?? 0, level: o.amplitude ?? 0, speaking: o.speaking ?? false, clock: o.now },
    motionScale: o.motionScale ?? 1,
    mouthOpen: o.mouthOpen ?? 0,
    mouthWide: 0,
    mouthRound: 0,
    lipsPressed: o.lipsPressed ?? 0,
    smile: 0,
    browRaise: 0,
  } as AvatarFrame;
}

/** Run the driver for `seconds` at 60 fps with a constant frame spec. */
function run(driver: OrbDriver, start: number, seconds: number, spec: Omit<FrameOptions, "now">, analyser = true, dimmed = false) {
  let t = start;
  const steps = Math.round(seconds * 60);
  for (let i = 0; i < steps; i++) {
    t += 1 / 60;
    driver.update(frame({ ...spec, now: t }), analyser, dimmed);
  }
  return t;
}

describe("presence palettes", () => {
  it("parses orb:<palette> and falls back to aurora", () => {
    expect(parseOrbModel("orb:sunrise")).toBe("sunrise");
    expect(parseOrbModel(" orb:ocean ")).toBe("ocean");
    expect(parseOrbModel("orb:neon")).toBe(DEFAULT_ORB_PALETTE);
    expect(parseOrbModel("/avatars/interviewer.glb")).toBe("aurora");
    expect(parseOrbModel(null)).toBe("aurora");
    for (const p of ORB_PALETTES) {
      expect(parseOrbModel(orbModelUrl(p))).toBe(p);
      expect(ORB_PALETTE_SPECS[p].flow).toHaveLength(4);
    }
  });

  it("maps organisation settings to the controller config", () => {
    expect(resolveAvatarModel("orb", "orb:ocean")).toEqual({ modelFormat: "orb", modelUrl: "orb:ocean" });
    expect(resolveAvatarModel(undefined, undefined)).toEqual(DEFAULT_PRESENCE);
    expect(resolveAvatarModel("cartoon", "cartoon:maya")).toEqual({ modelFormat: "orb", modelUrl: "orb:aurora" });
    expect(resolveAvatarModel("glb", "/avatars/custom.glb")).toEqual({ modelFormat: "glb", modelUrl: "/avatars/custom.glb" });
    expect(resolveAvatarModel("glb", "https://cdn.example.com/a.glb")).toEqual({ modelFormat: "glb", modelUrl: "/avatars/interviewer.glb" });
    expect(resolveAvatarModel("vrm", "//evil.example.com/a.vrm").modelUrl).toBe("/avatars/interviewer.glb");
    expect(resolveAvatarModel("procedural", "")).toEqual({ modelFormat: "procedural", modelUrl: "procedural" });
  });

  it("does not preload anything for the orb", () => {
    preloadAvatar();
    preloadAvatar("orb:sunrise");
    expect(document.head.querySelector('link[rel="preload"]')).toBeNull();
  });
});

describe("OrbDriver", () => {
  it("gives each state its own look, eased rather than cut", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 2, { state: "IDLE" });
    const idleScale = d.signals.scale;
    t = run(d, t, 2, { state: "LISTENING" });
    expect(d.signals.warmth).toBeLessThan(-0.3);
    t = run(d, t, 2.5, { state: "THINKING" });
    expect(d.signals.violet).toBeGreaterThan(0.6);
    expect(d.signals.swirl).toBeGreaterThan(0.7);
    expect(d.signals.shimmer).toBeGreaterThan(0.4);
    // One frame after entering SPEAKING the look has only begun to change.
    d.update(frame({ now: t + 1 / 60, state: "SPEAKING" }), true);
    expect(d.signals.violet).toBeGreaterThan(0.5);
    t = run(d, t + 1 / 60, 2.5, { state: "SPEAKING" });
    expect(d.signals.warmth).toBeGreaterThan(0.25);
    // Interruptions dim and contract quickly.
    t = run(d, t, 0.4, { state: "INTERRUPTED" });
    expect(d.signals.brightness).toBeLessThan(0.85);
    expect(d.signals.scale).toBeLessThan(idleScale);
  });

  it("follows the voice with a ~25 ms attack and a ~140 ms release; pauses settle within ~200 ms", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 0.5, { state: "SPEAKING", speaking: true, amplitude: 0 });
    // Attack: one time constant (25 ms) covers ~63 % of the step, 60 ms over 90 %.
    const target = voiceTarget(frame({ now: 0, speaking: true, amplitude: 1, mouthOpen: 0.5 }), true);
    t = run(d, t, 0.025, { state: "SPEAKING", speaking: true, amplitude: 1, mouthOpen: 0.5 });
    expect(d.signals.voice).toBeGreaterThan(0.55 * target);
    expect(d.signals.voice).toBeLessThan(0.75 * target);
    t = run(d, t, 0.035, { state: "SPEAKING", speaking: true, amplitude: 1, mouthOpen: 0.5 });
    expect(d.signals.voice).toBeGreaterThan(0.9 * target);
    // Release inside speech (a softer syllable follows): ~63 % of the drop in ~143 ms.
    const peak = d.signals.voice;
    const soft = voiceTarget(frame({ now: 0, speaking: true, amplitude: 0.1 }), true);
    t = run(d, t, 0.143, { state: "SPEAKING", speaking: true, amplitude: 0.1 });
    const dropped = (peak - d.signals.voice) / (peak - soft);
    expect(dropped).toBeGreaterThan(0.55);
    expect(dropped).toBeLessThan(0.72);
    // A pause (silence): settled within ~200 ms.
    run(d, t, 0.2, { state: "SPEAKING", speaking: true, amplitude: 0 });
    expect(d.signals.voice).toBeLessThan(0.1 * peak);
  });

  it("dips with the barge-in duck: the analyser reads after the duck gain", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 0.6, { state: "SPEAKING", speaking: true, amplitude: 0.8, mouthOpen: 0.5 });
    const full = d.signals.voice;
    // The duck drops the heard loudness to ~35 %: the pulses shrink with the ~140 ms release.
    t = run(d, t, 0.15, { state: "SPEAKING", speaking: true, amplitude: 0.8 * 0.35, mouthOpen: 0.5 });
    expect(d.signals.voice).toBeLessThan(full * 0.62);
    t = run(d, t, 0.25, { state: "SPEAKING", speaking: true, amplitude: 0.8 * 0.35, mouthOpen: 0.5 });
    expect(d.signals.voice).toBeLessThan(full * 0.42);
    run(d, t, 0.25, { state: "INTERRUPTED", speaking: false, amplitude: 0 });
    expect(d.signals.voice).toBeLessThan(0.02);
  });

  it("falls back to the visemes when the voice never reaches the analyser (browser speech)", () => {
    const d = new OrbDriver();
    // Speaking with visemes but a silent analyser: after a moment the visemes drive the pulses.
    let t = run(d, 0, 1.2, { state: "SPEAKING", speaking: true, amplitude: 0, mouthOpen: 0.7 });
    expect(d.signals.voice).toBeGreaterThan(0.4);
    t = run(d, t, 0.3, { state: "SPEAKING", speaking: true, amplitude: 0, lipsPressed: 1 });
    expect(d.signals.voice).toBeLessThan(0.1);
    // Real audio again: loudness leads at once.
    run(d, t, 0.1, { state: "SPEAKING", speaking: true, amplitude: 0.9, mouthOpen: 0.6 });
    expect(d.signals.voice).toBeGreaterThan(0.6);
  });

  it("articulates: open vowels push bigger pulses, lip closures dip", () => {
    const open = voiceTarget(frame({ now: 0, speaking: true, amplitude: 0.5, mouthOpen: 1 }), true);
    const closed = voiceTarget(frame({ now: 0, speaking: true, amplitude: 0.5, lipsPressed: 1 }), true);
    expect(open).toBeGreaterThan(closed + 0.4);
    // Without an analyser (browser speech fallback) the visemes alone drive it.
    expect(voiceTarget(frame({ now: 0, speaking: true, mouthOpen: 0.8 }), false)).toBeGreaterThan(0.5);
    expect(voiceTarget(frame({ now: 0, speaking: false, mouthOpen: 0.8 }), false)).toBe(0);
    // Silence (no speech, no loudness) is zero even with an analyser.
    expect(voiceTarget(frame({ now: 0, speaking: false, amplitude: 0 }), true)).toBe(0);
    expect(voiceTarget(frame({ now: 0, speaking: true, amplitude: VOICE_SILENCE * 0.5, mouthOpen: 1 }), true)).toBe(0);
  });

  it("emits ripple rings on stressed syllables and lets them fade", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 0.6, { state: "SPEAKING", speaking: true, amplitude: 0.15 });
    expect(d.signals.ripples).toHaveLength(0);
    t = run(d, t, 0.1, { state: "SPEAKING", speaking: true, amplitude: 1, mouthOpen: 1 });
    expect(d.signals.ripples.length).toBe(1);
    expect(d.signals.ripples[0]!.strength).toBeGreaterThan(0.3);
    run(d, t, RIPPLE_LIFE + 0.2, { state: "SPEAKING", speaking: true, amplitude: 0.15 });
    expect(d.signals.ripples).toHaveLength(0);
  });

  it("ripples gently with the candidate's microphone while listening", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 1, { state: "LISTENING" });
    const quiet = d.signals.ripple;
    t = run(d, t, 0.5, { state: "LISTENING", listen: { speaking: true, level: 0.5 } });
    expect(d.signals.listen).toBeGreaterThan(0.6);
    expect(d.signals.ripple).toBeGreaterThan(quiet);
    run(d, t, 1, { state: "LISTENING", listen: { speaking: false, level: 0 } });
    expect(d.signals.listen).toBeLessThan(0.05);
  });

  it("nods with a soft bounce", () => {
    const d = new OrbDriver();
    let t = run(d, 0, 1, { state: "ACKNOWLEDGING" });
    d.update(frame({ now: t + 1 / 60, state: "ACKNOWLEDGING", headGesture: { id: "nod_small", serial: 5 } }), true);
    t = run(d, t + 1 / 60, 0.2, { state: "ACKNOWLEDGING", headGesture: { id: "nod_small", serial: 5 } });
    expect(d.signals.lift).toBeLessThan(-0.02);
    run(d, t, 0.8, { state: "ACKNOWLEDGING", headGesture: { id: "nod_small", serial: 5 } });
    expect(Math.abs(d.signals.lift)).toBeLessThan(0.002);
  });

  it("keeps only brightness changes under reduced motion", () => {
    const d = new OrbDriver({ reducedMotion: true });
    let t = run(d, 0, 0.6, { state: "SPEAKING", speaking: true, amplitude: 0.15, motionScale: 0.35 });
    t = run(d, t, 0.2, { state: "SPEAKING", speaking: true, amplitude: 1, mouthOpen: 1, motionScale: 0.35 });
    expect(d.signals.displace).toBe(0);
    expect(d.signals.scale).toBe(1);
    expect(d.signals.lift).toBe(0);
    expect(d.signals.ripples).toHaveLength(0);
    expect(d.signals.brightness).toBeGreaterThan(1.05);
    // Breathing stays: full motion breathes in scale by about +-2 %.
    const full = new OrbDriver();
    let maxScale = 0;
    let minScale = 2;
    let u = 0;
    for (let i = 0; i < 600; i++) {
      u += 1 / 60;
      full.update(frame({ now: u, state: "IDLE" }), true);
      if (u > 2) {
        maxScale = Math.max(maxScale, full.signals.scale);
        minScale = Math.min(minScale, full.signals.scale);
      }
    }
    expect(maxScale - minScale).toBeGreaterThan(BREATH_DEPTH * 1.5);
    expect(maxScale - minScale).toBeLessThan(BREATH_DEPTH * 2.5);
  });

  it("desaturates and slows down while paused or offline", () => {
    const d = new OrbDriver();
    const t = run(d, 0, 1.5, { state: "IDLE" });
    const lively = { saturation: d.signals.saturation, brightness: d.signals.brightness };
    run(d, t, 3, { state: "IDLE" }, true, true);
    expect(d.signals.saturation).toBeLessThan(lively.saturation * 0.5);
    expect(d.signals.brightness).toBeLessThan(lively.brightness);
  });
});

describe("engine listen frame", () => {
  it("carries the candidate's microphone level", () => {
    let t = 10;
    const c = createAvatarController({ modelUrl: "orb:aurora", modelFormat: "orb", interviewerName: "Maya" }, { wallClock: () => t });
    const engine = getAvatarInternals(c)!.engine;
    c.setCandidateSpeaking(true, 0.42);
    t += 0.02;
    expect(engine.update().listen).toEqual({ speaking: true, level: 0.42 });
    c.setCandidateSpeaking(false, 0.9);
    t += 0.02;
    expect(engine.update().listen).toEqual({ speaking: false, level: 0 });
    c.dispose();
  });
});

describe("presence stage selection", () => {
  const orbConfig = { modelUrl: "orb:sunrise", modelFormat: "orb" as const, interviewerName: "Maya" };

  it("renders the 3D orb when WebGL is available", async () => {
    const c = createAvatarController(orbConfig);
    const { container } = render(<AvatarStage controller={c} />);
    expect(await screen.findByTestId("orb-3d")).toBeTruthy();
    expect(container.querySelector("[data-avatar-mode]")!.getAttribute("data-avatar-mode")).toBe("3d");
    c.dispose();
  });

  it("falls back to the 2D orb without WebGL or when 2D is requested", () => {
    webgl.available = false;
    const c = createAvatarController(orbConfig);
    const { container } = render(<AvatarStage controller={c} />);
    expect(container.querySelector("[data-avatar-mode]")!.getAttribute("data-avatar-mode")).toBe("2d");
    expect(container.querySelector("canvas[data-orb-2d]")!.getAttribute("data-orb-2d")).toBe("sunrise");
    expect(c.getStatus()).toMatchObject({ mode: "2d", error: "webgl_unavailable" });
    cleanup();
    webgl.available = true;
    const c2 = createAvatarController({ ...orbConfig, renderMode: "2d" });
    const r2 = render(<AvatarStage controller={c2} />);
    expect(r2.container.querySelector("canvas[data-orb-2d]")).toBeTruthy();
    expect(c2.getStatus()).toMatchObject({ mode: "2d", loaded: true, modelName: "orb:sunrise" });
    c.dispose();
    c2.dispose();
  });

  it("draws the 2D orb with rings for each live ripple", () => {
    const calls: string[] = [];
    const gradient = { addColorStop: () => {} };
    const ctx = new Proxy(
      {},
      {
        get: (_t, prop: string) => {
          if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
          return (...args: unknown[]) => {
            calls.push(prop);
            void args;
          };
        },
        set: () => true,
      },
    ) as unknown as CanvasRenderingContext2D;
    const d = new OrbDriver();
    let t = run(d, 0, 0.6, { state: "SPEAKING", speaking: true, amplitude: 0.15 });
    t = run(d, t, 0.1, { state: "SPEAKING", speaking: true, amplitude: 1, mouthOpen: 1 });
    void t;
    drawOrb(ctx, 400, 300, ORB_PALETTE_SPECS.aurora, d.signals);
    expect(calls.filter((c) => c === "stroke")).toHaveLength(d.signals.ripples.length);
    expect(calls).toContain("clip");
  });
});
