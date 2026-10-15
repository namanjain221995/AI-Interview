import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CodingView } from "@/features/realtime/store";

import { CodingWorkspace } from "../CodingWorkspace";

// The editor chunk failed to load: the boundary shows the plain-text fallback.
vi.mock("../CodeEditor", () => ({
  default: () => {
    throw new Error("Failed to fetch dynamically imported module");
  },
}));

function coding(): CodingView {
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
  };
}

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => undefined); // the boundary's expected error report
});
afterEach(() => vi.restoreAllMocks());

describe("CodingWorkspace without the code editor", () => {
  it("shows and submits the selected language's code in the fallback after a language switch", async () => {
    const handlers = { onCodeUpdate: vi.fn(), onRun: vi.fn(), onSubmit: vi.fn(), onHint: vi.fn(), onLanguage: vi.fn(), onPaste: vi.fn() };
    render(<CodingWorkspace coding={coding()} remainingS={600} blockPaste={false} pasteThreshold={200} {...handlers} />);
    fireEvent.change(await screen.findByLabelText("Your code"), { target: { value: "print(1)" } });
    fireEvent.change(screen.getByTestId("coding-language-select"), { target: { value: "javascript" } });
    expect((screen.getByLabelText("Your code") as HTMLTextAreaElement).value).toBe("function solve(logs) {}\n");
    fireEvent.click(screen.getByTestId("coding-submit"));
    expect(handlers.onSubmit).toHaveBeenCalledWith("function solve(logs) {}\n", "javascript"); // what is on screen
    fireEvent.change(screen.getByTestId("coding-language-select"), { target: { value: "python" } });
    expect((screen.getByLabelText("Your code") as HTMLTextAreaElement).value).toBe("print(1)");
  });
});
