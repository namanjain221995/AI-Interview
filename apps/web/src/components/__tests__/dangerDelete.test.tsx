import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { confirmPhraseMatches, deleteActionError } from "@/components/domain/DangerDeleteDialog";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { ApiError, api } from "@/lib/api";

import { DeleteIntakeDialog } from "@/features/intake/staff/components/review/DeleteIntakeDialog";
import { DeleteLinkDialog } from "@/features/links/components/DeleteLinkDialog";
import type { OpenLink } from "@/features/links/types";
import { DeleteSessionDialog } from "@/features/recruiter/review/session/DeleteSessionDialog";

/** Renders `ui` at `start`, with a marker page on each `targets` route to observe navigation. */
function wrap(ui: ReactNode, start: string, targets: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[start]}>
          <Routes>
            <Route path={start} element={ui} />
            {targets.map((path) => (
              <Route key={path} path={path} element={<p>landed on {path}</p>} />
            ))}
          </Routes>
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("confirm phrase", () => {
  it("ignores case and spacing but needs the whole phrase", () => {
    expect(confirmPhraseMatches("  priya   RAMAN ", "Priya Raman")).toBe(true);
    expect(confirmPhraseMatches("Priya", "Priya Raman")).toBe(false);
    expect(confirmPhraseMatches("", "")).toBe(false);
  });

  it("explains the delete conflicts", () => {
    const err = (status: number, code: string, details: Record<string, unknown> = {}) => new ApiError({ status, code, message: code, details });
    expect(deleteActionError(err(409, "session_busy", { retry_after_seconds: 900 }))).toMatch(/still running or being scored. Try again in about 15 minutes/);
    expect(deleteActionError(err(409, "intake_busy"))).toMatch(/being evaluated/);
    expect(deleteActionError(err(409, "link_has_registrations"))).toMatch(/Close the link instead/);
    expect(deleteActionError(err(503, "deletion_incomplete"))).toMatch(/nothing was removed/);
    expect(deleteActionError(err(403, "forbidden"))).toBe("Only administrators can do this.");
  });
});

describe("delete session dialog", () => {
  it("needs the candidate's name, calls the API and goes back to the sessions list", async () => {
    const del = vi.spyOn(api, "delete").mockResolvedValue({
      session_id: "s1",
      interview_id: "i1",
      candidate_id: "c1",
      deleted_at: "2026-10-09T10:00:00Z",
      sessions: 1,
      transcripts: 40,
      reports: 2,
      integrity_events: 3,
      coding_sessions: 1,
      media_files_deleted: 6,
      media_bytes_deleted: 1_048_576,
    } as never);
    wrap(<DeleteSessionDialog sessionId="s1" candidateName="Priya Raman" open onOpenChange={vi.fn()} />, "/app/sessions/s1", ["/app/sessions"]);
    expect(screen.getByText(/recordings, answer audio, camera and screen images/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Delete session" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "Priya" } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "priya raman" } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/sessions/s1"));
    expect(await screen.findByText("landed on /app/sessions")).toBeInTheDocument();
  });

  it("asks for DELETE without a candidate name and explains a busy session", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(new ApiError({ status: 409, code: "session_busy", message: "busy", details: { retry_after_seconds: 900 } }));
    const onOpenChange = vi.fn();
    wrap(<DeleteSessionDialog sessionId="s1" candidateName={null} open onOpenChange={onOpenChange} />, "/app/sessions/s1", ["/app/sessions"]);
    fireEvent.change(screen.getByLabelText(/Type DELETE to confirm/), { target: { value: "delete" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete session" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Try again in about 15 minutes/);
    expect(screen.queryByText("landed on /app/sessions")).not.toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

describe("delete intake dialog", () => {
  it("needs the candidate's name, calls the API and goes back to the intake list", async () => {
    const del = vi.spyOn(api, "delete").mockResolvedValue({
      intake_id: "k1",
      candidate_id: "c1",
      deleted_at: "2026-10-09T10:00:00Z",
      was_draft: false,
      interviews: 1,
      evaluations: 1,
      decisions: 1,
      auth_sessions_revoked: 1,
      sessions: { sessions: 1, reports: 1, media_files_deleted: 3, media_bytes_deleted: 4096 },
    } as never);
    wrap(<DeleteIntakeDialog intakeId="k1" candidateName="Alex Rivera" open onOpenChange={vi.fn()} />, "/app/intake/k1", ["/app/intake"]);
    const button = screen.getByRole("button", { name: "Delete intake interview" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/The candidate and their résumé stay/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "Alex Rivera" } });
    fireEvent.click(button);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/intake/interviews/k1"));
    expect(await screen.findByText("landed on /app/intake")).toBeInTheDocument();
  });
});

describe("delete link dialog", () => {
  const link = { id: "l1", name: "Campus drive Pune", counts: { registrations: 0, in_progress: 0, awaiting_review: 0, completed: 0, flagged: 0, blocked: 0 } } as unknown as OpenLink;

  it("needs the link name, calls the API and goes back to the links list", async () => {
    const del = vi.spyOn(api, "delete").mockResolvedValue({ link_id: "l1", deleted_at: "2026-10-09T10:00:00Z", previous_status: "active", registrations_deleted: 0 } as never);
    wrap(<DeleteLinkDialog link={link} open onOpenChange={vi.fn()} />, "/app/links/l1", ["/app/links"]);
    const button = screen.getByRole("button", { name: "Delete link" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "campus drive pune" } });
    fireEvent.click(button);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/open-links/l1"));
    expect(await screen.findByText("landed on /app/links")).toBeInTheDocument();
  });

  it("warns about registrations and shows the refusal", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(new ApiError({ status: 409, code: "link_has_registrations", message: "x", details: { registrations: 2 } }));
    wrap(<DeleteLinkDialog link={{ ...link, counts: { ...link.counts, registrations: 2 } }} open onOpenChange={vi.fn()} />, "/app/links/l1", ["/app/links"]);
    expect(screen.getByText(/People have registered through this link/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "Campus drive Pune" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Close the link instead/);
  });
});
