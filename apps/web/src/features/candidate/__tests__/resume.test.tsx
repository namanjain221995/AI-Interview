import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ComponentType } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateShellLayout } from "@/components/layout/CandidateShell";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";

import { normalizeResume } from "../api";
import { FIRST_READ_POLL_MS, REFINE_POLL_MS, resumePollInterval } from "../components/ResumeSummary";
import ResumePage from "../pages/ResumePage";

const INTERVIEW = {
  id: "i1",
  status: "invited",
  organization: { name: "Demo Corp" },
  job: { id: "j1", title: "Senior DevOps Engineer" },
  rounds_offered: [{ id: "r1", name: "Combined AI Round", round_type: "combined", duration_minutes: 30, expected_difficulty: "medium", coding_enabled: true }],
  selected_round_id: "r1",
  allow_round_selection: false,
  policy: { candidate_report_mode: "immediate", integrity: { enabled: true } },
  steps_completed: ["consent"],
  expires_at: "2026-10-09T12:00:00Z",
};

const SUMMARY = {
  candidate_name: "Jane Doe",
  headline: "Senior DevOps Engineer",
  experience_years: 5.8,
  roles: [
    { title: "Senior DevOps Engineer", company: "Bluepeak Analytics", start: "2023-01", end: "present" },
    { title: "DevOps Engineer", company: "Harborline Logistics", start: "2021-08", end: "2023-03" },
  ],
  top_skills: ["Kubernetes", "AWS", "Terraform"],
  education: [{ institution: "Lakeview State University", degree: "B.S.", field: "Computer Science" }],
  certifications: ["Certified Kubernetes Administrator (CKA)"],
};

function resumeDto(overrides: Record<string, unknown> = {}, resume: Record<string, unknown> = {}) {
  const at = new Date().toISOString();
  return {
    resume: {
      id: "r1",
      original_filename: "jane.pdf",
      size_bytes: 4062,
      status: "analyzing",
      profile_ready: false,
      created_at: at,
      steps: [
        { key: "uploaded", label: "Uploaded", state: "done", at },
        { key: "extracting", label: "Reading the document", state: "done", at },
        { key: "ocr", label: "Scanning pages", state: "skipped", at },
        { key: "analyzing", label: "Analyzing experience and skills", state: "active", at },
        { key: "ready", label: "Ready", state: "pending" },
      ],
      ...resume,
    },
    profile_summary: SUMMARY,
    profile_stage: "quick",
    sections: [
      { key: "basics", label: "Name and headline", state: "done" },
      { key: "experience", label: "Work experience", state: "active" },
      { key: "skills", label: "Skills", state: "quick" },
      { key: "education", label: "Education and certifications", state: "fallback" },
      { key: "interview", label: "Interview topics", state: "active" },
    ],
    ...overrides,
  };
}

const OCR_STEPS = [
  { key: "uploaded", label: "Uploaded", state: "done" },
  { key: "extracting", label: "Reading the document", state: "done" },
  { key: "ocr", label: "Scanning pages", state: "active" },
  { key: "analyzing", label: "Analyzing", state: "pending" },
];

let resumeResponse: unknown = null;

function renderPage(routes: Array<{ path: string; Component: ComponentType }> = [{ path: ":interviewId/resume", Component: ResumePage }]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/candidate", element: <CandidateShellLayout />, children: routes.map((r) => ({ path: r.path, Component: r.Component })) },
      { path: "*", element: <p>elsewhere</p> },
    ],
    { initialEntries: ["/candidate/i1/resume"] },
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
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path === "/portal/interviews/i1") return INTERVIEW as never;
    if (path === "/portal/interviews/i1/resume") {
      if (resumeResponse === null) throw new ApiError({ status: 404, code: "not_found", message: "No resume" });
      return resumeResponse as never;
    }
    throw new ApiError({ status: 404, code: "not_found", message: `unexpected ${path}` });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resumeResponse = null;
});

describe("normalizeResume (quick profile + sections)", () => {
  it("reads the profile stage, the section states and the education", () => {
    const view = normalizeResume(resumeDto());
    expect(view.profileStage).toBe("quick");
    expect(view.sections?.map((s) => [s.key, s.state])).toEqual([
      ["basics", "done"],
      ["experience", "active"],
      ["skills", "quick"],
      ["education", "fallback"],
      ["interview", "active"],
    ]);
    expect(view.summary?.education).toEqual([{ institution: "Lakeview State University", degree: "B.S.", field: "Computer Science" }]);
    expect(view.summary?.partial).toBeNull();
    expect(view.extracted).toBe(true);
  });

  it("treats a summary from an older server as the full profile and drops unknown sections", () => {
    const view = normalizeResume({ resume_id: "r1", status: "ready", profile_ready: true, summary: { name: "Jane" }, sections: [{ key: "projects", state: "done" }, { key: "skills", state: "weird" }] });
    expect(view.profileStage).toBe("full");
    expect(view.sections).toEqual([]);
    expect(normalizeResume({ resume: null }).profileStage).toBe("none");
  });

  it("marks a partial read of a scanned résumé", () => {
    const view = normalizeResume(resumeDto({ profile_summary: { ...SUMMARY, partial: true, pages_read: 1, pages_total: 2 } }, { status: "ocr", steps: OCR_STEPS }));
    expect(view.summary?.partial).toEqual({ pagesRead: 1, pagesTotal: 2 });
    expect(view.extracted).toBe(false);
  });
});

describe("resumePollInterval", () => {
  it("polls fast until the first read is visible, then calmly, then stops", () => {
    expect(resumePollInterval(normalizeResume(resumeDto({ profile_summary: null, profile_stage: "none" }, { status: "extracting" })))).toBe(FIRST_READ_POLL_MS);
    expect(resumePollInterval(normalizeResume(resumeDto()))).toBe(REFINE_POLL_MS);
    expect(resumePollInterval(normalizeResume(resumeDto({ profile_stage: "full" }, { status: "ready", profile_ready: true })))).toBe(false);
    expect(resumePollInterval(normalizeResume(resumeDto({}, { status: "failed", error: "nope" })))).toBe(false);
    expect(FIRST_READ_POLL_MS).toBeLessThanOrEqual(1000); // the quick read must show within ~3 s of upload
  });
});

describe("ResumePage progressive summary", () => {
  it("shows the first read immediately with honest per-section states, and continue is open", async () => {
    resumeResponse = resumeDto();
    renderPage();
    const summary = await screen.findByTestId("resume-summary");
    expect(summary).toHaveAttribute("data-stage", "quick");
    expect(within(summary).getByRole("heading", { name: "Jane Doe" })).toBeInTheDocument();
    expect(within(summary).getByTestId("resume-profile-stage")).toHaveTextContent("First read");
    expect(within(summary).getByText("Bluepeak Analytics", { exact: false })).toBeInTheDocument();
    expect(within(summary).getByText("Kubernetes")).toBeInTheDocument();
    expect(within(summary).getByText(/B\.S\. Computer Science/)).toBeInTheDocument();
    expect(within(summary).getByTestId("resume-section-basics")).toHaveAttribute("data-state", "done");
    expect(within(summary).getByTestId("resume-section-experience")).toHaveTextContent("Checking");
    expect(within(summary).getByTestId("resume-section-skills")).toHaveAttribute("data-state", "quick");
    expect(within(summary).getByTestId("resume-section-education")).toHaveTextContent("As read from your file");
    expect(within(summary).getByTestId("resume-section-interview")).toHaveTextContent("Preparing your interview topics");
    expect(summary).toHaveTextContent(/first read of your file/);
    expect(screen.getByTestId("resume-status")).toHaveTextContent("Read · checking the details in the background");
    expect(screen.getByText(/usually less than a minute/)).toBeInTheDocument();
    expect(screen.getByTestId("resume-continue")).toBeEnabled();
  });

  it("drops the progress marks once the checked profile is ready (fallback stays visible)", async () => {
    resumeResponse = resumeDto(
      {
        profile_stage: "full",
        sections: [
          { key: "basics", state: "done" },
          { key: "experience", state: "done" },
          { key: "skills", state: "done" },
          { key: "education", state: "fallback" },
          { key: "interview", state: "done" },
        ],
      },
      { status: "ready", profile_ready: true, steps: [] },
    );
    renderPage();
    const summary = await screen.findByTestId("resume-summary");
    expect(summary).toHaveAttribute("data-stage", "full");
    expect(within(summary).queryByTestId("resume-profile-stage")).toBeNull();
    expect(within(summary).queryByTestId("resume-section-experience")).toBeNull();
    expect(within(summary).queryByTestId("resume-section-interview")).toBeNull();
    expect(within(summary).getByTestId("resume-section-education")).toHaveAttribute("data-state", "fallback");
    expect(screen.getByTestId("resume-status")).toHaveTextContent(/ready/i);
  });

  it("shows page 1 of a scanned résumé while the rest is scanned (continue waits for the text)", async () => {
    resumeResponse = resumeDto(
      { profile_summary: { ...SUMMARY, partial: true, pages_read: 1, pages_total: 2 } },
      { status: "ocr", steps: OCR_STEPS },
    );
    renderPage();
    expect(await screen.findByTestId("resume-partial-note")).toHaveTextContent("From page 1 of 2");
    expect(screen.getByTestId("resume-status")).toHaveTextContent("Read page 1 of 2 · scanning the rest");
    expect(screen.getByTestId("resume-continue")).toBeDisabled();
  });

  it("shows no summary before the first read and never for a failed file", async () => {
    resumeResponse = resumeDto({ profile_summary: { ...SUMMARY }, profile_stage: "quick" }, { status: "failed", error: "We could not read it." });
    renderPage();
    expect(await screen.findByText("We couldn't use this file")).toBeInTheDocument();
    expect(screen.queryByTestId("resume-summary")).toBeNull();
  });
});

describe("formatResumeDate", () => {
  it("writes résumé dates the way people read them", async () => {
    const { formatResumeDate } = await import("../api");
    expect(formatResumeDate("2023-01")).toBe("Jan 2023");
    expect(formatResumeDate("2021")).toBe("2021");
    expect(formatResumeDate("present")).toBe("Present");
    expect(formatResumeDate("2021-13")).toBe("2021");
    expect(formatResumeDate(null)).toBeNull();
    const view = normalizeResume(resumeDto());
    expect(view.summary?.roles[0]?.period).toBe("Jan 2023 – Present");
  });
});
