import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { ApiError, api } from "@/lib/api";
import { permissionsForRole, resetAuthStore, useAuthStore } from "@/lib/auth";
import type { StaffRole } from "@/lib/types";

import { LINK } from "./fixtures";

const NOW = "2026-10-08T09:00:00Z";

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


const roundRow = (sequence: number, state: string, extra: Record<string, unknown> = {}) => ({
  sequence,
  round_id: `r${sequence + 1}`,
  name: sequence === 0 ? "Screening" : "Deep dive",
  state,
  attempt: 1,
  interview_id: state === "locked" ? null : `iv-${sequence}`,
  session_id: state === "completed" ? `s-${sequence}` : null,
  session_status: null,
  end_reason: null,
  started_at: null,
  completed_at: state === "completed" ? NOW : null,
  expires_at: null,
  ...extra,
});

const reg = (id: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  id,
  link_id: "l1",
  candidate: { id: `c-${id}`, full_name: name, email: `${id}@example.com` },
  full_name: name,
  email: `${id}@example.com`,
  phone: "+91 98765 43210",
  status,
  status_reason: status === "awaiting_review" ? "after_review" : null,
  current_round_index: 0,
  rounds: [roundRow(0, status === "registered" ? "invited" : "completed"), roundRow(1, "locked")],
  flags: [],
  flag_review: "none",
  created_at: NOW,
  updated_at: NOW,
  ...extra,
});

const REGS = [
  reg("a", "Asha Rao", "awaiting_review", {
    flags: [{ code: "same_device", severity: "high", message: "Registered from the same browser as another person", related_registration_id: "b", at: NOW }],
    flag_review: "open",
  }),
  reg("b", "Bilal Khan", "awaiting_review"),
  reg("c", "Chen Li", "registered"),
];

const DETAIL = {
  ...REGS[0],
  answers: [
    { key: "full_name", label: "Full name", type: "text", value: "Asha Rao" },
    { key: "c_shift", label: "Preferred shift", type: "select", value: "Night" },
  ],
  attempts: REGS[0]!.rounds,
  privacy_ack_at: NOW,
  personal_link_issued_at: NOW,
  resume_id: "res1",
  flag_reviewed_at: null,
  flag_review_note: null,
};

let getCalls: Array<{ path: string; params?: Record<string, unknown> }> = [];

function mockApi(extra: Record<string, unknown> = {}) {
  getCalls = [];
  const map: Record<string, unknown> = {
    "/open-links/l1": LINK,
    "/open-links/l1/registrations": { items: REGS, total: 3, limit: 50, offset: 0 },
    "/open-links/l1/registrations/a": DETAIL,
    ...extra,
  };
  vi.spyOn(api, "get").mockImplementation(async (path: string, options?: { params?: Record<string, unknown> }) => {
    getCalls.push({ path, params: options?.params });
    if (path === "/open-links/l1/registrations" && options?.params?.needs_review) return { items: REGS.slice(0, 1), total: 1 } as never;
    if (path in map) return map[path] as never;
    throw new ApiError({ status: 404, code: "not_found", message: `No mock for ${path}` });
  });
}

beforeEach(() => {
  resetAuthStore();
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

describe("link detail: registrations", () => {
  it("lists registrations with round chips, status and signals, and filters by needs review", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp("/app/links/l1");
    const rows = await screen.findAllByTestId("registration-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAttribute("data-registration-id", "a");
    expect(rows[0]).toHaveAttribute("data-status", "awaiting_review");
    expect(within(rows[0]!).getByTestId("registration-flags")).toHaveTextContent("1");
    expect(within(rows[0]!).getByRole("link", { name: /Round 1, Screening: Completed/ })).toHaveAttribute("href", "/app/sessions/s-0");
    expect(screen.getByRole("heading", { level: 1, name: "Campus drive, Pune" })).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("registrations-needs-review"));
    await waitFor(() => expect(screen.getAllByTestId("registration-row")).toHaveLength(1));
    expect(getCalls.some((c) => c.path === "/open-links/l1/registrations" && c.params?.needs_review === true)).toBe(true);
  });

  it("bulk-advances only people awaiting review and reports skipped rows", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ advanced: [{ registration_id: "a", interview_id: "iv-2", round_id: "r2" }], skipped: [{ registration_id: "b", code: "candidate_archived" }] } as never);
    signIn("RECRUITER");
    renderApp("/app/links/l1");
    await screen.findAllByTestId("registration-row");
    const advance = screen.getByTestId("registrations-advance-button");
    expect(advance).toBeDisabled();
    fireEvent.click(screen.getByTestId("registration-select-a"));
    fireEvent.click(screen.getByTestId("registration-select-b"));
    fireEvent.click(screen.getByTestId("registration-select-c"));
    expect(advance).toBeEnabled();
    expect(advance).toHaveTextContent("Advance 2");
    fireEvent.click(advance);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Advance 2 people/)).toBeInTheDocument();
    expect(within(dialog).getByText(/We don't send messages/)).toBeInTheDocument();
    expect(within(dialog).getByText(/1 selected person isn't waiting/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByTestId("advance-confirm"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/open-links/l1/registrations/advance", { registration_ids: ["a", "b"] }));
    expect(await screen.findByText("Advanced 1 person to the next round")).toBeInTheDocument();
    expect(screen.getByText(/Bilal Khan: candidate archived/)).toBeInTheDocument();
  });

  it("opens the detail sheet with answers and lets a recruiter clear a signal", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ ...DETAIL, flag_review: "cleared", flag_reviewed_at: NOW } as never);
    signIn("RECRUITER");
    renderApp("/app/links/l1");
    const rows = await screen.findAllByTestId("registration-row");
    fireEvent.click(rows[0]!);
    const sheet = await screen.findByTestId("registration-detail");
    expect(await within(sheet).findByText("Preferred shift")).toBeInTheDocument();
    expect(within(sheet).getByText("Night")).toBeInTheDocument();
    expect(within(sheet).getByText(/Registered from the same browser/)).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Report" })).toHaveAttribute("href", "/app/reports/s-0");
    fireEvent.change(within(sheet).getByLabelText("Review note"), { target: { value: "Siblings" } });
    fireEvent.click(within(sheet).getByTestId("registration-flag-clear"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/open-links/l1/registrations/a/flag-review", { decision: "cleared", note: "Siblings" }));
  });

  it("reissues a personal link and shows it once", async () => {
    mockApi();
    vi.spyOn(api, "post").mockResolvedValue({ personal_url: "https://interview.acme.test/r/new-personal-token-0123456789", issued_at: NOW } as never);
    signIn("RECRUITER");
    renderApp("/app/links/l1");
    const rows = await screen.findAllByTestId("registration-row");
    fireEvent.keyDown(within(rows[2]!).getByTestId("registration-actions"), { key: "Enter" });
    fireEvent.click(await screen.findByTestId("registration-reissue-link"));
    fireEvent.click(await screen.findByRole("button", { name: "Make new link" }));
    expect(await screen.findByTestId("registration-personal-url")).toHaveTextContent("https://interview.acme.test/r/new-personal-token-0123456789");
  });

  it("hides management actions from interviewers", async () => {
    mockApi();
    signIn("INTERVIEWER");
    renderApp("/app/links/l1");
    await screen.findAllByTestId("registration-row");
    expect(screen.queryByTestId("registrations-advance-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("registrations-export")).not.toBeInTheDocument();
    expect(screen.queryByTestId("registration-select-a")).not.toBeInTheDocument();
  });

  it("shares the link with copy, QR code and pause", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ ...LINK, status: "paused", state: "paused" } as never);
    signIn("RECRUITER");
    renderApp("/app/links/l1?tab=share");
    expect(await screen.findByTestId("link-url")).toHaveValue(LINK.url);
    expect(screen.getByTestId("link-qr")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("link-copy-button"));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(LINK.url));
    fireEvent.click(screen.getByTestId("link-pause-button"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/open-links/l1/status", { status: "paused" }));
    expect(await screen.findByTestId("link-resume-button")).toBeInTheDocument();
  });

  it("shows a list-level error with retry when the links list fails", async () => {
    mockApi({ "/jobs": { items: [], total: 0 } });
    signIn("RECRUITER");
    renderApp("/app/links");
    expect(await screen.findByText("Couldn't load the links")).toBeInTheDocument();
    expect(screen.queryByText(/doesn't exist or was removed/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("lists the organization's links", async () => {
    mockApi({ "/open-links": { items: [LINK], total: 1 }, "/jobs": { items: [], total: 0 } });
    signIn("RECRUITER");
    const router = renderApp("/app/links");
    const row = await screen.findByTestId("link-row");
    expect(row).toHaveAttribute("data-link-id", "l1");
    expect(within(row).getByText("Senior DevOps Engineer")).toBeInTheDocument();
    expect(screen.getByTestId("link-create-button")).toHaveAttribute("href", "/app/links/new");
    fireEvent.click(row);
    await waitFor(() => expect(router.state.location.pathname).toBe("/app/links/l1"));
  });
});
