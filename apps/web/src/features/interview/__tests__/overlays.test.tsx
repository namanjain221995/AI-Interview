import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_INTEGRITY } from "@/features/integrity/policy";
import { createInterviewStore } from "@/features/realtime/store";

import { BeginGate, NoticeArea, offlineCopy, type BeginGateProps } from "../components/Overlays";
import { MonitoringNotice, turnCue } from "../components/RoomChrome";

function gate(over: Partial<BeginGateProps> = {}) {
  const props: BeginGateProps = {
    preparation: { ready: true, phase: "ready", canStartAnyway: false, onStartAnyway: vi.fn(), resumeHref: "/candidate/i1/resume" },
    interviewerName: "Maya",
    roundName: "Combined AI Round",
    minutes: 30,
    resuming: false,
    integrity: DEFAULT_INTEGRITY,
    screenRequired: false,
    screenShared: false,
    onShareScreen: vi.fn(),
    onBegin: vi.fn(),
    starting: false,
    error: null,
    systemCheckHref: "/candidate/i1/system-check",
    ...over,
  };
  render(
    <MemoryRouter>
      <BeginGate {...props} />
    </MemoryRouter>,
  );
  return props;
}

describe("BeginGate", () => {
  it("starts the interview and states the monitoring honestly", () => {
    const props = gate();
    expect(screen.getByText("Full screen turns on when you begin")).toBeInTheDocument();
    expect(screen.getByText(/A browser can't lock your computer/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("interview-begin-button"));
    expect(props.onBegin).toHaveBeenCalled();
  });

  it("waits while the interview is being prepared from the résumé, with real progress", () => {
    const resume = {
      resumeId: "r1",
      createdAt: new Date(Date.now() - 2 * 60_000).toISOString(),
      filename: "cv.pdf",
      sizeBytes: 1,
      status: "analyzing",
      statusDetail: null,
      steps: [
        { key: "extracting", label: "Reading your résumé", state: "done" as const, at: null },
        { key: "analyzing", label: "Understanding your experience", state: "active" as const, at: new Date(Date.now() - 2 * 60_000).toISOString() },
      ],
      error: null,
      profileReady: false,
      extracted: true,
      summary: null,
      percent: null,
    };
    const props = gate({ preparation: { ready: false, phase: "analysing", activity: "Understanding your experience", resume, canStartAnyway: true, onStartAnyway: vi.fn(), resumeHref: "/r" } });
    expect(screen.getByRole("heading", { name: "Your interview is almost ready" })).toBeInTheDocument();
    const begin = screen.getByTestId("interview-begin-button");
    expect(begin).toBeDisabled();
    expect(begin).toHaveTextContent("Begin interview");
    expect(begin).toHaveAccessibleDescription(/Understanding your experience/);
    expect(screen.getByText("Understanding your experience")).toBeInTheDocument();
    expect(screen.getByText(/About 2 minutes so far · usually about 3 minutes/)).toBeInTheDocument();
    expect(screen.getByText(/Begin unlocks automatically/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Start without waiting"));
    expect(props.preparation.onStartAnyway).toHaveBeenCalled();
    expect(screen.getByText("The first questions may be more general.")).toBeInTheDocument();
  });

  it("lists what is observed instead of one long sentence", () => {
    gate();
    expect(screen.getByRole("heading", { name: "What's observed during the interview" })).toBeInTheDocument();
    expect(screen.getByText("When you switch to another tab or window")).toBeInTheDocument();
    // Full screen is stated once, as what happens on Begin.
    expect(screen.queryByText("Whether the interview stays in full screen")).not.toBeInTheDocument();
  });

  it("asks for a new résumé when the analysis failed", () => {
    gate({ preparation: { ready: false, phase: "resume_failed", canStartAnyway: false, onStartAnyway: vi.fn(), resumeHref: "/candidate/i1/resume" } });
    expect(screen.getByRole("link", { name: "Upload it again" })).toHaveAttribute("href", "/candidate/i1/resume");
    expect(screen.getByTestId("interview-begin-button")).toBeDisabled();
  });

  it("requires the screen to be shared when the policy says so", () => {
    gate({ screenRequired: true });
    expect(screen.getByTestId("interview-begin-button")).toBeDisabled();
    expect(screen.getByText("Share your entire screen")).toBeInTheDocument();
  });

  it("offers to rejoin a paused interview", () => {
    gate({ resuming: true });
    expect(screen.getByTestId("interview-begin-button")).toHaveTextContent("Rejoin interview");
  });
});

describe("MonitoringNotice", () => {
  it("states what is observed, and nothing when monitoring is off", async () => {
    const { container, unmount } = render(<MonitoringNotice observed={[]} />);
    expect(container).toBeEmptyDOMElement();
    unmount();
    render(<MonitoringNotice observed={["When you switch to another tab or window", "Copy and paste activity"]} />);
    fireEvent.click(screen.getByRole("button", { name: "What's observed during this interview" }));
    expect(await screen.findByText("Copy and paste activity")).toBeInTheDocument();
    expect(screen.getByText(/never automatic decisions/)).toBeInTheDocument();
  });
});

describe("room copy", () => {
  it("explains terminal disconnects without blame", () => {
    expect(offlineCopy("replaced_by_new_connection", null)).toMatchObject({ action: "reload", actionLabel: "Continue here" });
    expect(offlineCopy("too_many_reconnects", "Interrupted too many times.").body).toBe("Interrupted too many times.");
    expect(offlineCopy("gave_up", null)).toMatchObject({ action: "retry" });
    expect(offlineCopy("ticket_rejected", null).action).toBeNull();
  });

  it("names whose turn it is", () => {
    expect(turnCue("ai_speaking", "Maya")).toBe("Maya is speaking");
    expect(turnCue("processing", "Maya")).toBe("Maya is thinking");
    expect(turnCue("listening", "Maya")).toMatch(/Your turn/);
  });

  it("shows notices with the contract test id and lets reminders be dismissed", () => {
    const store = createInterviewStore();
    store.dispatch({ type: "server", msg: { type: "integrity.warning", seq: 1, event_type: "tab_hidden", message: "Please keep this tab open." }, at: performance.now() });
    store.dispatch({ type: "server", msg: { type: "transcript.final", seq: 2, speaker: "candidate", segment_id: "s1", text: "I led the migration" }, at: performance.now() } as never);
    store.dispatch({ type: "server", msg: { type: "notice", seq: 3, level: "warning", code: "stt_degraded", message: "Retrying." }, at: performance.now() });
    const onType = vi.fn();
    render(<NoticeArea store={store} fullscreenPrompt onFullscreen={vi.fn()} onTypeAnswer={onType} screenSharePrompt={false} onShareScreen={vi.fn()} />);
    const area = screen.getByTestId("interview-notice");
    expect(area).toHaveTextContent("Please keep this tab open.");
    expect(area).toHaveTextContent("This interview is set up to run in full screen.");
    fireEvent.click(screen.getByRole("button", { name: "Type answer" }));
    expect(onType).toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" }).at(-1)!);
    expect(area).not.toHaveTextContent("Please keep this tab open.");
  });

  it("never shows the transcription notice before the candidate has spoken, and lets it be dismissed", () => {
    const store = createInterviewStore();
    store.dispatch({ type: "seed", session: { id: "s1", interviewerName: "Maya" } });
    // A busy speech recogniser during the introduction (room noise opened a segment).
    store.dispatch({ type: "server", msg: { type: "notice", seq: 1, level: "warning", code: "stt_degraded", message: "Speech recognition is having trouble." }, at: performance.now() });
    render(<NoticeArea store={store} fullscreenPrompt={false} onFullscreen={vi.fn()} onTypeAnswer={vi.fn()} screenSharePrompt={false} onShareScreen={vi.fn()} />);
    const area = screen.getByTestId("interview-notice");
    expect(area).toBeEmptyDOMElement();
    act(() => store.dispatch({ type: "server", msg: { type: "turn.state", seq: 2, state: "candidate_speaking" }, at: performance.now() } as never));
    expect(area).toHaveTextContent(/Transcription is slow right now/);
    expect(area).not.toHaveTextContent(/trouble hearing you/i);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(area).toBeEmptyDOMElement();
    // Recovery clears it; new trouble later shows it again.
    act(() => store.dispatch({ type: "server", msg: { type: "notice", seq: 3, level: "info", code: "stt_recovered", message: "Working again." }, at: performance.now() }));
    act(() => store.dispatch({ type: "server", msg: { type: "notice", seq: 4, level: "warning", code: "stt_degraded", message: "Trouble." }, at: performance.now() }));
    expect(area).toHaveTextContent(/Transcription is slow right now/);
  });
});
