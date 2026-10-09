import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

import { NextRoundCard } from "../components/NextRoundCard";

const round = (sequence: number, state: string, extra: Record<string, unknown> = {}) => ({
  sequence,
  name: sequence === 0 ? "Screening" : "Deep dive",
  round_type: "technical_screening",
  duration_minutes: 30,
  state,
  interview_id: state === "locked" ? null : `i${sequence + 1}`,
  is_current: false,
  ...extra,
});

function progress(next_action: string, rounds: unknown[], extra: Record<string, unknown> = {}) {
  return { organization_name: "Demo Corp", job_title: "DevOps", link_name: "Campus drive", progression: "immediate", rounds, next_action, next_interview_id: null, next_round_name: null, deadline: null, ...extra };
}

function renderCard(poll = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(
    [
      { path: "/candidate/:interviewId/complete", element: <NextRoundCard poll={poll} /> },
      { path: "/candidate/:interviewId/:step", element: <p>journey</p> },
    ],
    { initialEntries: ["/candidate/i1/complete"] },
  );
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, client, ...view };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("NextRoundCard", () => {
  it("renders nothing for ordinary invitations (404)", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValue(new ApiError({ status: 404, code: "not_link_registration", message: "no" }));
    const { container } = renderCard();
    await waitFor(() => expect(get).toHaveBeenCalledWith("/portal/link/progress"));
    expect(container).toBeEmptyDOMElement();
  });

  it("starts the next round: switches the session and opens its first step", async () => {
    vi.spyOn(api, "get").mockResolvedValue(
      progress("start_next", [round(0, "completed", { is_current: true }), round(1, "invited")], { next_interview_id: "i2", next_round_name: "Deep dive", deadline: "2026-10-11T10:00:00Z" }) as never,
    );
    const post = vi.spyOn(api, "post").mockResolvedValue({ interview_id: "i2", next_step: "welcome", csrf_token: "c" } as never);
    const { router } = renderCard();
    const card = await screen.findByTestId("next-round-card");
    expect(card).toHaveAttribute("data-next-action", "start_next");
    expect(card).toHaveTextContent("Round 2 is ready");
    expect(card).toHaveTextContent("Completed");
    fireEvent.click(screen.getByTestId("next-round-continue"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate/i2/welcome"));
    expect(post).toHaveBeenCalledWith("/portal/link/continue", { interview_id: "i2" });
  });

  it("explains waiting for review and finishing every round", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(progress("wait_for_review", [round(0, "completed", { is_current: true }), round(1, "locked")]) as never);
    const first = renderCard();
    expect(await screen.findByTestId("next-round-card")).toHaveTextContent("The hiring team will review round 1");
    expect(screen.queryByTestId("next-round-continue")).not.toBeInTheDocument();
    first.unmount();
    get.mockResolvedValue(progress("all_done", [round(0, "completed"), round(1, "completed", { is_current: true })]) as never);
    renderCard();
    expect(await screen.findByTestId("next-round-card")).toHaveTextContent("You've completed all rounds");
  });

  it("polls while the finished round is being recorded, then shows the next round", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce(progress("continue_current", [round(0, "in_progress", { is_current: true }), round(1, "locked")]) as never)
      .mockResolvedValue(progress("start_next", [round(0, "completed", { is_current: true }), round(1, "invited")], { next_interview_id: "i2" }) as never);
    renderCard(true);
    expect(await screen.findByText("Checking for your next round")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(5_100));
    await waitFor(() => expect(screen.getByTestId("next-round-card")).toHaveAttribute("data-next-action", "start_next"));
    expect(get.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
