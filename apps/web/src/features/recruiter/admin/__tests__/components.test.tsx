import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/Tooltip";
import { ApiError, api } from "@/lib/api";
import type { DifficultyDistribution, Interview, Job, Round, RubricCategory } from "@/lib/types";

import { InviteDialog } from "../jobs/InviteDialog";
import { emptyJobDraft, newRoundDraft } from "../jobs/jobDraft";
import { saveJobDraft } from "../jobs/saveJob";
import { DifficultyMixEditor } from "../rounds/DifficultyMixEditor";
import { defaultRoundConfig } from "../rounds/roundConfig";
import { RubricEditor } from "../rounds/RubricEditor";

function wrap(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------- difficulty mix

function MixHarness({ onValue }: { onValue: (d: DifficultyDistribution) => void }) {
  const [value, setValue] = useState<DifficultyDistribution>({ easy: 0.3, medium: 0.4, hard: 0.3 });
  return (
    <DifficultyMixEditor
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue(next);
      }}
    />
  );
}

describe("DifficultyMixEditor", () => {
  it("rebalances the other two shares so the total stays 100%", () => {
    const onValue = vi.fn();
    wrap(<MixHarness onValue={onValue} />);
    const easy = screen.getByLabelText("Easy percentage");
    fireEvent.focus(easy);
    fireEvent.change(easy, { target: { value: "50" } });
    expect(onValue).toHaveBeenLastCalledWith({ easy: 0.5, medium: 0.29, hard: 0.21 });
    expect(screen.getByLabelText("Medium percentage")).toHaveValue("29");
    expect(screen.getByLabelText("Hard percentage")).toHaveValue("21");
  });

  it("keeps a locked share fixed", () => {
    const onValue = vi.fn();
    wrap(<MixHarness onValue={onValue} />);
    fireEvent.click(screen.getByRole("button", { name: "Lock Hard" }));
    const easy = screen.getByLabelText("Easy percentage");
    fireEvent.focus(easy);
    fireEvent.change(easy, { target: { value: "60" } });
    expect(onValue).toHaveBeenLastCalledWith({ easy: 0.6, medium: 0.1, hard: 0.3 });
    expect(screen.getByLabelText("Hard percentage")).toBeDisabled();
  });
});

// ------------------------------------------------------------------ rubric

function RubricHarness({ initial }: { initial: RubricCategory[] }) {
  const [value, setValue] = useState(initial);
  return <RubricEditor value={value} onChange={setValue} competencyOptions={[{ value: "kubernetes", label: "Kubernetes" }]} />;
}

const cat = (key: string, weight: number): RubricCategory => ({ key, label: key, weight, competency_keys: [], dimensions: ["depth"], description: null });

describe("RubricEditor", () => {
  it("validates the 100% total live and fixes it with one click", () => {
    wrap(<RubricHarness initial={[cat("Technical", 40), cat("Communication", 50)]} />);
    const total = screen.getByTestId("rubric-total");
    expect(total).toHaveTextContent("Weights add up to 90%. Assign 10 more points to reach 100%.");
    const weights = screen.getAllByRole("textbox").filter((el) => el.getAttribute("inputmode") === "numeric");
    fireEvent.focus(weights[1]!);
    fireEvent.change(weights[1]!, { target: { value: "60" } });
    expect(total).toHaveTextContent("Total 100%");
    fireEvent.change(weights[1]!, { target: { value: "75" } });
    expect(total).toHaveTextContent("Remove 15 points");
    fireEvent.click(screen.getByRole("button", { name: "Split evenly" }));
    expect(total).toHaveTextContent("Total 100%");
  });

  it("derives unique keys from labels and adds categories with the remaining points", () => {
    wrap(<RubricHarness initial={[cat("technical", 70)]} />);
    fireEvent.click(screen.getByRole("button", { name: "Add category" }));
    const labels = screen.getAllByPlaceholderText("e.g. Technical knowledge");
    expect(labels).toHaveLength(2);
    expect(screen.getByTestId("rubric-total")).toHaveTextContent("Total 100%");
  });
});

// -------------------------------------------------------------- saveJob

describe("saveJobDraft", () => {
  it("creates the job as a draft, adds skills and rounds, then opens it", async () => {
    const calls: string[] = [];
    vi.spyOn(api, "post").mockImplementation(async (path: string, body?: unknown) => {
      calls.push(`POST ${path} ${JSON.stringify((body as { status?: string })?.status ?? "")}`);
      if (path === "/jobs") return { id: "j9", status: "draft", title: "SRE" } as Job;
      if (path === "/jobs/j9/rounds") return { id: "r1", round_type: "technical_screening", config: defaultRoundConfig() } as Round;
      return {};
    });
    vi.spyOn(api, "patch").mockImplementation(async (path: string, body?: unknown) => {
      calls.push(`PATCH ${path} ${JSON.stringify(body)}`);
      return { id: "j9", status: "open" } as Job;
    });
    const round = newRoundDraft(defaultRoundConfig("technical_screening", "Screen"));
    const result = await saveJobDraft({
      jobId: null,
      draft: { ...emptyJobDraft(), title: "SRE", status: "open", required_skills: ["kubernetes"], rounds: [round] },
      requirements: [],
      rounds: [],
      catalog: [{ id: "c", key: "kubernetes", name: "Kubernetes", category: "technical", organization_id: null, description: null, rubric: {}, is_active: true, created_at: "", updated_at: "" }],
      fallbackReportMode: "after_review",
    });
    expect(result.ok).toBe(true);
    expect(result.createdRoundIds[round.uid]).toBe("r1");
    expect(calls).toEqual(['POST /jobs "draft"', 'POST /jobs/j9/requirements ""', 'POST /jobs/j9/rounds ""', 'PATCH /jobs/j9 {"status":"open"}']);
  });

  it("maps a round's validation error onto that round and keeps the job saved", async () => {
    vi.spyOn(api, "patch").mockResolvedValue({ id: "j1", status: "draft" } as Job);
    vi.spyOn(api, "post").mockImplementation(async (path: string) => {
      if (path === "/jobs/j1/rounds") {
        throw new ApiError({
          status: 422,
          code: "validation_failed",
          message: "Some fields need attention.",
          details: { errors: [{ loc: ["body", "config", "rubric"], msg: "Value error, rubric weights must sum to 100 (got 90)" }] },
        });
      }
      return {};
    });
    const round = newRoundDraft(defaultRoundConfig());
    const result = await saveJobDraft({
      jobId: "j1",
      draft: { ...emptyJobDraft(), title: "SRE", rounds: [round] },
      requirements: [],
      rounds: [],
      catalog: [],
      fallbackReportMode: "after_review",
    });
    expect(result.ok).toBe(false);
    expect(result.job?.id).toBe("j1");
    expect(result.roundErrors[round.uid]).toEqual({ rubric: "Rubric weights must sum to 100 (got 90)" });
  });

  it("maps job field errors and stops before touching rounds", async () => {
    vi.spyOn(api, "post").mockRejectedValue(
      new ApiError({ status: 422, code: "validation_failed", message: "Bad", details: { errors: [{ loc: ["body", "title"], msg: "String should have at most 200 characters" }] } }),
    );
    const result = await saveJobDraft({ jobId: null, draft: { ...emptyJobDraft(), title: "x" }, requirements: [], rounds: [], catalog: [], fallbackReportMode: "never" });
    expect(result.job).toBeNull();
    expect(result.jobErrors).toEqual({ title: "String should have at most 200 characters" });
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});

// ------------------------------------------------------------------ invite

describe("InviteDialog", () => {
  const job = { id: "j1", title: "Senior DevOps Engineer", allow_candidate_round_selection: false, status: "open" } as Job;
  const rounds = [
    { id: "r1", name: "Combined AI Round", round_type: "combined", duration_minutes: 30, expected_difficulty: "medium", is_active: true, candidate_selectable: false, config: defaultRoundConfig("combined") },
  ] as unknown as Round[];

  it("shows the invite link and access code exactly once with copy buttons", async () => {
    vi.spyOn(api, "get").mockImplementation(async (path: string) => {
      if (path === "/settings") return { require_access_code: false };
      if (path === "/candidates") return { items: [{ id: "c1", full_name: "Jane Doe", email: "jane@example.com", latest_interview_status: null }], total: 1 };
      throw new ApiError({ status: 404, code: "not_found", message: "nope" });
    });
    const post = vi.spyOn(api, "post").mockResolvedValue({
      interview: { id: "i1", invite_expires_at: "2026-10-14T10:00:00Z", candidate_id: "c1", job_id: "j1" } as Interview,
      invite_url: "https://interviews.example.com/i/tok_abc123",
      access_code: "482913",
    });
    wrap(<InviteDialog open onOpenChange={() => undefined} job={job} rounds={rounds} />);

    fireEvent.click(await screen.findByRole("radio", { name: /jane doe/i }));
    fireEvent.click(screen.getByRole("switch", { name: /require an access code/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    });

    expect(post).toHaveBeenCalledWith("/interviews", {
      candidate_id: "c1",
      job_id: "j1",
      round_id: "r1",
      allow_round_selection: false,
      expires_in_hours: 168,
      require_access_code: true,
    });
    expect(await screen.findByTestId("invite-link")).toHaveTextContent("https://interviews.example.com/i/tok_abc123");
    expect(screen.getByTestId("invite-access-code")).toHaveTextContent("482913");
    expect(screen.getByText(/won't be shown again/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy invite link" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy access code" })).toBeInTheDocument();
  });

  it("asks for a candidate before submitting", async () => {
    vi.spyOn(api, "get").mockImplementation(async (path: string) => (path === "/candidates" ? { items: [], total: 0 } : { require_access_code: true }));
    const post = vi.spyOn(api, "post");
    wrap(<InviteDialog open onOpenChange={() => undefined} job={job} rounds={rounds} />);
    await waitFor(() => expect(screen.getByRole("switch", { name: /require an access code/i })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Create invitation" }));
    expect(await screen.findByText("Choose a candidate, or add a new one.")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
    // Organization policy forces the access code on.
    expect(screen.getByRole("switch", { name: /require an access code/i })).toBeChecked();
    expect(within(screen.getByRole("dialog")).getByText(/required by your organization/i)).toBeInTheDocument();
  });
});
