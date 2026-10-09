import { describe, expect, it } from "vitest";

import { codingDraftFrom, emptyCodingDraft, matchesType, signaturePreview, starterTemplate, toCodingInput, validateCodingDraft } from "../coding/codingModel";
import { emptyQuestionDraft, mapQuestionServerErrors, toQuestionInput, validateQuestion } from "../question-bank/questionModel";
import { defaultOrganizationSettings, describeDays, toOrganizationSettings, validateSettings, withSettingsDefaults } from "../settings/settingsModel";
import { assignableRoles, checkDeactivate, checkRoleChange, passwordHints } from "../users/userRules";
import type { CodingQuestion } from "@/lib/types";

// ------------------------------------------------------------------ coding

function twoSumDraft() {
  const draft = emptyCodingDraft("function");
  draft.title = "Two Sum";
  draft.slug = "two_sum";
  draft.prompt_markdown = "## Two Sum\n\nReturn the indices of the two numbers that add up to target.";
  draft.signature = { function_name: "two_sum", params: [{ name: "nums", type: "int[]" }, { name: "target", type: "int" }], return_type: "int[]", comparison: "unordered" };
  draft.languages = ["python", "go"];
  draft.tests = [
    { id: "t1", hidden: false, description: "Example", argsText: "[[2, 7, 11, 15], 9]", expectedText: "[0, 1]", setupSql: "", expectedRowsText: "", ordered: false },
    { id: "t2", hidden: true, description: "", argsText: "[[3, 3], 6]", expectedText: "[0, 1]", setupSql: "", expectedRowsText: "", ordered: false },
  ];
  draft.hints = ["Brute force first.", "Store what you've seen.", "Check the complement before inserting."];
  return draft;
}

describe("coding model", () => {
  it("accepts a sound function problem (warnings only)", () => {
    const { errors, warnings } = validateCodingDraft(twoSumDraft());
    expect(errors).toEqual({});
    expect(warnings.tests).toMatch(/2 visible and 5 hidden/);
    expect(warnings["reference_solutions.python"]).toBeDefined();
  });

  it("treats hints as ordered levels; empty levels are written live (API allows up to three)", () => {
    const draft = twoSumDraft();
    draft.hints = ["One", "", "Three"];
    expect(validateCodingDraft(draft).errors["hints.2"]).toBe("Write hint 2 first: hints are given in order.");
    draft.hints = ["One", "", ""];
    const partial = validateCodingDraft(draft);
    expect(partial.errors).toEqual({});
    expect(partial.warnings.hints).toBe("Hint 2 and 3 will be written live when a candidate asks.");
    expect(toCodingInput(draft).hints).toEqual(["One"]);
    draft.hints = ["", "", ""];
    expect(validateCodingDraft(draft).warnings.hints).toMatch(/writes all three live/);
  });

  it("checks test arguments against the signature types and arity", () => {
    const draft = twoSumDraft();
    draft.tests[0]!.argsText = "[[2, 7], 9, 1]";
    draft.tests[1]!.argsText = '[["a"], 6]';
    draft.tests.push({ ...draft.tests[1]!, id: "t3", argsText: "[[1,2], 3", expectedText: "true" });
    const { errors } = validateCodingDraft(draft);
    expect(errors["tests.0.args"]).toBe("Expected 2 arguments, got 3.");
    expect(errors["tests.1.args"]).toBe("Argument 1 (nums) isn't a valid int[].");
    expect(errors["tests.2.args"]).toMatch(/Not valid JSON/);
    expect(errors["tests.2.expected"]).toBe("Isn't a valid int[].");
  });

  it("requires a visible test and unique ids, and advises hidden tests", () => {
    const draft = twoSumDraft();
    draft.tests = draft.tests.map((t) => ({ ...t, hidden: false, id: "t1" }));
    const { errors, warnings } = validateCodingDraft(draft);
    expect(warnings.tests).toMatch(/No hidden tests/);
    expect(errors["tests.1.id"]).toMatch(/unique/);
    draft.tests = draft.tests.map((t, i) => ({ ...t, hidden: true, id: `t${i}` }));
    expect(validateCodingDraft(draft).errors.tests).toMatch(/at least one visible test/);
    draft.tests[0]!.id = "T-1";
    expect(validateCodingDraft(draft).errors["tests.0.id"]).toMatch(/Lowercase letters/);
  });

  it("mirrors the API's signature rules (snake_case, reserved words, comparison/return type)", () => {
    const draft = twoSumDraft();
    draft.slug = "two-sum";
    draft.signature = { ...draft.signature, function_name: "class", return_type: "int", comparison: "unordered" };
    draft.signature.params = [{ name: "nums", type: "int[]" }, { name: "default", type: "int" }];
    const { errors } = validateCodingDraft(draft);
    expect(errors.slug).toBeDefined();
    expect(errors["signature.function_name"]).toMatch(/reserved word/);
    expect(errors["signature.params.1.name"]).toMatch(/reserved word/);
    expect(errors["signature.comparison"]).toMatch(/array return type/);
  });

  it("mirrors the backend type rules (int32 bounds, bool is not a number)", () => {
    expect(matchesType(2 ** 31, "int")).toBe(false);
    expect(matchesType(2 ** 31, "long")).toBe(true);
    expect(matchesType(true, "int")).toBe(false);
    expect(matchesType([[1], [2, 3]], "int[][]")).toBe(true);
    expect(matchesType(1.5, "float")).toBe(true);
  });

  it("serialises to the API shape with parsed tests and only enabled languages", () => {
    const draft = twoSumDraft();
    draft.starter_code = { python: "def two_sum(nums, target):\n    return []\n", java: "class Solution {}" };
    const input = toCodingInput(draft);
    expect(input.tests![0]).toEqual({ id: "t1", hidden: false, description: "Example", args: [[2, 7, 11, 15], 9], expected: [0, 1] });
    expect(input.tests![1]).toEqual({ id: "t2", hidden: true, args: [[3, 3], 6], expected: [0, 1] });
    expect(input.starter_code).toEqual({ python: "def two_sum(nums, target):\n    return []\n" });
    expect(input.sql_setup).toBeNull();
    expect(input.hints).toHaveLength(3);
  });

  it("round-trips SQL problems", () => {
    const q = {
      id: "q1",
      organization_id: null,
      slug: "second_highest_salary",
      title: "Second Highest Salary",
      difficulty: "easy",
      competency_keys: ["sql"],
      role_tags: [],
      prompt_markdown: "## Second highest",
      languages: ["sql"],
      signature: {},
      starter_code: { sql: "SELECT 1;" },
      reference_solutions: { sql: "SELECT MAX(salary) FROM employee;" },
      tests: [{ id: "t1", hidden: false, setup_sql: "INSERT INTO employee VALUES (1);", expected_rows: [[200]] }],
      sql_setup: "CREATE TABLE employee (id INTEGER);",
      expected_concepts: [],
      optimal_complexity: { time: "O(n)" },
      rubric: { excellent: "Great", criteria: { correctness: "All pass" } },
      hints: ["a", "b", "c"],
      followups: [],
      common_errors: [],
      time_limit_minutes: 12,
      source: "curated",
      status: "active",
      created_at: "",
      updated_at: "",
    } as unknown as CodingQuestion;
    const draft = codingDraftFrom(q);
    expect(draft.mode).toBe("sql");
    expect(draft.tests[0]!.expectedRowsText).toBe("[[200]]");
    expect(draft.rubric_criteria).toEqual([{ key: "correctness", value: "All pass" }]);
    const input = toCodingInput(draft);
    expect(input.signature).toBeNull();
    expect(input.tests![0]).toEqual({ id: "t1", hidden: false, setup_sql: "INSERT INTO employee VALUES (1);", expected_rows: [[200]] });
    expect(input.rubric).toEqual({ excellent: "Great", criteria: { correctness: "All pass" } });
  });

  it("previews the entry point per language like the sandbox harness", () => {
    const sig = twoSumDraft().signature;
    expect(signaturePreview(sig, "python")).toBe("def two_sum(nums: list[int], target: int) -> list[int]:");
    expect(signaturePreview(sig, "typescript")).toBe("function twoSum(nums: number[], target: number): number[]");
    expect(signaturePreview(sig, "java")).toBe("public int[] twoSum(int[] nums, int target)");
    expect(signaturePreview(sig, "go")).toBe("func twoSum(nums []int, target int) []int");
    expect(signaturePreview(sig, "cpp")).toBe("vector<int> twoSum(vector<int>& nums, int target)");
    expect(starterTemplate(sig, "java")).toContain("return new int[0];");
    expect(starterTemplate(sig, "go")).toMatch(/^package main/);
    expect(starterTemplate(sig, "cpp")).toContain("class Solution {");
  });
});

// ---------------------------------------------------------- question bank

describe("question model", () => {
  it("validates the essentials", () => {
    const errors = validateQuestion({ ...emptyQuestionDraft(), text: "Why?", topic: "x".repeat(121) });
    expect(errors.competency_key).toBeDefined();
    expect(errors.text).toMatch(/10 characters/);
    expect(errors.topic).toMatch(/120 characters/);
    expect(errors.expected_concepts).toBeDefined();
  });

  it("builds the rubric object and cleans lists", () => {
    const input = toQuestionInput({
      ...emptyQuestionDraft(),
      competency_key: "python",
      text: "  When would you use a generator?  ",
      expected_concepts: ["lazy evaluation", " "],
      rubric_excellent: "Explains memory",
      rubric_dimensions: [{ key: "depth", value: "Goes beyond syntax" }, { key: "", value: "" }],
      followups: ["", "What about send()?"],
    });
    expect(input.text).toBe("When would you use a generator?");
    expect(input.expected_concepts).toEqual(["lazy evaluation"]);
    expect(input.rubric).toEqual({ excellent: "Explains memory", dimensions: { depth: "Goes beyond syntax" } });
    expect(input.followups).toEqual(["What about send()?"]);
  });

  it("maps server rubric paths onto draft fields", () => {
    expect(mapQuestionServerErrors({ "rubric.excellent": "Too long", "text": "Required", "rubric.dimensions.depth": "Bad" })).toEqual({
      rubric_excellent: "Too long",
      text: "Required",
      rubric_dimensions: "Bad",
    });
  });
});

// ---------------------------------------------------------------- settings

describe("settings model", () => {
  it("serialises exactly the OrganizationSettings keys", () => {
    const out = toOrganizationSettings(withSettingsDefaults({ integrity: { enabled: false, unknown: 1 } } as never));
    expect(Object.keys(out).sort()).toEqual(["avatar", "candidate_report_mode", "data_processing_notice", "integrity", "recording", "require_access_code", "retention"]);
    expect(out.integrity.enabled).toBe(false);
    expect((out.integrity as unknown as Record<string, unknown>).unknown).toBeUndefined();
  });

  it("never lets camera observations pause or end an interview", () => {
    const settings = defaultOrganizationSettings();
    settings.integrity.rules = [
      { event_type: "possible_phone_visible", enabled: true, min_confidence: 0.7, min_duration_seconds: 2, min_occurrences: 1, window_seconds: 300, action: "terminate_interview", candidate_message: "  " },
      { event_type: "screen_share_stopped", enabled: true, min_confidence: 0.6, min_duration_seconds: 0, min_occurrences: 1, window_seconds: 60, action: "pause_interview", candidate_message: null },
    ];
    const out = toOrganizationSettings(settings);
    expect(out.integrity.rules[0]!.action).toBe("flag_for_human_review");
    expect(out.integrity.rules[0]!.candidate_message).toBeNull();
    expect(out.integrity.rules[1]!.action).toBe("pause_interview");
  });

  it("validates ranges, the avatar path and screen recording dependencies", () => {
    const settings = defaultOrganizationSettings();
    settings.integrity.camera_frame_interval_seconds = 1;
    settings.retention.audio_days = 0;
    settings.avatar.model_url = "https://cdn.example.com/a.glb";
    settings.recording.record_screen = true;
    const paths = validateSettings(settings).map((i) => i.path);
    expect(paths).toEqual(expect.arrayContaining(["integrity.camera_frame_interval_seconds", "retention.audio_days", "avatar.model_url", "recording.record_screen"]));
    expect(validateSettings(defaultOrganizationSettings())).toEqual([]);
  });

  it("describes retention periods in plain words", () => {
    expect(describeDays(365)).toBe("1 year");
    expect(describeDays(730)).toBe("2 years");
    expect(describeDays(90)).toBe("About 3 months");
    expect(describeDays(14)).toBe("2 weeks");
    expect(describeDays(45)).toBe("45 days");
  });
});

// ------------------------------------------------------------------- users

describe("user rules (mirror of the server)", () => {
  const admin = { id: "me", role: "ADMIN" as const };
  it("never offers super admin to admins", () => {
    expect(assignableRoles("ADMIN")).toEqual(["ADMIN", "RECRUITER", "INTERVIEWER"]);
    expect(assignableRoles("SUPER_ADMIN")).toContain("SUPER_ADMIN");
    expect(assignableRoles("RECRUITER")).toEqual([]);
  });

  it("blocks self-demotion, super admin escalation and removing the last admin", () => {
    expect(checkRoleChange(admin, { id: "me", role: "ADMIN", is_active: true }, "RECRUITER", 3)).toMatchObject({ allowed: false, reason: expect.stringMatching(/your own role/) });
    expect(checkRoleChange(admin, { id: "x", role: "RECRUITER", is_active: true }, "SUPER_ADMIN", 3)).toMatchObject({ allowed: false });
    expect(checkRoleChange(admin, { id: "x", role: "SUPER_ADMIN", is_active: true }, "ADMIN", 3)).toMatchObject({ allowed: false });
    expect(checkRoleChange(admin, { id: "x", role: "ADMIN", is_active: true }, "RECRUITER", 1)).toMatchObject({ allowed: false, reason: expect.stringMatching(/only active administrator/) });
    expect(checkRoleChange(admin, { id: "x", role: "ADMIN", is_active: true }, "RECRUITER", 2)).toEqual({ allowed: true });
    expect(checkDeactivate(admin, { id: "me", role: "ADMIN", is_active: true }, 3)).toMatchObject({ allowed: false });
    expect(checkDeactivate(admin, { id: "x", role: "RECRUITER", is_active: true }, 1)).toEqual({ allowed: true });
  });

  it("gives early password feedback", () => {
    expect(passwordHints("short")).toContain("At least 12 characters.");
    expect(passwordHints("aaaaaaaaaaaaaa")).toContain("Less repetitive.");
    expect(passwordHints("janedoe-rocks-2026", { email: "jane.doe@acme.test", fullName: "Jane Doe" })).toContain("Doesn't contain the person's name or email.");
    expect(passwordHints("Tq7-vR2m!xPz9w")).toEqual([]);
  });
});
