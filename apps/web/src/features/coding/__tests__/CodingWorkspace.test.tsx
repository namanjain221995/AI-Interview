import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CodingView } from "@/features/realtime/store";

import { CodingWorkspace } from "../CodingWorkspace";

// Monaco does not run in jsdom: a textarea stands in for the lazy editor.
vi.mock("../CodeEditor", () => ({
  default: ({ path, defaultValue, onChange }: { path: string; defaultValue: string; onChange: (v: string) => void }) => (
    <textarea key={path} aria-label="Code editor" data-path={path} defaultValue={defaultValue} onChange={(e) => onChange(e.target.value)} />
  ),
}));

function coding(over: Partial<CodingView> = {}): CodingView {
  return {
    sessionId: "c1",
    question: {
      coding_question_id: "q1",
      title: "Most frequent error service",
      prompt_markdown: "Return the service with the most **errors**.",
      difficulty: "medium",
      languages: ["python", "javascript"],
      starter_code: { python: "def solve(logs):\n    pass\n", javascript: "function solve(logs) {}\n" },
      visible_tests: [{ id: "t1", args: [["a:error"]], expected: "a" }],
    },
    language: "python",
    initialCode: null,
    timeLimitS: 1200,
    elapsedS: 0,
    syncedAtMs: 0,
    hintsAvailable: 3,
    hintsUsed: 0,
    hintsRemaining: 3,
    hints: [],
    lastRun: null,
    pendingRun: null,
    hintPending: false,
    ended: null,
    ...over,
  };
}

function setup(view: CodingView = coding(), props: Partial<Parameters<typeof CodingWorkspace>[0]> = {}) {
  const handlers = {
    onCodeUpdate: vi.fn(),
    onRun: vi.fn(),
    onSubmit: vi.fn(),
    onHint: vi.fn(),
    onLanguage: vi.fn(),
    onPaste: vi.fn(),
  };
  const utils = render(<CodingWorkspace coding={view} remainingS={600} blockPaste={false} pasteThreshold={200} {...handlers} {...props} />);
  return { ...utils, handlers };
}

beforeEach(() => {
  sessionStorage.clear();
});
afterEach(() => vi.useRealTimers());

async function editor(): Promise<HTMLTextAreaElement> {
  return (await screen.findByLabelText("Code editor")) as HTMLTextAreaElement;
}

describe("CodingWorkspace", () => {
  it("renders the contract test ids and the exact hints text", async () => {
    setup();
    expect(screen.getByTestId("coding-workspace")).toBeInTheDocument();
    expect(screen.getByTestId("coding-editor")).toBeInTheDocument();
    expect(screen.getByTestId("coding-hints-remaining")).toHaveTextContent(/^Hints remaining: 3$/);
    expect(screen.getByTestId("coding-results")).toHaveAttribute("data-status", "idle");
    expect(screen.getByTestId("coding-language-select")).toHaveValue("python");
    expect((await editor()).value).toBe("def solve(logs):\n    pass\n");
  });

  it("debounces code.update to 800 ms with a monotonic version", async () => {
    const { handlers } = setup();
    const ed = await editor();
    vi.useFakeTimers();
    fireEvent.change(ed, { target: { value: "def solve(logs):\n    return 1\n" } });
    fireEvent.change(ed, { target: { value: "def solve(logs):\n    return 2\n" } });
    act(() => vi.advanceTimersByTime(799));
    expect(handlers.onCodeUpdate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(handlers.onCodeUpdate).toHaveBeenCalledTimes(1);
    expect(handlers.onCodeUpdate.mock.calls[0]![0]).toMatchObject({ code: "def solve(logs):\n    return 2\n", language: "python", version: 2 });
  });

  it("switches language with separate buffers and announces it", async () => {
    const { handlers } = setup();
    fireEvent.change(await editor(), { target: { value: "print(1)" } });
    fireEvent.change(screen.getByTestId("coding-language-select"), { target: { value: "javascript" } });
    expect(handlers.onLanguage).toHaveBeenCalledWith("javascript");
    expect(((await screen.findByLabelText("Code editor")) as HTMLTextAreaElement).value).toBe("function solve(logs) {}\n");
    fireEvent.change(screen.getByTestId("coding-language-select"), { target: { value: "python" } });
    expect(((await screen.findByLabelText("Code editor")) as HTMLTextAreaElement).value).toBe("print(1)");
  });

  it("runs tests and submits the current buffer", async () => {
    const { handlers } = setup();
    fireEvent.change(await editor(), { target: { value: "print(2)" } });
    fireEvent.click(screen.getByTestId("coding-run-tests"));
    expect(handlers.onRun).toHaveBeenCalledWith("test", "print(2)", "python");
    fireEvent.click(screen.getByTestId("coding-submit"));
    expect(handlers.onSubmit).toHaveBeenCalledWith("print(2)", "python");
  });

  it("shows hints with their level and results with the run status", () => {
    const { rerender, handlers } = setup(coding({ hints: [{ level: 1, text: "Use a counter." }], hintsUsed: 1, hintsRemaining: 2 }));
    expect(screen.getByTestId("coding-hint-1")).toHaveTextContent("Hint 1");
    expect(screen.getByTestId("coding-hints-remaining")).toHaveTextContent("Hints remaining: 2");
    fireEvent.click(screen.getByTestId("coding-hint-button"));
    expect(handlers.onHint).toHaveBeenCalled();
    rerender(
      <CodingWorkspace
        coding={coding({
          lastRun: { type: "coding.run_result", seq: 1, coding_session_id: "c1", run_id: "r1", kind: "test", status: "compile_error", stdout: "", stderr: "", compile_output: "SyntaxError: invalid syntax", passed: 0, total: 1, tests: [] },
        })}
        remainingS={500}
        blockPaste={false}
        pasteThreshold={200}
        {...handlers}
      />,
    );
    expect(screen.getByTestId("coding-results")).toHaveAttribute("data-status", "compile_error");
    expect(screen.getByText("SyntaxError: invalid syntax")).toBeInTheDocument();
  });

  it("shows why a run was rejected", () => {
    setup(
      coding({
        lastRun: { type: "coding.run_result", seq: 2, coding_session_id: "c1", run_id: "r2", kind: "test", status: "rejected", stdout: "", stderr: "A run is already in progress.", compile_output: "", passed: 0, total: 0, tests: [] },
      }),
    );
    expect(screen.getByTestId("coding-results")).toHaveAttribute("data-status", "rejected");
    expect(screen.getByText("A run is already in progress.")).toBeInTheDocument();
  });

  it("says a platform fault is on our side, never the candidate's code", () => {
    setup(
      coding({
        lastRun: { type: "coding.run_result", seq: 3, coding_session_id: "c1", run_id: "r3", kind: "submit", status: "error", stdout: "", stderr: "The code runner is busy, please try again", compile_output: "", passed: 0, total: 2, tests: [], platform_fault: true },
      }),
    );
    expect(screen.getByTestId("coding-platform-fault")).toHaveTextContent("That's on our side, not your code, and nothing was submitted.");
    expect(screen.getByText("Couldn't run")).toBeInTheDocument();
    expect(screen.queryByText("Runtime error")).not.toBeInTheDocument();
    expect(screen.queryByText("0/2 passed")).not.toBeInTheDocument(); // nothing ran
    expect(screen.queryByText("The code runner is busy, please try again")).not.toBeInTheDocument();
  });

  it("blocks paste when the policy says so and reports it", async () => {
    const { handlers } = setup(coding(), { blockPaste: true });
    const ed = await editor();
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "x".repeat(300) } });
    act(() => {
      ed.dispatchEvent(paste);
    });
    expect(paste.defaultPrevented).toBe(true);
    expect(handlers.onPaste).toHaveBeenCalledWith(300, 1, true);
    expect(screen.getByRole("status")).toHaveTextContent(/Pasting is turned off/);
  });

  it("allows paste by default but still reports large pastes", async () => {
    const { handlers } = setup();
    const ed = await editor();
    const paste = new Event("paste", { bubbles: true, cancelable: true }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "y".repeat(250) } });
    act(() => {
      ed.dispatchEvent(paste);
    });
    expect(paste.defaultPrevented).toBe(false);
    expect(handlers.onPaste).toHaveBeenCalledWith(250, 1, false);
  });

  it("locks editing once the exercise ended", () => {
    setup(coding({ ended: "submitted" }));
    expect(screen.getByTestId("coding-submit")).toBeDisabled();
    expect(screen.getByTestId("coding-run-tests")).toBeDisabled();
    expect(screen.getByTestId("coding-hint-button")).toBeDisabled();
  });
});
