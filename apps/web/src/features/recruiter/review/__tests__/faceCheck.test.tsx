/**
 * Session Integrity tab: the face-check card (share of monitored time with one
 * face, intervals, pictures set aside) in neutral wording, and summary rows
 * kept out of the observation list.
 */
import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";

import { DEMO_IDS, demoIntegrity } from "../__fixtures__/demoData";
import { installMockApi, signInAs } from "./mockApi";

const base = demoIntegrity[0]!;
const faceEvents = [
  { ...base, id: "fc-sum", type: "face_check_summary", severity: "info", action: "log_only", review_status: "unreviewed", count: 3, duration_ms: null, details: { runs: { run_a: { monitored_ms: 1_200_000, one_face_ms: 1_140_000, no_face_ms: 48_000, multi_face_ms: 12_000, static_faces: 1 } } } },
  { ...base, id: "fc-nf", type: "face_not_visible", severity: "low", action: "log_only", review_status: "unreviewed", count: 2, duration_ms: 48_000, details: { episodes: { nf_aaaa: 18_000, nf_bbbb: 30_000 }, server_checks: { nf_aaaa: "confirmed" } } },
  { ...base, id: "fc-mf", type: "multiple_faces_visible", severity: "medium", action: "flag_for_human_review", review_status: "unreviewed", count: 1, duration_ms: 12_000, details: { episodes: { mf_cccc: 12_000 }, server_check: { status: "confirmed", faces: 2 }, server_checks: { mf_cccc: "confirmed" } } },
  { ...base, id: "fc-st", type: "static_face_ignored", severity: "info", action: "log_only", review_status: "unreviewed", count: 1, duration_ms: null, details: { still_seconds: 30, position: "right" } },
];

function renderTab(events: unknown[], policy: Record<string, unknown>) {
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.spyOn(api, "get").mockImplementation(async (path: string, options?: Parameters<typeof api.get>[1]) => {
    if (path === `/sessions/${DEMO_IDS.completed}/integrity`) return { session_id: DEMO_IDS.completed, events, counts: {}, counts_by_severity: {}, policy } as never;
    return original(path, options);
  });
  const router = createMemoryRouter(routes, { initialEntries: [`/app/sessions/${DEMO_IDS.completed}?tab=integrity`] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

beforeEach(() => {
  resetAuthStore();
  installMockApi();
});

afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

describe("integrity tab face check", () => {
  it("summarises face presence as observations and keeps summary rows out of the list", async () => {
    act(() => signInAs("RECRUITER"));
    renderTab([...demoIntegrity, ...faceEvents], { enabled: true, monitor_focus: true, face_presence_check: true, camera_frame_analysis: true });
    const card = await screen.findByTestId("integrity-face-presence", undefined, { timeout: 8000 });
    expect(within(card).getByTestId("integrity-face-share")).toHaveAttribute("aria-valuenow", "95");
    expect(card).toHaveTextContent("One face visible95%");
    expect(card).toHaveTextContent("Of 20m monitored");
    expect(within(card).getByTestId("integrity-face-no-face")).toHaveTextContent("2 intervals");
    expect(within(card).getByTestId("integrity-face-no-face")).toHaveTextContent("1 snapshot confirmed");
    expect(within(card).getByTestId("integrity-face-multiple")).toHaveTextContent("1 interval");
    expect(within(card).getByTestId("integrity-face-static")).toHaveTextContent("1 motionless face (a photo, poster or screen), not counted");
    expect(card).toHaveTextContent("Review each interval in context");
    expect(card.textContent ?? "").not.toMatch(/cheat|suspicious|dishonest|fail/i);
    // The summary row feeds the card, not the observation list.
    const ids = screen.getAllByTestId("integrity-event").map((row) => row.getAttribute("data-event-id"));
    expect(ids).toContain("fc-mf");
    expect(ids).not.toContain("fc-sum");
    expect(screen.getByText("The candidate's device checked that one face was visible; a snapshot was sent only when a check needed review.")).toBeInTheDocument();
  });

  it("shows an empty card when the check was on but recorded nothing, and no card when it was off", async () => {
    act(() => signInAs("RECRUITER"));
    renderTab(demoIntegrity, { enabled: true, face_presence_check: true });
    expect(await screen.findByText("No face-check data was recorded for this session.", undefined, { timeout: 8000 })).toBeInTheDocument();
    cleanup();
    renderTab(demoIntegrity, { enabled: true, face_presence_check: false, monitor_focus: true });
    expect(await screen.findByText("The on-device face check was off.", undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.queryByTestId("integrity-face-presence")).not.toBeInTheDocument();
  });
});
