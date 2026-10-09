import { describe, expect, it } from "vitest";

import { asServerMessage, parseServerMessage } from "@/lib/realtime/parse";

import {
  activityCue,
  activityDockMode,
  contentVisible,
  finishEnabled,
  finishLocksForS,
  normalizeContent,
  normalizeIntake,
  phaseCopy,
  reduceActivityPhase,
  reduceActivityStart,
  spokenDuration,
  type ActivityView,
} from "../room/activityModel";

function view(overrides: Partial<ActivityView> = {}): ActivityView {
  return {
    id: "a1",
    kind: "read_relay",
    stage: "reading",
    title: "Gradient Descent",
    phases: [],
    attempt: 1,
    resumed: false,
    status: "running",
    phase: "read",
    durationMs: 30_000,
    remainingMs: 30_000,
    syncedAtMs: 0,
    capture: false,
    canFinish: true,
    content: { title: "Gradient Descent", paragraphs: ["p1"], bullets: [], instruction: null },
    hiddenBy: null,
    relayReached: false,
    shownMs: 0,
    shownSinceMs: null,
    finishRequested: false,
    stale: false,
    endReason: null,
    endedAtMs: null,
    ...overrides,
  };
}

describe("parse guards for the activity messages", () => {
  it("accepts the three server messages with their required fields", () => {
    expect(parseServerMessage(JSON.stringify({ type: "activity.start", seq: 4, activity_id: "a1", kind: "read_relay", phases: [] }))?.type).toBe("activity.start");
    expect(asServerMessage({ type: "activity.phase", activity_id: "a1", phase: "read", status: "running", remaining_ms: 30_000 })?.type).toBe("activity.phase");
    expect(asServerMessage({ type: "activity.end", activity_id: "a1" })?.type).toBe("activity.end");
  });

  it("drops them when a required field is missing", () => {
    expect(asServerMessage({ type: "activity.start", activity_id: "a1", kind: "read_relay" })).toBeNull();
    expect(asServerMessage({ type: "activity.phase", activity_id: "a1", phase: "read", status: "running" })).toBeNull();
    expect(asServerMessage({ type: "activity.end" })).toBeNull();
    expect(asServerMessage({ type: "activity.unknown", activity_id: "a1" })).toBeNull();
  });
});

describe("slice reducers", () => {
  it("ignores malformed messages instead of crashing the room", () => {
    expect(reduceActivityStart(null, { type: "activity.start", seq: 1, activity_id: "a1", kind: "bogus" as never, stage: "reading", title: "t", phases: [], status: "armed", attempt: 1, resumed: false }, 0)).toBeNull();
    const prev = view();
    expect(reduceActivityPhase(prev, { type: "activity.phase", seq: 2, activity_id: "a1", kind: "read_relay", phase: "nope" as never, status: "running", duration_ms: 1, remaining_ms: 1, capture: false, can_finish: true, hide_content: false, server_time_ms: 0 }, 0)).toBe(prev);
  });

  it("normalises content and rejects empty bodies", () => {
    expect(normalizeContent({ title: "T", paragraphs: ["a", "", "b"] })).toEqual({ title: "T", paragraphs: ["a", "b"], bullets: [], instruction: null });
    expect(normalizeContent({ title: "T", paragraphs: [] })).toBeNull();
    expect(normalizeContent(null)).toBeNull();
  });

  it("normalises the snapshot without content", () => {
    const intake = normalizeIntake({ stage: "reading", stage_number: 4, stage_title: "Reading exercise", stages: [], activity: { activity_id: "a1", kind: "read_relay", phase: "relay", status: "running", remaining_ms: 5_000 }, answer_mode: "standard" });
    expect(intake?.activity).toEqual({ id: "a1", kind: "read_relay", phase: "relay", status: "running", remainingMs: 5_000 });
  });
});

describe("selectors", () => {
  it("shows the relay passage only during a running read with time left", () => {
    expect(contentVisible(view(), 1)).toBe(true);
    expect(contentVisible(view(), 0)).toBe(false);
    expect(contentVisible(view({ status: "between" }), 10_000)).toBe(false);
    expect(contentVisible(view({ phase: "relay" }), 10_000)).toBe(false);
    expect(contentVisible(view({ kind: "topic_card", phase: "speak" }), 0)).toBe(true);
    expect(contentVisible(view({ status: "ended" }), 10_000)).toBe(false);
  });

  it("unlocks finishing after 5 s of reading aloud and 10 s of silent reading", () => {
    const aloud = view({ kind: "reading_aloud", durationMs: 180_000, capture: true });
    expect(finishEnabled(aloud, 176_000)).toBe(false);
    expect(finishLocksForS(aloud, 176_000)).toBe(1);
    expect(finishEnabled(aloud, 175_000)).toBe(true);
    expect(finishEnabled(view(), 21_000)).toBe(false);
    expect(finishEnabled(view(), 20_000)).toBe(true);
    expect(finishEnabled(view({ kind: "topic_card", phase: "prepare", durationMs: 60_000 }), 60_000)).toBe(true);
    expect(finishEnabled(view({ canFinish: false }), 1_000)).toBe(false);
    expect(finishEnabled(view({ finishRequested: true }), 1_000)).toBe(false);
    expect(finishEnabled(view({ stale: true }), 1_000)).toBe(false);
  });

  it("tells the dock what it may offer", () => {
    expect(activityDockMode(null)).toBe("none");
    expect(activityDockMode(view({ status: "armed" }))).toBe("listening");
    expect(activityDockMode(view({ kind: "reading_aloud", capture: true }))).toBe("reading");
    expect(activityDockMode(view())).toBe("silent");
    expect(activityDockMode(view({ phase: "relay", capture: true }))).toBe("capture");
    expect(activityDockMode(view({ status: "ended" }))).toBe("ended");
  });

  it("words each phase plainly, with the finish labels from the design", () => {
    expect(phaseCopy(view({ kind: "reading_aloud" }), "Maya").finishLabel).toBe("Finish reading");
    expect(phaseCopy(view(), "Maya").finishLabel).toBe("I'm ready");
    expect(phaseCopy(view({ phase: "relay" }), "Maya").finishLabel).toBe("I'm done");
    expect(phaseCopy(view({ kind: "topic_card", phase: "prepare" }), "Maya").finishLabel).toBe("Start now");
    expect(phaseCopy(view({ kind: "topic_card", phase: "speak" }), "Maya").finishLabel).toBe("I'm done");
    expect(phaseCopy(view({ status: "armed" }), "Maya")).toMatchObject({ finishLabel: null, lede: "A short passage to read silently, then explain." });
    // Copy never mentions scores, ratings or evaluation.
    const all = (["reading_aloud", "read_relay", "topic_card"] as const)
      .flatMap((kind) => (["read", "relay", "prepare", "speak"] as const).map((phase) => phaseCopy(view({ kind, phase }), "Maya")))
      .map((c) => Object.values(c).join(" "))
      .join(" ");
    expect(all).not.toMatch(/\b(scores?|rating|assess\w*|evaluat\w*|pass|passed|fail\w*)\b/i);
  });

  it("speaks durations for the countdown label", () => {
    expect(spokenDuration(65)).toBe("1 minute 5 seconds");
    expect(spokenDuration(120)).toBe("2 minutes");
    expect(spokenDuration(1)).toBe("1 second");
    expect(spokenDuration(0)).toBe("0 seconds");
  });

  it("cues whose turn it is and what kind of time it is", () => {
    expect(activityCue(view({ status: "armed" }), "ai_speaking", "Maya")).toEqual({ text: "Maya is explaining the exercise", tone: "speaking" });
    expect(activityCue(view(), "listening", "Maya")).toEqual({ text: "Reading time", tone: "quiet" });
    expect(activityCue(view({ kind: "topic_card", phase: "prepare" }), "listening", "Maya").text).toBe("Preparation time");
    expect(activityCue(view({ phase: "relay", capture: true }), "candidate_speaking", "Maya")).toEqual({ text: "Listening", tone: "listening" });
  });
});
