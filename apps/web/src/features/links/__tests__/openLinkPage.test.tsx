import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";

import OpenLinkPage from "../pages/OpenLinkPage";
import PersonalLinkPage from "../pages/PersonalLinkPage";

const DATA_USE = {
  video_recording: true,
  audio_recording: true,
  screen_recording: false,
  camera_monitoring: true,
  screen_monitoring: true,
  candidate_report_mode: "after_review",
  retention_days: { resume: 180, audio: 90, video: 30, transcript: 365, report: 365 },
  notice_markdown: "Questions? Write to **privacy@demo.test**.",
  ai_assessment: true,
  human_decision: true,
};

const field = (key: string, type: string, label: string, extra: Record<string, unknown> = {}) => ({
  key,
  type,
  label,
  help_text: null,
  placeholder: null,
  required: true,
  options: [],
  min: null,
  max: null,
  max_length: null,
  ...extra,
});

const INFO = {
  organization_name: "Demo Corp",
  job_title: "Senior DevOps Engineer",
  job_location: "Pune",
  link_name: "Campus drive, Pune",
  description: "Two short rounds.",
  state: "open",
  opens_at: null,
  closes_at: "2026-10-30T12:00:00Z",
  rounds: [
    { name: "Screening", round_type: "technical_screening", duration_minutes: 20, sequence: 0 },
    { name: "Deep dive", round_type: "deep_technical", duration_minutes: 40, sequence: 1 },
  ],
  progression: "immediate",
  requires_access_code: false,
  require_resume: true,
  interviewer_name: "Maya",
  form: {
    fields: [
      field("full_name", "text", "Full name", { max_length: 200 }),
      field("email", "email", "Email"),
      field("phone", "phone", "Mobile number"),
      field("c_shift", "select", "Preferred shift", { required: false, options: ["Day", "Night"] }),
    ],
  },
  data_use: DATA_USE,
  honeypot_field: "website",
};

let info: Record<string, unknown> = INFO;

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/j/:token", element: <CandidateShellLayout />, children: [{ index: true, Component: OpenLinkPage }] },
      { path: "/r/:token", element: <CandidateShellLayout />, children: [{ index: true, Component: PersonalLinkPage }] },
      { path: "/candidate/:interviewId/:step", element: <p>journey</p> },
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
  return { router, client };
}

function fillValid() {
  fireEvent.change(screen.getByTestId("open-link-field-full_name"), { target: { value: "Jane Doe" } });
  fireEvent.change(screen.getByTestId("open-link-field-email"), { target: { value: "jane@example.com" } });
  fireEvent.change(screen.getByTestId("open-link-field-phone"), { target: { value: "+91 98765 43210" } });
  fireEvent.click(screen.getByTestId("open-link-privacy-ack"));
}

beforeEach(() => {
  info = { ...INFO };
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path === "/link-access/tok123") return info as never;
    if (path === "/link-return/ptok123") return { organization_name: "Demo Corp", job_title: "Senior DevOps Engineer", link_name: "Campus drive", state: "active" } as never;
    throw new ApiError({ status: 404, code: "link_not_found", message: "Not found" });
  });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OpenLinkPage (/j/:token)", () => {
  it("shows the job, rounds and data-use disclosure, validates, registers and shows the personal link once", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({
      outcome: "registered",
      interview_id: "i1",
      next_step: "welcome",
      csrf_token: "c",
      personal_url: "https://interview.demo.test/r/personal-token-abcdefghijklmnop",
    } as never);
    const { router } = renderAt("/j/tok123");
    const page = await screen.findByTestId("open-link-page");
    await waitFor(() => expect(page).toHaveAttribute("data-state", "open"));
    expect(screen.getByRole("heading", { level: 1, name: "Senior DevOps Engineer" })).toBeInTheDocument();
    expect(screen.getByText("Screening")).toBeInTheDocument();
    expect(screen.getByText("Deep dive")).toBeInTheDocument();
    const dataUse = screen.getByTestId("open-link-data-use");
    expect(within(dataUse).getByText(/records your voice and your camera video/)).toBeInTheDocument();
    expect(within(dataUse).getByText(/makes every decision/)).toBeInTheDocument();
    expect(within(dataUse).getByText(/The AI never sees them/)).toBeInTheDocument();
    expect(within(dataUse).getByText(/privacy@demo.test/)).toBeInTheDocument();
    expect(within(dataUse).getByText(/review and accept the full details before the interview starts/)).toBeInTheDocument();

    // Native input types for phones.
    expect(screen.getByTestId("open-link-field-email")).toHaveAttribute("type", "email");
    expect(screen.getByTestId("open-link-field-phone")).toHaveAttribute("type", "tel");

    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(await screen.findByTestId("open-link-field-error-full_name")).toHaveTextContent("Fill in this field.");
    expect(screen.getByText(/confirm you've read how your data is used/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();

    fillValid();
    fireEvent.click(screen.getByTestId("open-link-submit"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/link-access/tok123/register",
        { answers: { full_name: "Jane Doe", email: "jane@example.com", phone: "+91 98765 43210", c_shift: null }, privacy_ack: true },
        { skipAuthRefresh: true },
      ),
    );

    expect(await screen.findByTestId("personal-link-url")).toHaveTextContent("https://interview.demo.test/r/personal-token-abcdefghijklmnop");
    expect(page).toHaveAttribute("data-state", "registered");
    const next = screen.getByTestId("personal-link-continue");
    expect(next).toBeDisabled();
    fireEvent.click(screen.getByTestId("personal-link-copy"));
    await waitFor(() => expect(next).toBeEnabled());
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("https://interview.demo.test/r/personal-token-abcdefghijklmnop");
    fireEvent.click(next);
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/welcome"));
  });

  it("sends the same browser straight back into the journey when the registration resumes", async () => {
    vi.spyOn(api, "post").mockResolvedValue({ outcome: "resumed", interview_id: "i1", next_step: "resume", csrf_token: "c", personal_url: null } as never);
    const { router } = renderAt("/j/tok123");
    await screen.findByTestId("open-link-field-full_name");
    fillValid();
    fireEvent.click(screen.getByTestId("open-link-submit"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/resume"));
  });

  it.each([
    [new ApiError({ status: 409, code: "already_completed", message: "done" }), "already_completed", /already completed this interview/],
    [new ApiError({ status: 409, code: "already_registered", message: "dup", details: { resume_with: "personal_link" } }), "already_registered", /personal link you saved/],
    [new ApiError({ status: 409, code: "already_registered", message: "dup", details: { state: "awaiting_review", round: 1 } }), "awaiting_review", /finished round 1.*will review it/],
    [new ApiError({ status: 409, code: "already_invited", message: "inv" }), "already_invited", /link in your invitation/],
    [new ApiError({ status: 403, code: "registration_blocked", message: "no" }), "unavailable", /contact the hiring team/i],
    [new ApiError({ status: 423, code: "access_code_locked", message: "locked", retryAfterSeconds: 600 }), "wait", /about 10 minutes/],
    [new ApiError({ status: 409, code: "link_unavailable", message: "full", details: { state: "full" } }), "full", /All places are taken/],
  ])("maps %s onto a clear panel", async (error, outcome, text) => {
    vi.spyOn(api, "post").mockRejectedValue(error);
    renderAt("/j/tok123");
    await screen.findByTestId("open-link-field-full_name");
    fillValid();
    fireEvent.click(screen.getByTestId("open-link-submit"));
    const panel = await screen.findByTestId("open-link-error");
    expect(panel).toHaveAttribute("data-outcome", outcome);
    expect(panel).toHaveTextContent(text);
    expect(screen.queryByTestId("open-link-submit")).not.toBeInTheDocument();
  });

  it("puts server field errors and access-code problems next to the fields", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(new ApiError({ status: 422, code: "validation_failed", message: "bad", details: { fields: { phone: "Enter a phone number with 7 to 15 digits." } } }))
      .mockRejectedValueOnce(new ApiError({ status: 422, code: "access_code_required", message: "code" }))
      .mockRejectedValueOnce(new ApiError({ status: 401, code: "invalid_access_code", message: "wrong" }));
    renderAt("/j/tok123");
    await screen.findByTestId("open-link-field-full_name");
    fillValid();
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(await screen.findByTestId("open-link-field-error-phone")).toHaveTextContent("7 to 15 digits");
    fireEvent.click(screen.getByTestId("open-link-submit"));
    const code = await screen.findByTestId("open-link-access-code-input");
    fireEvent.change(code, { target: { value: "WRONG1" } });
    fireEvent.click(screen.getByTestId("open-link-submit"));
    expect(await screen.findByText(/access code isn't right/)).toBeInTheDocument();
    expect(post).toHaveBeenLastCalledWith("/link-access/tok123/register", expect.objectContaining({ access_code: "WRONG1" }), { skipAuthRefresh: true });
  });

  it("shows state panels instead of the form when the link isn't open, and a clear 404", async () => {
    info = { ...INFO, state: "scheduled", opens_at: "2026-11-01T09:00:00Z", form: null };
    renderAt("/j/tok123");
    expect(await screen.findByText("Registration hasn't opened yet")).toBeInTheDocument();
    expect(screen.queryByTestId("open-link-submit")).not.toBeInTheDocument();
    expect(screen.getByTestId("open-link-page")).toHaveAttribute("data-state", "scheduled");
  });

  it("explains an invalid link", async () => {
    renderAt("/j/unknown-token");
    expect(await screen.findByText("This link isn't valid")).toBeInTheDocument();
    expect(screen.getByTestId("open-link-page")).toHaveAttribute("data-state", "not_found");
  });
});

describe("PersonalLinkPage (/r/:token)", () => {
  it("verifies the email and opens the current interview", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ interview_id: "i2", next_step: "welcome", csrf_token: "c", progress: null } as never);
    const { router } = renderAt("/r/ptok123");
    fireEvent.change(await screen.findByTestId("personal-link-email-input"), { target: { value: "jane@example.com" } });
    fireEvent.click(screen.getByTestId("personal-link-verify"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i2/welcome"));
    expect(post).toHaveBeenCalledWith("/link-return/ptok123/verify", { email: "jane@example.com" }, { skipAuthRefresh: true });
  });

  it("explains a wrong email, a lockout and a cancelled registration", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(new ApiError({ status: 401, code: "invalid_credentials", message: "no" }))
      .mockRejectedValueOnce(new ApiError({ status: 423, code: "invite_locked", message: "locked", details: { retry_after_seconds: 300 } }))
      .mockRejectedValueOnce(new ApiError({ status: 410, code: "registration_cancelled", message: "gone" }));
    renderAt("/r/ptok123");
    fireEvent.change(await screen.findByTestId("personal-link-email-input"), { target: { value: "other@example.com" } });
    fireEvent.click(screen.getByTestId("personal-link-verify"));
    expect(await screen.findByTestId("personal-link-error")).toHaveTextContent(/doesn't match this registration/);
    fireEvent.click(screen.getByTestId("personal-link-verify"));
    await waitFor(() => expect(screen.getByTestId("personal-link-error")).toHaveTextContent(/about 5 minutes/));
    fireEvent.click(screen.getByTestId("personal-link-verify"));
    expect(await screen.findByText("This registration was cancelled")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(3);
  });
});
