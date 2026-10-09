import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmDialogHost } from "@/components/ui/AlertDialog";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { ApiError, api } from "@/lib/api";

import { candidateActionError, candidateListParams } from "../api/candidates";
import { ArchivedCandidateBanner, CandidateLifecycleMenu, deletionSummary, EraseCandidateDialog } from "../candidates/CandidateLifecycle";
import type { AdminCandidate } from "../types";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter>
          {ui}
          <ConfirmDialogHost />
        </MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const CANDIDATE: AdminCandidate = {
  id: "c1",
  organization_id: "o1",
  full_name: "Priya Raman",
  email: "priya@example.com",
  phone: null,
  location: null,
  source: null,
  external_ref: null,
  notes: null,
  created_by_id: null,
  created_at: "2026-10-07T10:00:00Z",
  updated_at: "2026-10-07T10:00:00Z",
  archived_at: null,
};
const ARCHIVED: AdminCandidate = { ...CANDIDATE, archived_at: "2026-10-07T12:00:00Z" };

afterEach(() => {
  vi.restoreAllMocks();
});

describe("candidate list params", () => {
  it("hides archived candidates unless asked", () => {
    expect(candidateListParams({ limit: 25, offset: 0 })).toMatchObject({ include_archived: undefined, archived_only: undefined });
    expect(candidateListParams({ archived: "include", limit: 25, offset: 0 })).toMatchObject({ include_archived: true, archived_only: undefined });
    expect(candidateListParams({ archived: "only", limit: 25, offset: 0 })).toMatchObject({ include_archived: undefined, archived_only: true });
    expect(candidateListParams({ archived: "only", limit: 25, offset: 0 })).not.toHaveProperty("archived");
  });
});

describe("candidateActionError", () => {
  const err = (status: number, code: string, details: Record<string, unknown> = {}) => new ApiError({ status, code, message: `server: ${code}`, details });
  it("explains each lifecycle conflict in plain words", () => {
    expect(candidateActionError(err(409, "candidate_archived"))).toMatch(/Restore them first/);
    expect(candidateActionError(err(409, "candidate_exists", { archived: true }))).toMatch(/archived candidate already uses this email/);
    expect(candidateActionError(err(409, "candidate_exists", { archived: false }))).toBe("A candidate with this email already exists.");
    expect(candidateActionError(err(409, "candidate_interview_in_progress"))).toMatch(/interview is in progress/);
    expect(candidateActionError(err(409, "candidate_not_archived"))).toMatch(/Archive the candidate before/);
    expect(candidateActionError(err(409, "candidate_busy", { blockers: ["session"], retry_after_seconds: 600 }))).toMatch(/in about 10 minutes/);
    expect(candidateActionError(err(503, "erasure_incomplete"))).toMatch(/nothing was removed/);
    expect(candidateActionError(err(403, "forbidden"))).toBe("Only administrators can do this.");
  });
});

describe("candidate lifecycle UI", () => {
  it("archives after a confirmation that says what archiving does", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ ...ARCHIVED } as never);
    wrap(<CandidateLifecycleMenu candidate={CANDIDATE} canWrite canErase={false} onErased={vi.fn()} />);
    // Keyboard users open the menu with Enter (jsdom has no pointer events for Radix triggers).
    fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: /Archive candidate/ }));
    expect(await screen.findByText(/open invitation links stop working/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Archive candidate" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/candidates/c1/archive"));
  });

  it("shows the archived state with a way back", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ ...CANDIDATE } as never);
    wrap(<ArchivedCandidateBanner candidate={ARCHIVED} canWrite canErase={false} onErased={vi.fn()} />);
    expect(screen.getByText(/Hidden from candidate lists/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete permanently/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/candidates/c1/restore"));
  });

  it("erases only after the email is typed, with a valid optional reference", async () => {
    const result = { candidate_id: "c1", deleted_at: "2026-10-07T12:30:00Z", interviews: 2, sessions: 1, resumes: 1, profiles: 1, media_files_deleted: 7, media_bytes_deleted: 2_400_000 };
    const del = vi.spyOn(api, "delete").mockResolvedValue(result as never);
    const onErased = vi.fn();
    wrap(<EraseCandidateDialog candidate={ARCHIVED} open onOpenChange={vi.fn()} onErased={onErased} />);
    const confirmButton = screen.getByRole("button", { name: "Delete permanently" });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByText(/answer audio, camera and screen images, recordings/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "PRIYA@example.com " } });
    fireEvent.change(screen.getByLabelText(/Request reference/), { target: { value: "jane doe" } });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByText(/no spaces/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Request reference/), { target: { value: "DSR-2026-0142" } });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/candidates/c1", { params: { request_reference: "DSR-2026-0142" } }));
    await waitFor(() => expect(onErased).toHaveBeenCalledWith(result));
    expect(deletionSummary(result)).toBe("Removed 2 interviews, 1 session, 1 résumé, 7 stored files (2.3 MB).");
  });

  it("keeps the dialog open and explains a busy candidate", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(new ApiError({ status: 409, code: "candidate_busy", message: "busy", details: { retry_after_seconds: 300 } }));
    const onErased = vi.fn();
    wrap(<EraseCandidateDialog candidate={ARCHIVED} open onOpenChange={vi.fn()} onErased={onErased} />);
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "priya@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/still being processed. Try again in about 5 minutes/);
    expect(onErased).not.toHaveBeenCalled();
  });
});
