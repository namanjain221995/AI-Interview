/**
 * System-check face check: the row result only ever passes or warns (never
 * blocks), it runs only under a consent that described it, and the overlay
 * draws the boxes and one friendly tip from the on-device monitor.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FaceMonitorStatus, FaceMonitorView, FacePresenceMonitor, FacePresenceMonitorOptions } from "@/features/integrity/face/monitor";
import type { FaceGuidance } from "@/features/integrity/face/states";

import { FaceCheckOverlay } from "../systemCheck/FaceCheckOverlay";
import { faceCheckAllowed, faceCheckResult, type FaceCheckSnapshot } from "../systemCheck/faceCheck";

const snap = (s: Partial<FaceCheckSnapshot>): FaceCheckSnapshot => ({ status: "running", guidance: null, counted: 1, steady: false, ...s });

describe("faceCheckResult", () => {
  it("passes, warns or waits, and never fails", () => {
    expect(faceCheckResult(null, false).state).toBe("pending");
    expect(faceCheckResult(snap({ status: "loading" }), true).state).toBe("running");
    expect(faceCheckResult(snap({ steady: true }), true)).toMatchObject({ state: "pass", summary: "Your face is clearly visible" });
    expect(faceCheckResult(snap({ guidance: "too_far" }), true)).toMatchObject({ state: "warn", summary: "Move a little closer." });
    expect(faceCheckResult(snap({ guidance: "low_light" }), true).summary).toBe("Add some light in front of you.");
    expect(faceCheckResult(snap({ guidance: "multiple_faces", counted: 2 }), true).summary).toBe("Only you should be in view.");
    expect(faceCheckResult(snap({ status: "unsupported" }), true)).toMatchObject({ state: "warn", detail: expect.stringContaining("works without it") as string });
    expect(faceCheckResult(snap({ status: "paused" }), true).state).toBe("running");
    const states = (["idle", "loading", "running", "paused", "unsupported", "error", "stopped"] as FaceMonitorStatus[]).flatMap((status) =>
      ([null, "no_face", "multiple_faces", "too_close"] as Array<FaceGuidance | null>).map((guidance) => faceCheckResult(snap({ status, guidance }), true).state),
    );
    expect(states).not.toContain("fail");
  });
});

describe("faceCheckAllowed", () => {
  const disclosure = (enabled: boolean, consented = true) => ({ consented, items: [{ key: "face_check", enabled }] });

  it("needs the policy and a consent that covered the face check", () => {
    const on = { enabled: true, camera_required: true, camera_frame_analysis: true, face_presence_check: true };
    expect(faceCheckAllowed(on, { face_check: true, camera: true }, null)).toBe(true);
    expect(faceCheckAllowed(on, { face_check: true, camera: false }, null)).toBe(false);
    expect(faceCheckAllowed(on, null, disclosure(true))).toBe(true);
    expect(faceCheckAllowed(on, null, disclosure(true, false))).toBe(false);
    expect(faceCheckAllowed(on, null, disclosure(false))).toBe(false);
    expect(faceCheckAllowed({ ...on, face_presence_check: false }, { face_check: true }, disclosure(true))).toBe(false);
    expect(faceCheckAllowed({ ...on, enabled: false }, { face_check: true }, disclosure(true))).toBe(false);
    expect(faceCheckAllowed({ ...on, camera_required: false, camera_frame_analysis: false }, { face_check: true }, disclosure(true))).toBe(false);
  });
});

describe("FaceCheckOverlay", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("draws the face boxes over the mirrored preview with one tip, reporting nothing", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private readonly cb: ResizeObserverCallback) {}
        observe() {
          this.cb([{ contentRect: { width: 400, height: 300 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
        }
        disconnect() {}
      },
    );
    let options: FacePresenceMonitorOptions | null = null;
    const monitor = { start: vi.fn(async () => undefined), stop: vi.fn() };
    const createMonitor = vi.fn((o: FacePresenceMonitorOptions) => {
      options = o;
      return monitor as unknown as FacePresenceMonitor;
    });
    const onSnapshot = vi.fn();
    const video = document.createElement("video");
    render(<FaceCheckOverlay video={video} enabled onSnapshot={onSnapshot} createMonitor={createMonitor} />);
    await vi.waitFor(() => expect(monitor.start).toHaveBeenCalled());
    expect(options!.report).toBe(false);
    expect(options!.onEvent).toBeUndefined();

    const view: FaceMonitorView = {
      faces: [
        { id: 1, box: { x: 0.1, y: 0.2, w: 0.2, h: 0.3 }, live: true, static: false, primary: true },
        { id: 2, box: { x: 0.7, y: 0.1, w: 0.1, h: 0.15 }, live: false, static: true, primary: false },
      ],
      counted: 1,
      live: 1,
      guidance: "too_far",
      videoWidth: 640,
      videoHeight: 480,
    };
    act(() => {
      options!.onStatus!("running");
      options!.onView!(view);
      options!.onGuidance!("too_far");
    });
    const boxes = screen.getAllByTestId("syscheck-face-box");
    expect(boxes.map((b) => b.getAttribute("data-kind"))).toEqual(["primary", "static"]);
    // Mirrored: a box at x 0.1-0.3 of the frame shows at 70-90 % from the left.
    expect(boxes[0]!.style.left).toBe("70%");
    expect(screen.getByText("Picture · not counted")).toBeInTheDocument();
    expect(screen.getByTestId("syscheck-face-guidance")).toHaveTextContent("Move a little closer.");
    expect(onSnapshot).toHaveBeenLastCalledWith({ status: "running", guidance: "too_far", counted: 1, steady: false });

    // One face, no tip, held for 1.5 s: "clearly visible".
    vi.useFakeTimers();
    try {
      act(() => options!.onGuidance!(null));
      act(() => vi.advanceTimersByTime(1_600));
      expect(screen.getByTestId("syscheck-face-guidance")).toHaveAttribute("data-guidance", "ok");
      expect(onSnapshot).toHaveBeenLastCalledWith({ status: "running", guidance: null, counted: 1, steady: true });
    } finally {
      vi.useRealTimers();
    }
    cleanup();
    expect(monitor.stop).toHaveBeenCalled();
  });
});
