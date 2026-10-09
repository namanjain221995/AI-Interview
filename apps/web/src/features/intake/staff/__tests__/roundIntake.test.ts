import { describe, expect, it } from "vitest";

import { applyRoundTypeChange, defaultRoundConfig, sectionsFor, toRoundConfig, validateRoundConfig, withDefaults, type RoundConfigWithIntake } from "@/features/recruiter/admin/rounds/roundConfig";

import { INTAKE_ROUND_DEFAULTS, planBudgets, readIntakeRound, validateIntakeRound, writeIntakeRound } from "../lib/roundIntake";

describe("intake section of the round model", () => {
  it("reads defaults and writes changes without dropping unknown keys", () => {
    expect(readIntakeRound({})).toEqual(INTAKE_ROUND_DEFAULTS);
    const stored = { kit_version: "v1", future_option: true, followup_limits: { greeting: 1, extra_limit: 4 } };
    const view = readIntakeRound(stored);
    expect(view.followup_limits.greeting).toBe(1);
    const written = writeIntakeRound(stored, { ...view, scenarios: 0, topic_card: "4" });
    expect(written).toMatchObject({ future_option: true, scenarios: 0, topic_card: "4", followup_limits: { greeting: 1, extra_limit: 4 } });
  });

  it("validates ranges, distinct passages and the work-authorisation country", () => {
    const issues = validateIntakeRound({
      ...INTAKE_ROUND_DEFAULTS,
      followup_limits: { ...INTAKE_ROUND_DEFAULTS.followup_limits, greeting: 4 },
      claims: { min_claims: 3, max_claims: 2 },
      passage_drill_a: "B",
      passage_drill_b: "B",
      work_authorization: { enabled: true, country: "", ask_sponsorship: true, variant: "default" },
    });
    expect(issues.map((i) => i.path)).toEqual(["intake.followup_limits.greeting", "intake.claims.max_claims", "intake.passage_drill_b", "intake.work_authorization.country"]);
  });

  it("plans stage budgets with the server's formula (45 min -> 314, 566, 251, 471, 471, 314, 251 s)", () => {
    expect(planBudgets(45).map((b) => b.seconds)).toEqual([314, 566, 251, 471, 471, 314, 251]);
    expect(planBudgets(35)[0]!.seconds).toBeLessThan(planBudgets(50)[0]!.seconds);
  });
});

describe("round config with intake rounds", () => {
  it("defaults an intake round like the intake template", () => {
    const config = defaultRoundConfig("intake") as RoundConfigWithIntake;
    expect(config).toMatchObject({ round_type: "intake", duration_minutes: 45, max_duration_minutes: 50, candidate_selectable: false, coding: { enabled: false }, resume_validation: { enabled: true }, intake: {} });
    expect(config.rubric.reduce((n, c) => n + c.weight, 0)).toBe(100);
    expect(validateRoundConfig(config).filter((i) => i.severity === "error")).toEqual([]);
  });

  it("keeps the intake section opaque on intake rounds and never sends it for other rounds", () => {
    const loaded = withDefaults({ round_type: "intake", name: "Intake", duration_minutes: 40, intake: { scenarios: 1, unknown_key: "kept" } });
    expect(loaded.intake).toEqual({ scenarios: 1, unknown_key: "kept" });
    expect(toRoundConfig(loaded).intake).toEqual({ scenarios: 1, unknown_key: "kept" });
    const other = withDefaults({ round_type: "behavioral", intake: { scenarios: 1 } });
    expect(other).not.toHaveProperty("intake");
    expect(toRoundConfig(other as RoundConfigWithIntake)).not.toHaveProperty("intake");
  });

  it("enforces the intake rules: 35-50 minutes, no coding, not candidate-selectable", () => {
    const base = defaultRoundConfig("intake") as RoundConfigWithIntake;
    const issues = validateRoundConfig({ ...base, duration_minutes: 30, coding: { ...base.coding, enabled: true }, candidate_selectable: true, intake: { claims: { min_claims: 3, max_claims: 1 } } });
    const errors = issues.filter((i) => i.severity === "error").map((i) => i.path);
    expect(errors).toEqual(expect.arrayContaining(["duration_minutes", "intake", "candidate_selectable", "intake.claims.max_claims"]));
    expect(validateRoundConfig({ ...base, max_duration_minutes: 60 }).find((i) => i.path === "max_duration_minutes")?.severity).toBe("warning");
    // Other round types keep the generic 5-240 minute range.
    expect(validateRoundConfig({ ...defaultRoundConfig("behavioral"), duration_minutes: 30 }).some((i) => i.path === "duration_minutes")).toBe(false);
  });

  it("applies intake rules when switching to intake and drops them when leaving", () => {
    const coding = { ...defaultRoundConfig("coding"), duration_minutes: 60, candidate_selectable: true } as RoundConfigWithIntake;
    const intake = applyRoundTypeChange(coding, "intake");
    expect(intake).toMatchObject({ round_type: "intake", duration_minutes: 45, coding: { enabled: false }, candidate_selectable: false, resume_validation: { enabled: true }, intake: {} });
    expect(intake.max_duration_minutes).toBeLessThanOrEqual(50);
    const back = applyRoundTypeChange(intake, "behavioral");
    expect(back.round_type).toBe("behavioral");
    expect(back).not.toHaveProperty("intake");
  });

  it("swaps the Coding tab for an Intake tab", () => {
    expect(sectionsFor("intake").map((s) => s.key)).toEqual(["setup", "scoring", "flow", "intake", "candidate"]);
    expect(sectionsFor("coding").map((s) => s.key)).toContain("coding");
  });
});
