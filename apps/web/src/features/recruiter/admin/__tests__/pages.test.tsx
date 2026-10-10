import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import type { StaffRole } from "@/lib/types";

import { defaultRoundConfig } from "../rounds/roundConfig";
import { defaultOrganizationSettings } from "../settings/settingsModel";

function signIn(role: StaffRole) {
  act(() =>
    useAuthStore.getState().setSession({
      user: { id: "u1", organization_id: "o1", email: "dana@acme.test", full_name: "Dana Whitfield", role, is_active: true, last_login_at: null, created_at: "", updated_at: "" },
      organization: { id: "o1", name: "Acme Robotics", slug: "acme" },
      permissions: permissionsForRole(role),
    }),
  );
}

function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

const NOW = "2026-10-07T09:00:00Z";
const job = {
  id: "j1",
  organization_id: "o1",
  title: "Senior DevOps Engineer",
  department: "Platform",
  location: "Remote",
  employment_type: "full_time",
  seniority: "senior",
  description: "## About the role",
  experience_min_years: 5,
  experience_max_years: 10,
  status: "open",
  interview_instructions: null,
  coding_requirements: { enabled: true, languages: ["python"], difficulty: "easy" },
  integrity_settings: {},
  recording_settings: {},
  candidate_report_mode: "immediate",
  allow_candidate_round_selection: false,
  created_by_id: null,
  created_at: NOW,
  updated_at: NOW,
  rounds_count: 1,
  candidates_count: 2,
  sessions_count: 1,
};

function mockApi(routesMap: Record<string, unknown>) {
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path in routesMap) return routesMap[path] as never;
    throw new ApiError({ status: 404, code: "not_found", message: `No mock for ${path}` });
  });
}

beforeEach(() => resetAuthStore());
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

describe("admin pages", () => {
  it("lists jobs with status counts and hides creation for interviewers", async () => {
    mockApi({ "/jobs": { items: [job], total: 1, limit: 25, offset: 0 } });
    signIn("INTERVIEWER");
    renderApp("/app/jobs");
    expect(await screen.findByRole("link", { name: "Senior DevOps Engineer" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Filter by status" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /new job/i })).not.toBeInTheDocument();
  });

  it("shows the job detail with the invite button and round blueprint", async () => {
    const round = { id: "r1", job_id: "j1", template_id: null, name: "Combined AI Round", round_type: "combined", sequence: 1, duration_minutes: 30, max_duration_minutes: 35, expected_difficulty: "medium", candidate_selectable: false, is_active: true, instructions: null, config: { ...defaultRoundConfig("combined", "Combined AI Round"), duration_minutes: 30, max_duration_minutes: 35 }, created_at: NOW, updated_at: NOW };
    mockApi({
      "/jobs/j1": job,
      "/jobs/j1/requirements": { items: [], total: 0 },
      "/jobs/j1/rounds": { items: [round], total: 1 },
      "/jobs/j1/pipeline": { items: [], total: 0, limit: 25, offset: 0, counts: { invited: 0, in_progress: 0, completed: 0, closed: 0, all: 0 } },
      "/competencies": { items: [], total: 0 },
      "/settings": defaultOrganizationSettings(),
    });
    signIn("RECRUITER");
    renderApp("/app/jobs/j1?tab=rounds");
    expect(await screen.findByRole("heading", { level: 1, name: "Senior DevOps Engineer" })).toBeInTheDocument();
    expect(screen.getByTestId("invite-candidate-button")).toBeEnabled();
    expect(await screen.findByRole("article", { name: "Round 1: Combined AI Round" })).toBeInTheDocument();
  });

  it("pages the job pipeline on the server and shows its exact counts (S14)", async () => {
    const expired = {
      id: "i-old",
      candidate_id: "c-old",
      job_id: "j1",
      round_id: null,
      allow_round_selection: false,
      status: "invited",
      is_expired: true,
      invite_expires_at: "2026-10-01T09:00:00Z",
      invite_opened_at: null,
      verified_at: null,
      policy_snapshot: {},
      system_check: {},
      invited_by_id: null,
      scheduled_for: null,
      completed_at: null,
      notes: null,
      created_at: NOW,
      updated_at: NOW,
      candidate: { id: "c-old", full_name: "Asha Rao", email: "asha@example.com" },
    };
    const get = vi.spyOn(api, "get").mockImplementation(async (path: string) => {
      if (path === "/jobs/j1") return job as never;
      if (path === "/jobs/j1/pipeline") return { items: [{ interview: expired, session: null }], total: 600, limit: 25, offset: 0, counts: { invited: 310, in_progress: 40, completed: 200, closed: 50, all: 600 } } as never;
      if (path === "/settings") return defaultOrganizationSettings() as never;
      return { items: [], total: 0 } as never;
    });
    signIn("RECRUITER");
    renderApp("/app/jobs/j1");
    expect(await screen.findByText("Asha Rao")).toBeInTheDocument();
    expect(screen.getAllByText("310")).toHaveLength(2); // stat card and stage filter: the server count, never capped at 200
    expect(screen.getByRole("tab", { name: /Pipeline\s*600/ })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Filter by stage" })).toBeInTheDocument();
    const row = screen.getByText("Asha Rao").closest("tr")!;
    expect(within(row).getAllByText(/Expired/).length).toBeGreaterThanOrEqual(2); // badge and date: past its deadline
    const call = get.mock.calls.find(([path]) => path === "/jobs/j1/pipeline");
    expect(call?.[1]).toMatchObject({ params: { stage: "all", limit: 25, offset: 0 } });
  });

  it("guards the settings page behind settings:manage and renders every section for admins", async () => {
    mockApi({ "/settings": defaultOrganizationSettings(), "/settings/voices": { voices: [] } });
    signIn("RECRUITER");
    renderApp("/app/settings");
    expect(await screen.findByText("You don't have access to this page")).toBeInTheDocument();
    cleanup();
    signIn("ADMIN");
    renderApp("/app/settings");
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    for (const label of ["Integrity", "Recording", "Data retention", "Interviewer", "Candidate reports", "Access", "Data notice"]) {
      expect(within(nav).getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("switch", { name: "Collect integrity signals" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
  });

  it("never offers the super admin role to an admin creating users", async () => {
    mockApi({ "/users": { items: [], total: 0, limit: 25, offset: 0 } });
    signIn("ADMIN");
    renderApp("/app/users");
    (await screen.findByRole("button", { name: "Add user" })).click();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: /^Admin/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: /Super admin/ })).not.toBeInTheDocument();
  });

  it("opens the add-candidate dialog from ?new=1 and drops the param on close, but not for read-only roles", async () => {
    mockApi({ "/candidates": { items: [], total: 0, limit: 25, offset: 0 }, "/jobs": { items: [job], total: 1, limit: 200, offset: 0 } });
    signIn("RECRUITER");
    const router = renderApp("/app/candidates?new=1");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Add a candidate" })).toBeInTheDocument();
    act(() => within(dialog).getByRole("button", { name: "Cancel" }).click());
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(router.state.location.search).toBe("");
    cleanup();
    signIn("INTERVIEWER");
    renderApp("/app/candidates?new=1");
    expect(await screen.findByRole("heading", { level: 1, name: "Candidates" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
