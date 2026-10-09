import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

import { IntegrityBatcher } from "../batcher";
import { BlockedDetector, looksBlocked, luminanceStats } from "../frames";
import { effectivePolicy, monitoringSummary, DEFAULT_INTEGRITY } from "../policy";
import { classifyShortcut } from "../shortcuts";

const key = (k: Partial<KeyboardEventInit> & { key: string }) => ({ code: undefined, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...k });

describe("classifyShortcut", () => {
  it("recognises devtools, print, save, view-source and PrintScreen", () => {
    expect(classifyShortcut(key({ key: "F12" }))?.kind).toBe("devtools");
    expect(classifyShortcut(key({ key: "I", code: "KeyI", ctrlKey: true, shiftKey: true }))).toMatchObject({ kind: "devtools", combo: "Ctrl+Shift+I", preventable: false });
    expect(classifyShortcut(key({ key: "j", code: "KeyJ", metaKey: true, altKey: true }))?.kind).toBe("devtools");
    expect(classifyShortcut(key({ key: "p", code: "KeyP", metaKey: true }))).toMatchObject({ kind: "print", combo: "Cmd+P", preventable: true });
    expect(classifyShortcut(key({ key: "s", ctrlKey: true }))?.kind).toBe("save");
    expect(classifyShortcut(key({ key: "u", ctrlKey: true }))?.kind).toBe("view_source");
    expect(classifyShortcut(key({ key: "PrintScreen" }))?.kind).toBe("print_screen");
  });

  it("ignores ordinary typing and editing shortcuts", () => {
    expect(classifyShortcut(key({ key: "a" }))).toBeNull();
    expect(classifyShortcut(key({ key: "c", ctrlKey: true }))).toBeNull();
    expect(classifyShortcut(key({ key: "v", metaKey: true }))).toBeNull();
    expect(classifyShortcut(key({ key: "z", ctrlKey: true, shiftKey: true }))).toBeNull();
    expect(classifyShortcut(key({ key: "Tab", altKey: true }))).toBeNull(); // Alt+Tab never reaches the page anyway
  });
});

describe("IntegrityBatcher", () => {
  const ev = (i: number): ClientIntegrityEvent => ({ event_type: "focus_lost", occurred_at: new Date(i).toISOString() });
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("batches over the WebSocket", async () => {
    const ws = vi.fn(() => true);
    const rest = vi.fn(async () => undefined);
    const b = new IntegrityBatcher({ sendWs: ws, sendRest: rest, flushMs: 1000 });
    b.push(ev(1));
    b.push(ev(2));
    expect(ws).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(ws).toHaveBeenCalledTimes(1);
    expect((ws.mock.calls[0] as unknown as [ClientIntegrityEvent[]])[0]).toHaveLength(2);
    expect(rest).not.toHaveBeenCalled();
  });

  it("falls back to REST, caps batches at 50 and retries failures", async () => {
    const ws = vi.fn(() => false);
    let fail = true;
    const rest = vi.fn(async () => {
      if (fail) throw new Error("offline");
    });
    const b = new IntegrityBatcher({ sendWs: ws, sendRest: rest, flushMs: 100 });
    for (let i = 0; i < 70; i++) b.push(ev(i));
    await vi.advanceTimersByTimeAsync(0);
    // The 20th event triggered an immediate flush, which failed and was re-queued.
    expect(rest).toHaveBeenCalled();
    expect(b.pending).toBe(70);
    fail = false;
    const before = rest.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(b.pending).toBe(0);
    const sizes = rest.mock.calls.slice(before).map((c) => (c as unknown as [ClientIntegrityEvent[]])[0].length);
    expect(sizes.every((n) => n <= 50)).toBe(true);
    expect(sizes.reduce((a, n) => a + n, 0)).toBe(70);
  });
});

describe("camera-blocked check", () => {
  const pixels = (fill: (i: number) => [number, number, number], n = 64 * 48) => {
    const data = new Uint8ClampedArray(n * 4);
    for (let i = 0; i < n; i++) {
      const [r, g, b] = fill(i);
      data.set([r, g, b, 255], i * 4);
    }
    return data;
  };

  it("measures luminance and flags dark or uniform frames", () => {
    const dark = luminanceStats(pixels(() => [5, 6, 8]));
    expect(dark.mean).toBeLessThan(10);
    expect(looksBlocked(dark)).toBe(true);
    const flat = luminanceStats(pixels(() => [200, 120, 90]));
    expect(flat.stddev).toBeLessThan(1);
    expect(looksBlocked(flat)).toBe(true);
    const scene = luminanceStats(pixels((i) => (i % 7 < 3 ? [30, 30, 30] : [210, 190, 170])));
    expect(looksBlocked(scene)).toBe(false);
  });

  it("reports only sustained blocking, once, and re-arms after recovery", () => {
    const d = new BlockedDetector(3);
    const blocked = { mean: 4, stddev: 1 };
    const ok = { mean: 120, stddev: 40 };
    expect([d.push(blocked), d.push(blocked), d.push(blocked), d.push(blocked)]).toEqual([false, false, true, false]);
    d.push(ok);
    expect([d.push(blocked), d.push(blocked), d.push(blocked)]).toEqual([false, false, true]);
  });
});

describe("effectivePolicy", () => {
  it("merges snapshot, live flags and consent", () => {
    const p = effectivePolicy(
      { integrity: { ...DEFAULT_INTEGRITY, camera_frame_interval_seconds: 15, block_paste_in_coding: false }, recording: { record_answer_audio: true, record_video: true, record_screen: false, show_live_subtitles: false, allow_typed_answers: true } },
      { block_paste_in_coding: true, large_paste_threshold_chars: 120, unknown_flag: true },
      { camera_frames: false, video_recording: true },
    );
    expect(p.integrity.block_paste_in_coding).toBe(true);
    expect(p.integrity.camera_frame_interval_seconds).toBe(15);
    expect(p.largePasteThreshold).toBe(120);
    expect(p.uploadCameraFrames).toBe(false);
    expect(p.recordVideo).toBe(true);
    expect(p.recording.show_live_subtitles).toBe(false);
    expect(p.monitor.require_fullscreen).toBe(true);
  });

  it("never records video without consent and defaults sensibly", () => {
    const p = effectivePolicy({ recording: { record_answer_audio: true, record_video: true, record_screen: false, show_live_subtitles: true, allow_typed_answers: true } }, null, null);
    expect(p.recordVideo).toBe(false);
    expect(p.uploadCameraFrames).toBe(true);
    expect(p.largePasteThreshold).toBe(200);
  });

  it("describes what is monitored in plain language", () => {
    expect(monitoringSummary({ ...DEFAULT_INTEGRITY, enabled: false })).toEqual([]);
    const items = monitoringSummary({ ...DEFAULT_INTEGRITY, screen_share_required: true });
    expect(items).toContain("Your shared screen");
    expect(items.length).toBeGreaterThanOrEqual(5);
  });
});

describe("effective policy intervals", () => {
  it("uses the server's camera interval and falls back to the backend default of 15 s", () => {
    expect(DEFAULT_INTEGRITY.camera_frame_interval_seconds).toBe(15);
    expect(effectivePolicy(null, null, null).integrity.camera_frame_interval_seconds).toBe(15);
    expect(effectivePolicy({ integrity: { ...DEFAULT_INTEGRITY, camera_frame_interval_seconds: 20 } }, null, null).integrity.camera_frame_interval_seconds).toBe(20);
    // A live override from session.state wins, but never outside the server's bounds.
    expect(effectivePolicy({ integrity: { ...DEFAULT_INTEGRITY, camera_frame_interval_seconds: 20 } }, { camera_frame_interval_seconds: 30 }, null).integrity.camera_frame_interval_seconds).toBe(30);
    expect(effectivePolicy({ integrity: { ...DEFAULT_INTEGRITY, camera_frame_interval_seconds: 1 } }, null, null).integrity.camera_frame_interval_seconds).toBe(15);
    expect(effectivePolicy({ integrity: { ...DEFAULT_INTEGRITY, screen_frame_interval_seconds: 999 } }, null, null).integrity.screen_frame_interval_seconds).toBe(20);
  });

  it("records the shared screen only with the policy and a matching consent", () => {
    const policy = { recording: { record_answer_audio: true, record_video: false, record_screen: true, show_live_subtitles: true, allow_typed_answers: true } };
    expect(effectivePolicy(policy, null, { screen_share: true }).recordScreen).toBe(true);
    expect(effectivePolicy(policy, null, { video_recording: true }).recordScreen).toBe(true);
    expect(effectivePolicy(policy, null, null).recordScreen).toBe(false);
    expect(effectivePolicy({ recording: { ...policy.recording, record_screen: false } }, null, { screen_share: true }).recordScreen).toBe(false);
  });
});
