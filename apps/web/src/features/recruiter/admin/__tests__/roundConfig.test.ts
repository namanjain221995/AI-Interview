import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import type { RoundConfig } from "@/lib/types";

import { fieldErrorsOf, rebaseErrors } from "../lib/serverErrors";
import {
  countBySection,
  defaultRoundConfig,
  hasBlockingIssues,
  issuesToErrors,
  roundColumns,
  sectionForPath,
  toRoundConfig,
  validateRoundConfig,
  withDefaults,
} from "../rounds/roundConfig";

/** Every key of app/schemas/rounds.py, per model (extra="forbid" rejects anything else). */
const SCHEMA_KEYS = {
  RoundConfig: [
    "candidate_selectable",
    "coding",
    "competencies",
    "confidence_threshold",
    "difficulty_distribution",
    "duration_minutes",
    "expected_difficulty",
    "instructions",
    "interviewer_notes",
    "max_duration_minutes",
    "min_evidence_per_competency",
    "name",
    "passing_guidance",
    "question_strategy",
    "resume_validation",
    "round_type",
    "rubric",
    "stopping",
  ],
  RoundCompetency: ["focus_areas", "key", "min_evidence", "name", "required", "target_difficulty", "weight"],
  RubricCategory: ["competency_keys", "description", "dimensions", "key", "label", "weight"],
  DifficultyDistribution: ["easy", "hard", "medium"],
  QuestionStrategy: ["adaptive_difficulty", "allow_candidate_questions_at_end", "allow_generated_questions", "max_followup_depth", "prefer_curated_ratio", "style"],
  StoppingConditions: ["max_questions", "min_duration_minutes", "min_questions", "stop_on_sufficient_evidence"],
  CodingSection: [
    "allow_generated",
    "difficulty",
    "duration_minutes",
    "enabled",
    "hint3_may_reveal_solution",
    "hints_enabled",
    "languages",
    "large_paste_threshold_chars",
    "max_hints",
    "max_problems",
    "min_seconds_between_comments",
    "proactive_comments",
    "question_ids",
    "restrict_paste",
    "start_after_minutes",
  ],
  ResumeValidation: ["enabled", "max_claims", "min_priority"],
  PassingGuidance: ["borderline_min_score", "min_confidence", "min_required_coverage", "proceed_min_score", "reviewer_notes", "strong_proceed_min_score"],
};

const sorted = (o: object) => Object.keys(o).sort();

function sample(): RoundConfig {
  const config = defaultRoundConfig("combined", "  Combined AI Round  ");
  config.duration_minutes = 30;
  config.competencies = [
    { key: "kubernetes", name: "Kubernetes", weight: 1.2, required: true, min_evidence: null, target_difficulty: null, focus_areas: [" scaling ", ""] },
    { key: "aws", name: "", weight: 1, required: true, min_evidence: 2, target_difficulty: "hard", focus_areas: [] },
  ];
  config.rubric = [
    { key: "technical", label: "Technical Knowledge", weight: 60, competency_keys: ["kubernetes", "aws"], dimensions: ["depth"], description: "  " },
    { key: "communication", label: "Communication", weight: 40, competency_keys: [], dimensions: ["communication_clarity"], description: "Clear answers" },
  ];
  config.coding = { ...config.coding, enabled: true, duration_minutes: 12, languages: ["python", "go"], question_ids: ["q1"] };
  config.interviewer_notes = "   ";
  return config;
}

describe("toRoundConfig (UI draft -> RoundConfig JSON)", () => {
  it("emits exactly the schema keys at every level", () => {
    const out = toRoundConfig(sample());
    expect(sorted(out)).toEqual(SCHEMA_KEYS.RoundConfig);
    expect(sorted(out.competencies[0]!)).toEqual(SCHEMA_KEYS.RoundCompetency);
    expect(sorted(out.rubric[0]!)).toEqual(SCHEMA_KEYS.RubricCategory);
    expect(sorted(out.difficulty_distribution)).toEqual(SCHEMA_KEYS.DifficultyDistribution);
    expect(sorted(out.question_strategy)).toEqual(SCHEMA_KEYS.QuestionStrategy);
    expect(sorted(out.stopping)).toEqual(SCHEMA_KEYS.StoppingConditions);
    expect(sorted(out.coding)).toEqual(SCHEMA_KEYS.CodingSection);
    expect(sorted(out.resume_validation)).toEqual(SCHEMA_KEYS.ResumeValidation);
    expect(sorted(out.passing_guidance)).toEqual(SCHEMA_KEYS.PassingGuidance);
  });

  it("drops unknown keys a stored config may carry", () => {
    const loaded = withDefaults({ ...sample(), legacy_field: true, coding: { ...sample().coding, editor_theme: "dark" } } as unknown as RoundConfig);
    const out = toRoundConfig(loaded) as unknown as Record<string, unknown>;
    expect(out.legacy_field).toBeUndefined();
    expect((out.coding as Record<string, unknown>).editor_theme).toBeUndefined();
  });

  it("trims text, turns blank optional text into null and cleans lists", () => {
    const out = toRoundConfig(sample());
    expect(out.name).toBe("Combined AI Round");
    expect(out.interviewer_notes).toBeNull();
    expect(out.competencies[0]!.focus_areas).toEqual(["scaling"]);
    expect(out.competencies[1]!.name).toBeNull();
    expect(out.rubric[0]!.description).toBeNull();
    expect(out.rubric[1]!.description).toBe("Clear answers");
  });

  it("serialises the difficulty mix as fractions summing to 1", () => {
    const config = sample();
    config.difficulty_distribution = { easy: 0.2, medium: 0.5, hard: 0.3 };
    const out = toRoundConfig(config);
    expect(out.difficulty_distribution).toEqual({ easy: 0.2, medium: 0.5, hard: 0.3 });
  });

  it("never sends a maximum shorter than the duration and restores required defaults", () => {
    const config = sample();
    config.max_duration_minutes = 20;
    config.instructions = "  ";
    config.passing_guidance.reviewer_notes = "";
    const out = toRoundConfig(config);
    expect(out.max_duration_minutes).toBe(30);
    expect(out.instructions.length).toBeGreaterThan(10);
    expect(out.passing_guidance.reviewer_notes).toMatch(/human reviewer/i);
  });

  it("derives the round row columns from the config", () => {
    expect(roundColumns(sample())).toEqual({
      name: "Combined AI Round",
      round_type: "combined",
      duration_minutes: 30,
      max_duration_minutes: 40,
      expected_difficulty: "medium",
      candidate_selectable: false,
      instructions: expect.any(String),
    });
  });
});

describe("withDefaults (partial config -> complete editor model)", () => {
  it("fills every section from backend defaults", () => {
    const config = withDefaults({ duration_minutes: 15 } as Partial<RoundConfig>, { round_type: "behavioral", name: "Behavioral" });
    expect(config.round_type).toBe("behavioral");
    expect(config.duration_minutes).toBe(15);
    expect(config.max_duration_minutes).toBe(40);
    expect(config.coding.enabled).toBe(false);
    expect(config.coding.languages).toContain("python");
    expect(config.question_strategy.style).toEqual(["experience_deep_dive", "scenario", "conceptual"]);
    expect(config.passing_guidance.proceed_min_score).toBe(65);
    expect(sorted(toRoundConfig(config))).toEqual(SCHEMA_KEYS.RoundConfig);
  });

  it("keeps template values and normalises the difficulty mix", () => {
    const config = withDefaults({
      round_type: "resume_screening",
      name: "Resume Screening",
      difficulty_distribution: { easy: 2, medium: 1, hard: 1 },
      competencies: [{ key: "ownership", weight: 1, focus_areas: ["outcomes"] }],
      rubric: [{ key: "claims", label: "Claims", weight: 100, dimensions: ["consistency"] }],
    } as unknown as Partial<RoundConfig>);
    expect(config.difficulty_distribution).toEqual({ easy: 0.5, medium: 0.25, hard: 0.25 });
    expect(config.competencies[0]).toEqual({
      key: "ownership",
      name: null,
      weight: 1,
      required: true,
      min_evidence: null,
      target_difficulty: null,
      focus_areas: ["outcomes"],
    });
    expect(config.rubric[0]!.competency_keys).toEqual([]);
  });

  it("enables coding by default for coding rounds and survives garbage", () => {
    expect(withDefaults(null, { round_type: "coding" }).coding.enabled).toBe(true);
    const config = withDefaults({ coding: { languages: ["python", "cobol"] }, competencies: [null, { weight: 2 }] } as unknown as Partial<RoundConfig>);
    expect(config.coding.languages).toEqual(["python"]);
    expect(config.competencies).toEqual([]);
  });
});

describe("validateRoundConfig", () => {
  it("accepts a sound configuration", () => {
    const issues = validateRoundConfig(sample());
    expect(hasBlockingIssues(issues)).toBe(false);
  });

  it("flags rubric weights that don't sum to 100", () => {
    const config = sample();
    config.rubric[1]!.weight = 30;
    const errors = issuesToErrors(validateRoundConfig(config));
    expect(errors.rubric).toBe("Weights add up to 90%. Assign 10 more points to reach 100%.");
  });

  it("flags duplicate competencies, bad ranges and inverted thresholds", () => {
    const config = sample();
    config.competencies.push({ ...config.competencies[0]! });
    config.competencies[1]!.weight = 12;
    config.duration_minutes = 3;
    config.passing_guidance.proceed_min_score = 90;
    config.coding.languages = [];
    const errors = issuesToErrors(validateRoundConfig(config));
    expect(errors["competencies.2.key"]).toMatch(/already in the round/);
    expect(errors["competencies.1.weight"]).toMatch(/between 0 and 10/);
    expect(errors.duration_minutes).toMatch(/between 5 and 240/);
    expect(errors.passing_guidance).toMatch(/strong ≥ proceed ≥ borderline/);
    expect(errors["coding.languages"]).toMatch(/at least one language/);
  });

  it("separates warnings from blocking errors", () => {
    const config = sample();
    config.rubric[0]!.competency_keys = ["kubernetes", "terraform"];
    const issues = validateRoundConfig(config);
    expect(hasBlockingIssues(issues)).toBe(false);
    expect(issuesToErrors(issues, "warning")["rubric.0.competency_keys"]).toMatch(/terraform/);
  });

  it("checks coding time against the round and the stopping bounds", () => {
    const config = sample();
    config.coding.duration_minutes = 45;
    config.stopping.max_questions = 2;
    config.stopping.min_questions = 4;
    const errors = issuesToErrors(validateRoundConfig(config));
    expect(errors["coding.duration_minutes"]).toMatch(/longer than the whole round/);
    expect(errors["stopping.max_questions"]).toMatch(/can't be lower/);
  });
});

describe("sections and server errors", () => {
  it("routes config paths to editor tabs", () => {
    expect(sectionForPath("rubric.0.weight")).toBe("scoring");
    expect(sectionForPath("coding.languages")).toBe("coding");
    expect(sectionForPath("stopping.max_questions")).toBe("flow");
    expect(sectionForPath("instructions")).toBe("candidate");
    expect(sectionForPath("competencies.1.key")).toBe("setup");
    expect(countBySection(["rubric", "coding.languages", "name", "rubric.1.label"])).toEqual({ setup: 1, scoring: 2, flow: 0, coding: 1, candidate: 0 });
  });

  it("maps pydantic errors from a round payload onto config paths", () => {
    const error = new ApiError({
      status: 422,
      code: "validation_failed",
      message: "Some fields need attention.",
      details: {
        errors: [
          { loc: ["body", "config", "rubric"], msg: "Value error, rubric weights must sum to 100 (got 90)" },
          { loc: ["body", "config", "competencies", 0, "weight"], msg: "Input should be less than or equal to 10" },
          { loc: ["body", "name"], msg: "Field required" },
        ],
      },
    });
    const errors = rebaseErrors(fieldErrorsOf(error), "config");
    expect(errors.rubric).toBe("Rubric weights must sum to 100 (got 90)");
    expect(errors["competencies.0.weight"]).toBe("Input should be less than or equal to 10");
    expect(errors.name).toBe("Field required");
  });

  it("maps domain errors that name a single field", () => {
    const error = new ApiError({ status: 409, code: "conflict", message: "A user with this email already exists.", details: { field: "email" } });
    expect(fieldErrorsOf(error)).toEqual({ email: "A user with this email already exists." });
    const weak = new ApiError({
      status: 422,
      code: "weak_password",
      message: "Password does not meet the password policy.",
      details: { field: "password", problems: ["Password must be at least 12 characters long.", "Password is too common."] },
    });
    expect(fieldErrorsOf(weak).password).toBe("Password must be at least 12 characters long. Password is too common.");
  });
});
