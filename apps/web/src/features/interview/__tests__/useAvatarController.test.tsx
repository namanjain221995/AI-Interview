import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PortalInterviewView } from "@/features/candidate/api";
import { DEFAULT_PREFS } from "@/features/candidate/journey";

const created: Array<{ dispose: ReturnType<typeof vi.fn>; config: unknown }> = [];
vi.mock("@/features/avatar", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/avatar")>()),
  createAvatarController: (config: unknown) => {
    const controller = { config, dispose: vi.fn() };
    created.push(controller);
    return controller;
  },
}));

const { avatarConfig, useAvatarController } = await import("../useAvatarController");

function interview(overrides: Partial<PortalInterviewView> = {}): PortalInterviewView {
  return {
    id: "i1",
    status: "in_progress",
    organizationName: "Demo Corp",
    job: { id: "j1", title: "Senior DevOps Engineer", department: null, location: null, description: null },
    rounds: [],
    selectedRoundId: "r1",
    allowRoundSelection: false,
    policy: { avatar: { model_url: "/avatars/interviewer.glb", model_format: "glb", interviewer_name: "Maya" } as never },
    stepsCompleted: ["consent"],
    expiresAt: null,
    interviewerName: "Maya",
    latestSession: { id: "s1", status: "ready" },
    candidateName: "Jane Doe",
    requiresResume: true,
    ...overrides,
  };
}

describe("useAvatarController", () => {
  it("keeps one controller when the interview is refetched with a new object", () => {
    created.length = 0;
    const { result, rerender } = renderHook(({ value }) => useAvatarController(value, DEFAULT_PREFS, false), { initialProps: { value: interview() } });
    const first = result.current;
    expect(created).toHaveLength(1);
    // A focus refetch brings a new object (the session went live) with the same avatar settings.
    rerender({ value: interview({ latestSession: { id: "s1", status: "live" } }) });
    expect(result.current).toBe(first);
    expect(created).toHaveLength(1);
    expect(created[0]!.dispose).not.toHaveBeenCalled();
  });

  it("replaces the controller only when the avatar settings change", () => {
    created.length = 0;
    const { rerender, unmount } = renderHook(({ value }) => useAvatarController(value, DEFAULT_PREFS, false), { initialProps: { value: interview() } });
    rerender({ value: interview({ policy: { avatar: { model_format: "procedural" } as never } }) });
    expect(created).toHaveLength(2);
    expect(created[0]!.dispose).toHaveBeenCalledTimes(1);
    unmount();
    expect(created[1]!.dispose).toHaveBeenCalledTimes(1);
  });

  it("defaults to the presence orb and keeps the optional realistic avatar", () => {
    const orb = avatarConfig(interview({ policy: { avatar: undefined } as never }), DEFAULT_PREFS, false);
    expect(orb).toMatchObject({ modelFormat: "orb", modelUrl: "orb:aurora", interviewerName: "Maya" });
    const sunrise = avatarConfig(interview({ policy: { avatar: { model_format: "orb", model_url: "orb:sunrise" } as never } }), DEFAULT_PREFS, true);
    expect(sunrise).toMatchObject({ modelFormat: "orb", modelUrl: "orb:sunrise", reducedMotion: true });
    // The retired cartoon character (and anything unknown) becomes the orb.
    const retired = avatarConfig(interview({ policy: { avatar: { model_format: "cartoon", model_url: "cartoon:maya" } as never } }), DEFAULT_PREFS, false);
    expect(retired).toMatchObject({ modelFormat: "orb", modelUrl: "orb:aurora" });
    const glb = avatarConfig(interview(), DEFAULT_PREFS, false);
    expect(glb).toMatchObject({ modelFormat: "glb", modelUrl: "/avatars/interviewer.glb" });
  });
});
