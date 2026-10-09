import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";

import type { IntakeDecisionIn } from "../types";
import { DECIDED_DETAIL, DECISION, DETAIL, EVALUATION, IDS } from "./fixtures";
import { mockApi, renderApp, signIn } from "./helpers";

beforeEach(() => resetAuthStore());
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

const page = (tab = "overview") => `/app/intake/${IDS.intake}?tab=${tab}`;

describe("intake review page", () => {
  it("opens on the overview: candidate, eligibility for humans only, stages and review items", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp(page());
    const root = await screen.findByTestId("intake-review-page");
    expect(within(root).getByRole("heading", { level: 1, name: "Asha Rao" })).toBeInTheDocument();
    expect(within(root).getAllByLabelText("AI recommendation: Accept with notes").length).toBeGreaterThan(0);
    const eligibility = screen.getByTestId("intake-eligibility");
    expect(eligibility).toHaveTextContent("For human review only — never used by the AI interviewer or in any score");
    expect(eligibility).toHaveTextContent("Authorised to work in the United States");
    expect(screen.getAllByTestId(/^intake-stage-row-/)).toHaveLength(7);
    expect(screen.getByTestId("intake-stage-row-resume")).toHaveAttribute("data-status", "completed");
    const items = screen.getAllByTestId("intake-review-item");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveAttribute("data-kind", "claims_not_supported");
    expect(screen.getByTestId("intake-report-link")).toHaveAttribute("href", `/app/reports/${IDS.session}`);
    for (const tab of ["overview", "stages", "reading", "topic", "scorecard", "recording", "integrity", "decision"]) expect(screen.getByTestId(`intake-tab-${tab}`)).toBeInTheDocument();
  });

  it("shows the scorecard: seven weighted competencies, the total and the band as an AI recommendation", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp(page("scorecard"));
    const card = await screen.findByTestId("intake-scorecard");
    expect(screen.getByTestId("intake-weighted-total")).toHaveTextContent("3.63");
    expect(screen.getByTestId("intake-normalized-100")).toHaveTextContent("72.6 out of 100 (conversion)");
    expect(screen.getByTestId("intake-band")).toHaveAttribute("data-band", "accept_with_notes");
    expect(card).toHaveTextContent("AI recommendation — a human reviewer decides");
    const rows = screen.getAllByTestId(/^intake-competency-/);
    expect(rows).toHaveLength(7);
    const reading = screen.getByTestId("intake-competency-technical_reading");
    expect(reading).toHaveAttribute("data-score", "3.4");
    expect(reading).toHaveTextContent("Weight 20%");
    expect(reading).toHaveTextContent("+0.68");
    expect(within(screen.getByTestId("intake-competency-technical")).getAllByTestId("intake-evidence-ref")).toHaveLength(3);
    expect(screen.getByTestId("intake-validator-issues")).toBeInTheDocument();
  });

  it("labels a partial total and shows insufficient evidence instead of a score", async () => {
    const competencies = EVALUATION.competencies.map((c) => (c.key === "composure" ? { ...c, status: "insufficient" as const, score: null, weighted: null, missing_evidence: ["No follow-up or challenge was answered"] } : c));
    mockApi((path) => (path === `/intake/interviews/${IDS.intake}` ? { ...DETAIL, evaluation: { ...EVALUATION, competencies, weighted_total: null, normalized_100: null, partial_total: "3.5888", partial_label: "Partial total (90% of the weight)", band: "insufficient_evidence", band_label: "Insufficient evidence" } } : undefined));
    signIn("RECRUITER");
    renderApp(page("scorecard"));
    const total = await screen.findByTestId("intake-weighted-total");
    expect(total).toHaveTextContent("Partial total (90% of the weight)");
    expect(total).toHaveTextContent("3.58");
    expect(screen.getByTestId("intake-band")).toHaveAttribute("data-band", "insufficient_evidence");
    const composure = screen.getByTestId("intake-competency-composure");
    expect(composure).toHaveAttribute("data-status", "insufficient");
    expect(composure).toHaveTextContent("Insufficient evidence");
    expect(composure).toHaveTextContent("No follow-up or challenge was answered");
  });

  it("lists every question and answer by stage, with evidence chips that deep-link back", async () => {
    mockApi();
    signIn("RECRUITER");
    const router = renderApp(page("stages"));
    await waitFor(() => expect(screen.getAllByTestId("intake-qa-item").length).toBe(20));
    const qa = screen.getAllByTestId("intake-qa-item").find((el) => el.getAttribute("data-question-id") === "q-07")!;
    expect(qa).toHaveTextContent("a senior colleague trained the model");
    const chip = within(qa).getByTestId("intake-evidence-ref");
    expect(chip).toHaveAttribute("data-supports", "negative");
    fireEvent.click(chip);
    await waitFor(() => expect(router.state.location.search).toContain("q=q-07"));
  });

  it("shows Drill A with word-level marks and Drill B concepts separately", async () => {
    const { calls } = mockApi();
    signIn("RECRUITER");
    renderApp(page("reading"));
    const drillA = await screen.findByTestId("intake-reading-drill-a");
    const alignment = await within(drillA).findByTestId("intake-alignment");
    await waitFor(() => expect(alignment).toHaveAttribute("data-level", "word"));
    expect(alignment.querySelector('[data-mark="omission"]')).toHaveTextContent("on a foggy hill");
    expect(alignment.querySelectorAll('[data-mark="substitution"]')).toHaveLength(2);
    expect(alignment.querySelectorAll('[data-mark="extra"]')).toHaveLength(2);
    expect(within(drillA).getByTestId("intake-reading-metric-completeness")).toHaveTextContent("94%");
    expect(within(drillA).getByTestId("intake-reading-metric-wpm")).toHaveTextContent("140 wpm");
    expect(screen.getByTestId("intake-pronunciation")).toHaveTextContent("Pronunciation: not assessed");
    expect(screen.getByTestId("intake-technical-reading")).toHaveTextContent("0.4 × 4 + 0.6 × 3 = 3.4");
    const drillB = screen.getByTestId("intake-reading-drill-b");
    const concepts = within(drillB).getAllByTestId("intake-relay-concept");
    expect(concepts).toHaveLength(8);
    expect(concepts.filter((c) => c.getAttribute("data-status") === "covered")).toHaveLength(5);
    expect(drillB).toHaveTextContent("In their own words");
    // The evaluation carries the alignment events, so the report isn't fetched.
    expect(calls.some((c) => c.path.endsWith("/reports/recruiter"))).toBe(false);
  });

  it("takes the word events from the generated report when the evaluation has none", async () => {
    const withoutEvents = { ...DETAIL, evaluation: { ...EVALUATION, summary: { ...EVALUATION.summary, drill_a: { ...EVALUATION.summary.drill_a!, events: null } } } };
    const { calls } = mockApi((path) => (path === `/intake/interviews/${IDS.intake}` ? withoutEvents : undefined));
    signIn("RECRUITER");
    renderApp(page("reading"));
    const alignment = await screen.findByTestId("intake-alignment");
    await waitFor(() => expect(alignment).toHaveAttribute("data-level", "word"));
    expect(calls.some((c) => c.path === `/sessions/${IDS.session}/reports/recruiter`)).toBe(true);
  });

  it("falls back to sentence coverage when no word events are available yet", async () => {
    const withoutEvents = { ...EVALUATION, summary: { ...EVALUATION.summary, drill_a: { ...EVALUATION.summary.drill_a!, events: null } } };
    mockApi((path) => (path === `/intake/interviews/${IDS.intake}` ? { ...DETAIL, report: null, evaluation: withoutEvents } : undefined));
    signIn("RECRUITER");
    renderApp(page("reading"));
    const alignment = await screen.findByTestId("intake-alignment");
    expect(alignment).toHaveAttribute("data-level", "sentence");
  });

  it("shows the topic card with each bullet's coverage", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp(page("topic"));
    const card = await screen.findByTestId("intake-topic-card");
    expect(card).toHaveTextContent("The Importance of Time Management");
    expect(screen.getByTestId("intake-bullet-coverage-3")).toHaveAttribute("data-status", "partial");
    expect(screen.getByTestId("intake-bullet-coverage-1")).toHaveAttribute("data-status", "covered");
  });

  it("records a decision: validation, the override warning and the request body", async () => {
    mockApi();
    let body: IntakeDecisionIn | null = null;
    vi.spyOn(api, "post").mockImplementation(async (path: string, payload?: unknown) => {
      if (path === `/intake/interviews/${IDS.intake}/decisions`) {
        body = payload as IntakeDecisionIn;
        return DECISION as never;
      }
      throw new ApiError({ status: 404, code: "not_found", message: path });
    });
    signIn("RECRUITER");
    renderApp(page("decision"));
    const form = await screen.findByTestId("intake-decision-form");
    expect(form).toHaveTextContent("The AI recommendation is advisory. Your decision is recorded with your name and rationale.");

    fireEvent.click(screen.getByTestId("intake-decision-submit"));
    expect(await screen.findByText("Choose an outcome.")).toBeInTheDocument();
    expect(body).toBeNull();

    fireEvent.click(screen.getByTestId("intake-decision-outcome-reject"));
    expect(await screen.findByTestId("intake-override-warning")).toHaveTextContent("This differs from the AI recommendation");
    fireEvent.click(screen.getByTestId("intake-decision-outcome-accept_with_notes"));
    await waitFor(() => expect(screen.queryByTestId("intake-override-warning")).not.toBeInTheDocument());

    fireEvent.change(screen.getByTestId("intake-decision-rationale"), { target: { value: "Solid base; flag structured speaking for the trainers." } });
    fireEvent.click(within(screen.getByTestId("intake-review-item-resolution-ri_7c1e0a9b3d")).getByLabelText("Confirmed"));
    fireEvent.click(screen.getByTestId("intake-decision-submit"));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({
      outcome: "accept_with_notes",
      rationale: "Solid base; flag structured speaking for the trainers.",
      review_items: [{ item_id: "ri_7c1e0a9b3d", resolution: "confirmed" }],
      competency_notes: {},
      requested_evidence: [],
    });
  });

  it("explains a running evaluation and shows the decision history", async () => {
    mockApi((path) => (path === `/intake/interviews/${IDS.intake}` ? DECIDED_DETAIL : undefined));
    vi.spyOn(api, "post").mockRejectedValue(new ApiError({ status: 409, code: "evaluation_running", message: "running" }));
    signIn("RECRUITER");
    renderApp(page("decision"));
    const history = await screen.findByTestId("intake-decision-history");
    expect(within(history).getByTestId("intake-decision-entry")).toHaveAttribute("data-outcome", "accept_with_notes");
    expect(history).toHaveTextContent("Solid analytics base and honest about gaps.");
    fireEvent.click(screen.getByTestId("intake-decision-outcome-accept"));
    fireEvent.change(screen.getByTestId("intake-decision-rationale"), { target: { value: "Upgrading after the second look." } });
    fireEvent.click(screen.getByTestId("intake-decision-submit"));
    expect(await screen.findByText(/A new evaluation is running/)).toBeInTheDocument();
  });

  it("re-evaluates after a confirmation", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue({ evaluation_version: 2, job_id: "job-1" } as never);
    signIn("RECRUITER");
    renderApp(page());
    fireEvent.click(await screen.findByTestId("intake-reevaluate"));
    fireEvent.click(await screen.findByRole("button", { name: "Re-evaluate" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(`/intake/interviews/${IDS.intake}/evaluate`));
  });

  it("embeds the existing integrity tab for the session", async () => {
    mockApi((path) => {
      if (path === `/sessions/${IDS.session}/integrity`) return { session_id: IDS.session, events: [], counts: {}, counts_by_severity: {}, policy: {} };
      if (path.startsWith(`/sessions/${IDS.session}/`)) return { items: [], total: 0 };
      return undefined;
    });
    signIn("RECRUITER");
    renderApp(page("integrity"));
    expect(await screen.findByText("Signals are observations, not determinations.")).toBeInTheDocument();
  });

  it("says when an intake doesn't exist", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp("/app/intake/it-missing");
    expect(await screen.findByText("This intake interview doesn't exist or was removed")).toBeInTheDocument();
  });
});
