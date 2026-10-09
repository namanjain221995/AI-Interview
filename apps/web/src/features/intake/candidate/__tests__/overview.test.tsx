import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ComponentType } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import InstructionsPage from "@/features/candidate/pages/InstructionsPage";
import InviteEntryPage from "@/features/candidate/pages/InviteEntryPage";
import WelcomePage from "@/features/candidate/pages/WelcomePage";
import { api, ApiError } from "@/lib/api";

import { minutesLabel } from "../components/IntakeOverview";

const INTERVIEW = {
  id: "i1",
  status: "invited",
  organization: { name: "TechSara Solutions" },
  job: { id: "j1", title: "AI/ML Training Program — Intake" },
  rounds_offered: [{ id: "r1", name: "Intake Interview", round_type: "intake", duration_minutes: 45, expected_difficulty: "medium" }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: { integrity: { enabled: true, require_fullscreen: true, camera_required: true, monitor_focus: true }, recording: { allow_typed_answers: true } },
  steps_completed: [] as string[],
  expires_at: "2026-10-16T12:00:00Z",
  interviewer_name: "Maya",
  intake: { intake_id: "in1", title: "Intake Interview", estimated_minutes: 45, presence: "orb" },
};

const PORTAL_INTAKE = {
  intake_id: "in1",
  interview_id: "i1",
  title: "Intake Interview",
  program_name: "AI/ML Training Program",
  organization_name: "TechSara Solutions",
  estimated_minutes: 45,
  duration_range: { min: 35, max: 50 },
  parts: [
    { number: 1, key: "greeting", title: "Introduction", minutes: { min: 5, max: 5 } },
    { number: 2, key: "resume", title: "Your experience", minutes: { min: 8, max: 10 } },
    { number: 3, key: "mandatory", title: "Standard questions", minutes: { min: 3, max: 5 } },
    { number: 4, key: "reading", title: "Reading exercise", minutes: { min: 7, max: 8 } },
    { number: 5, key: "topic", title: "Speaking exercise", minutes: { min: 7, max: 8 } },
    { number: 6, key: "learning", title: "Learning and growth", minutes: { min: 5, max: 5 } },
    { number: 7, key: "close", title: "Your questions", minutes: { min: 3, max: 5 } },
  ],
  presence: "orb",
  eligibility: { enabled: false },
};

const DISCLOSURE = {
  policy_version: "2026-10-01",
  policy_hash: "hash-1",
  title: "Before you begin",
  intro: "",
  items: [
    { key: "microphone", title: "Microphone", description: "Your voice is transcribed.", required: true, enabled: true },
    { key: "audio_recording", title: "Audio recording", description: "Kept for review.", required: true, enabled: true },
    { key: "video_recording", title: "Video recording", description: "Kept for review.", required: true, enabled: true },
  ],
  retention_summary: "Recordings are deleted after 90 days.",
  purpose: "",
  human_review_statement: "",
  limitations_statement: "",
};

let intakeAvailable = true;

function renderAt(path: string, routes: Array<{ path: string; Component: ComponentType }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/i/:token", element: <CandidateShellLayout />, children: [{ index: true, Component: InviteEntryPage }] },
      { path: "/candidate", element: <CandidateShellLayout />, children: routes },
    ],
    { initialEntries: [path] },
  );
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  intakeAvailable = true;
  sessionStorage.clear();
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path.startsWith("/candidate-access/"))
      return { organization_name: "TechSara Solutions", job_title: "AI/ML Training Program", round_name: "Intake Interview", round_type: "intake", candidate_first_name: "Jane", requires_access_code: false, expires_at: "2026-10-16T12:00:00Z", status: "invited", duration_minutes: 45 } as never;
    if (path === "/portal/me") return { candidate: { id: "c1", full_name: "Jane Doe", email: "jane@example.com" }, interview_id: "i1", organization: { name: "TechSara Solutions" } } as never;
    if (path === "/portal/interviews/i1") return INTERVIEW as never;
    if (path === "/portal/interviews/i1/disclosure") return DISCLOSURE as never;
    if (path === "/portal/intake/i1") {
      if (!intakeAvailable) throw new ApiError({ status: 404, code: "intake_disabled", message: "Off" });
      return PORTAL_INTAKE as never;
    }
    throw new ApiError({ status: 404, code: "not_found", message: `unexpected ${path}` });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("intake welcome", () => {
  it("describes the interview, its seven parts, privacy and who decides, without any scoring", async () => {
    renderAt("/candidate/i1/welcome", [{ path: ":interviewId/welcome", Component: WelcomePage }]);
    const overview = await screen.findByTestId("intake-overview");
    expect(await within(overview).findByRole("heading", { level: 1, name: "Your intake interview for the AI/ML Training Program" })).toBeInTheDocument();
    expect(overview).toHaveTextContent("about 45 minutes (35 to 50)");
    expect(overview).toHaveTextContent("A person at TechSara Solutions makes every decision. You won't see scores or a report.");
    const parts = [...overview.querySelectorAll("[data-part]")].map((el) => el.getAttribute("data-part"));
    expect(parts).toEqual(["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"]);
    expect(within(overview).getAllByText("Timed")).toHaveLength(2);
    expect(screen.getByTestId("intake-parts-strip").children).toHaveLength(7);
    expect(await within(overview).findByText("Recordings are deleted after 90 days.")).toBeInTheDocument();
    expect(overview).toHaveTextContent("Microphone, Audio recording, Video recording");
    expect(overview).toHaveTextContent("Your camera on");
    expect(overview).toHaveTextContent("Full screen");
    expect(overview.textContent).not.toMatch(/rubric|weight|competenc|\bband\b|out of 100|strong accept|reject/i);
    expect(screen.getByTestId("welcome-continue")).toBeInTheDocument();
    // The stepper shows the intake journey.
    expect(screen.getAllByText("Your details").length).toBeGreaterThan(0);
  });

  it("falls back to the kit's seven parts when the intake routes are unavailable", async () => {
    intakeAvailable = false;
    renderAt("/candidate/i1/welcome", [{ path: ":interviewId/welcome", Component: WelcomePage }]);
    const overview = await screen.findByTestId("intake-overview");
    expect(overview.querySelectorAll("[data-part]")).toHaveLength(7);
    expect(within(overview).getByRole("heading", { level: 1 })).toHaveTextContent("Your intake interview");
  });

  it("labels part lengths plainly", () => {
    expect(minutesLabel({ min: 5, max: 5 })).toBe("about 5 min");
    expect(minutesLabel({ min: 8, max: 10 })).toBe("8 to 10 min");
    expect(minutesLabel(null)).toBeNull();
  });
});

describe("intake instructions", () => {
  it("explains both exercises, that not knowing is fine, and that there's no feedback", async () => {
    renderAt("/candidate/i1/instructions", [{ path: ":interviewId/instructions", Component: InstructionsPage }]);
    const root = await screen.findByTestId("intake-instructions");
    expect(screen.getByRole("heading", { level: 1, name: "How the intake interview works" })).toBeInTheDocument();
    expect(root).toHaveTextContent("read it silently, then explain it in your own words");
    expect(root).toHaveTextContent("disappears after 30 seconds");
    expect(root).toHaveTextContent("60 seconds to prepare");
    expect(root).toHaveTextContent('Saying "I don\'t know" is fine.');
    expect(root).toHaveTextContent("There's no feedback at the end");
    expect(root).toHaveTextContent("except in the reading exercise");
    expect(screen.getByTestId("instructions-continue")).toBeInTheDocument();
  });
});

describe("intake invite entry", () => {
  it("names the intake interview before verification", async () => {
    renderAt("/i/tok123", []);
    expect(await screen.findByText(/Hi Jane, welcome/)).toBeInTheDocument();
    expect(screen.getByText(/has invited you to an intake interview for/)).toBeInTheDocument();
    expect(screen.getByText("Intake interview")).toBeInTheDocument();
    expect(screen.getByText("About 45 minutes")).toBeInTheDocument();
  });
});
