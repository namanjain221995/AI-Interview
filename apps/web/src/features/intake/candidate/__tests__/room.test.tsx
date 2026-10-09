/**
 * The interview room with an intake session: the real InterviewRoom, a fake
 * runtime (the store is driven directly) and a stub interviewer. Covers the
 * stage indicator, the activity layout (frameless orb, no subtitles, no Done
 * answering button), the finish wiring and the return to the conversation.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/Tooltip";
import { normalizeInterview } from "@/features/candidate/api";
import type { InterviewAction, InterviewStore } from "@/features/realtime/store";

import { activityDockMode } from "../room/activityModel";
import { activityEnd, activityPhase, activityStart, sessionState } from "./fixtures";

const runtimes: FakeRuntime[] = [];

class FakeRuntime {
  readonly store: InterviewStore;
  activityFinish = vi.fn(() => true);
  endTurn = vi.fn();
  repeat = vi.fn();
  sendTypedAnswer = vi.fn(() => true);
  endInterview = vi.fn();
  constructor(options: { store: InterviewStore }) {
    this.store = options.store;
    runtimes.push(this);
  }
  prepareGesture() {}
  async begin() {
    this.store.dispatch({ type: "begun" });
    this.store.dispatch({ type: "connection", status: "connected", at: performance.now() });
  }
  setAvatar() {}
  dispose() {}
  typingAllowed() {
    const mode = activityDockMode(this.store.getState().activity);
    return mode === "none" || mode === "capture";
  }
  onLevel() {
    return () => undefined;
  }
  onMedia() {
    return () => undefined;
  }
  get cameraStream() {
    return null;
  }
  get hasMicrophone() {
    return true;
  }
  get isEnded() {
    return false;
  }
  requestFullscreen() {}
  retryConnection() {}
  recordScreenShareEvent() {}
}

vi.mock("@/features/interview/runtime", () => ({
  InterviewRuntime: FakeRuntime,
  MediaAccessError: class MediaAccessError extends Error {},
}));

vi.mock("@/features/avatar", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/avatar")>()),
  createAvatarController: () => ({
    getStatus: () => ({ mode: "3d", loaded: true, fps: 60 }),
    subscribe: () => () => undefined,
    dispose: () => undefined,
    setState: () => undefined,
  }),
  AvatarStage: () => <div data-testid="orb-canvas" />,
}));

vi.mock("@/features/candidate/readiness", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/candidate/readiness")>()),
  useInterviewReadiness: () => ({ ready: true, phase: "ready", resume: null, sessionStatus: "ready", canStartAnyway: false }),
}));

const { InterviewRoom } = await import("@/features/interview/InterviewRoom");

const INTERVIEW = normalizeInterview({
  id: "i1",
  status: "in_progress",
  organization: { name: "TechSara Solutions" },
  job: { id: "j1", title: "AI/ML Training Program — Intake" },
  rounds_offered: [{ id: "r1", name: "Intake Interview", round_type: "intake", duration_minutes: 45 }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: {
    integrity: { enabled: true, require_fullscreen: false, camera_required: false, camera_frame_analysis: false, monitor_focus: true },
    recording: { record_answer_audio: true, record_video: false, allow_typed_answers: true, show_live_subtitles: true },
  },
  steps_completed: ["welcome", "instructions", "consent", "resume", "details", "resume-review", "system-check"],
  expires_at: "2026-10-16T12:00:00Z",
  interviewer_name: "Maya",
  latest_session: { id: "s1", status: "ready" },
  intake: { intake_id: "in1", title: "Intake Interview", estimated_minutes: 45, presence: "orb" },
});

function renderRoom() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={["/candidate/i1/interview"]}>
          <InterviewRoom interview={INTERVIEW} sessionId="s1" sessionStatus="ready" />
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

async function begin(): Promise<FakeRuntime> {
  renderRoom();
  fireEvent.click(screen.getByTestId("interview-begin-button"));
  await waitFor(() => expect(runtimes).toHaveLength(1));
  const runtime = runtimes[0]!;
  await waitFor(() => expect(screen.queryByTestId("interview-begin-button")).toBeNull());
  return runtime;
}

function send(runtime: FakeRuntime, ...actions: InterviewAction[]) {
  act(() => {
    for (const action of actions) runtime.store.dispatch(action);
  });
}

beforeEach(() => {
  runtimes.length = 0;
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("interview room, intake session", () => {
  it("shows the stage in the top bar with the seven-part strip", async () => {
    const runtime = await begin();
    send(runtime, sessionState({}, performance.now()));
    const indicator = screen.getByTestId("intake-stage-indicator");
    expect(indicator).toHaveAttribute("data-stage", "reading");
    expect(indicator).toHaveAttribute("data-stage-number", "4");
    expect(indicator).toHaveTextContent("Part 4 of 7");
    expect(indicator).toHaveTextContent("Reading exercise");
    const strip = screen.getByTestId("intake-stage-strip");
    expect([...strip.children].map((li) => li.getAttribute("data-status"))).toEqual(["done", "done", "done", "current", "pending", "pending", "pending"]);
    // Ordinary layout until an exercise starts.
    expect(screen.getByTestId("subtitle-interviewer")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).toBeNull();
  });

  it("switches to the exercise layout: frameless interviewer, no subtitles, the panel owns the finish action", async () => {
    const runtime = await begin();
    send(runtime, sessionState({}, performance.now()), activityStart("read_relay", {}, performance.now()));
    const room = screen.getByTestId("interview-room");
    expect(room).toHaveAttribute("data-activity-kind", "read_relay");
    expect(room).toHaveAttribute("data-activity-phase", "armed");
    const stage = screen.getByTestId("activity-stage");
    const avatar = within(stage).getByTestId("avatar-stage");
    // The interviewer floats: no frame, ring or fill on the slot that holds it.
    const slot = avatar.parentElement!.parentElement!;
    expect(slot.className).not.toMatch(/ring|border|bg-|shadow/);
    expect(screen.queryByTestId("subtitle-interviewer")).toBeNull();
    expect(screen.queryByTestId("subtitle-candidate")).toBeNull();
    expect(screen.queryByTestId("done-answering-button")).toBeNull();
    expect(screen.getByTestId("repeat-question-button")).toHaveAccessibleName("Repeat instructions");

    send(runtime, activityPhase("read_relay", "read", {}, performance.now()));
    expect(room).toHaveAttribute("data-activity-phase", "read");
    expect(screen.getByTestId("activity-passage")).toBeInTheDocument();
    expect(screen.getByTestId("type-answer-button")).toBeDisabled();
    expect(screen.getByTestId("repeat-question-button")).toBeDisabled();
    expect(screen.getByTestId("activity-cue")).toHaveTextContent("Reading time");

    send(runtime, activityPhase("read_relay", "read", { status: "ended", remaining_ms: 0, hide_content: true, content: null }, performance.now()));
    send(runtime, activityPhase("read_relay", "relay", {}, performance.now()));
    expect(screen.queryByTestId("activity-passage")).toBeNull();
    expect(screen.getByTestId("type-answer-button")).toBeEnabled();
    expect(screen.getByTestId("activity-cue")).toHaveTextContent("Listening");
    fireEvent.click(screen.getByTestId("activity-finish-button"));
    expect(runtime.activityFinish).toHaveBeenCalledWith("act-read_relay", "relay");
  });

  it("says Done, then returns to the conversation layout", async () => {
    const runtime = await begin();
    send(runtime, sessionState({}, performance.now()), activityStart("topic_card", {}, performance.now()), activityPhase("topic_card", "prepare", {}, performance.now()));
    fireEvent.click(screen.getByTestId("activity-finish-button"));
    expect(runtime.activityFinish).toHaveBeenCalledWith("act-topic_card", "prepare");
    send(runtime, activityPhase("topic_card", "speak", {}, performance.now()), activityEnd("topic_card", "completed", performance.now()));
    expect(within(screen.getByTestId("activity-panel")).getByText("Done")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("activity-panel")).toBeNull(), { timeout: 3000 });
    expect(screen.getByTestId("subtitle-interviewer")).toBeInTheDocument();
    expect(screen.getByTestId("done-answering-button")).toBeInTheDocument();
    expect(screen.getByTestId("interview-room")).not.toHaveAttribute("data-activity-kind");
  });

  it("closes the typed-answer panel when reading aloud starts", async () => {
    const runtime = await begin();
    send(runtime, sessionState({ turn_state: "listening" }, performance.now()), { type: "server", msg: { seq: 99, type: "turn.state", state: "listening" } as never, at: performance.now() });
    fireEvent.click(screen.getByTestId("type-answer-button"));
    expect(screen.getByTestId("typed-answer-input")).toBeInTheDocument();
    send(runtime, activityStart("reading_aloud", {}, performance.now()), activityPhase("reading_aloud", "read", {}, performance.now()));
    await waitFor(() => expect(screen.queryByTestId("typed-answer-input")).toBeNull());
  });
});
