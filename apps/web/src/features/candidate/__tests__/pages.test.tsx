import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentType } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";

import CandidateDashboardPage from "../pages/CandidateDashboardPage";
import CandidateReportPage from "../pages/CandidateReportPage";
import CompletePage from "../pages/CompletePage";
import ConsentPage from "../pages/ConsentPage";
import InviteEntryPage from "../pages/InviteEntryPage";
import ResumePage from "../pages/ResumePage";
import WelcomePage from "../pages/WelcomePage";

const INTERVIEW = {
  id: "i1",
  status: "invited",
  organization: { name: "Demo Corp" },
  job: { id: "j1", title: "Senior DevOps Engineer" },
  rounds_offered: [{ id: "r1", name: "Combined AI Round", round_type: "combined", duration_minutes: 30, expected_difficulty: "medium", coding_enabled: true }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: { candidate_report_mode: "immediate", integrity: { enabled: true, require_fullscreen: true, monitor_focus: true } },
  steps_completed: [] as string[],
  expires_at: "2026-10-09T12:00:00Z",
  interviewer_name: "Maya",
};

const DISCLOSURE = {
  policy_version: "2026-10-01",
  policy_hash: "hash-1",
  title: "Before you begin",
  intro: "Here is exactly what happens during your interview.",
  items: [
    { key: "microphone", title: "Microphone", description: "Your voice is transcribed by the speech service.", summary: "You answer by voice.", required: true, enabled: true },
    { key: "integrity_monitoring", title: "Monitoring", description: "Tab and window changes are noted.", required: true, enabled: true },
    { key: "camera_frames", title: "Camera checks", description: "A still image every 10 seconds.", required: false, enabled: true },
  ],
  required_items: ["microphone", "integrity_monitoring"],
  consent_items: { microphone: true, integrity_monitoring: true, camera_frames: true },
  retention_summary: "Audio is kept for 90 days.",
  purpose: "To assess your answers for this role.",
  human_review_statement: "A person makes every hiring decision.",
  limitations_statement: "A browser can't lock your computer.",
};

let interviewState = { ...INTERVIEW };
let sessionState: Record<string, unknown> = {};

function renderAt(path: string, routes: Array<{ path: string; Component: ComponentType }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/i/:token", element: <CandidateShellLayout />, children: [{ index: true, Component: InviteEntryPage }] },
      { path: "/candidate", element: <CandidateShellLayout />, children: routes.map((r) => ({ path: r.path, Component: r.Component })) },
      { path: "*", element: <p>elsewhere</p> },
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
  interviewState = { ...INTERVIEW, steps_completed: [] };
  sessionState = { id: "s1", interview_id: "i1", status: "completed", candidate_report_available: true };
  sessionStorage.clear();
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path.startsWith("/candidate-access/")) return { organization_name: "Demo Corp", job_title: "Senior DevOps Engineer", round_name: "Combined AI Round", candidate_first_name: "Jane", requires_access_code: false, expires_at: "2026-10-09T12:00:00Z", status: "invited" } as never;
    if (path === "/portal/me") return { candidate: { id: "c1", full_name: "Jane Doe", email: "jane@example.com" }, interview_id: "i1", organization: { name: "Demo Corp" } } as never;
    if (path === "/portal/interviews/i1") return interviewState as never;
    if (path === "/portal/interviews/i1/disclosure") return DISCLOSURE as never;
    if (path === "/portal/interviews/i1/resume") throw new ApiError({ status: 404, code: "not_found", message: "No resume" });
    if (path === "/portal/sessions/s1") return sessionState as never;
    if (path === "/portal/sessions/s1/report")
      return {
        status: "ready",
        overall_score: 74,
        content: {
          narrative: {
            overall_summary: "You explained Kubernetes operations clearly.",
            technical_strengths: [{ title: "Incident response", detail: "Clear timeline and ownership.", evidence_ids: [], competency_key: null }],
            areas_to_improve: [{ title: "Cost measurement", detail: "Numbers were missing.", learning_guidance: "Practise quoting baselines.", evidence_ids: [], competency_key: null }],
            strong_answers: [],
            incomplete_answers: [],
            recommended_study_areas: [{ topic: "FinOps", why: "Cost questions came up.", next_steps: ["Read the FinOps framework"] }],
            next_steps: ["Review the study areas"],
          },
          competencies: [{ key: "kubernetes", name: "Kubernetes", score: 7.8 }],
        },
      } as never;
    throw new ApiError({ status: 404, code: "not_found", message: `unexpected ${path}` });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteEntryPage", () => {
  it("verifies the candidate and resumes at the first step", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ interview_id: "i1" } as never);
    const router = renderAt("/i/tok123", [{ path: ":interviewId/welcome", Component: WelcomePage }]);
    expect(await screen.findByText(/Hi Jane, welcome/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("invite-verify-button"));
    expect(await screen.findByText(/Enter the email address/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("invite-email-input"), { target: { value: "jane@example.com" } });
    fireEvent.click(screen.getByTestId("invite-verify-button"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/welcome"));
    expect(post).toHaveBeenCalledWith("/candidate-access/tok123/verify", { email: "jane@example.com" }, { skipAuthRefresh: true });
    expect(await screen.findByTestId("welcome-continue")).toBeInTheDocument();
  });

  it("explains a mismatched email and a locked invitation", async () => {
    const post = vi.spyOn(api, "post").mockRejectedValueOnce(new ApiError({ status: 401, code: "invalid_credentials", message: "Invalid" }));
    renderAt("/i/tok123", []);
    fireEvent.change(await screen.findByTestId("invite-email-input"), { target: { value: "other@example.com" } });
    fireEvent.click(screen.getByTestId("invite-verify-button"));
    expect(await screen.findByTestId("invite-error")).toHaveTextContent(/doesn't match this invitation/);
    post.mockRejectedValueOnce(new ApiError({ status: 423, code: "account_locked", message: "Locked", retryAfterSeconds: 600 }));
    fireEvent.click(screen.getByTestId("invite-verify-button"));
    await waitFor(() => expect(screen.getByTestId("invite-error")).toHaveTextContent(/about 10 minutes/));
  });
});

describe("ConsentPage", () => {
  it("confirms every required item with one tick, then posts the consent map with the policy hash", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      interviewState = { ...interviewState, steps_completed: ["consent"] };
      return {} as never;
    });
    const router = renderAt("/candidate/i1/consent", [
      { path: ":interviewId/consent", Component: ConsentPage },
      { path: ":interviewId/resume", Component: ResumePage },
    ]);
    const accept = await screen.findByTestId("consent-accept-button");
    // Summaries by default, the full text behind "Details".
    expect(screen.getByTestId("consent-item-microphone")).toHaveTextContent("You answer by voice.");
    expect(screen.getByText("Your voice is transcribed by the speech service.").closest("details")).not.toBeNull();
    expect(screen.getByText("A browser can't lock your computer.")).toBeInTheDocument();
    expect(accept).toBeDisabled();
    fireEvent.click(screen.getByTestId("consent-agree-all"));
    expect(accept).toBeEnabled();
    fireEvent.click(accept);
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/resume"));
    // The optional item stays off unless the candidate turns it on; never bundled with the required ones.
    expect(post).toHaveBeenCalledWith("/portal/interviews/i1/consent", {
      policy_hash: "hash-1",
      items: { microphone: true, integrity_monitoring: true, camera_frames: false },
    });
  });

  it("records an optional item only when its own switch is turned on", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      interviewState = { ...interviewState, steps_completed: ["consent"] };
      return {} as never;
    });
    renderAt("/candidate/i1/consent", [
      { path: ":interviewId/consent", Component: ConsentPage },
      { path: ":interviewId/resume", Component: ResumePage },
    ]);
    const accept = await screen.findByTestId("consent-accept-button");
    fireEvent.click(screen.getByTestId("consent-optional-camera_frames"));
    expect(accept).toBeDisabled(); // an optional choice never stands in for the required confirmation
    fireEvent.click(screen.getByTestId("consent-agree-all"));
    fireEvent.click(accept);
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/portal/interviews/i1/consent", {
      policy_hash: "hash-1",
      items: { microphone: true, integrity_monitoring: true, camera_frames: true },
    });
  });

  it("sends the candidate back to consent before the résumé step", async () => {
    const router = renderAt("/candidate/i1/resume", [
      { path: ":interviewId/consent", Component: ConsentPage },
      { path: ":interviewId/resume", Component: ResumePage },
    ]);
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/consent"));
  });
});

describe("ResumePage", () => {
  it("tags the file input, uploads with progress and unlocks continue once ready", async () => {
    interviewState = { ...INTERVIEW, steps_completed: ["consent"] };
    const upload = vi.spyOn(api, "upload").mockResolvedValue({
      resume_id: "r1",
      status: "ready",
      profile_ready: true,
      summary: { name: "Jane Doe", headline: "DevOps engineer, Kubernetes and AWS", experience_years: 8, skills: ["Kubernetes", "AWS"], roles: [{ title: "Senior SRE", company: "Acme" }] },
    } as never);
    renderAt("/candidate/i1/resume", [{ path: ":interviewId/resume", Component: ResumePage }]);
    const input = (await screen.findByTestId("resume-file-input")) as HTMLInputElement;
    expect(screen.getByTestId("resume-continue")).toBeDisabled();
    // The upload response is ready immediately; subsequent polls read the same state.
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/portal/interviews/i1/resume") return { resume_id: "r1", status: "ready", profile_ready: true, summary: { name: "Jane Doe", headline: "DevOps engineer, Kubernetes and AWS", skills: ["Kubernetes"] } } as never;
      return interviewState as never;
    });
    const file = new File(["%PDF-1.4 resume"], "jane_doe_devops.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(upload).toHaveBeenCalled());
    expect(await screen.findByTestId("resume-summary")).toHaveTextContent(/Kubernetes/);
    expect(screen.getByTestId("resume-status")).toHaveTextContent(/ready/i);
    expect(screen.getByTestId("resume-continue")).toBeEnabled();
  });

  it("lets the candidate continue while the interview is tailored in the background", async () => {
    interviewState = { ...INTERVIEW, steps_completed: ["consent"] };
    const at = new Date(Date.now() - 70_000).toISOString();
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/portal/interviews/i1/resume")
        return {
          resume: {
            id: "r1",
            original_filename: "cv.pdf",
            size_bytes: 10,
            status: "analyzing",
            profile_ready: false,
            created_at: at,
            steps: [
              { key: "extracting", label: "Reading the document", state: "done", at },
              { key: "analyzing", label: "Analyzing experience and skills", state: "active", at },
              { key: "ready", label: "Ready", state: "pending" },
            ],
          },
          profile_summary: null,
        } as never;
      return interviewState as never;
    });
    renderAt("/candidate/i1/resume", [{ path: ":interviewId/resume", Component: ResumePage }]);
    expect(await screen.findByText("You can continue now")).toBeInTheDocument();
    expect(screen.getByTestId("resume-status")).toHaveTextContent("Understanding your experience");
    expect(screen.getByText(/About a minute so far · usually less than a minute/)).toBeInTheDocument();
    expect(screen.getByTestId("resume-continue")).toBeEnabled();
    expect(screen.getByRole("button", { name: /Upload a different file/ })).toBeInTheDocument();
  });
});

describe("Completion and report", () => {
  it("shows the report link once the candidate report is released", async () => {
    interviewState = { ...INTERVIEW, status: "completed", steps_completed: ["consent", "resume", "system_check"] };
    renderAt("/candidate/i1/complete?session=s1", [
      { path: ":interviewId/complete", Component: CompletePage },
      { path: ":interviewId/report", Component: CandidateReportPage },
    ]);
    expect(await screen.findByTestId("complete-page")).toBeInTheDocument();
    const link = await screen.findByTestId("report-link");
    fireEvent.click(link);
    expect(await screen.findByTestId("candidate-report")).toBeInTheDocument();
    expect(screen.getByTestId("report-overall-score")).toHaveTextContent("74 out of 100");
    expect(screen.getByText("Practise quoting baselines.")).toBeInTheDocument();
    expect(screen.getByText("FinOps")).toBeInTheDocument();
  });

  it("says the interview is still being closed while the session is open (End pressed while offline)", async () => {
    interviewState = { ...INTERVIEW, status: "in_progress", steps_completed: ["consent", "resume", "system_check"] };
    sessionState = { id: "s1", interview_id: "i1", status: "live", candidate_report_available: false, candidate_report_mode: "immediate" };
    renderAt("/candidate/i1/complete?session=s1", [{ path: ":interviewId/complete", Component: CompletePage }]);
    const outcome = await screen.findByTestId("complete-outcome");
    await waitFor(() => expect(outcome).toHaveAttribute("data-outcome", "wrapping_up"));
    expect(outcome).toHaveTextContent("closing your interview");
    expect(screen.queryByTestId("feedback-card")).not.toBeInTheDocument(); // nothing promised before it is closed
  });

  it("re-sends a remembered end request while the session is still open (End pressed offline)", async () => {
    sessionStorage.setItem("interview:end-requested:s1", "candidate_ended");
    const post = vi.spyOn(api, "post").mockResolvedValue({ status: "ending" } as never);
    interviewState = { ...INTERVIEW, status: "in_progress", steps_completed: ["consent", "resume", "system_check"] };
    sessionState = { id: "s1", interview_id: "i1", status: "paused", candidate_report_available: false, candidate_report_mode: "never" };
    renderAt("/candidate/i1/complete?session=s1", [{ path: ":interviewId/complete", Component: CompletePage }]);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/portal/sessions/s1/end", { reason: "candidate_ended" }, expect.anything()));
    sessionStorage.removeItem("interview:end-requested:s1");
  });

  it("tells a candidate who left before the start that nothing ended and offers the way back", async () => {
    interviewState = { ...INTERVIEW, status: "in_progress", steps_completed: ["consent", "resume", "system_check"] };
    sessionState = { id: "s1", interview_id: "i1", status: "ready", candidate_report_available: false, candidate_report_mode: "immediate" };
    renderAt("/candidate/i1/complete?session=s1", [{ path: ":interviewId/complete", Component: CompletePage }]);
    const outcome = await screen.findByTestId("complete-outcome");
    await waitFor(() => expect(outcome).toHaveAttribute("data-outcome", "not_started"));
    expect(outcome).toHaveTextContent("hasn't started yet");
    // G3: only stored files follow a deletion schedule; the transcript stays until the hiring team deletes it.
    expect(screen.getByTestId("complete-page")).toHaveTextContent("Your transcript and the written assessment stay with");
    expect(screen.getByTestId("complete-page")).not.toHaveTextContent(/transcript and any images are kept only/);
    expect(screen.getByTestId("return-to-interview")).toHaveAttribute("href", "/candidate/i1/interview");
    expect(screen.queryByTestId("feedback-card")).not.toBeInTheDocument();
  });

  it("hides every feedback element when the session's report mode is never (the session wins over the invitation)", async () => {
    interviewState = { ...INTERVIEW, organization: { name: "TechSara Solutions" }, status: "completed", steps_completed: ["consent", "resume", "system_check"] };
    sessionState = { id: "s1", interview_id: "i1", status: "completed", candidate_report_available: false, candidate_report_mode: "never" };
    const router = renderAt("/candidate/i1/complete?session=s1", [
      { path: ":interviewId/complete", Component: CompletePage },
      { path: ":interviewId/report", Component: CandidateReportPage },
    ]);
    const page = await screen.findByTestId("complete-page");
    await waitFor(() => expect(page).toHaveTextContent("TechSara Solutions will review it"));
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith("/portal/sessions/s1"));
    expect(screen.queryByTestId("feedback-card")).not.toBeInTheDocument();
    expect(page).not.toHaveTextContent(/feedback|preparing/i);
    expect(screen.queryByTestId("report-link")).not.toBeInTheDocument();
    // Branding: the TechSara mark next to the organization name, never initials.
    const header = document.querySelector("header")!;
    expect(header.querySelector("[data-brand-mark]")).not.toBeNull();
    expect(header).toHaveTextContent("TechSara Solutions");
    // A direct visit to the report route gets a neutral next-steps page.
    await router.navigate("/candidate/i1/report?session=s1");
    expect(await screen.findByTestId("report-not-shared")).toHaveTextContent("The hiring team will contact you");
    expect(screen.queryByTestId("candidate-report")).not.toBeInTheDocument();
    expect(vi.mocked(api.get)).not.toHaveBeenCalledWith("/portal/sessions/s1/report");
  });

  it("never shows a feedback card for an intake interview, even when reports are immediate (S21)", async () => {
    interviewState = { ...INTERVIEW, status: "completed", steps_completed: ["consent", "resume", "system_check"], intake: { intake_id: "in1", title: "Intake interview", estimated_minutes: 25, presence: "orb" } } as typeof interviewState;
    sessionState = { id: "s1", interview_id: "i1", status: "completed", candidate_report_available: false, candidate_report_mode: "immediate" };
    renderAt("/candidate/i1/complete?session=s1", [{ path: ":interviewId/complete", Component: CompletePage }]);
    const page = await screen.findByTestId("complete-page");
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith("/portal/sessions/s1"));
    expect(screen.queryByTestId("feedback-card")).not.toBeInTheDocument();
    expect(page).not.toHaveTextContent(/preparing/i);
  });

  it("keeps the dashboard free of feedback promises when the invitation shares none", async () => {
    interviewState = { ...INTERVIEW, policy: { ...INTERVIEW.policy, candidate_report_mode: "never" }, status: "completed", latest_session: { id: "s1", status: "completed" } } as never;
    sessionState = { id: "s1", interview_id: "i1", status: "completed", candidate_report_available: false, candidate_report_mode: "never" };
    renderAt("/candidate", [{ path: "", Component: CandidateDashboardPage }]);
    expect(await screen.findByText("Senior DevOps Engineer")).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(api.get)).toHaveBeenCalledWith("/portal/sessions/s1"));
    expect(document.body).not.toHaveTextContent(/feedback/i);
    expect(screen.getByRole("link", { name: "What happens next" })).toBeInTheDocument();
  });

  it("still promises feedback when the organisation shares it immediately", async () => {
    interviewState = { ...INTERVIEW, status: "completed", steps_completed: ["consent", "resume", "system_check"] };
    sessionState = { id: "s1", interview_id: "i1", status: "completed", candidate_report_available: false, candidate_report_mode: "immediate" };
    renderAt("/candidate/i1/complete?session=s1", [{ path: ":interviewId/complete", Component: CompletePage }]);
    expect(await screen.findByTestId("feedback-card")).toHaveTextContent("Preparing");
  });
});
