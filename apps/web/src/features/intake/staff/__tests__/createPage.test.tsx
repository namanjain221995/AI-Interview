import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";

import type { IntakeBatchCreate, IntakeCreate } from "../types";
import { SUMMARY_COMPLETED } from "./fixtures";
import { mockApi, renderApp, signIn } from "./helpers";

beforeEach(() => {
  resetAuthStore();
  Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true });
});
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

const CREATED = { intake: { ...SUMMARY_COMPLETED, id: "it-new", bucket: "active", session: null, evaluation: null }, invite_url: "https://interview.acme.test/i/Tok3n", access_code: null, expires_at: "2026-10-16T10:00:00Z" };

describe("create intake: one candidate", () => {
  it("validates, creates a new candidate's invitation and shows the link once", async () => {
    mockApi();
    let body: IntakeCreate | null = null;
    vi.spyOn(api, "post").mockImplementation(async (path: string, payload?: unknown) => {
      if (path === "/intake/interviews") {
        body = payload as IntakeCreate;
        return CREATED as never;
      }
      throw new ApiError({ status: 404, code: "not_found", message: path });
    });
    signIn("RECRUITER");
    renderApp("/app/intake/new");
    await screen.findByTestId("intake-create-page");
    const send = await screen.findByTestId("intake-send");

    fireEvent.click(send);
    expect(await screen.findByText("Check the highlighted fields.")).toBeInTheDocument();
    expect(screen.getByText("Choose a candidate, or add a new one.")).toBeInTheDocument();
    expect(body).toBeNull();

    fireEvent.mouseDown(screen.getByRole("tab", { name: /New candidate/ }));
    fireEvent.click(screen.getByRole("tab", { name: /New candidate/ }));
    fireEvent.change(await screen.findByTestId("intake-candidate-name"), { target: { value: "Chen Li" } });
    fireEvent.change(screen.getByTestId("intake-candidate-email"), { target: { value: "Chen@Example.com" } });
    // The only intake job is preselected; the defaults come from the intake settings.
    expect(screen.getByTestId("intake-job-select")).toHaveTextContent("AI/ML Training Program — Intake");
    fireEvent.click(screen.getByTestId("intake-send"));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      candidate_id: null,
      candidate: { full_name: "Chen Li", email: "chen@example.com" },
      job_id: "job-intake",
      round_id: "round-intake",
      target_role: null,
      send: true,
      settings: { duration_minutes: 45, presence: "orb", max_reconnects: 10, expires_in_hours: 168, resume_required: true, work_authorization: null, scheduled_for: null },
    });

    const result = await screen.findByTestId("intake-invite-result");
    expect(within(result).getByTestId("intake-invite-link")).toHaveTextContent("https://interview.acme.test/i/Tok3n");
    fireEvent.click(within(result).getByTestId("intake-copy-link"));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("https://interview.acme.test/i/Tok3n"));
    fireEvent.click(within(result).getByTestId("intake-copy-message"));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(expect.stringContaining("Your personal link (please don't share it): https://interview.acme.test/i/Tok3n")));
    expect(within(result).getByRole("link", { name: /Open email draft/ })).toHaveAttribute("href", expect.stringMatching(/^mailto:asha\.rao%40example\.com\?subject=/));
  });

  it("asks for a country when work authorisation is asked, and saves drafts without a link", async () => {
    mockApi();
    let body: IntakeCreate | null = null;
    vi.spyOn(api, "post").mockImplementation(async (path: string, payload?: unknown) => {
      if (path === "/intake/interviews") {
        body = payload as IntakeCreate;
        return { ...CREATED, invite_url: null, intake: { ...CREATED.intake, status: "draft", bucket: "drafts" } } as never;
      }
      throw new ApiError({ status: 404, code: "not_found", message: path });
    });
    signIn("RECRUITER");
    const router = renderApp("/app/intake/new");
    const option = await screen.findByTestId("intake-candidate-option");
    fireEvent.click(option);
    expect(await screen.findByTestId("intake-candidate-selected")).toHaveTextContent("Asha Rao");

    fireEvent.click(within(screen.getByTestId("intake-mq1-mode")).getByLabelText("Ask for this interview"));
    fireEvent.click(screen.getByTestId("intake-send"));
    expect(await screen.findByText(/Name the country, as it should read/)).toBeInTheDocument();
    expect(body).toBeNull();

    fireEvent.change(screen.getByTestId("intake-mq1-country"), { target: { value: "Canada" } });
    fireEvent.click(screen.getByTestId("intake-save-draft"));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ candidate_id: "ca-0001", candidate: null, send: false, settings: { work_authorization: { enabled: true, country: "Canada", ask_sponsorship: true } } });
    await waitFor(() => expect(router.state.location.pathname).toBe("/app/intake"));
    expect(router.state.location.search).toBe("?bucket=drafts");
  });

  it("explains server refusals in plain words", async () => {
    mockApi();
    vi.spyOn(api, "post").mockRejectedValue(new ApiError({ status: 409, code: "interview_exists", message: "exists" }));
    signIn("RECRUITER");
    renderApp("/app/intake/new");
    fireEvent.click(await screen.findByTestId("intake-candidate-option"));
    fireEvent.click(screen.getByTestId("intake-send"));
    expect(await screen.findByText(/already has an open invitation for this job/)).toBeInTheDocument();
  });
});

describe("create intake: batch", () => {
  it("previews rows with per-row checks, creates the valid ones and offers the links as CSV", async () => {
    mockApi();
    let body: IntakeBatchCreate | null = null;
    vi.spyOn(api, "post").mockImplementation(async (path: string, payload?: unknown) => {
      if (path === "/intake/interviews/batch") {
        body = payload as IntakeBatchCreate;
        return {
          batch_id: "batch-1234567890",
          created: [{ row: 1, intake_id: "i1", candidate_id: "c1", email: "asha@example.com", candidate_reused: false, invite_url: "https://x.test/i/a", access_code: null, expires_at: "2026-10-16T10:00:00Z" }],
          failed: [{ row: 2, email: "bilal@example.com", code: "candidate_archived", message: "This candidate is archived." }],
        } as never;
      }
      throw new ApiError({ status: 404, code: "not_found", message: path });
    });
    const createObjectURL = vi.fn(() => "blob:csv");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    signIn("RECRUITER");
    renderApp("/app/intake/new?mode=batch");
    const input = await screen.findByTestId("intake-batch-input");
    fireEvent.change(input, { target: { value: "Asha Rao, asha@example.com, AI/ML\nBilal Khan, bilal@example.com\nBad Row, not-an-email\nAsha Twin, ASHA@example.com" } });

    const preview = await screen.findByTestId("intake-batch-preview");
    const rows = within(preview).getAllByTestId("intake-batch-row");
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["valid", "valid", "invalid", "invalid"]);
    expect(within(preview).getByText("Same email as row 1.")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("intake-batch-submit"));
    // Rows with problems are left out after a confirmation.
    fireEvent.click(await screen.findByRole("button", { name: "Create 2 invitations" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ job_id: "job-intake", round_id: "round-intake", send: true, rows: [{ full_name: "Asha Rao", email: "asha@example.com", target_role: "ai_ml" }, { full_name: "Bilal Khan", email: "bilal@example.com", target_role: null }] });

    const results = await screen.findByTestId("intake-batch-results");
    expect(within(results).getAllByTestId("intake-batch-row").map((r) => r.getAttribute("data-status"))).toEqual(["created", "failed"]);
    expect(within(results).getByText("This candidate is archived.")).toBeInTheDocument();
    fireEvent.click(within(results).getByTestId("intake-batch-download"));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    expect(within(results).getByText("Links downloaded")).toBeInTheDocument();
    expect(within(results).getByRole("link", { name: /View this batch/ })).toHaveAttribute("href", "/app/intake?batch=batch-1234567890");
  });
});
