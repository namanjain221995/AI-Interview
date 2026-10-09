import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";

import { defaultOrganizationSettings } from "@/features/recruiter/admin/settings/settingsModel";

import { defaultSchema } from "../formSchema";
import type { FormSchema, OpenLinkCreate } from "../types";
import { LINK } from "./fixtures";

const NOW = "2026-10-08T09:00:00Z";
const JOB = {
  id: "j1",
  organization_id: "o1",
  title: "Senior DevOps Engineer",
  department: null,
  location: "Pune",
  employment_type: null,
  seniority: null,
  description: "",
  experience_min_years: null,
  experience_max_years: null,
  status: "open",
  interview_instructions: null,
  coding_requirements: {},
  integrity_settings: {},
  recording_settings: { record_video: false },
  candidate_report_mode: "after_review",
  allow_candidate_round_selection: false,
  created_by_id: null,
  created_at: NOW,
  updated_at: NOW,
};
const ROUND = (id: string, name: string, sequence: number) => ({
  id,
  job_id: "j1",
  template_id: null,
  name,
  round_type: "technical_screening",
  sequence,
  duration_minutes: 30,
  max_duration_minutes: 35,
  expected_difficulty: "medium",
  candidate_selectable: false,
  is_active: true,
  instructions: null,
  config: {},
  created_at: NOW,
  updated_at: NOW,
});

function signIn() {
  act(() =>
    useAuthStore.getState().setSession({
      user: { id: "u1", organization_id: "o1", email: "dana@acme.test", full_name: "Dana Whitfield", role: "RECRUITER", is_active: true, last_login_at: null, created_at: "", updated_at: "" },
      organization: { id: "o1", name: "Acme Robotics", slug: "acme" },
      permissions: permissionsForRole("RECRUITER"),
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

let serverErrors: Array<{ key: string; label: string; topic: string; message: string }> = [];

beforeEach(() => {
  resetAuthStore();
  serverErrors = [];
  const map: Record<string, unknown> = {
    "/open-links/form/catalog": { builtins: defaultSchema().fields, types: ["text"], limits: { max_fields: 30, max_options: 50 }, default_schema: defaultSchema() },
    "/jobs": { items: [JOB], total: 1 },
    "/jobs/j1": JOB,
    "/jobs/j1/rounds": { items: [ROUND("r1", "Screening", 1), ROUND("r2", "Deep dive", 2)], total: 2 },
    "/settings": defaultOrganizationSettings(),
    "/open-links/l1": LINK,
    "/open-links/l1/registrations": { items: [], total: 0 },
  };
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    if (path in map) return map[path] as never;
    throw new ApiError({ status: 404, code: "not_found", message: `No mock for ${path}` });
  });
});
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

function mockPosts(onCreate: (body: OpenLinkCreate) => unknown) {
  return vi.spyOn(api, "post").mockImplementation(async (path: string, body?: unknown) => {
    if (path === "/open-links/form/validate") {
      const schema = (body as { form_schema: FormSchema }).form_schema;
      const keys = new Set(schema.fields.map((f) => f.key));
      return { ok: true, errors: serverErrors.filter((e) => keys.has(e.key)), warnings: [], normalized: schema } as never;
    }
    if (path === "/open-links") return onCreate(body as OpenLinkCreate) as never;
    throw new ApiError({ status: 404, code: "not_found", message: path });
  });
}

describe("link editor", () => {
  it("builds a two-round link with a custom question, blocks protected fields, needs warning acknowledgement, and shows the code once", async () => {
    let created: OpenLinkCreate | null = null;
    const post = mockPosts((body) => {
      created = body;
      return { link: LINK, access_code: "K7P2-QX9M" };
    });
    signIn();
    const router = renderApp("/app/links/new?job_id=j1");

    await screen.findByTestId("form-builder");
    fireEvent.change(screen.getByTestId("link-name-input"), { target: { value: "Campus drive, Pune" } });
    // First active round is chosen by default; switch to a two-round pipeline.
    await waitFor(() => expect(screen.getByTestId("link-round-option-r1")).toHaveAttribute("data-state", "checked"));
    fireEvent.click(screen.getByTestId("link-round-mode-pipeline"));
    fireEvent.click(await screen.findByTestId("link-round-option-r2"));
    fireEvent.click(await screen.findByTestId("link-progression-after_review"));
    expect(screen.getByText("Progression never depends on AI scores.")).toBeInTheDocument();
    // Job policy notice: video is off for this job.
    expect(screen.getByText("Video recording is off for this job")).toBeInTheDocument();

    // Add a question and ask for something protected.
    fireEvent.keyDown(screen.getByTestId("form-add-field-button"), { key: "Enter" });
    fireEvent.click(await screen.findByTestId("form-add-field-text"));
    const label = await screen.findByTestId("form-field-label-c_new_question");
    fireEvent.change(label, { target: { value: "Age" } });
    expect(await screen.findByTestId("form-field-error-c_new_question")).toHaveTextContent(/never collect/);
    fireEvent.click(screen.getByTestId("link-save-button"));
    await waitFor(() => expect(screen.getAllByText("Check the highlighted sections").length).toBeGreaterThan(0));
    expect(post.mock.calls.some(([path]) => path === "/open-links")).toBe(false);

    // Reword into a borderline question: the key follows the label, and a warning needs an acknowledgement.
    fireEvent.change(label, { target: { value: "Expected salary" } });
    fireEvent.blur(label);
    const ack = await screen.findByTestId("form-field-warning-ack-c_expected_salary");
    expect(screen.queryByTestId("form-field-error-c_expected_salary")).not.toBeInTheDocument();
    // The live preview renders the candidate's form with the new question.
    expect(within(screen.getByTestId("form-preview")).getByTestId("open-link-field-c_expected_salary")).toBeInTheDocument();
    fireEvent.click(ack);

    fireEvent.click(screen.getByTestId("link-access-code-toggle"));
    fireEvent.click(screen.getByTestId("link-campus-preset"));
    fireEvent.click(screen.getByTestId("form-field-enabled-location"));
    fireEvent.click(screen.getByTestId("link-save-button"));

    await waitFor(() => expect(created).not.toBeNull());
    const body = created as unknown as OpenLinkCreate;
    expect(body).toMatchObject({
      job_id: "j1",
      name: "Campus drive, Pune",
      round_ids: ["r1", "r2"],
      progression: "after_review",
      require_access_code: true,
      per_ip_hourly_limit: 1000,
      round_window_hours: 72,
      require_resume: true,
      acknowledged_warnings: ["c_expected_salary:expected_pay"],
      duplicate_policy: { scope: "job", device: "flag", network: "flag" },
    });
    expect(body.form_schema!.fields.find((f) => f.key === "location")).toMatchObject({ enabled: true });
    expect(body.form_schema!.fields.at(-1)).toMatchObject({ key: "c_expected_salary", label: "Expected salary", builtin: false, type: "text" });

    await waitFor(() => expect(router.state.location.pathname).toBe("/app/links/l1"));
    expect(router.state.location.search).toBe("?tab=share");
    expect(await screen.findByTestId("link-access-code-value")).toHaveTextContent("K7P2-QX9M");
  });

  it("shows protected-field errors that only the server catches", async () => {
    serverErrors = [{ key: "c_new_question", label: "Gotra", topic: "religion", message: "This asks about caste or religion, which we never collect." }];
    const post = mockPosts(() => ({ link: LINK, access_code: null }));
    signIn();
    renderApp("/app/links/new?job_id=j1");
    await screen.findByTestId("form-builder");
    fireEvent.change(screen.getByTestId("link-name-input"), { target: { value: "Drive" } });
    fireEvent.keyDown(screen.getByTestId("form-add-field-button"), { key: "Enter" });
    fireEvent.click(await screen.findByTestId("form-add-field-text"));
    fireEvent.change(await screen.findByTestId("form-field-label-c_new_question"), { target: { value: "Gotra" } });
    expect(await screen.findByTestId("form-field-error-c_new_question", {}, { timeout: 3000 })).toHaveTextContent(/caste or religion/);
    fireEvent.click(screen.getByTestId("link-save-button"));
    await waitFor(() => expect(screen.getAllByText("Check the highlighted sections").length).toBeGreaterThan(0));
    expect(post.mock.calls.some(([path]) => path === "/open-links")).toBe(false);
  });

  it("maps a protected_field refusal on save onto the field", async () => {
    mockPosts(() => {
      throw new ApiError({
        status: 422,
        code: "protected_field",
        message: "This form asks for information we never collect: religion.",
        details: { fields: [{ key: "c_new_question", label: "Kul", topic: "religion", message: "Never collected." }] },
      });
    });
    signIn();
    renderApp("/app/links/new?job_id=j1");
    await screen.findByTestId("form-builder");
    fireEvent.change(screen.getByTestId("link-name-input"), { target: { value: "Drive" } });
    fireEvent.keyDown(screen.getByTestId("form-add-field-button"), { key: "Enter" });
    fireEvent.click(await screen.findByTestId("form-add-field-text"));
    fireEvent.change(await screen.findByTestId("form-field-label-c_new_question"), { target: { value: "Kul" } });
    await waitFor(() => expect(screen.getByTestId("link-round-option-r1")).toHaveAttribute("data-state", "checked"));
    fireEvent.click(screen.getByTestId("link-save-button"));
    expect(await screen.findByTestId("form-field-error-c_new_question")).toHaveTextContent("Never collected.");
    expect(screen.getByText("The link wasn't saved")).toBeInTheDocument();
  });
});
