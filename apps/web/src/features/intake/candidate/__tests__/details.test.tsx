import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";

import { onboardingFieldErrors } from "../api";
import { draftFrom, formFieldFor, toOnboardingBody, validateOnboarding, type OnboardingDraft } from "../onboarding";
import IntakeDetailsPage from "../pages/IntakeDetailsPage";

const INTERVIEW = {
  id: "i1",
  status: "invited",
  organization: { name: "TechSara Solutions" },
  job: { id: "j1", title: "AI/ML Training Program — Intake" },
  rounds_offered: [{ id: "r1", name: "Intake Interview", round_type: "intake", duration_minutes: 45, expected_difficulty: "medium" }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: {},
  steps_completed: ["welcome", "instructions", "consent", "resume"],
  expires_at: "2026-10-16T12:00:00Z",
  interviewer_name: "Maya",
  intake: { intake_id: "in1", title: "Intake Interview", estimated_minutes: 45, presence: "orb" },
};

function portalIntake(overrides: Record<string, unknown> = {}) {
  return {
    intake_id: "in1",
    interview_id: "i1",
    title: "Intake Interview",
    program_name: "AI/ML Training Program",
    organization_name: "TechSara Solutions",
    estimated_minutes: 45,
    duration_range: { min: 35, max: 50 },
    parts: [],
    presence: "orb",
    onboarding: null,
    onboarding_submitted: false,
    onboarding_locked: false,
    eligibility: { enabled: true, country: "the United Kingdom", ask_sponsorship: true, answered: false },
    resume_required: true,
    resume_uploaded: true,
    resume_review: { required: true, confirmed: false, skipped: false },
    ready_to_start: false,
    missing: ["onboarding", "resume_review"],
    ...overrides,
  };
}

let intakeState: Record<string, unknown> = portalIntake();

function filled(overrides: Partial<OnboardingDraft> = {}): OnboardingDraft {
  return {
    ...draftFrom(null, "Jane Doe"),
    target_role: "data_analyst",
    open_to_ai_ml: "maybe",
    technical_background: "BSc in statistics; two years of reporting in SQL and Python.",
    primary_language: "python",
    self_rating: 6,
    scripting: "some",
    scripting_details: "Pandas scripts that clean weekly exports",
    experience_level_comfort: "unsure",
    training_commitment: "full",
    availability: "within_2_weeks",
    accuracy_confirmed: true,
    ...overrides,
  };
}

describe("onboarding model", () => {
  it("requires the core answers and the conditional 'other' fields", () => {
    const errors = validateOnboarding(draftFrom(null, null), { enabled: false, askSponsorship: true });
    expect(Object.keys(errors).sort()).toEqual(
      ["accuracy_confirmed", "availability", "experience_level_comfort", "full_name", "open_to_ai_ml", "primary_language", "scripting", "self_rating", "target_role", "technical_background", "training_commitment"].sort(),
    );
    expect(validateOnboarding(filled({ target_role: "other" }), null).target_role_other).toBeDefined();
    expect(validateOnboarding(filled({ primary_language: "other" }), null).primary_language_other).toBeDefined();
    expect(validateOnboarding(filled({ years_hands_on: "60" }), null).years_hands_on).toBeDefined();
    expect(validateOnboarding(filled({ weekly_hours: "12.5" }), null).weekly_hours).toBeDefined();
    expect(validateOnboarding(filled({ technical_background: "x".repeat(1501) }), null).technical_background).toMatch(/1,500/);
    expect(validateOnboarding(filled(), null)).toEqual({});
  });

  it("asks the work-authorisation questions only when the organisation turned them on", () => {
    expect(validateOnboarding(filled(), { enabled: true, askSponsorship: true })).toMatchObject({ work_authorized: expect.any(String), needs_sponsorship: expect.any(String) });
    expect(validateOnboarding(filled(), { enabled: true, askSponsorship: false })).not.toHaveProperty("needs_sponsorship");
    expect(toOnboardingBody(filled(), { enabled: false, askSponsorship: true }).eligibility).toBeNull();
    expect(toOnboardingBody(filled({ work_authorized: "yes", needs_sponsorship: "discuss" }), { enabled: true, askSponsorship: true }).eligibility).toEqual({ authorized: true, needs_sponsorship: null });
    expect(toOnboardingBody(filled({ work_authorized: "no" }), { enabled: true, askSponsorship: false }).eligibility).toEqual({ authorized: false, needs_sponsorship: null });
  });

  it("builds the request body the server expects", () => {
    const body = toOnboardingBody(filled({ target_role: "ai_ml", open_to_ai_ml: "", years_hands_on: "2,5", weekly_hours: "20", scripting: "none" }), null);
    expect(body).toMatchObject({ target_role: "ai_ml", open_to_ai_ml: "yes", years_hands_on: 2.5, weekly_hours: 20, scripting_details: null, target_role_other: null, accuracy_confirmed: true, available_from: null });
    expect(body).not.toHaveProperty("email");
  });

  it("maps server field errors onto the form", () => {
    const pydantic = new ApiError({ status: 422, code: "validation_failed", message: "The request is invalid.", details: { errors: [{ loc: ["body", "self_rating"], msg: "Input should be less than or equal to 10" }, { loc: ["body", "eligibility", "authorized"], msg: "Required" }] } });
    expect(onboardingFieldErrors(pydantic)).toEqual({ self_rating: "Input should be less than or equal to 10", "eligibility.authorized": "Required" });
    const single = new ApiError({ status: 422, code: "target_role_other_required", message: "Name the role.", details: { field: "target_role_other" } });
    expect(onboardingFieldErrors(single)).toEqual({ target_role_other: "Name the role." });
    expect(formFieldFor("eligibility.authorized")).toBe("work_authorized");
    expect(formFieldFor("eligibility.needs_sponsorship")).toBe("needs_sponsorship");
    expect(formFieldFor("nonsense")).toBeNull();
  });

  it("prefills from saved answers but asks to confirm accuracy again", () => {
    const draft = draftFrom({ full_name: "Jane Doe", target_role: "ai_ml", self_rating: 7 }, "Ignored", { enabled: true, country: "x", askSponsorship: true, answered: true, authorized: true, needsSponsorship: null });
    expect(draft).toMatchObject({ full_name: "Jane Doe", target_role: "ai_ml", self_rating: 7, accuracy_confirmed: false, work_authorized: "yes", needs_sponsorship: "discuss" });
  });
});

function renderDetails() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      {
        path: "/candidate",
        element: <CandidateShellLayout />,
        children: [
          { path: ":interviewId/details", Component: IntakeDetailsPage },
          { path: ":interviewId/resume-review", element: <p>review step</p> },
          { path: ":interviewId/consent", element: <p>consent step</p> },
        ],
      },
    ],
    { initialEntries: ["/candidate/i1/details"] },
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

function choose(name: string, value: string) {
  fireEvent.click(screen.getByTestId(`intake-field-${name}-${value}`));
}

function fillRequired() {
  choose("target_role", "data_analyst");
  choose("open_to_ai_ml", "maybe");
  fireEvent.change(screen.getByTestId("intake-field-technical_background"), { target: { value: "BSc statistics, two years of SQL reporting." } });
  choose("primary_language", "python");
  choose("self_rating", "6");
  choose("scripting", "some");
  choose("experience_level_comfort", "unsure");
  choose("training_commitment", "full");
  choose("availability", "within_2_weeks");
  fireEvent.click(screen.getByTestId("intake-field-accuracy_confirmed"));
}

beforeEach(() => {
  intakeState = portalIntake();
  sessionStorage.clear();
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path === "/portal/interviews/i1") return INTERVIEW as never;
    if (path === "/portal/me") return { candidate: { id: "c1", full_name: "Jane Doe", email: "jane@example.com" }, interview_id: "i1", organization: { name: "TechSara Solutions" } } as never;
    if (path === "/portal/intake/i1") return intakeState as never;
    throw new ApiError({ status: 404, code: "not_found", message: `unexpected ${path}` });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("IntakeDetailsPage", () => {
  it("validates on submit, asks the eligibility questions with the configured country, and saves", async () => {
    const put = vi.spyOn(api, "put").mockResolvedValue(portalIntake({ onboarding_submitted: true }) as never);
    const router = renderDetails();
    expect(await screen.findByTestId("intake-details-page")).toBeInTheDocument();
    expect(await screen.findByTestId("intake-field-full_name")).toHaveValue("Jane Doe");
    expect(screen.getByText(/jane@example.com/)).toBeInTheDocument();
    expect(screen.getByText("Are you legally authorised to work in the United Kingdom for this role?")).toBeInTheDocument();
    expect(screen.getByText(/never by the AI interviewer or in any assessment/)).toBeInTheDocument();
    // Nothing about citizenship, nationality or visas.
    expect(document.body.textContent).not.toMatch(/citizen|nationality|visa/i);

    fireEvent.click(screen.getByTestId("intake-details-submit"));
    expect(await screen.findByTestId("intake-field-error-target_role")).toBeInTheDocument();
    expect(screen.getByTestId("intake-field-error-work_authorized")).toBeInTheDocument();
    expect(screen.getByTestId("intake-field-error-accuracy_confirmed")).toBeInTheDocument();
    expect(screen.getByText(/answers need attention/)).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();

    fillRequired();
    choose("work_authorized", "yes");
    choose("needs_sponsorship", "discuss");
    expect(screen.queryByTestId("intake-field-error-target_role")).toBeNull();
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const [path, body] = put.mock.calls[0]!;
    expect(path).toBe("/portal/intake/i1/onboarding");
    expect(body).toMatchObject({
      full_name: "Jane Doe",
      target_role: "data_analyst",
      open_to_ai_ml: "maybe",
      primary_language: "python",
      self_rating: 6,
      scripting: "some",
      experience_level_comfort: "unsure",
      training_commitment: "full",
      availability: "within_2_weeks",
      accuracy_confirmed: true,
      eligibility: { authorized: true, needs_sponsorship: null },
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/resume-review"));
  });

  it("does not show or send the eligibility questions when they are off, and hides the AI/ML question for AI/ML", async () => {
    intakeState = portalIntake({ eligibility: { enabled: false, country: null, ask_sponsorship: true, answered: false } });
    const put = vi.spyOn(api, "put").mockResolvedValue(portalIntake({ onboarding_submitted: true }) as never);
    renderDetails();
    await screen.findByTestId("intake-field-full_name");
    expect(screen.queryByTestId("intake-field-work_authorized")).toBeNull();
    expect(screen.queryByText(/legally authorised/)).toBeNull();
    fillRequired();
    choose("target_role", "ai_ml");
    expect(screen.queryByTestId("intake-field-open_to_ai_ml")).toBeNull();
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0]![1]).toMatchObject({ eligibility: null, open_to_ai_ml: "yes", target_role: "ai_ml" });
  });

  it("shows conditional fields only when they apply", async () => {
    renderDetails();
    await screen.findByTestId("intake-field-full_name");
    expect(screen.queryByTestId("intake-field-target_role_other")).toBeNull();
    choose("target_role", "other");
    expect(screen.getByTestId("intake-field-target_role_other")).toBeInTheDocument();
    choose("primary_language", "other");
    expect(screen.getByTestId("intake-field-primary_language_other")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-field-scripting_details")).toBeNull();
    choose("scripting", "regular");
    expect(screen.getByTestId("intake-field-scripting_details")).toBeInTheDocument();
    choose("availability", "later");
    expect(screen.getByTestId("intake-field-available_from")).toBeInTheDocument();
  });

  it("puts server validation errors on their fields", async () => {
    vi.spyOn(api, "put").mockRejectedValue(new ApiError({ status: 422, code: "validation_failed", message: "The request is invalid.", details: { errors: [{ loc: ["body", "technical_background"], msg: "String should have at most 1500 characters" }] } }));
    renderDetails();
    await screen.findByTestId("intake-field-full_name");
    fillRequired();
    choose("work_authorized", "no");
    choose("needs_sponsorship", "yes");
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    expect(await screen.findByTestId("intake-field-error-technical_background")).toHaveTextContent("at most 1500 characters");
    // Changing the answer clears the server's message.
    fireEvent.change(screen.getByTestId("intake-field-technical_background"), { target: { value: "Shorter background." } });
    expect(screen.queryByTestId("intake-field-error-technical_background")).toBeNull();
  });

  it("explains a locked form and lets the candidate continue", async () => {
    intakeState = portalIntake({ onboarding_locked: true, onboarding_submitted: true, onboarding: { full_name: "Jane Doe", target_role: "ai_ml", self_rating: 7 } });
    const router = renderDetails();
    expect(await screen.findByText("These details are locked")).toBeInTheDocument();
    expect(screen.getByTestId("intake-field-full_name")).toBeDisabled();
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/resume-review"));
  });

  it("takes the candidate to consent when the server says it is missing", async () => {
    vi.spyOn(api, "put").mockRejectedValue(new ApiError({ status: 409, code: "consent_required", message: "Consent first." }));
    const router = renderDetails();
    await screen.findByTestId("intake-field-full_name");
    fillRequired();
    choose("work_authorized", "yes");
    choose("needs_sponsorship", "no");
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/consent"));
  });

  it("shows a role the hiring team set as read-only and sends it", async () => {
    intakeState = portalIntake({ target_role_preset: "ai_ml", eligibility: { enabled: false, country: null, ask_sponsorship: true, answered: false } });
    const put = vi.spyOn(api, "put").mockResolvedValue(portalIntake({ onboarding_submitted: true }) as never);
    renderDetails();
    const preset = await screen.findByTestId("intake-field-target_role");
    expect(preset.tagName).toBe("P");
    expect(preset).toHaveAttribute("data-preset", "ai_ml");
    expect(preset).toHaveTextContent("AI/ML");
    expect(screen.getByText("The hiring team set this for your interview.")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-field-target_role-data_analyst")).toBeNull();
    expect(screen.queryByTestId("intake-field-open_to_ai_ml")).toBeNull();
    fireEvent.change(screen.getByTestId("intake-field-technical_background"), { target: { value: "Self-taught Python, two years." } });
    choose("primary_language", "python");
    choose("self_rating", "5");
    choose("scripting", "none");
    choose("experience_level_comfort", "yes");
    choose("training_commitment", "partial");
    choose("availability", "immediately");
    fireEvent.click(screen.getByTestId("intake-field-accuracy_confirmed"));
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0]![1]).toMatchObject({ target_role: "ai_ml", open_to_ai_ml: "yes", target_role_other: null, eligibility: null });
  });

  it("puts the server's intake error codes on their fields", async () => {
    vi.spyOn(api, "put").mockRejectedValue(
      new ApiError({ status: 422, code: "eligibility_required", message: "Please answer the work eligibility questions.", details: { field: "eligibility", errors: [{ loc: ["body"], msg: "Please answer the work eligibility questions.", type: "eligibility_required" }] } }),
    );
    renderDetails();
    await screen.findByTestId("intake-field-full_name");
    fillRequired();
    choose("work_authorized", "yes");
    choose("needs_sponsorship", "no");
    fireEvent.click(screen.getByTestId("intake-details-submit"));
    expect(await screen.findByTestId("intake-field-error-work_authorized")).toHaveTextContent("Please answer the work eligibility questions.");
  });

  it("still works with defaults when the intake routes are not available", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path === "/portal/interviews/i1") return INTERVIEW as never;
      if (path === "/portal/me") return { candidate: { id: "c1", full_name: "Jane Doe", email: "jane@example.com" }, interview_id: "i1", organization: { name: "TechSara Solutions" } } as never;
      throw new ApiError({ status: 404, code: "intake_disabled", message: "Intake interviews are turned off." });
    });
    renderDetails();
    const form = await screen.findByTestId("intake-details-form");
    expect(within(form).queryByTestId("intake-field-work_authorized")).toBeNull();
    expect(within(form).getByTestId("intake-field-target_role")).toBeInTheDocument();
  });
});
