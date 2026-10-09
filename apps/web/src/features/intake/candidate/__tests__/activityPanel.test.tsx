import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createInterviewStore, type InterviewStore } from "@/features/realtime/store";

import { ActivityPanel, FONT_STEPS } from "../room/ActivityPanel";
import { activityEnd, activityPhase, activityStart, sessionState } from "./fixtures";

function setup() {
  const store = createInterviewStore();
  const onFinish = vi.fn();
  const now = performance.now();
  store.dispatch({ type: "connection", status: "connected", at: now });
  store.dispatch(sessionState({}, now));
  render(<ActivityPanel store={store} onFinish={onFinish} recording micMeter={<span data-testid="meter" />} interviewId="i1" />);
  return { store, onFinish };
}

function dispatch(store: InterviewStore, action: Parameters<InterviewStore["dispatch"]>[0]) {
  act(() => store.dispatch(action));
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "performance", "Date"] });
  sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ActivityPanel", () => {
  it("asks the candidate to listen while the instructions play, without content or a timer", () => {
    const { store } = setup();
    dispatch(store, activityStart("read_relay", {}, performance.now()));
    const panel = screen.getByTestId("activity-panel");
    expect(panel).toHaveAttribute("data-kind", "read_relay");
    expect(panel).toHaveAttribute("data-status", "armed");
    expect(screen.getByTestId("activity-title")).toHaveTextContent("Gradient Descent");
    expect(screen.getByText("Listen to the instructions")).toBeInTheDocument();
    expect(screen.getByText("A short passage to read silently, then explain.")).toBeInTheDocument();
    expect(screen.getByText(/30 seconds to read the passage\. Then it disappears/)).toBeInTheDocument();
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(screen.queryByTestId("activity-countdown")).toBeNull();
    expect(screen.queryByTestId("activity-finish-button")).toBeNull();
  });

  it("shows the passage for reading aloud, with recording status, and unlocks Finish reading after 5 s", () => {
    const { store, onFinish } = setup();
    dispatch(store, activityStart("reading_aloud", {}, performance.now()));
    dispatch(store, activityPhase("reading_aloud", "read", {}, performance.now()));
    const passage = screen.getByTestId("activity-passage");
    expect(passage).toHaveAttribute("lang", "en");
    expect(passage).toHaveTextContent("foggy hill");
    expect(passage.className).not.toContain("select-none");
    expect(screen.getByTestId("activity-mic-status")).toHaveAttribute("data-capture", "true");
    expect(screen.getByTestId("activity-mic-status")).toHaveTextContent("Recording");
    expect(screen.getByTestId("meter")).toBeInTheDocument();
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "180");
    expect(screen.getByTestId("activity-countdown")).toHaveAccessibleName("3 minutes left");
    const finish = screen.getByTestId("activity-finish-button");
    expect(finish).toHaveTextContent("Finish reading");
    expect(finish).toBeDisabled();
    expect(finish).toHaveAccessibleDescription(/Available in 5 seconds/);
    advance(5_000);
    expect(screen.getByTestId("activity-finish-button")).toBeEnabled();
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "175");
    fireEvent.click(screen.getByTestId("activity-finish-button"));
    expect(onFinish).toHaveBeenCalledWith("act-reading_aloud", "read");
    expect(screen.getByTestId("activity-announcer")).toHaveTextContent("Reading time has started. Read the passage aloud.");
  });

  it("removes the read-and-relay passage at its own zero, announces it, and never shows it in the relay", () => {
    const { store, onFinish } = setup();
    dispatch(store, activityStart("read_relay", {}, performance.now()));
    dispatch(store, activityPhase("read_relay", "read", {}, performance.now()));
    const passage = screen.getByTestId("activity-passage");
    expect(passage.className).toContain("select-none");
    expect(screen.getByTestId("activity-mic-status")).toHaveTextContent("Not recording");
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "30");
    expect(screen.getByTestId("activity-finish-button")).toHaveTextContent("I'm ready");
    expect(screen.getByTestId("activity-finish-button")).toBeDisabled();
    advance(10_000);
    expect(screen.getByTestId("activity-finish-button")).toBeEnabled();
    advance(10_000);
    expect(screen.getByTestId("activity-announcer")).toHaveTextContent("10 seconds left.");
    // The server's hide is late: the passage still goes at the local zero.
    advance(10_000);
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(screen.getByTestId("activity-hidden-notice")).toHaveTextContent("The passage is hidden");
    dispatch(store, { type: "activity.hide_local", reason: "countdown", activityId: "act-read_relay", at: performance.now() });
    expect(screen.getByTestId("activity-announcer")).toHaveTextContent("The passage is now hidden.");
    dispatch(store, activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null }, performance.now()));
    expect(screen.getByTestId("activity-panel")).toHaveAttribute("data-status", "between");
    expect(screen.getByText(/won't be shown again/)).toBeInTheDocument();
    dispatch(store, activityPhase("read_relay", "relay", { content: { title: "Gradient Descent", paragraphs: ["leak"] } }, performance.now()));
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(document.body.textContent).not.toContain("leak");
    expect(document.body.textContent).not.toContain("foggy hill");
    expect(screen.getByTestId("activity-hidden-notice")).toHaveTextContent("Explain it in your own words");
    expect(screen.getByTestId("activity-mic-status")).toHaveAttribute("data-capture", "true");
    const done = screen.getByTestId("activity-finish-button");
    expect(done).toHaveTextContent("I'm done");
    expect(done).toBeEnabled();
    fireEvent.click(done);
    expect(onFinish).toHaveBeenLastCalledWith("act-read_relay", "relay");
  });

  it("hides the passage while the connection is down", () => {
    const { store } = setup();
    dispatch(store, activityStart("read_relay", {}, performance.now()));
    dispatch(store, activityPhase("read_relay", "read", {}, performance.now()));
    advance(4_000);
    dispatch(store, { type: "connection", status: "reconnecting", at: performance.now() });
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(screen.getByTestId("activity-hidden-notice")).toHaveTextContent("hidden while we reconnect");
    expect(screen.getByTestId("activity-finish-button")).toBeDisabled();
  });

  it("shows the topic card's four points while preparing, then while speaking", () => {
    const { store, onFinish } = setup();
    dispatch(store, activityStart("topic_card", {}, performance.now()));
    dispatch(store, activityPhase("topic_card", "prepare", {}, performance.now()));
    const bullets = screen.getByTestId("activity-card-bullets");
    expect(bullets.querySelectorAll("li")).toHaveLength(4);
    expect(bullets).toHaveTextContent("Which you would choose, and why");
    expect(screen.getByText(/connecting all four points below/)).toBeInTheDocument();
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "60");
    expect(screen.getByTestId("activity-countdown")).toHaveTextContent("to prepare");
    expect(screen.getByTestId("activity-mic-status")).toHaveTextContent("Not recording");
    fireEvent.click(screen.getByTestId("activity-finish-button"));
    expect(onFinish).toHaveBeenCalledWith("act-topic_card", "prepare");
    // The runtime marks the request (activityFinish); the button waits for the server.
    dispatch(store, { type: "activity.finish_requested", activityId: "act-topic_card", phase: "prepare", at: performance.now() });
    expect(screen.getByTestId("activity-finish-button")).toHaveTextContent("One moment");
    dispatch(store, activityPhase("topic_card", "speak", {}, performance.now()));
    expect(screen.getByTestId("activity-card-bullets").querySelectorAll("li")).toHaveLength(4);
    expect(screen.getByTestId("activity-mic-status")).toHaveTextContent("Recording");
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "120");
  });

  it("changes the text size and the page within limits, and remembers them (never the passage)", () => {
    const { store } = setup();
    dispatch(store, activityStart("reading_aloud", {}, performance.now()));
    dispatch(store, activityPhase("reading_aloud", "read", {}, performance.now()));
    const size = () => Number.parseInt(screen.getByTestId("activity-passage").style.fontSize, 10);
    expect(size()).toBe(18);
    expect(screen.getByTestId("activity-font-smaller")).toBeDisabled();
    fireEvent.click(screen.getByTestId("activity-font-larger"));
    expect(size()).toBe(20);
    for (let i = 0; i < 6; i += 1) fireEvent.click(screen.getByTestId("activity-font-larger"));
    expect(size()).toBe(FONT_STEPS[FONT_STEPS.length - 1]);
    expect(screen.getByTestId("activity-font-larger")).toBeDisabled();
    const toggle = screen.getByTestId("activity-contrast-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("activity-body")).toHaveAttribute("data-page", "light");
    expect(JSON.parse(sessionStorage.getItem("interview:i1:reading") ?? "null")).toEqual({ size: 28, light: true });
    expect(JSON.stringify(Object.entries(sessionStorage))).not.toContain("foggy");
  });

  it("says Done briefly when the exercise ends", () => {
    const { store } = setup();
    dispatch(store, activityStart("reading_aloud", {}, performance.now()));
    dispatch(store, activityPhase("reading_aloud", "read", {}, performance.now()));
    dispatch(store, activityEnd("reading_aloud", "completed", performance.now()));
    expect(screen.getByTestId("activity-panel")).toHaveAttribute("data-status", "ended");
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(screen.queryByTestId("activity-finish-button")).toBeNull();
  });
});

describe("ActivityPanel after a reconnect", () => {
  it("shows a re-shown reading window as its share of the 30 seconds, and thanks after a capture", () => {
    const { store } = setup();
    dispatch(store, activityStart("read_relay", { resumed: true }, performance.now()));
    dispatch(store, activityPhase("read_relay", "read", { duration_ms: 12_000, remaining_ms: 12_000 }, performance.now()));
    const bar = screen.getByTestId("activity-panel").querySelector<HTMLElement>('[aria-hidden="true"] > div')!;
    expect(bar.style.width).toBe("40%");
    expect(screen.getByTestId("activity-countdown")).toHaveAttribute("data-remaining-s", "12");
    dispatch(store, activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null }, performance.now()));
    dispatch(store, activityPhase("read_relay", "relay", {}, performance.now()));
    dispatch(store, activityPhase("read_relay", "relay", { status: "ended", remaining_ms: 0 }, performance.now()));
    expect(screen.getByTestId("activity-panel")).toHaveAttribute("data-status", "between");
    expect(screen.getByText("Thank you. One moment.")).toBeInTheDocument();
    expect(screen.queryByText(/won't be shown again/)).toBeNull();
  });
});
