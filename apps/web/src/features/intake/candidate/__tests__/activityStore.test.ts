/**
 * The room store's intake slice: activity lifecycle, server-authoritative timers
 * and the read-and-relay passage secrecy rules (INTAKE_INTERVIEW.md §7.8, §7.9).
 */
import { describe, expect, it } from "vitest";

import { initialInterviewState, reduceInterview, type InterviewAction, type InterviewViewState } from "@/features/realtime/store";

import { activityRemainingMs, contentVisible } from "../room/activityModel";
import { activityEnd, activityPhase, activityStart, PASSAGE_B, server, sessionState } from "./fixtures";

function apply(actions: InterviewAction[], state: InterviewViewState = initialInterviewState()): InterviewViewState {
  return actions.reduce(reduceInterview, state);
}

const connected = (at = 0): InterviewAction => ({ type: "connection", status: "connected", at });
const dropped = (at: number): InterviewAction => ({ type: "connection", status: "reconnecting", at });

/** Every string reachable from a value (to prove the passage is nowhere in the state). */
function allText(value: unknown): string {
  return JSON.stringify(value);
}

describe("intake stage state", () => {
  it("takes the stage, titles and statuses from session.state", () => {
    const state = apply([connected(), sessionState()]);
    expect(state.intake).toMatchObject({ stage: "reading", stageNumber: 4, stageTitle: "Reading exercise", answerMode: "standard" });
    expect(state.intake?.stages.map((s) => s.status)).toEqual(["done", "done", "done", "current", "pending", "pending", "pending"]);
  });

  it("is null for every other round type, and survives a state without the field", () => {
    expect(apply([sessionState({ intake: null })]).intake).toBeNull();
    const kept = apply([sessionState(), sessionState({ intake: undefined })]);
    expect(kept.intake?.stage).toBe("reading");
  });

  it("falls back to the candidate-safe titles when the server sends no stages", () => {
    const state = apply([sessionState({ intake: { stage: "topic", stage_number: 5, stages: [], answer_mode: "long", soft_limit_s: 90 } })]);
    expect(state.intake?.stages.map((s) => s.title)).toEqual(["Introduction", "Your experience", "Standard questions", "Reading exercise", "Speaking exercise", "Learning and growth", "Your questions"]);
    expect(state.intake?.stages[4]?.status).toBe("current");
    expect(state.intake?.stageTitle).toBe("Speaking exercise");
    expect(state.intake?.softLimitS).toBe(90);
  });
});

describe("activity lifecycle", () => {
  it("arms without content, runs a phase with content, and counts down from receipt", () => {
    let state = apply([connected(), sessionState(), activityStart("reading_aloud", {}, 100)]);
    expect(state.activity).toMatchObject({ kind: "reading_aloud", status: "armed", phase: null, content: null, title: "What Is an API?" });
    state = apply([activityPhase("reading_aloud", "read", { remaining_ms: 180_000 }, 1_000)], state);
    expect(state.activity).toMatchObject({ status: "running", phase: "read", capture: true, canFinish: true });
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
    expect(activityRemainingMs(state.activity, 61_000, true)).toBe(120_000);
    // A paused clock (disconnected) does not count down.
    expect(activityRemainingMs(state.activity, 61_000, false)).toBe(180_000);
  });

  it("ends with a reason, drops the content and is dismissed after the brief Done", () => {
    let state = apply([connected(), activityStart("reading_aloud"), activityPhase("reading_aloud", "read"), activityEnd("reading_aloud", "finished_early", 5_000)]);
    expect(state.activity).toMatchObject({ status: "ended", endReason: "finished_early", content: null, endedAtMs: 5_000 });
    state = apply([{ type: "activity.dismiss", activityId: "other" }], state);
    expect(state.activity).not.toBeNull();
    state = apply([{ type: "activity.dismiss", activityId: "act-reading_aloud" }], state);
    expect(state.activity).toBeNull();
  });

  it("keeps the topic card from preparation to speaking", () => {
    let state = apply([connected(), activityStart("topic_card"), activityPhase("topic_card", "prepare")]);
    expect(state.activity?.content?.bullets).toHaveLength(4);
    state = apply([activityPhase("topic_card", "prepare", { status: "ended", remaining_ms: 0 })], state);
    expect(state.activity).toMatchObject({ status: "between", phase: "prepare" });
    expect(state.activity?.content?.bullets).toHaveLength(4);
    state = apply([activityPhase("topic_card", "speak", { content: null })], state);
    expect(state.activity).toMatchObject({ status: "running", phase: "speak", capture: true });
    expect(state.activity?.content?.title).toBe("Working From Home vs. Working in an Office");
  });

  it("marks a finish request and clears it when the server answers", () => {
    let state = apply([connected(), activityStart("topic_card"), activityPhase("topic_card", "prepare")]);
    state = apply([{ type: "activity.finish_requested", activityId: "act-topic_card", phase: "prepare", at: 10 }], state);
    expect(state.activity?.finishRequested).toBe(true);
    // A request for another phase or activity is ignored.
    expect(apply([{ type: "activity.finish_requested", activityId: "act-topic_card", phase: "speak", at: 11 }], apply([connected(), activityStart("topic_card"), activityPhase("topic_card", "prepare")])).activity?.finishRequested).toBe(false);
    state = apply([activityPhase("topic_card", "prepare", { status: "ended", remaining_ms: 0 })], state);
    expect(state.activity?.finishRequested).toBe(false);
  });

  it("clears the activity when the session ends", () => {
    const state = apply([connected(), activityStart("reading_aloud"), activityPhase("reading_aloud", "read"), server({ type: "session.ended", reason: "completed", report_status: "pending" })]);
    expect(state.activity).toBeNull();
  });

  it("restarts cleanly on a new attempt after a disconnect", () => {
    let state = apply([connected(), activityStart("reading_aloud"), activityPhase("reading_aloud", "read", { remaining_ms: 90_000 })]);
    state = apply([activityStart("reading_aloud", { attempt: 2, resumed: true })], state);
    expect(state.activity).toMatchObject({ status: "armed", attempt: 2, resumed: true, phase: null, remainingMs: null });
  });
});

describe("read-and-relay passage secrecy", () => {
  const reading = (at = 0) => apply([connected(), sessionState(), activityStart("read_relay"), activityPhase("read_relay", "read", { remaining_ms: 30_000 }, at)]);

  it("never takes content from activity.start or the session.state snapshot", () => {
    let state = apply([connected(), activityStart("read_relay", { content: { title: "x", paragraphs: ["leak"] } })]);
    expect(state.activity?.content).toBeNull();
    state = apply([sessionState({ intake: { stage: "reading", stage_number: 4, stages: [], activity: { activity_id: "act-read_relay", kind: "read_relay", phase: "read", status: "running", remaining_ms: 20_000, content: { paragraphs: ["leak"] } } } })], state);
    expect(allText(state)).not.toContain("leak");
  });

  it("hides the passage on the server's message at 30 seconds", () => {
    let state = reading();
    expect(contentVisible(state.activity, 29_000)).toBe(true);
    state = apply([activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null })], state);
    expect(state.activity).toMatchObject({ status: "between", content: null, hiddenBy: "server" });
    expect(allText(state)).not.toContain("foggy hill");
  });

  it("hides at the local countdown zero even when the server is late", () => {
    const state = reading(1_000);
    expect(contentVisible(state.activity, activityRemainingMs(state.activity, 30_999, true))).toBe(true);
    expect(contentVisible(state.activity, activityRemainingMs(state.activity, 31_000, true))).toBe(false);
    const hidden = apply([{ type: "activity.hide_local", reason: "countdown", activityId: "act-read_relay", at: 31_000 }], state);
    expect(hidden.activity).toMatchObject({ content: null, hiddenBy: "countdown", canFinish: false });
    expect(allText(hidden)).not.toContain("foggy hill");
  });

  it("removes the passage the moment the connection drops, and freezes the clock", () => {
    const state = apply([dropped(11_000)], reading(1_000));
    expect(state.activity).toMatchObject({ content: null, hiddenBy: "disconnect", stale: true, remainingMs: 20_000 });
    expect(allText(state)).not.toContain("foggy hill");
    // Hidden too by the explicit local action, idempotently.
    expect(apply([{ type: "activity.hide_local", reason: "disconnect", at: 12_000 }], state).activity?.content).toBeNull();
  });

  it("re-shows only the remaining reading time after a reconnect", () => {
    let state = apply([dropped(11_000), connected(15_000)], reading(1_000));
    state = apply([sessionState({ intake: { stage: "reading", stage_number: 4, stages: [], activity: { activity_id: "act-read_relay", kind: "read_relay", phase: "read", status: "running", remaining_ms: 20_000 } } }, 15_100)], state);
    expect(state.activity).toMatchObject({ stale: false, content: null, remainingMs: 20_000 });
    state = apply([activityStart("read_relay", { resumed: true }, 15_200), activityPhase("read_relay", "read", { remaining_ms: 19_800 }, 15_300)], state);
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
    expect(activityRemainingMs(state.activity, 15_300, true)).toBe(19_800);
  });

  it("never shows content in the relay phase, nor again once relay was reached", () => {
    let state = apply([activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null })], reading());
    state = apply([activityPhase("read_relay", "relay", { content: { title: "Gradient Descent", paragraphs: ["leak relay"] }, hide_content: false })], state);
    expect(state.activity).toMatchObject({ phase: "relay", content: null, relayReached: true, capture: true });
    // A resumed relay and even a (wrong) read message cannot bring it back.
    state = apply([activityStart("read_relay", { resumed: true }), activityPhase("read_relay", "read", { remaining_ms: 10_000 })], state);
    expect(state.activity?.content).toBeNull();
    expect(allText(state)).not.toContain("leak relay");
    expect(allText(state)).not.toContain("foggy hill");
  });

  it("keeps the passage on I'm ready until the server ends the phase (a refused finish costs no reading time)", () => {
    let state = apply([{ type: "activity.finish_requested", activityId: "act-read_relay", phase: "read", at: 12_000 }], reading());
    expect(state.activity).toMatchObject({ finishRequested: true });
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
    // Refused (pressed before 10 s): a calm hint, and the button works again.
    state = apply([server({ type: "error", code: "finish_not_available", message: "You can finish in a few seconds.", recoverable: true })], state);
    expect(state.activity).toMatchObject({ finishRequested: false });
    expect(state.notices.at(-1)).toMatchObject({ level: "info", code: "finish_not_available", message: "You can finish in a few seconds." });
    // Accepted: the server's ended message hides it at once.
    state = apply([activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null }, 13_000)], state);
    expect(state.activity?.content).toBeNull();
  });

  it("keeps the passage when the delayed finish button is announced without content", () => {
    let state = apply([activityPhase("read_relay", "read", { can_finish: false }, 0)], reading());
    expect(state.activity?.canFinish).toBe(false);
    state = apply([activityPhase("read_relay", "read", { can_finish: true, content: null, remaining_ms: 20_000 }, 10_000)], state);
    expect(state.activity).toMatchObject({ canFinish: true, remainingMs: 20_000, syncedAtMs: 10_000 });
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
  });

  it("never re-shows the passage past its 30 seconds in total, whatever a resumed phase says", () => {
    // Shown 25 s, then the connection drops.
    let state = apply([dropped(25_000), connected(30_000)], reading(0));
    expect(state.activity?.shownMs).toBe(25_000);
    // The server re-shows it for the 5 s left: allowed.
    state = apply([activityStart("read_relay", { resumed: true }, 30_100), activityPhase("read_relay", "read", { remaining_ms: 5_000, duration_ms: 5_000 }, 30_200)], state);
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
    // Those 5 s run out and it drops again; a (wrong) further re-show is refused by the client.
    state = apply([dropped(35_200), connected(40_000), activityStart("read_relay", { resumed: true }, 40_100), activityPhase("read_relay", "read", { remaining_ms: 8_000 }, 40_200)], state);
    expect(state.activity?.shownMs).toBe(30_000);
    expect(state.activity?.content).toBeNull();
    expect(allText(state)).not.toContain("foggy hill");
  });

  it("turns the other activity refusals into calm hints", () => {
    const state = apply([
      server({ type: "error", code: "typed_not_allowed_for_reading", message: "", recoverable: true }),
      server({ type: "error", code: "activity_in_progress", message: "Please wait for the exercise to continue.", recoverable: true }),
    ]);
    expect(state.notices.map((n) => [n.code, n.level, n.message])).toEqual([
      ["typed_not_allowed_for_reading", "info", "The reading exercise needs your voice: please read the passage aloud."],
      ["activity_in_progress", "info", "Please wait for the exercise to continue."],
    ]);
    expect(state.fatalError).toBeNull();
  });

  it("forgets an activity that ended while disconnected (end messages are never replayed)", () => {
    let state = apply([dropped(5_000), connected(9_000)], reading());
    state = apply([sessionState({ intake: { stage: "topic", stage_number: 5, stages: [], activity: null } }, 9_100)], state);
    expect(state.activity).toBeNull();
  });

  it("trusts live messages over a session.state pushed while connected", () => {
    const state = apply([sessionState({ intake: { stage: "reading", stage_number: 4, stages: [], activity: null } })], reading());
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
  });

  it("does not let a late snapshot resurrect a finished activity", () => {
    let state = apply([activityEnd("read_relay"), { type: "activity.dismiss", activityId: "act-read_relay" }], reading());
    state = apply([sessionState({ intake: { stage: "reading", stage_number: 4, stages: [], activity: { activity_id: "act-read_relay", kind: "read_relay", phase: "relay", status: "running", remaining_ms: 100_000 } } })], state);
    expect(state.activity).toBeNull();
  });

  it("other activities keep their content through a dropped connection (the server restarts them)", () => {
    const state = apply([connected(), activityStart("reading_aloud"), activityPhase("reading_aloud", "read"), dropped(5_000)]);
    expect(state.activity?.content?.paragraphs).toEqual(PASSAGE_B);
    expect(state.activity?.stale).toBe(true);
  });
});
