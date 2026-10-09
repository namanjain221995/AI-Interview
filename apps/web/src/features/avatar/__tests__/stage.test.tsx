import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AvatarStage } from "../AvatarStage";
import { createAvatarController, getAvatarInternals } from "../controller";
import { ContextRestorePolicy } from "../render/contextRestore";
import type { AvatarRig } from "../rig/rig";
import type { AvatarConfig } from "../types";

// WebGL "available", and a stand-in 3D renderer that lets the test lose the context.
const webgl = vi.hoisted(() => ({ available: true, probes: 0 }));
vi.mock("../render/environment", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../render/environment")>();
  return {
    ...actual,
    isWebGLAvailable: (fresh = false) => {
      if (fresh) webgl.probes += 1;
      return webgl.available;
    },
  };
});
const renderer = vi.hoisted(() => ({ onContextLost: null as null | (() => void), mounts: 0 }));
vi.mock("../render/Avatar3D", async () => {
  const { useEffect } = await import("react");
  function Fake3D(props: { onContextLost: () => void }) {
    renderer.onContextLost = props.onContextLost;
    useEffect(() => {
      renderer.mounts += 1;
    }, []);
    return <div data-testid="fake-3d" />;
  }
  return { default: Fake3D };
});

const config = (overrides: Partial<AvatarConfig> = {}): AvatarConfig => ({
  modelUrl: "/avatars/interviewer.glb",
  modelFormat: "glb",
  interviewerName: "Maya",
  reducedMotion: false,
  ...overrides,
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  webgl.available = true;
  webgl.probes = 0;
  renderer.mounts = 0;
});

const fakeRig = (name: string) => ({ name, dispose: vi.fn() }) as unknown as AvatarRig;

describe("controller rig cache", () => {
  it("keeps the loaded rig for remounts and disposes it with the controller", () => {
    const c = createAvatarController(config());
    const internals = getAvatarInternals(c)!;
    expect(internals.claimRig("glb:/avatars/interviewer.glb")).toBeNull();
    const first = fakeRig("interviewer.glb");
    internals.retainRig({ key: "glb:/avatars/interviewer.glb", rig: first });
    expect(internals.claimRig("glb:/avatars/interviewer.glb")?.rig).toBe(first);
    expect(internals.claimRig("glb:/avatars/interviewer.glb")?.rig).toBe(first); // claiming does not consume it
    expect(internals.claimRig("vrm:/avatars/other.vrm")).toBeNull();
    // A different model replaces (and frees) the cached one.
    const second = fakeRig("procedural");
    internals.retainRig({ key: "procedural:procedural", rig: second, error: "model_load_failed: HTTP 404" });
    expect(first.dispose).toHaveBeenCalledTimes(1);
    expect(internals.claimRig("procedural:procedural")).toMatchObject({ error: "model_load_failed: HTTP 404" });
    c.dispose();
    expect(second.dispose).toHaveBeenCalledTimes(1);
    expect(internals.claimRig("procedural:procedural")).toBeNull();
  });

  it("frees a rig handed over after dispose", () => {
    const c = createAvatarController(config());
    const internals = getAvatarInternals(c)!;
    c.dispose();
    const late = fakeRig("late");
    internals.retainRig({ key: "k", rig: late });
    expect(late.dispose).toHaveBeenCalledTimes(1);
  });
});

describe("ContextRestorePolicy", () => {
  it("retries after a cooldown, a few probes per loss", () => {
    const p = new ContextRestorePolicy({ retryMs: 3000, maxProbes: 3, flapWindowMs: 60_000 });
    expect(p.onLost(0)).toBe(3000);
    expect(p.onProbeFailed()).toBe(3000);
    expect(p.onProbeFailed()).toBe(3000);
    expect(p.onProbeFailed()).toBeNull();
    expect(p.givenUp).toBe(true);
    expect(p.onLost(100_000)).toBeNull();
  });

  it("stays 2D when the context is lost again soon after a restore, retries after a stable period", () => {
    const flappy = new ContextRestorePolicy();
    expect(flappy.onLost(0)).not.toBeNull();
    flappy.onRestored(3_000);
    expect(flappy.onLost(20_000)).toBeNull();
    expect(flappy.givenUp).toBe(true);

    const stable = new ContextRestorePolicy();
    stable.onLost(0);
    stable.onRestored(3_000);
    expect(stable.onLost(3_000 + 61_000)).not.toBeNull();
  });
});

describe("AvatarStage WebGL context recovery", () => {
  it("shows the 2D portrait after a loss, restores 3D once the GPU is back, then gives up on flapping", async () => {
    const c = createAvatarController(config());
    const { container } = render(<AvatarStage controller={c} />);
    const stage = () => container.querySelector("[data-avatar-mode]")!;
    // The lazy renderer mounts on real timers (Testing Library polls with them).
    expect(await screen.findByTestId("fake-3d")).toBeTruthy();
    expect(stage().getAttribute("data-avatar-mode")).toBe("3d");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });

    act(() => renderer.onContextLost!());
    expect(stage().getAttribute("data-avatar-mode")).toBe("2d");
    expect(c.getStatus()).toMatchObject({ mode: "2d", error: "webgl_context_lost" });

    // After the cooldown a fresh context can be created: 3D comes back.
    await act(async () => {
      vi.advanceTimersByTime(3000);
      await vi.dynamicImportSettled();
    });
    expect(webgl.probes).toBe(1);
    expect(stage().getAttribute("data-avatar-mode")).toBe("3d");
    expect(screen.getByTestId("fake-3d")).toBeTruthy();

    // Lost again right away: the GPU is unstable, stay on the portrait.
    act(() => renderer.onContextLost!());
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    expect(stage().getAttribute("data-avatar-mode")).toBe("2d");
    expect(webgl.probes).toBe(1);
  });

  it("keeps the portrait when no fresh context can be created", async () => {
    const c = createAvatarController(config());
    const { container } = render(<AvatarStage controller={c} />);
    await screen.findByTestId("fake-3d");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    webgl.available = false;
    act(() => renderer.onContextLost!());
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    expect(webgl.probes).toBe(3);
    expect(container.querySelector("[data-avatar-mode]")!.getAttribute("data-avatar-mode")).toBe("2d");
  });
});
