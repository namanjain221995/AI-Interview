import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";

import { normalizeResumeReview, toWireDoc } from "../api";
import IntakeResumeReviewPage, { reviewPollInterval } from "../pages/IntakeResumeReviewPage";
import { countChanges, splitCommas, splitLines, validateEducation, validateRole } from "../resumeReview";
import type { ResumeReviewDoc } from "../types";

const INTERVIEW = {
  id: "i1",
  status: "invited",
  organization: { name: "TechSara Solutions" },
  job: { id: "j1", title: "AI/ML Training Program — Intake" },
  rounds_offered: [{ id: "r1", name: "Intake Interview", round_type: "intake", duration_minutes: 45 }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: {},
  steps_completed: ["welcome", "instructions", "consent", "resume", "details"],
  expires_at: "2026-10-16T12:00:00Z",
  interviewer_name: "Maya",
  intake: { intake_id: "in1", title: "Intake Interview", estimated_minutes: 45, presence: "orb" },
};

const QUICK_DOC = {
  version: 1,
  headline: "Data analyst moving into machine learning",
  experience_years: 2,
  roles: [{ ref: "r1", title: "Data Analyst", company: "Northwind", start: "2023-01", end: null, responsibilities: ["Weekly sales reporting in SQL"], technologies: ["SQL", "Python"], achievements: [] }],
  projects: [{ ref: "p1", name: "Churn dashboard", role: "Author", description: "A dashboard of monthly churn.", technologies: ["Power BI"], outcomes: [] }],
  skills: ["SQL", "Excel"],
  languages: ["Python"],
  tools: ["Git"],
  education: [{ ref: "e1", institution: "University of Leeds", degree: "BSc", field: "Statistics" }],
  certifications: [],
};

const FULL_DOC = { ...QUICK_DOC, skills: ["SQL", "Excel", "pandas"], tools: ["Git", "Jupyter"] };

function reviewOut(overrides: Record<string, unknown> = {}) {
  return { status: "ready", profile_stage: "quick", sections: [{ key: "experience", label: "Work experience", state: "active" }], document: QUICK_DOC, confirmed: false, skipped: false, confirmed_at: null, can_skip: false, skip_available_in_s: 120, ...overrides };
}

let responses: Array<Record<string, unknown>> = [];
let intakeState: Record<string, unknown> = {};

function renderReview() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      {
        path: "/candidate",
        element: <CandidateShellLayout />,
        children: [
          { path: ":interviewId/resume-review", Component: IntakeResumeReviewPage },
          { path: ":interviewId/system-check", element: <p>system check</p> },
          { path: ":interviewId/resume", element: <p>resume step</p> },
        ],
      },
    ],
    { initialEntries: ["/candidate/i1/resume-review"] },
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
  sessionStorage.clear();
  responses = [reviewOut()];
  intakeState = { intake_id: "in1", interview_id: "i1", resume_required: true, resume_uploaded: true, eligibility: { enabled: false }, resume_review: { required: true, confirmed: false, skipped: false } };
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path === "/portal/interviews/i1") return INTERVIEW as never;
    if (path === "/portal/intake/i1") return intakeState as never;
    if (path === "/portal/intake/i1/resume-review") return (responses.length > 1 ? responses.shift() : responses[0]) as never;
    throw new ApiError({ status: 404, code: "not_found", message: `unexpected ${path}` });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("résumé review helpers", () => {
  it("polls fast until the first read, calmly until checked, then stops", () => {
    expect(reviewPollInterval(undefined)).toBe(600);
    expect(reviewPollInterval(normalizeResumeReview({ status: "waiting", document: null }))).toBe(600);
    expect(reviewPollInterval(normalizeResumeReview(reviewOut()))).toBe(1500);
    expect(reviewPollInterval(normalizeResumeReview(reviewOut({ profile_stage: "full" })))).toBe(false);
    expect(reviewPollInterval(normalizeResumeReview(reviewOut({ confirmed: true })))).toBe(false);
  });

  it("sends items the candidate added with ref new, and counts corrections", () => {
    const doc = normalizeResumeReview(reviewOut()).document!;
    const edited: ResumeReviewDoc = {
      ...doc,
      roles: [{ ...doc.roles[0]!, title: "Senior Data Analyst" }, { ref: "new-7", title: "Intern", company: "Contoso", start: null, end: null, responsibilities: [], technologies: [], achievements: [] }],
      skills: ["SQL", "Docker"],
      projects: [],
    };
    expect(toWireDoc(edited).roles.map((r) => r.ref)).toEqual(["r1", "new"]);
    // role changed + role added + project removed + Excel removed + Docker added
    expect(countChanges(doc, edited)).toBe(5);
    expect(countChanges(doc, doc)).toBe(0);
  });

  it("mirrors the server's item rules", () => {
    const role = { ref: "r1", title: "", company: "Contoso", start: null, end: null, responsibilities: [], technologies: [], achievements: [] };
    expect(validateRole(role).title).toBe("Add the job title.");
    expect(validateRole({ ...role, title: "Analyst", start: "the spring of 2021 or so" }).start).toBeDefined();
    expect(validateRole({ ...role, title: "Analyst", responsibilities: ["x".repeat(401)] }).responsibilities).toBeDefined();
    expect(validateRole({ ...role, title: "Analyst" })).toEqual({});
    expect(validateEducation({ ref: "e1", institution: "", degree: "BSc", field: null }).institution).toBeDefined();
  });

  it("parses the item forms' lists", () => {
    expect(splitLines("- one\n\n• two\nthree", 2)).toEqual(["one", "two"]);
    expect(splitCommas("SQL, Python; sql, ,Git", 15)).toEqual(["SQL", "Python", "Git"]);
  });
});

describe("IntakeResumeReviewPage", () => {
  it("shows the first read as soon as it exists and the checked version when it arrives", async () => {
    responses = [{ status: "waiting", profile_stage: "none", sections: [], document: null, confirmed: false, skipped: false, can_skip: false, skip_available_in_s: 170 }, reviewOut(), reviewOut({ profile_stage: "full", document: FULL_DOC, sections: [] })];
    renderReview();
    expect(await screen.findByText("Reading your résumé")).toBeInTheDocument();
    expect(screen.getByTestId("intake-resume-review")).toHaveAttribute("data-stage", "none");
    expect(await screen.findByTestId("resume-review-stage", {}, { timeout: 3000 })).toHaveTextContent("First read");
    expect(screen.getByTestId("intake-resume-review")).toHaveAttribute("data-stage", "quick");
    expect(screen.getByTestId("resume-review-item-r1")).toHaveTextContent("Data Analyst");
    await waitFor(() => expect(screen.getByTestId("resume-review-stage")).toHaveTextContent("Checked"), { timeout: 4000 });
    expect(screen.getByTestId("resume-review-section-skills")).toHaveTextContent("pandas");
  }, 10_000);

  it("edits, adds, removes and undoes, then confirms the corrected document", async () => {
    responses = [reviewOut({ profile_stage: "full" })];
    const put = vi.spyOn(api, "put").mockResolvedValue(reviewOut({ profile_stage: "full", confirmed: true }) as never);
    const router = renderReview();
    await screen.findByTestId("resume-review-item-r1");
    for (const section of ["roles", "projects", "skills", "languages", "tools", "education", "certifications"]) {
      expect(screen.getByTestId(`resume-review-section-${section}`)).toBeInTheDocument();
    }

    // Edit a role.
    fireEvent.click(screen.getByTestId("resume-review-edit-r1"));
    const form = screen.getByRole("group", { name: /Edit Data Analyst/ });
    fireEvent.change(within(form).getByLabelText("Job title"), { target: { value: "Senior Data Analyst" } });
    fireEvent.click(within(form).getByTestId("resume-review-item-save"));
    expect(screen.getByTestId("resume-review-item-r1")).toHaveTextContent("Senior Data Analyst");

    // Add a role, then cancel another add: only the saved one stays.
    fireEvent.click(screen.getByTestId("resume-review-add-roles"));
    const add = screen.getByRole("group", { name: "Add a role" });
    fireEvent.change(within(add).getByLabelText("Job title"), { target: { value: "Analytics Intern" } });
    fireEvent.change(within(add).getByLabelText("Company"), { target: { value: "Contoso" } });
    fireEvent.click(within(add).getByTestId("resume-review-item-save"));
    fireEvent.click(screen.getByTestId("resume-review-add-roles"));
    fireEvent.click(within(screen.getByRole("group", { name: "Add a role" })).getByText("Cancel"));
    expect(within(screen.getByTestId("resume-review-section-roles")).getAllByText(/Analytics Intern/)).toHaveLength(1);

    // Remove a project and undo it; remove education for good.
    fireEvent.click(screen.getByTestId("resume-review-remove-p1"));
    expect(screen.queryByTestId("resume-review-item-p1")).toBeNull();
    fireEvent.click(within(screen.getByTestId("resume-review-section-projects")).getByText("Undo"));
    expect(screen.getByTestId("resume-review-item-p1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("resume-review-remove-e1"));

    // Chips: add, remove, edit.
    const skillInput = within(screen.getByTestId("resume-review-section-skills")).getByPlaceholderText("Add a skill");
    fireEvent.change(skillInput, { target: { value: "Docker" } });
    fireEvent.click(screen.getByTestId("resume-review-add-skills"));
    fireEvent.click(screen.getByTestId("resume-review-remove-skills-1"));
    fireEvent.click(screen.getByTestId("resume-review-edit-tools-0"));
    const chipEditor = screen.getByLabelText("Edit Git");
    fireEvent.change(chipEditor, { target: { value: "GitHub" } });
    fireEvent.keyDown(chipEditor, { key: "Enter" });
    expect(screen.getByTestId("resume-review-section-tools")).toHaveTextContent("GitHub");

    expect(screen.getByText(/corrections will be saved with your résumé/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("resume-review-confirm"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const [path, body] = put.mock.calls[0]! as [string, { action: string; document: ResumeReviewDoc }];
    expect(path).toBe("/portal/intake/i1/resume-review");
    expect(body.action).toBe("confirm");
    expect(body.document.roles.map((r) => [r.ref, r.title])).toEqual([
      ["r1", "Senior Data Analyst"],
      ["new", "Analytics Intern"],
    ]);
    expect(body.document.projects.map((p) => p.ref)).toEqual(["p1"]);
    expect(body.document.education).toEqual([]);
    expect(body.document.skills).toEqual(["SQL", "Docker"]);
    expect(body.document.tools).toEqual(["GitHub"]);
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/system-check"));
  });

  it("offers the latest version when the résumé changed under the edits (unknown_ref)", async () => {
    responses = [reviewOut({ profile_stage: "full" })];
    vi.spyOn(api, "put").mockRejectedValue(new ApiError({ status: 422, code: "unknown_ref", message: "Some items do not match your résumé any more. Please reload the page and try again.", details: { field: "roles[r1]" } }));
    renderReview();
    await screen.findByTestId("resume-review-item-r1");
    fireEvent.click(screen.getByTestId("resume-review-remove-skills-0"));
    fireEvent.click(screen.getByTestId("resume-review-confirm"));
    const error = await screen.findByTestId("resume-review-error");
    expect(error).toHaveTextContent("Some items do not match your résumé any more.");
    fireEvent.click(within(error).getByText("Show the latest version"));
    expect(screen.queryByTestId("resume-review-error")).toBeNull();
    expect(screen.getByTestId("resume-review-section-skills")).toHaveTextContent("SQL");
  });

  it("holds the confirm button while an item form is open", async () => {
    responses = [reviewOut({ profile_stage: "full" })];
    renderReview();
    await screen.findByTestId("resume-review-item-r1");
    fireEvent.click(screen.getByTestId("resume-review-edit-r1"));
    expect(screen.getByTestId("resume-review-confirm")).toBeDisabled();
    expect(screen.getByText("Save or cancel the item you're editing first.")).toBeInTheDocument();
  });

  it("offers the checked version without overwriting the candidate's edits", async () => {
    responses = [reviewOut(), reviewOut({ profile_stage: "full", document: FULL_DOC })];
    renderReview();
    await screen.findByTestId("resume-review-item-r1");
    fireEvent.click(screen.getByTestId("resume-review-remove-skills-0"));
    expect(await screen.findByText("We've finished checking your résumé", {}, { timeout: 4000 })).toBeInTheDocument();
    // The edit survived the refresh.
    expect(screen.getByTestId("resume-review-section-skills")).not.toHaveTextContent("pandas");
    fireEvent.click(screen.getByText("Show the checked version"));
    expect(screen.getByTestId("resume-review-section-skills")).toHaveTextContent("pandas");
  }, 10_000);

  it("offers to continue without reviewing once processing has taken three minutes", async () => {
    responses = [{ status: "waiting", profile_stage: "none", sections: [], document: null, confirmed: false, skipped: false, can_skip: false, skip_available_in_s: 1 }];
    const put = vi.spyOn(api, "put").mockResolvedValue({ status: "waiting", document: null, skipped: true, confirmed: false } as never);
    const router = renderReview();
    await screen.findByText("Reading your résumé");
    expect(screen.queryByTestId("resume-review-skip")).toBeNull();
    const skip = await screen.findByTestId("resume-review-skip", {}, { timeout: 3000 });
    fireEvent.click(skip);
    await waitFor(() => expect(put).toHaveBeenCalledWith("/portal/intake/i1/resume-review", { action: "skip", document: null }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/system-check"));
  });

  it("sends the candidate back to upload a required résumé, and lets an optional one go", async () => {
    intakeState = { ...intakeState, resume_uploaded: false };
    const router = renderReview();
    expect(await screen.findByText("Upload your résumé first")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Upload your résumé"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i1/resume"));
    cleanup();
    intakeState = { ...intakeState, resume_uploaded: false, resume_required: false };
    const second = renderReview();
    expect(await screen.findByText(/You didn't add a résumé/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("resume-review-confirm"));
    await waitFor(() => expect(second.state.location.pathname).toBe("/candidate/i1/system-check"));
  });
});
