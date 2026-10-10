import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";
import { HUMAN_DECISION_NOTE } from "@/components/domain/RecommendationBadge";

import { DEMO_IDS, demoSessions } from "../__fixtures__/demoData";
import { installMockApi, signInAs, type RecordedCall } from "./mockApi";

let calls: RecordedCall[] = [];

function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

beforeEach(() => {
  resetAuthStore();
  calls = installMockApi();
  act(() => signInAs("RECRUITER"));
});

afterEach(() => {
  cleanup();
  resetAuthStore();
});

describe("session detail", () => {
  it("shows every tab, the overall score and the assistive recommendation with the human-decision note", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}`);
    // The first test also pays for the lazy route import.
    expect(await screen.findByRole("heading", { level: 1, name: "Jane Doe" }, { timeout: 8000 })).toBeInTheDocument();
    for (const tab of ["overview", "transcript", "questions", "competencies", "timeline", "coding", "integrity", "plan", "review"]) {
      expect(screen.getByTestId(`session-tab-${tab}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("session-tab-live")).not.toBeInTheDocument();
    const score = await screen.findByTestId("session-overall-score");
    await waitFor(() => expect(score).toHaveAttribute("data-score", "69"));
    const recommendation = screen.getByTestId("session-recommendation");
    expect(recommendation).toHaveAttribute("data-recommendation", "proceed");
    expect(within(recommendation).getByText("Evidence supports proceeding")).toBeInTheDocument();
    expect(within(recommendation).getByText(HUMAN_DECISION_NOTE)).toBeInTheDocument();
    // Strongest evidence and gaps are evidence items on the overview.
    expect((await screen.findAllByTestId("evidence-item")).length).toBeGreaterThanOrEqual(6);
    // The interview tape exposes each question as a button.
    expect(screen.getByRole("button", { name: /^Q1: To start/ })).toBeInTheDocument();
  });

  it("explains a competency score from its evidence and links to the questions behind it", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}?tab=competencies&c=aws`);
    const cards = await screen.findAllByTestId("competency-card");
    expect(cards.map((c) => c.getAttribute("data-competency-key"))).toContain("kubernetes");
    expect(cards.find((c) => c.getAttribute("data-competency-key") === "aws")).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("How the score was calculated")).toBeInTheDocument();
    expect(screen.getByText("Unsure how egress and VPC endpoints were configured.")).toBeInTheDocument();
    expect(screen.getByText("Egress and VPC endpoint design")).toBeInTheDocument();
    fireEvent.click(cards.find((c) => c.getAttribute("data-competency-key") === "kubernetes")!);
    expect(await screen.findByText(/Could not describe how stateful workloads behaved/)).toBeInTheDocument();
  });

  it("renders a speaker-labelled transcript with question markers and search", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}?tab=transcript`);
    const entries = await screen.findAllByTestId("transcript-entry");
    expect(entries.length).toBeGreaterThan(20);
    expect(entries[0]).toHaveAttribute("data-speaker", "interviewer");
    expect(screen.getAllByRole("button", { name: "Play answer" }).length).toBeGreaterThan(5);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search the transcript" }), { target: { value: "Karpenter" } });
    expect(await screen.findByText(/1 of \d+ turns/)).toBeInTheDocument();
    expect(document.querySelectorAll("mark").length).toBeGreaterThan(0);
  });

  it("shows the timeline chart with window summaries from the API", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}?tab=timeline`);
    expect(await screen.findByTestId("timeline-chart")).toBeInTheDocument();
    expect(await screen.findByText("Minute 0–10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "5 min" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/timeline") && c.params?.window_minutes === 5)).toBe(true));
  });

  it("lists integrity observations with the banner and saves a review", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}?tab=integrity`);
    expect(await screen.findByText("Signals are observations, not determinations.")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("integrity-event");
    expect(rows).toHaveLength(4);
    const phone = rows.find((r) => within(r).queryByText("Possible phone in view"))!;
    fireEvent.click(within(phone).getByRole("button", { name: "Note" }));
    fireEvent.change(within(phone).getByRole("textbox", { name: "Review note" }), { target: { value: "Checked the frame; it's a mug." } });
    fireEvent.click(within(phone).getByRole("button", { name: "Save review" }));
    await waitFor(() =>
      expect(calls).toContainEqual(expect.objectContaining({ method: "PATCH", path: "/integrity/events/ie-4", body: { review_status: "noted", review_note: "Checked the frame; it's a mug." } })),
    );
  });

  it("records a human decision and shows the history with the AI suggestion at review time", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.completed}?tab=review`);
    expect(await screen.findByText("AI suggestion at the time: Evidence supports proceeding")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /Do not advance/ }));
    expect(screen.getByText("This differs from the AI suggestion")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Notes/), { target: { value: "Networking depth is below what the role needs." } });
    fireEvent.click(screen.getByLabelText(/Also release the candidate's feedback report/));
    fireEvent.click(screen.getByTestId("review-submit"));
    await waitFor(() =>
      expect(calls).toContainEqual(
        expect.objectContaining({
          method: "POST",
          path: `/sessions/${DEMO_IDS.completed}/reviews`,
          body: { decision: "reject", notes: "Networking depth is below what the role needs.", release_candidate_report: true },
        }),
      ),
    );
  });

  it("opens live sessions on the read-only live monitor", async () => {
    renderApp(`/app/sessions/${DEMO_IDS.live}`);
    expect(await screen.findByTestId("session-tab-live")).toHaveAttribute("data-state", "active");
    expect(await screen.findByText("Design an endpoint that lets clients page through ten million orders efficiently.", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("Competency estimates")).toBeInTheDocument();
    expect(await screen.findByText("Interview window lost focus")).toBeInTheDocument();
    // jsdom has no EventSource: the monitor falls back to periodic refresh.
    expect(screen.getByText("Refreshing every few seconds")).toBeInTheDocument();
  });
});

describe("sessions list", () => {
  it("renders one row per session with its id and filters by view", async () => {
    const router = renderApp("/app/sessions");
    const rows = await screen.findAllByTestId("session-row");
    expect(rows).toHaveLength(25);
    expect(rows[0]).toHaveAttribute("data-session-id", demoSessions[0]!.id);
    fireEvent.click(screen.getByRole("radio", { name: /Live now/ }));
    await waitFor(() => expect(router.state.location.search).toBe("?view=live"));
    await waitFor(() => expect(screen.getAllByTestId("session-row").every((r) => ["live", "paused"].includes(r.getAttribute("data-status")!))).toBe(true));
    expect(calls.some((c) => c.path === "/sessions" && Array.isArray(c.params?.status) && (c.params?.status as string[]).includes("live"))).toBe(true);
  });

  it("honours the /app/reports redirect to completed sessions", async () => {
    renderApp("/app/reports");
    const rows = await screen.findAllByTestId("session-row");
    expect(rows.every((r) => r.getAttribute("data-status") === "completed")).toBe(true);
    expect(screen.getByRole("radio", { name: "Completed" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("recruiter report", () => {
  it("renders every section and gates release on a recorded decision", async () => {
    renderApp(`/app/reports/${DEMO_IDS.completed}`);
    const report = await screen.findByTestId("recruiter-report");
    for (const heading of ["Summary", "Competency scores", "Competency evidence", "Question-by-question evaluation", "Transcript excerpts", "Coding", "Resume claims", "Statements to reconcile", "Integrity summary", "Areas for human follow-up"]) {
      expect(within(report).getByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
    }
    expect(within(report).getByText(/Jane gave specific, first-hand accounts/)).toBeInTheDocument();
    expect(within(report).getByText("AWS networking: egress design, NAT versus VPC endpoints")).toBeInTheDocument();
    expect(within(report).getByText("Joined Bluepeak in January 2023")).toBeInTheDocument();
    // A review exists in the demo, so the after-review release is available.
    expect(screen.getByRole("button", { name: "Release candidate report" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Export JSON" })).toBeEnabled();
  });
});

describe("re-evaluating answers scored during an AI outage (S12)", () => {
  it("explains the unassessed answers and queues their re-evaluation", async () => {
    const get = vi.mocked(api.get).getMockImplementation()!;
    vi.mocked(api.get).mockImplementation(async (path: string, options?: unknown) => {
      const value = await get(path, options as never);
      return (path === `/sessions/${DEMO_IDS.completed}` ? { ...(value as object), fallback_evaluations: 2 } : value) as never;
    });
    const post = vi.mocked(api.post).getMockImplementation()!;
    vi.mocked(api.post).mockImplementation(async (path: string, body?: unknown) => {
      if (path === `/sessions/${DEMO_IDS.completed}/reevaluate`) {
        calls.push({ method: "POST", path, body });
        return { queued: 2, fallback_evaluations: 2 } as never;
      }
      return post(path, body as never);
    });
    renderApp(`/app/reports/${DEMO_IDS.completed}`);
    const warning = await screen.findByTestId("fallback-evaluations");
    expect(warning).toHaveTextContent("2 answers were not assessed: the AI model was unavailable");
    fireEvent.click(within(warning).getByRole("button", { name: "Re-evaluate 2 answers" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/sessions/${DEMO_IDS.completed}/reevaluate`)).toBe(true));
  });
});

describe("integrity queue", () => {
  it("defaults to actionable unreviewed observations and bulk-reviews the selection", async () => {
    renderApp("/app/integrity");
    const rows = await screen.findAllByTestId("integrity-event");
    expect(rows.length).toBeGreaterThan(3);
    const queueCall = calls.find((c) => c.path === "/integrity/events");
    expect(queueCall?.params).toMatchObject({ status: "unreviewed", actionable: true });
    fireEvent.click(within(rows[0]!).getByRole("checkbox"));
    fireEvent.click(within(rows[1]!).getByRole("checkbox"));
    expect(screen.getByText("2 observations selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Dismiss" }));
    fireEvent.click(screen.getByRole("button", { name: "Save 2 reviews" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/integrity/events/review")?.body).toMatchObject({ review_status: "dismissed", event_ids: [rows[0]!.getAttribute("data-event-id"), rows[1]!.getAttribute("data-event-id")] }));
  });
});

describe("dashboard and analytics", () => {
  it("shows KPIs, live interviews, the funnel and decisions waiting", async () => {
    renderApp("/app/dashboard");
    expect(await screen.findByRole("heading", { level: 1, name: /good (morning|afternoon|evening), sam/i })).toBeInTheDocument();
    expect(await screen.findByText("Waiting for your decision")).toBeInTheDocument();
    expect(screen.getByText("Funnel")).toBeInTheDocument();
    expect((await screen.findAllByText("Marcus Okafor")).length).toBeGreaterThan(0);
    expect(calls.some((c) => c.path === "/analytics/overview" && c.params?.days === 30)).toBe(true);
  });

  it("loads every analytics breakdown for the chosen period", async () => {
    renderApp("/app/analytics?days=90");
    expect(await screen.findByRole("heading", { level: 1, name: "Analytics" })).toBeInTheDocument();
    expect(await screen.findByText("Interviewer turns")).toBeInTheDocument();
    for (const name of ["overview", "funnel", "scores", "competencies", "completion", "integrity", "hints", "model-usage"]) {
      expect(calls.some((c) => c.path === `/analytics/${name}` && c.params?.days === 90)).toBe(true);
    }
  });
});
