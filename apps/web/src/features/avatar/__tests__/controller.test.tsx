import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render } from "@testing-library/react";
import { createAvatarController, getAvatarInternals } from "../controller";
import { AvatarStage } from "../AvatarStage";
import { AdaptiveDpr } from "../render/quality";
import type { AvatarConfig, AvatarStatus } from "../types";

const config = (overrides: Partial<AvatarConfig> = {}): AvatarConfig => ({
  modelUrl: "/avatars/interviewer.glb",
  modelFormat: "glb",
  interviewerName: "Maya",
  reducedMotion: false,
  ...overrides,
});

afterEach(() => cleanup());

describe("createAvatarController status", () => {
  it("starts provisional and reports model loading through subscribe()", () => {
    const c = createAvatarController(config());
    expect(c.getStatus()).toMatchObject({ mode: "3d", loaded: false, fps: 0 });
    const seen: AvatarStatus[] = [];
    const off = c.subscribe((s) => seen.push(s));
    getAvatarInternals(c)!.reportModelLoaded("interviewer.glb");
    expect(seen.at(-1)).toMatchObject({ loaded: true, modelName: "interviewer.glb" });
    off();
    getAvatarInternals(c)!.setStatus({ fps: 42 });
    expect(seen).toHaveLength(1); // unsubscribed
    expect(c.getStatus().fps).toBe(42);
  });

  it("switches to 2D with an error on simulated WebGL context loss", () => {
    const c = createAvatarController(config());
    const internals = getAvatarInternals(c)!;
    internals.reportModelLoaded("interviewer.glb");
    const listener = vi.fn();
    c.subscribe(listener);
    internals.reportContextLost();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(c.getStatus()).toMatchObject({ mode: "2d", loaded: true, error: "webgl_context_lost" });
    // Idempotent: a second loss does not notify again.
    internals.reportContextLost();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("render errors become status, never exceptions", () => {
    const c = createAvatarController(config());
    getAvatarInternals(c)!.reportRenderError(new Error("shader compile failed"));
    expect(c.getStatus()).toMatchObject({ mode: "2d", error: "render_error: shader compile failed" });
  });

  it("falls back to the procedural model with an explanatory error", () => {
    const c = createAvatarController(config());
    getAvatarInternals(c)!.reportModelLoaded("procedural", "model_load_failed: HTTP 404");
    expect(c.getStatus()).toMatchObject({ loaded: true, modelName: "procedural", error: "model_load_failed: HTTP 404" });
  });

  it("throttles FPS notifications to whole-fps changes at most once per second", () => {
    let now = 0;
    const c = createAvatarController(config(), { wallClock: () => now });
    const listener = vi.fn();
    c.subscribe(listener);
    const internals = getAvatarInternals(c)!;
    now = 10;
    internals.reportFps(58.2, 1.5);
    internals.reportFps(30, 1.5); // same second: ignored
    now = 10.5;
    internals.reportFps(31, 1.5); // still within a second
    now = 11.2;
    internals.reportFps(58.4, 1.5); // rounds to the last reported value: ignored
    internals.reportFps(44, 1.25);
    expect(listener).toHaveBeenCalledTimes(2);
    expect(c.getStatus()).toMatchObject({ fps: 44, dpr: 1.25 });
  });

  it("honours renderMode none and ignores invalid input", () => {
    const c = createAvatarController(config({ renderMode: "none" }));
    expect(c.getStatus()).toMatchObject({ mode: "none", loaded: true });
    expect(() => {
      c.setState("DANCING" as never);
      c.applyBehavior(null as never);
      c.enqueueSpeech(null as never);
      c.speakWordBoundary("", 100);
      c.attachAudio(null as never, null);
    }).not.toThrow();
  });

  it("interrupt() empties the speech queue and enters INTERRUPTED", () => {
    const c = createAvatarController(config());
    const internals = getAvatarInternals(c)!;
    c.enqueueSpeech({ utteranceId: "u", chunkId: 0, startTime: internals.engine.now() + 0.1, durationMs: 800, visemes: [{ t: 0, v: "aa", w: 1 }], text: "a" });
    expect(internals.engine.lipSync.queuedKeys).toBeGreaterThan(0);
    c.interrupt();
    expect(internals.engine.lipSync.queuedKeys).toBe(0);
    expect(internals.engine.behavior.getState()).toBe("INTERRUPTED");
  });

  it("dispose() stops notifications and turns calls into no-ops", () => {
    const c = createAvatarController(config());
    const listener = vi.fn();
    c.subscribe(listener);
    c.dispose();
    const internals = getAvatarInternals(c)!;
    internals.reportContextLost();
    expect(listener).not.toHaveBeenCalled();
    expect(internals.isDisposed()).toBe(true);
    expect(() => c.setState("LISTENING")).not.toThrow();
    expect(c.subscribe(() => {})).toBeTypeOf("function");
  });

  it("returns no internals for foreign controllers", () => {
    expect(getAvatarInternals({} as never)).toBeNull();
    expect(getAvatarInternals(null)).toBeNull();
  });
});

describe("AvatarStage modes", () => {
  it("renders the 2D portrait when 2D is requested", () => {
    const c = createAvatarController(config({ renderMode: "2d" }));
    const { container } = render(<AvatarStage controller={c} className="h-64" />);
    expect(container.querySelector('[data-avatar-mode="2d"]')).not.toBeNull();
    expect(container.querySelector("svg[data-avatar-portrait]")).not.toBeNull();
    expect(container.querySelector('[role="img"]')?.getAttribute("aria-label")).toBe("Maya, AI interviewer");
    expect(c.getStatus()).toMatchObject({ mode: "2d", loaded: true });
  });

  it("falls back to 2D when WebGL is unavailable", () => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = (() => null) as typeof original;
    try {
      const c = createAvatarController(config());
      const { container } = render(<AvatarStage controller={c} />);
      expect(container.querySelector('[data-avatar-mode="2d"]')).not.toBeNull();
      expect(c.getStatus()).toMatchObject({ mode: "2d", error: "webgl_unavailable" });
    } finally {
      HTMLCanvasElement.prototype.getContext = original;
    }
  });

  it("renders nothing in none mode", () => {
    const c = createAvatarController(config({ renderMode: "none" }));
    const { container } = render(<AvatarStage controller={c} />);
    expect(container.querySelector('[data-avatar-mode="none"]')).not.toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("drives the 2D portrait from the engine every frame", async () => {
    const c = createAvatarController(config({ renderMode: "2d" }));
    const { container } = render(<AvatarStage controller={c} />);
    const mouth = container.querySelector('path[fill="#4a1c1e"]')!;
    const before = mouth.getAttribute("d");
    c.speakWordBoundary("hello", 400);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 250));
    });
    expect(mouth.getAttribute("d")).not.toBe(before);
  });
});

describe("AdaptiveDpr", () => {
  it("drops the pixel ratio after 2 s below 45 fps, never below the floor", () => {
    const q = new AdaptiveDpr({ max: 1.75, initial: 1.75 });
    expect(q.update(30, 0)).toBeNull();
    expect(q.update(30, 1.9)).toBeNull();
    expect(q.update(30, 2.05)).toBe(1.5);
    expect(q.update(30, 3)).toBeNull(); // timer restarted
    expect(q.update(30, 4.1)).toBe(1.25);
    q.update(30, 6.2);
    q.update(30, 8.3);
    expect(q.dpr).toBe(0.75);
    expect(q.update(10, 20)).toBeNull();
  });

  it("recovers after sustained headroom, with cooldown and a cap on failed upgrades", () => {
    const q = new AdaptiveDpr({ max: 1.5, initial: 1.5, cooldown: 20 });
    q.update(30, 0);
    expect(q.update(30, 2.1)).toBe(1.25);
    expect(q.update(60, 3)).toBeNull();
    expect(q.update(60, 12)).toBeNull(); // still cooling down (until 22.1)
    expect(q.update(60, 23)).toBe(1.5); // 8 s of headroom since 12 + cooldown passed...
    // Upgrade fails twice -> stays down for good.
    q.update(30, 24);
    expect(q.update(30, 26.1)).toBe(1.25);
    q.update(60, 47);
    expect(q.update(60, 55.5)).toBe(1.5);
    q.update(30, 56);
    expect(q.update(30, 58.1)).toBe(1.25);
    q.update(60, 80);
    expect(q.update(60, 100)).toBeNull();
    expect(q.dpr).toBe(1.25);
  });
});
