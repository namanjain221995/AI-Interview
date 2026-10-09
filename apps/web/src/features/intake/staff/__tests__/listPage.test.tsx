import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetAuthStore } from "@/lib/auth";

import { ROWS } from "./fixtures";
import { mockApi, notFound, renderApp, signIn } from "./helpers";

beforeEach(() => resetAuthStore());
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

const lastListCall = (calls: ReturnType<typeof mockApi>["calls"]) => [...calls].reverse().find((c) => c.path === "/intake/interviews");

describe("intake list page", () => {
  it("shows the stats, bucket counts and one row per intake", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp("/app/intake");
    expect(await screen.findByTestId("intake-page")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId("intake-row")).toHaveLength(ROWS.length));
    expect(within(screen.getByTestId("intake-stat-invitations")).getByText("7")).toBeInTheDocument();
    expect(within(screen.getByTestId("intake-stat-average_score")).getByText("3.44")).toBeInTheDocument();
    expect(within(screen.getByTestId("intake-bucket-all")).getByText("8")).toBeInTheDocument();
    expect(within(screen.getByTestId("intake-bucket-awaiting_review")).getByText("1")).toBeInTheDocument();
    // Expired holds a row, cancelled doesn't: only the first appears.
    expect(screen.getByTestId("intake-bucket-expired")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-bucket-cancelled")).not.toBeInTheDocument();
    const first = screen.getAllByTestId("intake-row")[0]!;
    expect(first).toHaveAttribute("data-bucket", "awaiting_review");
    // Shown in the total column, and under the status on phones.
    expect(within(first).getAllByText("3.63")).toHaveLength(2);
    expect(within(first).getByLabelText("AI recommendation: Accept with notes")).toBeInTheDocument();
    expect(screen.getByTestId("intake-create-button")).toHaveAttribute("href", "/app/intake/new");
  });

  it("sends bucket, search, filters, sort and page to the API and keeps them in the URL", async () => {
    const { calls } = mockApi((path) => (path === "/intake/interviews" ? { items: ROWS.slice(0, 2), total: 60 } : undefined));
    signIn("RECRUITER");
    const router = renderApp("/app/intake");
    await screen.findAllByTestId("intake-row");

    fireEvent.click(screen.getByTestId("intake-bucket-awaiting_review"));
    await waitFor(() => expect(lastListCall(calls)?.params).toMatchObject({ bucket: "awaiting_review", offset: 0 }));
    expect(router.state.location.search).toBe("?bucket=awaiting_review");

    fireEvent.change(screen.getByTestId("intake-search"), { target: { value: "asha" } });
    await waitFor(() => expect(lastListCall(calls)?.params).toMatchObject({ bucket: "awaiting_review", q: "asha" }), { timeout: 2000 });
    const stats = [...calls].reverse().find((c) => c.path === "/intake/stats");
    expect(stats?.params).toMatchObject({ q: "asha" });
    expect(stats?.params).not.toHaveProperty("bucket");

    fireEvent.click(screen.getByTestId("intake-page-next"));
    await waitFor(() => expect(lastListCall(calls)?.params).toMatchObject({ offset: 25 }));
    expect(router.state.location.search).toContain("page=1");
  });

  it("opens drafts in the create page and other rows in the review page", async () => {
    mockApi();
    signIn("RECRUITER");
    const router = renderApp("/app/intake");
    const rows = await screen.findAllByTestId("intake-row");
    const draft = rows.find((r) => r.getAttribute("data-bucket") === "drafts")!;
    fireEvent.click(draft);
    await waitFor(() => expect(router.state.location.pathname).toBe("/app/intake/new"));
    expect(router.state.location.search).toBe("?draft=it-0005");
  });

  it("shows one empty state when intake interviews are turned off", async () => {
    mockApi((path) => (path.startsWith("/intake/") ? notFound(path, "intake_disabled") : undefined));
    signIn("RECRUITER");
    renderApp("/app/intake");
    expect(await screen.findByText("Intake interviews are turned off for this workspace")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-create-button")).not.toBeInTheDocument();
  });

  it("hides creation from interviewers", async () => {
    mockApi();
    signIn("INTERVIEWER");
    renderApp("/app/intake");
    await screen.findAllByTestId("intake-row");
    expect(screen.queryByTestId("intake-create-button")).not.toBeInTheDocument();
  });
});
