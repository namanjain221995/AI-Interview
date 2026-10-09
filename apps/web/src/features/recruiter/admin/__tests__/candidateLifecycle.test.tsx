import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmDialogHost } from "@/components/ui/AlertDialog";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { ApiError, api } from "@/lib/api";

import { candidateActionError, candidateListParams, eraseParams } from "../api/candidates";
import { ArchivedCandidateBanner, CandidateDangerZone, CandidateLifecycleMenu, deletionSummary, EraseCandidateDialog } from "../candidates/CandidateLifecycle";
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
const PREVIEW = {
  candidate_id: "c1",
  archived: true,
  interviews: 2,
  sessions: 3,
  resumes: 1,
  reports: 3,
  intake_interviews: 1,
  link_registrations: 1,
  stored_files: 9,
  stored_bytes: 4_800_000,
  blockers: [],
};

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

  it("erases only after the full name is typed, with a valid optional reference", async () => {
    const result = { candidate_id: "c1", deleted_at: "2026-10-07T12:30:00Z", interviews: 2, sessions: 1, resumes: 1, profiles: 1, media_files_deleted: 7, media_bytes_deleted: 2_400_000 };
    vi.spyOn(api, "get").mockResolvedValue(PREVIEW as never);
    const del = vi.spyOn(api, "delete").mockResolvedValue(result as never);
    const onErased = vi.fn();
    wrap(<EraseCandidateDialog candidate={ARCHIVED} open onOpenChange={vi.fn()} onErased={onErased} />);
    const confirmButton = screen.getByRole("button", { name: "Delete permanently" });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByText(/answer audio, camera and screen images, recordings/)).toBeInTheDocument();
    expect(await screen.findByTestId("erase-candidate-counts")).toHaveTextContent(/2 interviews, 3 sessions, 3 reports, 1 résumé, 1 intake interview, 1 link registration, 9 stored files/);
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "priya@example.com" } });
    expect(confirmButton).toBeDisabled(); // the email is not the name
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "  priya   RAMAN " } });
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
    vi.spyOn(api, "get").mockResolvedValue(PREVIEW as never);
    vi.spyOn(api, "delete").mockRejectedValue(new ApiError({ status: 409, code: "candidate_busy", message: "busy", details: { retry_after_seconds: 300 } }));
    const onErased = vi.fn();
    wrap(<EraseCandidateDialog candidate={ARCHIVED} open onOpenChange={vi.fn()} onErased={onErased} />);
    fireEvent.change(screen.getByLabelText(/Type .* to confirm/), { target: { value: "Priya Raman" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/still being processed. Try again in about 5 minutes/);
    expect(onErased).not.toHaveBeenCalled();
  });

  it("offers an active candidate's permanent delete in the menu and archives + deletes in one step", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ ...PREVIEW, archived: false } as never);
    const result = { candidate_id: "c1", deleted_at: "2026-10-07T12:30:00Z", interviews: 1, sessions: 1, resumes: 0, profiles: 0, media_files_deleted: 2, media_bytes_deleted: 2048, archived_in_request: true };
    const del = vi.spyOn(api, "delete").mockResolvedValue(result as never);
    const onErased = vi.fn();
    wrap(<CandidateLifecycleMenu candidate={CANDIDATE} canWrite canErase onErased={onErased} />);
    fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
    fireEvent.click(await screen.findByRole("menuitem", { name: /Delete candidate permanently/ }));
    expect(await screen.findByText(/archived first .* and then deleted, in one step/)).toBeInTheDocument();
    const confirmButton = screen.getByRole("button", { name: "Archive and delete permanently" });
    expect(confirmButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Type the candidate's full name/), { target: { value: "Priya Raman" } });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/candidates/c1", { params: { archive_first: true } }));
    await waitFor(() => expect(onErased).toHaveBeenCalledWith(result));
  });

  it("keeps the delete menu item for administrators only", async () => {
    wrap(<CandidateLifecycleMenu candidate={CANDIDATE} canWrite canErase={false} onErased={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("button", { name: "More actions" }), { key: "Enter" });
    await screen.findByRole("menuitem", { name: /Archive candidate/ });
    expect(screen.queryByRole("menuitem", { name: /Delete candidate permanently/ })).not.toBeInTheDocument();
  });

  it("shows a danger zone card that opens the same confirmation", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ ...PREVIEW, archived: false } as never);
    wrap(<CandidateDangerZone candidate={CANDIDATE} onErased={vi.fn()} />);
    expect(screen.getByText(/archived first, in the same step/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("candidate-danger-delete"));
    expect(await screen.findByTestId("erase-candidate-dialog")).toBeInTheDocument();
  });

  it("builds the erase params", () => {
    expect(eraseParams(null, false)).toBeUndefined();
    expect(eraseParams("DSR-1", false)).toEqual({ request_reference: "DSR-1" });
    expect(eraseParams(null, true)).toEqual({ archive_first: true });
  });
});
