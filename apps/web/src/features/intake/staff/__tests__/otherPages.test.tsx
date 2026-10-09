import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";

import { defaultRoundConfig, type RoundConfigWithIntake } from "@/features/recruiter/admin/rounds/roundConfig";
import { RoundConfigEditor } from "@/features/recruiter/admin/rounds/RoundConfigEditor";
import { AppProviders } from "@/app/providers";

import type { IntakeOrgSettingsIn } from "../types";
import { IDS, ORG_SETTINGS, ROWS } from "./fixtures";
import { mockApi, renderApp, SESSION_DETAIL, signIn } from "./helpers";

beforeEach(() => resetAuthStore());
afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

describe("printable intake report", () => {
  it("dispatches intake sessions to the intake report with sections A-K", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp(`/app/reports/${IDS.session}`);
    const report = await screen.findByTestId("intake-report");
    expect(report).toHaveAttribute("data-report-root");
    expect([...report.querySelectorAll("section[data-section]")].map((s) => s.getAttribute("data-section"))).toEqual(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]);
    expect(report).toHaveTextContent("This is an AI recommendation. A human reviewer makes the decision.");
    expect(report).toHaveTextContent("Not read: on, a, foggy, hill");
    expect(report).toHaveTextContent("Work-authorisation answers are reviewed by people only");
    expect(screen.queryByTestId("recruiter-report")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Intake review/ })).toHaveAttribute("href", `/app/intake/${IDS.intake}`);
  });
});

describe("intake analytics page", () => {
  it("renders the KPIs and charts, and scopes them by period", async () => {
    const { calls } = mockApi();
    signIn("RECRUITER");
    const router = renderApp("/app/intake/analytics");
    const kpis = await screen.findByTestId("intake-analytics-kpis");
    expect(kpis).toHaveTextContent("42");
    expect(kpis).toHaveTextContent("3.41");
    expect(screen.getAllByText("AI recommendations").length).toBeGreaterThan(0);
    expect(screen.getByText("3 of 21 decisions differed from the AI recommendation (14%).")).toBeInTheDocument();
    expect(screen.getAllByText("Common technical weaknesses").length).toBeGreaterThan(0);
    expect(calls.find((c) => c.path === "/intake/analytics")?.params).toMatchObject({ days: 30 });

    fireEvent.click(screen.getByTestId("intake-analytics-period"));
    fireEvent.click(await screen.findByRole("option", { name: "Last 90 days" }));
    await waitFor(() => expect([...calls].reverse().find((c) => c.path === "/intake/analytics")?.params).toMatchObject({ days: 90 }));
    expect(router.state.location.search).toBe("?days=90");
  });
});

describe("intake settings page", () => {
  it("edits the program, eligibility default and approved answers, then saves", async () => {
    mockApi();
    let body: IntakeOrgSettingsIn | null = null;
    vi.spyOn(api, "put").mockImplementation(async (path: string, payload?: unknown) => {
      if (path === "/intake/settings") {
        body = payload as IntakeOrgSettingsIn;
        return { ...ORG_SETTINGS, ...(payload as object) } as never;
      }
      throw new ApiError({ status: 404, code: "not_found", message: path });
    });
    signIn("ADMIN");
    renderApp("/app/intake/settings");
    await screen.findByTestId("intake-settings-page");
    expect(await screen.findAllByTestId("intake-approved-info-row")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Add an answer" }));
    expect(screen.getAllByTestId("intake-approved-info-row")).toHaveLength(3);
    fireEvent.click(screen.getByTestId("intake-settings-save"));
    expect(await screen.findByText("Add a topic.")).toBeInTheDocument();
    expect(body).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Remove topic 3" }));
    fireEvent.change(screen.getByDisplayValue("AI/ML Training Program"), { target: { value: "  Data Engineering Bootcamp " } });
    fireEvent.click(screen.getByTestId("intake-settings-save"));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ program_name: "Data Engineering Bootcamp", work_authorization: { enabled: true, country: "the United States" }, approved_info: ORG_SETTINGS.approved_info });
  });

  it("is only for administrators", async () => {
    mockApi();
    signIn("RECRUITER");
    renderApp("/app/intake/settings");
    expect(await screen.findByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByTestId("intake-settings-page")).not.toBeInTheDocument();
  });
});

describe("sessions and intake routing", () => {
  it("resolves a session to its intake review with one exact lookup", async () => {
    const { calls } = mockApi((path, params) => (path === "/intake/interviews" ? (params.session_id === IDS.session ? { items: ROWS.slice(0, 1), total: 1 } : { items: [], total: 0 }) : undefined));
    signIn("RECRUITER");
    const router = renderApp(`/app/intake/session/${IDS.session}?tab=scorecard`);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/app/intake/${IDS.intake}`));
    expect(router.state.location.search).toBe("?tab=scorecard");
    const lookups = calls.filter((c) => c.path === "/intake/interviews");
    expect(lookups[0]?.params).toMatchObject({ session_id: IDS.session, limit: 1 });
    expect(calls.some((c) => c.path === `/sessions/${IDS.session}`)).toBe(false);
  });

  it("says so when a session has no intake record", async () => {
    mockApi((path) => (path === "/intake/interviews" ? { items: [], total: 0 } : undefined));
    signIn("RECRUITER");
    renderApp(`/app/intake/session/${IDS.session}`);
    expect(await screen.findByText("No intake record for this session")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the session" })).toHaveAttribute("href", `/app/sessions/${IDS.session}`);
  });

  it("offers the intake review from the session header and hides the generic recompute", async () => {
    mockApi((path) => (path.startsWith(`/sessions/${IDS.session}/`) ? { items: [], total: 0 } : undefined));
    signIn("RECRUITER");
    renderApp(`/app/sessions/${IDS.session}?tab=overview`);
    const link = await screen.findByTestId("session-intake-review-link");
    expect(link).toHaveAttribute("href", `/app/intake/session/${IDS.session}`);
    fireEvent.keyDown(screen.getByRole("button", { name: "More session actions" }), { key: "Enter" });
    expect(await screen.findByText("Copy link to this session")).toBeInTheDocument();
    expect(screen.queryByText("Recompute scores from evidence")).not.toBeInTheDocument();
  });

  it("points the session decision tab at the intake review instead of the generic form", async () => {
    mockApi((path) => (path.startsWith(`/sessions/${IDS.session}/`) ? { items: [], total: 0 } : undefined));
    signIn("RECRUITER");
    renderApp(`/app/sessions/${IDS.session}?tab=review`);
    const pointer = await screen.findByTestId("session-intake-decision-pointer");
    expect(within(pointer).getByRole("link", { name: "Open intake review" })).toHaveAttribute("href", `/app/intake/session/${IDS.session}?tab=decision`);
    expect(screen.queryByText("Record your decision")).not.toBeInTheDocument();
    expect(await screen.findByText("No decisions yet")).toBeInTheDocument();
  });

  it("filters the sessions list by round type", async () => {
    const { calls } = mockApi((path) => (path === "/sessions" ? { items: [{ ...SESSION_DETAIL, overall_score: 72.6, recommendation: "proceed" }], total: 1 } : path === "/jobs" ? { items: [], total: 0 } : undefined));
    signIn("RECRUITER");
    const router = renderApp("/app/sessions");
    await screen.findByTestId("session-row");
    fireEvent.click(screen.getByTestId("sessions-filter-round-type"));
    fireEvent.click(await screen.findByRole("option", { name: "Intake interview" }));
    await waitFor(() => expect([...calls].reverse().find((c) => c.path === "/sessions" && c.params?.round_type)?.params).toMatchObject({ round_type: ["intake"] }));
    expect(router.state.location.search).toBe("?type=intake");
  });
});

describe("round editor for intake rounds", () => {
  function Harness({ onConfig }: { onConfig: (c: RoundConfigWithIntake) => void }) {
    const [config, setConfig] = useState<RoundConfigWithIntake>(defaultRoundConfig("intake") as RoundConfigWithIntake);
    return (
      <RoundConfigEditor
        value={config}
        onChange={(next) => {
          setConfig(next as RoundConfigWithIntake);
          onConfig(next as RoundConfigWithIntake);
        }}
        catalog={[]}
        problems={[]}
      />
    );
  }

  it("swaps coding for intake settings and writes them into config.intake", async () => {
    mockApi();
    const seen: RoundConfigWithIntake[] = [];
    render(
      <AppProviders>
        <Harness onConfig={(c) => seen.push(c)} />
      </AppProviders>,
    );
    expect(screen.getByTestId("intake-round-blueprint")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Coding/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Difficulty mix")).not.toBeInTheDocument();
    const intakeTab = screen.getByRole("tab", { name: /Intake/ });
    fireEvent.mouseDown(intakeTab);
    fireEvent.click(intakeTab);
    const panel = await screen.findByTestId("intake-round-panel");
    const introduction = within(panel).getByLabelText("Introduction");
    fireEvent.change(introduction, { target: { value: "3" } });
    await waitFor(() => expect(seen.at(-1)?.intake).toMatchObject({ followup_limits: { greeting: 3 } }));
  });
});
