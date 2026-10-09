/**
 * Page-test harness: the real router and providers, a signed-in staff user, and
 * `api.get/post/put/patch` routed to the contract fixtures. Every call is recorded.
 */

import { QueryClient } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, useAuthStore } from "@/lib/auth";
import type { StaffRole } from "@/lib/types";

import { defaultOrganizationSettings } from "@/features/recruiter/admin/settings/settingsModel";

import { ANALYTICS, CONFIG, DETAIL, IDS, ORG_SETTINGS, REPORT_CONTENT, ROWS, STATS, TIMELINE } from "./fixtures";

export interface Call {
  method: "GET" | "POST" | "PUT" | "PATCH";
  path: string;
  params?: Record<string, unknown>;
  body?: unknown;
}

export function signIn(role: StaffRole = "RECRUITER") {
  act(() =>
    useAuthStore.getState().setSession({
      user: { id: IDS.reviewer, organization_id: "o1", email: "dana@acme.test", full_name: "Dana Whitfield", role, is_active: true, last_login_at: null, created_at: "", updated_at: "" },
      organization: { id: "o1", name: "Acme Robotics", slug: "acme" },
      permissions: permissionsForRole(role),
    }),
  );
}

export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

export const notFound = (path: string, code = "not_found") => new ApiError({ status: 404, code, message: `No mock for ${path}` });

export const SESSION_DETAIL = {
  id: IDS.session,
  interview_id: IDS.interview,
  organization_id: "o1",
  candidate_id: IDS.candidate,
  job_id: IDS.job,
  round_id: IDS.round,
  status: "completed",
  started_at: "2026-10-08T09:02:00Z",
  ended_at: "2026-10-08T09:46:30Z",
  end_reason: "time_limit",
  elapsed_seconds: 2650,
  reconnect_count: 0,
  candidate: { id: IDS.candidate, full_name: "Asha Rao", email: "asha.rao@example.com" },
  job: { id: IDS.job, title: "AI/ML Training Program — Intake" },
  round: { id: IDS.round, name: "Intake Interview", round_type: "intake" },
  round_config: { round_type: "intake", name: "Intake Interview", duration_minutes: 45 },
  plan: null,
  created_at: "2026-10-08T09:00:00Z",
  updated_at: "2026-10-08T09:52:00Z",
};

export type GetHandler = (path: string, params: Record<string, unknown>) => unknown;

/** Default GET routes for the intake pages; `extra` handlers run first (return undefined to fall through). */
export function mockApi(extra: GetHandler = () => undefined) {
  const calls: Call[] = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (path: string, options?: { params?: Record<string, unknown> }) => {
    const params = options?.params ?? {};
    calls.push({ method: "GET", path, params });
    const custom = extra(path, params);
    if (custom instanceof Error) throw custom;
    if (custom !== undefined) return custom as never;
    switch (path) {
      case "/intake/config":
        return CONFIG as never;
      case "/intake/settings":
        return ORG_SETTINGS as never;
      case "/intake/interviews":
        return { items: ROWS, total: ROWS.length, limit: 25, offset: 0 } as never;
      case "/intake/stats":
        return STATS as never;
      case "/intake/analytics":
        return ANALYTICS as never;
      case `/intake/interviews/${IDS.intake}`:
        return DETAIL as never;
      case `/intake/interviews/${IDS.intake}/timeline`:
        return TIMELINE as never;
      case `/sessions/${IDS.session}`:
        return SESSION_DETAIL as never;
      case `/sessions/${IDS.session}/reports/recruiter`:
        return { id: "rep-1", session_id: IDS.session, type: "recruiter", version: 1, status: "ready", content: REPORT_CONTENT, artifact_media_id: "media-report-1", generated_at: "2026-10-08T09:53:00Z", versions: [] } as never;
      case "/settings":
        return defaultOrganizationSettings() as never;
      case "/candidates":
        return { items: [{ id: IDS.candidate, full_name: "Asha Rao", email: "asha.rao@example.com" }], total: 1 } as never;
      case "/users":
        return { items: [], total: 0 } as never;
      case "/competencies":
        return { items: [], total: 0 } as never;
      default:
        throw notFound(path);
    }
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (path: string, body?: unknown) => {
    calls.push({ method: "POST", path, body });
    throw notFound(path);
  });
  const put = vi.spyOn(api, "put").mockImplementation(async (path: string, body?: unknown) => {
    calls.push({ method: "PUT", path, body });
    throw notFound(path);
  });
  const patch = vi.spyOn(api, "patch").mockImplementation(async (path: string, body?: unknown) => {
    calls.push({ method: "PATCH", path, body });
    throw notFound(path);
  });
  return { calls, get, post, put, patch };
}
