/**
 * On-device face check: geometry, guidance, liveness (static-face suppression),
 * hysteresis, the monitor loop (with a fake detector) and the room wiring.
 * Observations only: nothing here may pause, end or fail an interview.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

import { analyseFrame, classifyGuidance, roughYaw, type FaceLight } from "../face/analysis";
import { clipBox, iou, overlayRect, type FaceBox } from "../face/geometry";
import { FACE_GUIDANCE_COPY, GuidanceThrottle } from "../face/guidance";
import { FrameCalm, lumaPatch, MotionMeter, type PixelSource } from "../face/liveness";
import { FacePresenceMonitor, type FaceSnapshotRequest } from "../face/monitor";
import { RoomFaceCheck } from "../face/roomFaceCheck";
import { FacePresenceStates, randomId, type FaceTransition } from "../face/states";
import { FaceTracker, type RawDetection, type TrackedFace } from "../face/tracker";

// ------------------------------------------------------------------ helpers

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const W = 80;
const H = 60;

/** A grey test frame: a soft gradient, sensor-like noise, optional bright rectangles (pixels). */
function frame(random: () => number, noise = 2, rects: Array<[number, number, number, number, number]> = []): PixelSource {
  const data = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let v = 60 + x + y + (random() * 2 - 1) * noise;
      for (const [rx, ry, rw, rh, add] of rects) if (x >= rx && x < rx + rw && y >= ry && y < ry + rh) v += add;
      const i = (y * W + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = v;
      data[i + 3] = 255;
    }
  }
  return { data, width: W, height: H };
}

const det = (box: FaceBox, score = 0.9): RawDetection => ({ box, score, keypoints: [] });

function tracked(box: FaceBox, extra: Partial<TrackedFace> = {}): TrackedFace {
  return { id: 1, box, smoothBox: box, score: 0.9, keypoints: [], live: true, static: false, newlyStatic: false, presentMs: 0, stillMs: 0, motion: { peak: null, floor: 0 }, ...extra };
}

// ------------------------------------------------------------------ geometry

describe("face geometry", () => {
  it("computes overlap and clips boxes to the frame", () => {
    expect(iou({ x: 0, y: 0, w: 0.2, h: 0.2 }, { x: 0, y: 0, w: 0.2, h: 0.2 })).toBeCloseTo(1);
    expect(iou({ x: 0, y: 0, w: 0.2, h: 0.2 }, { x: 0.5, y: 0.5, w: 0.2, h: 0.2 })).toBe(0);
    expect(clipBox({ x: -0.1, y: 0.9, w: 0.3, h: 0.3 })).toEqual({ x: 0, y: 0.9, w: expect.closeTo(0.2) as number, h: expect.closeTo(0.1) as number });
    expect(clipBox({ x: 1.2, y: 0, w: 0.1, h: 0.1 })).toBeNull();
  });

  it("places a box over a mirrored, cover-fitted preview", () => {
    // 4:3 video in a 4:3 container: no crop; mirrored x.
    const rect = overlayRect({ x: 0.1, y: 0.2, w: 0.2, h: 0.3 }, { width: 400, height: 300 }, { width: 640, height: 480 }, true);
    expect(rect.left).toBeCloseTo(70);
    expect(rect.top).toBeCloseTo(20);
    expect(rect.width).toBeCloseTo(20);
    expect(rect.height).toBeCloseTo(30);
    // 16:9 video in a 4:3 container: cropped left/right, so the box shifts and grows.
    const wide = overlayRect({ x: 0.5, y: 0.5, w: 0.1, h: 0.1 }, { width: 400, height: 300 }, { width: 1280, height: 720 }, false);
    expect(wide.width).toBeGreaterThan(10);
    expect(wide.left + wide.width / 2).toBeCloseTo(50 + wide.width / 2);
  });
});

// ------------------------------------------------------------------ guidance

describe("framing and light guidance", () => {
  const ok: FaceLight = { face: 140, frame: 130, sharpness: 50 };

  it("gives no tip for a centred, well-lit face", () => {
    expect(classifyGuidance(tracked({ x: 0.35, y: 0.25, w: 0.25, h: 0.35 }), ok)).toEqual([]);
    expect(classifyGuidance(null, ok)).toEqual([]);
  });

  it("asks to move closer, back, into the middle or to add light", () => {
    expect(classifyGuidance(tracked({ x: 0.45, y: 0.4, w: 0.08, h: 0.1 }), ok)).toEqual(["too_far"]);
    expect(classifyGuidance(tracked({ x: 0.1, y: 0.05, w: 0.7, h: 0.9 }), ok)).toEqual(["too_close"]);
    expect(classifyGuidance(tracked({ x: 0.85, y: 0.3, w: 0.2, h: 0.3 }), ok)).toEqual(["partly_out"]);
    expect(classifyGuidance(tracked({ x: 0.35, y: 0.25, w: 0.25, h: 0.35 }), { face: 40, frame: 45, sharpness: 5 })).toEqual(["low_light"]);
    // Backlit: the face is much darker than the room.
    expect(classifyGuidance(tracked({ x: 0.35, y: 0.25, w: 0.25, h: 0.35 }), { face: 80, frame: 200, sharpness: 5 })).toEqual(["low_light"]);
  });

  it("estimates head direction for context only and ignores motionless faces", () => {
    expect(roughYaw([{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: 0.5, y: 0.5 }])).toBe(0);
    expect(roughYaw([{ x: 0.4, y: 0.4 }, { x: 0.6, y: 0.4 }, { x: 0.55, y: 0.5 }])).toBeGreaterThan(20);
    expect(roughYaw([])).toBeNull();
    const person = tracked({ x: 0.3, y: 0.2, w: 0.2, h: 0.3 });
    const poster = tracked({ x: 0.7, y: 0.1, w: 0.25, h: 0.35 }, { id: 2, live: false, static: true });
    const result = analyseFrame([person, poster], null);
    expect(result.counted).toEqual([person]);
    expect(result.primary).toBe(person);
    expect(result.live).toEqual([person]);
    // A larger picture not yet set aside never becomes the primary face while someone is moving.
    const bigPicture = tracked({ x: 0.05, y: 0.05, w: 0.4, h: 0.5 }, { id: 3, live: false, static: false });
    expect(analyseFrame([bigPicture, person], null).primary).toBe(person);
    expect(analyseFrame([bigPicture], null).primary).toBe(bigPicture);
  });
});

// ------------------------------------------------------------------ liveness

describe("micro-motion (live face vs. picture)", () => {
  it("does not treat sensor noise on a still picture as motion", () => {
    const random = rng(7);
    const meter = new MotionMeter();
    const box = { x: 0.3, y: 0.3, w: 0.3, h: 0.4 };
    let evidence = 0;
    for (let i = 0; i < 80; i++) if (meter.push(lumaPatch(frame(random, 3), box)).evidence) evidence++;
    expect(evidence).toBe(0);
    expect(meter.floor).toBeLessThan(3);
  });

  it("sees a blink-sized local change inside the face region", () => {
    const random = rng(11);
    const meter = new MotionMeter();
    const box = { x: 0.3, y: 0.3, w: 0.3, h: 0.4 };
    const hits: number[] = [];
    for (let i = 0; i < 40; i++) {
      const blink = i % 8 === 0 || i % 8 === 1;
      const rects: Array<[number, number, number, number, number]> = i > 12 && blink ? [[28, 22, 8, 6, -45]] : [];
      if (meter.push(lumaPatch(frame(random, 2, rects), box)).evidence) hits.push(i);
    }
    expect(hits.length).toBeGreaterThanOrEqual(4);
    expect(hits[0]).toBeGreaterThanOrEqual(16);
  });

  it("flags a whole-frame jump (lights, exposure) as not calm", () => {
    const random = rng(3);
    const calm = new FrameCalm();
    const whole = { x: 0, y: 0, w: 1, h: 1 };
    for (let i = 0; i < 12; i++) expect(calm.push(lumaPatch(frame(random, 2), whole))).toBe(true);
    // A big structured change across the frame (camera moved / lights): median block change jumps.
    const shifted = frame(random, 2, Array.from({ length: 8 }, (_, k) => [k * 10, 0, 5, H, 60] as [number, number, number, number, number]));
    expect(calm.push(lumaPatch(shifted, whole))).toBe(false);
  });
});

// ------------------------------------------------------------------ tracker

describe("FaceTracker (static-face suppression)", () => {
  it("sets a motionless face aside after 30 s and reports it once", () => {
    const tracker = new FaceTracker();
    const box = { x: 0.6, y: 0.2, w: 0.15, h: 0.2 };
    let newly = 0;
    let last: TrackedFace[] = [];
    for (let t = 0; t <= 31_000; t += 250) {
      last = tracker.update([det(box)], t);
      if (last[0]!.newlyStatic) {
        newly++;
        expect(t).toBe(30_000);
      }
    }
    expect(newly).toBe(1);
    expect(last[0]).toMatchObject({ static: true, live: false });
    expect(tracker.staticBoxes(31_000)).toEqual([box]);
  });

  it("keeps a moving face live (never static) and separates two people", () => {
    const tracker = new FaceTracker();
    let out: TrackedFace[] = [];
    for (let i = 0; i <= 160; i++) {
      const t = i * 250;
      const dx = 0.06 * Math.sin(i / 6); // natural head movement
      out = tracker.update([det({ x: 0.2 + dx, y: 0.3, w: 0.18, h: 0.24 }), det({ x: 0.6 - dx, y: 0.3, w: 0.18, h: 0.24 })], t);
    }
    expect(out).toHaveLength(2);
    expect(out.every((f) => f.live && !f.static)).toBe(true);
    expect(new Set(out.map((f) => f.id)).size).toBe(2);
    expect(tracker.staticBoxes(160 * 250)).toEqual([]);
  });

  it("counts a picture-like face as live once it blinks (pixels)", () => {
    const random = rng(5);
    const tracker = new FaceTracker();
    // Box in pixels: x 24..48, y 18..42 of the 80x60 frame.
    const box = { x: 0.3, y: 0.3, w: 0.3, h: 0.4 };
    let live = false;
    let liveAt = -1;
    for (let i = 0; i < 60 && !live; i++) {
      const blink = i > 12 && (i % 8 === 0 || i % 8 === 1);
      const pixels = frame(random, 2, blink ? [[28, 22, 8, 6, -45]] : []);
      live = tracker.update([det(box)], i * 250, pixels)[0]!.live;
      if (live) liveAt = i;
    }
    expect(live).toBe(true);
    expect(liveAt).toBeLessThan(30);
  });

  it("stops counting a face as an extra person after 30 s without life, but never sets a once-live face aside", () => {
    const tracker = new FaceTracker();
    let out: TrackedFace[] = [];
    // Moves for 5 s (live), then holds perfectly still (a poster that took over the track).
    for (let i = 0; i <= 160; i++) {
      const x = i <= 20 ? 0.2 + 0.006 * i : 0.32;
      out = tracker.update([det({ x, y: 0.3, w: 0.18, h: 0.24 })], i * 250);
      if (i === 20) expect(out[0]!.live).toBe(true);
    }
    expect(out[0]).toMatchObject({ live: false, static: false, newlyStatic: false });
    expect(tracker.staticBoxes(160 * 250)).toEqual([]);
  });

  it("drops faces that left the picture", () => {
    const tracker = new FaceTracker();
    tracker.update([det({ x: 0.1, y: 0.1, w: 0.2, h: 0.2 })], 0);
    expect(tracker.size).toBe(1);
    tracker.update([], 7_000);
    expect(tracker.size).toBe(0);
  });
});

// ------------------------------------------------------------------ hysteresis

describe("FacePresenceStates", () => {
  const WALL = 1_700_000_000_000;

  function drive(states: FacePresenceStates, from: number, to: number, counted: number, live: number, guidance: Parameters<FacePresenceStates["step"]>[0]["guidance"] = []) {
    const transitions: FaceTransition[] = [];
    let last: ReturnType<FacePresenceStates["step"]> | null = null;
    for (let t = from; t <= to; t += 250) {
      last = states.step({ t, wall: WALL + t, counted, live, guidance });
      transitions.push(...last.transitions);
    }
    return { transitions, guidance: last?.guidance ?? null };
  }

  it("reports no face after 5 s, re-reports long intervals and ends after 1.5 s with a face", () => {
    const states = new FacePresenceStates({}, () => "nf_test");
    states.setFrameInterval(250);
    const first = drive(states, 0, 4_750, 0, 0);
    expect(first.transitions).toEqual([]);
    const start = drive(states, 5_000, 20_000, 0, 0).transitions;
    expect(start[0]).toEqual({ kind: "no_face", phase: "start", episode: "nf_test", startedAt: WALL, durationMs: 5_000, faces: 0 });
    expect(start[1]).toMatchObject({ phase: "update", durationMs: 20_000 });
    // A face for 1 s is not enough to end it ...
    expect(drive(states, 20_250, 21_250, 1, 0).transitions).toEqual([]);
    drive(states, 21_500, 21_500, 0, 0);
    // ... a face for 1.5 s ends it where the face appeared.
    const end = drive(states, 21_750, 23_500, 1, 1).transitions;
    expect(end).toEqual([{ kind: "no_face", phase: "end", episode: "nf_test", startedAt: WALL, durationMs: 21_750, faces: 0 }]);
    expect(states.openIntervals).toEqual([]);
  });

  it("needs two faces seen moving for 3 s before reporting more than one face", () => {
    const states = new FacePresenceStates({}, (kind) => `${kind}_x`);
    // Two faces, one not yet seen moving (a picture before it is set aside): never "multiple".
    expect(drive(states, 0, 10_000, 2, 1).transitions).toEqual([]);
    const result = drive(states, 10_250, 13_500, 2, 2).transitions;
    expect(result).toEqual([{ kind: "multiple_faces", phase: "start", episode: "multiple_faces_x", startedAt: WALL + 10_250, durationMs: 3_000, faces: 2 }]);
  });

  it("tolerates a face missed for a frame or two before an interval starts", () => {
    const states = new FacePresenceStates({}, (kind) => `${kind}_d`);
    const out: FaceTransition[] = [];
    // The detector misses the second face in one frame of four.
    for (let t = 0; t <= 4_000; t += 250) out.push(...states.step({ t, wall: WALL + t, counted: 2, live: t % 1_000 === 750 ? 1 : 2, guidance: [] }).transitions);
    expect(out).toEqual([{ kind: "multiple_faces", phase: "start", episode: "multiple_faces_d", startedAt: WALL, durationMs: 3_000, faces: 2 }]);
    // A real gap (>= 750 ms) restarts the clock.
    const fresh = new FacePresenceStates();
    drive(fresh, 0, 2_000, 0, 0);
    drive(fresh, 2_250, 3_000, 1, 1);
    expect(drive(fresh, 3_250, 7_750, 0, 0).transitions).toEqual([]);
    expect(drive(fresh, 8_000, 8_250, 0, 0).transitions).toHaveLength(1);
  });

  it("closes open intervals when monitoring pauses (tab hidden) and keeps the time summary", () => {
    const states = new FacePresenceStates({}, () => "ep_1");
    states.setFrameInterval(250);
    drive(states, 0, 10_000, 1, 1);
    drive(states, 10_250, 17_000, 0, 0);
    expect(states.openIntervals).toEqual(["no_face"]);
    expect(states.pause(18_000)).toEqual([{ kind: "no_face", phase: "end", episode: "ep_1", startedAt: WALL + 10_250, durationMs: 7_750, faces: 0 }]);
    const summary = states.summary();
    expect(summary.monitored_ms).toBe(17_000);
    expect(summary.one_face_ms).toBe(10_000);
    expect(summary.no_face_ms).toBe(7_000);
    expect(summary.multi_face_ms).toBe(0);
    // A gap (hidden tab) does not count as monitored time.
    drive(states, 60_000, 61_000, 1, 1);
    expect(states.summary().monitored_ms).toBe(18_000);
  });

  it("shows framing tips with a short hysteresis, presence first", () => {
    const states = new FacePresenceStates();
    expect(drive(states, 0, 1_750, 1, 1, ["too_far"]).guidance).toBeNull();
    expect(drive(states, 2_000, 2_000, 1, 1, ["too_far"]).guidance).toBe("too_far");
    expect(drive(states, 2_250, 3_000, 1, 1, []).guidance).toBe("too_far");
    expect(drive(states, 3_250, 3_250, 1, 1, []).guidance).toBeNull();
    expect(drive(states, 3_500, 6_000, 0, 0, ["low_light"]).guidance).toBe("no_face");
  });

  it("makes ids the server accepts", () => {
    expect(randomId()).toMatch(/^[a-z0-9]{10}$/);
  });
});

// ------------------------------------------------------------------ throttle

describe("GuidanceThrottle (room tips)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows a lasting framing tip once per cooldown and never presence tips", () => {
    const notify = vi.fn();
    const throttle = new GuidanceThrottle(8_000, 120_000);
    throttle.change("too_far", notify);
    vi.advanceTimersByTime(7_999);
    expect(notify).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(notify).toHaveBeenCalledWith("too_far");
    throttle.change(null, notify);
    throttle.change("too_far", notify);
    vi.advanceTimersByTime(10_000);
    expect(notify).toHaveBeenCalledTimes(1);
    // A tip that clears before it lasted is never shown.
    throttle.change("low_light", notify);
    vi.advanceTimersByTime(3_000);
    throttle.change(null, notify);
    vi.advanceTimersByTime(10_000);
    expect(notify).toHaveBeenCalledTimes(1);
    // Presence is handled by the server's reminders, not by tips.
    throttle.change("no_face", notify);
    throttle.change("multiple_faces", notify);
    vi.advanceTimersByTime(20_000);
    expect(notify).toHaveBeenCalledTimes(1);
  });
});

// ------------------------------------------------------------------ monitor

interface Harness {
  monitor: FacePresenceMonitor;
  events: ClientIntegrityEvent[];
  snapshots: FaceSnapshotRequest[];
  statuses: string[];
  doc: { visibilityState: string };
  run: (ms: number) => void;
  now: () => number;
}

async function harness(faces: (t: number) => FaceBox[], report = true): Promise<Harness> {
  let now = 0;
  let pending: { fn: () => void; ms: number } | null = null;
  const doc = {
    visibilityState: "visible",
    createElement: () => ({ getContext: () => null }),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  const video = { readyState: 4, videoWidth: 640, videoHeight: 480, srcObject: null } as unknown as HTMLVideoElement;
  const events: ClientIntegrityEvent[] = [];
  const snapshots: FaceSnapshotRequest[] = [];
  const statuses: string[] = [];
  const monitor = new FacePresenceMonitor({
    video,
    report,
    createDetector: async () => ({ name: "fake-detector", detect: (_source, t) => faces(t).map((box) => det(box)), close: vi.fn() }),
    onEvent: (event) => events.push(event),
    onSnapshot: (request) => snapshots.push(request),
    onStatus: (status) => statuses.push(status),
    env: {
      now: () => 1_700_000_000_000 + now,
      perf: () => now,
      doc: doc as unknown as Document,
      setTimer: (fn, ms) => {
        pending = { fn, ms };
        return 1;
      },
      clearTimer: () => {
        pending = null;
      },
    },
  });
  await monitor.start();
  const run = (ms: number) => {
    const end = now + ms;
    while (pending && now + pending.ms <= end) {
      const next: { fn: () => void; ms: number } = pending;
      pending = null;
      now += next.ms;
      next.fn();
    }
    now = end;
  };
  return { monitor, events, snapshots, statuses, doc, run, now: () => now };
}

const person = (t: number): FaceBox => ({ x: 0.4 + 0.05 * Math.sin(t / 1500), y: 0.3, w: 0.2, h: 0.27 });

describe("FacePresenceMonitor", () => {
  it("reports a no-face interval with one snapshot request, then its end and a time summary", async () => {
    const h = await harness((t) => (t < 4_000 || t >= 14_000 ? [person(t)] : []));
    h.run(20_000);
    const noFace = h.events.filter((e) => e.event_type === "face_not_visible");
    expect(noFace.map((e) => e.details?.phase)).toEqual(["start", "end"]);
    expect(noFace[0]!.duration_ms).toBe(5_000);
    expect(noFace[1]!.duration_ms).toBe(10_000);
    expect(noFace[0]!.details).toMatchObject({ faces: 0, detector: "fake-detector", fps: 4 });
    expect(h.snapshots).toHaveLength(1);
    expect(h.snapshots[0]).toMatchObject({ reason: "no_face", durationMs: 5_000, staticBoxes: [] });
    h.monitor.stop();
    const summary = h.events.find((e) => e.event_type === "face_check_summary");
    expect(summary?.details).toMatchObject({ run: h.monitor.runId, static_faces: 0 });
    expect(Number(summary?.details?.monitored_ms)).toBeGreaterThan(19_000);
    expect(Number(summary?.details?.no_face_ms)).toBeGreaterThan(9_000);
    expect(h.statuses.at(-1)).toBe("stopped");
    // Only observations leave the monitor: no head direction, no verdicts.
    for (const event of h.events) {
      expect(["face_not_visible", "multiple_faces_visible", "static_face_ignored", "face_check_summary"]).toContain(event.event_type);
      expect(JSON.stringify(event.details)).not.toMatch(/yaw|pitch|gaze|emotion|identity/);
    }
  });

  it("reports more than one face only for two moving faces, and sets a poster aside", async () => {
    const poster: FaceBox = { x: 0.75, y: 0.1, w: 0.12, h: 0.16 };
    const h = await harness((t) => [person(t), poster]);
    h.run(32_000);
    expect(h.events.some((e) => e.event_type === "multiple_faces_visible")).toBe(false);
    const ignored = h.events.filter((e) => e.event_type === "static_face_ignored");
    expect(ignored).toHaveLength(1);
    expect(ignored[0]!.details).toMatchObject({ still_seconds: 30, position: "right" });
    expect(h.monitor.staticBoxes()).toEqual([[0.75, 0.1, 0.12, 0.16]]);

    const second = (t: number): FaceBox => ({ x: 0.05 + 0.05 * Math.cos(t / 1300), y: 0.35, w: 0.18, h: 0.24 });
    const two = await harness((t) => [person(t), second(t)]);
    two.run(12_000);
    const multi = two.events.filter((e) => e.event_type === "multiple_faces_visible");
    expect(multi[0]?.details).toMatchObject({ phase: "start", faces: 2 });
    expect(two.snapshots.map((s) => s.reason)).toEqual(["multiple_faces"]);
  });

  it("pauses while the tab is hidden and closes the open interval", async () => {
    const h = await harness(() => []);
    h.run(7_000);
    expect(h.events.filter((e) => e.event_type === "face_not_visible").map((e) => e.details?.phase)).toEqual(["start"]);
    h.doc.visibilityState = "hidden";
    h.run(2_000);
    expect(h.monitor.status).toBe("paused");
    const end = h.events.filter((e) => e.event_type === "face_not_visible").at(-1)!;
    expect(end.details?.phase).toBe("end");
    h.doc.visibilityState = "visible";
    h.run(1_000);
    expect(h.monitor.status).toBe("running");
  });

  it("sends nothing in the system check (report off)", async () => {
    const h = await harness(() => []);
    h.monitor.stop();
    const quiet = await harness(() => [], false);
    quiet.run(20_000);
    quiet.monitor.stop();
    expect(quiet.events).toEqual([]);
    expect(quiet.snapshots).toEqual([]);
  });

  it("reports an unsupported browser instead of failing", async () => {
    const { FaceCheckUnsupportedError } = await import("../face/detector");
    const statuses: string[] = [];
    const monitor = new FacePresenceMonitor({
      report: true,
      createDetector: async () => {
        throw new FaceCheckUnsupportedError("WebAssembly SIMD is not available");
      },
      onStatus: (status) => statuses.push(status),
    });
    await monitor.start();
    expect(statuses).toEqual(["loading", "unsupported"]);
  });
});

// ------------------------------------------------------------------ room wiring

function fakeMonitor() {
  return { start: vi.fn(async () => undefined), stop: vi.fn(), finalize: vi.fn(), staticBoxes: vi.fn(() => [[0.7, 0.1, 0.1, 0.1]]) };
}

describe("RoomFaceCheck", () => {
  const stream = {} as MediaStream;

  it("never starts without the policy and the candidate's consent", async () => {
    const createMonitor = vi.fn();
    const off = new RoomFaceCheck({ stream, decision: false, emit: vi.fn(), captureSnapshot: vi.fn(), uploadSnapshot: vi.fn(), createMonitor });
    await off.start();
    const declined = new RoomFaceCheck({ stream, decision: null, checkConsent: async () => false, emit: vi.fn(), captureSnapshot: vi.fn(), uploadSnapshot: vi.fn(), createMonitor });
    await declined.start();
    expect(createMonitor).not.toHaveBeenCalled();
  });

  it("starts after the server confirms consent, uploads one snapshot per gap and stops on the server's flag", async () => {
    let t = 0;
    const monitor = fakeMonitor();
    let options: Parameters<NonNullable<ConstructorParameters<typeof RoomFaceCheck>[0]["createMonitor"]>>[0] | null = null;
    const uploadSnapshot = vi.fn(async (_image: Blob, _capturedAt: string, _faceCheck: Record<string, unknown>) => undefined);
    const emit = vi.fn();
    const check = new RoomFaceCheck({
      stream,
      decision: null,
      checkConsent: async () => true,
      emit,
      captureSnapshot: async () => new Blob(["jpeg"]),
      uploadSnapshot,
      now: () => t,
      createMonitor: (o) => {
        options = o;
        return monitor as unknown as FacePresenceMonitor;
      },
    });
    await check.start();
    expect(monitor.start).toHaveBeenCalledTimes(1);
    expect(options!.report).toBe(true);
    options!.onEvent!({ event_type: "face_not_visible", occurred_at: new Date().toISOString(), duration_ms: 5_000 });
    expect(emit).toHaveBeenCalledTimes(1);

    const request: FaceSnapshotRequest = { reason: "multiple_faces", episode: "mf_abcdef", durationMs: 3_000, startedAt: new Date(0).toISOString(), faces: 2, staticBoxes: [] };
    options!.onSnapshot!(request);
    await vi.waitFor(() => expect(uploadSnapshot).toHaveBeenCalledTimes(1));
    expect(uploadSnapshot.mock.calls[0]![2]).toEqual({ reason: "multiple_faces", episode: "mf_abcdef", duration_ms: 3_000, started_at: new Date(0).toISOString(), faces: 2, static_boxes: [] });
    t = 5_000;
    options!.onSnapshot!(request);
    t = 10_500;
    options!.onSnapshot!(request);
    await vi.waitFor(() => expect(uploadSnapshot).toHaveBeenCalledTimes(2));

    expect(check.periodicFrameHint()).toEqual({ static_boxes: [[0.7, 0.1, 0.1, 0.1]] });
    check.onServerFlags({ face_presence_check: false });
    expect(monitor.stop).toHaveBeenCalledTimes(1);
    expect(check.active).toBe(false);
  });

  it("turns a lasting framing problem into one gentle tip", async () => {
    vi.useFakeTimers();
    try {
      const monitor = fakeMonitor();
      let options: { onGuidance?: (g: "too_far" | null) => void } | null = null;
      const notify = vi.fn();
      const check = new RoomFaceCheck({
        stream,
        decision: true,
        emit: vi.fn(),
        captureSnapshot: vi.fn(),
        uploadSnapshot: vi.fn(),
        notify,
        createMonitor: (o) => {
          options = o as typeof options;
          return monitor as unknown as FacePresenceMonitor;
        },
      });
      await check.start();
      options!.onGuidance!("too_far");
      vi.advanceTimersByTime(8_000);
      expect(notify).toHaveBeenCalledWith("too_far", FACE_GUIDANCE_COPY.too_far);
      check.stop();
    } finally {
      vi.useRealTimers();
    }
  });
});
