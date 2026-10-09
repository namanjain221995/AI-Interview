import { describe, expect, it } from "vitest";

import type { Competency, Job, JobRequirement, Round } from "@/lib/types";

import { deriveCodingRequirements, emptyJobDraft, jobDraftFrom, newRoundDraft, planRequirements, planRounds, roundCreatePayload, roundUpdatePayload, toJobInput, validateJobDraft } from "../jobs/jobDraft";
import { defaultRoundConfig } from "../rounds/roundConfig";

const NOW = "2026-10-07T10:00:00Z";

function competency(key: string, name: string): Competency {
  return { id: key, organization_id: null, key, name, category: "technical", description: null, rubric: {}, is_active: true, created_at: NOW, updated_at: NOW };
}

const catalog = [competency("kubernetes", "Kubernetes"), competency("aws", "Amazon Web Services"), competency("terraform", "Terraform")];

function requirement(id: string, kind: JobRequirement["kind"], name: string, competency_key: string | null, sort_order = 0): JobRequirement {
  return { id, job_id: "j1", kind, name, competency_key, description: null, weight: 1, min_years: null, sort_order, created_at: NOW, updated_at: NOW };
}

function round(id: string, sequence: number, name: string): Round {
  const config = defaultRoundConfig("technical_screening", name);
  return {
    id,
    job_id: "j1",
    template_id: null,
    name,
    round_type: config.round_type,
    sequence,
    duration_minutes: config.duration_minutes,
    max_duration_minutes: config.max_duration_minutes,
    expected_difficulty: config.expected_difficulty,
    candidate_selectable: false,
    is_active: true,
    instructions: config.instructions,
    config,
    created_at: NOW,
    updated_at: NOW,
  };
}

const job: Job = {
  id: "j1",
  organization_id: "o1",
  title: "Senior DevOps Engineer",
  department: "Platform",
  location: "Remote",
  employment_type: "full_time",
  seniority: "senior",
  description: "## Role",
  experience_min_years: 5,
  experience_max_years: 10,
  status: "open",
  interview_instructions: null,
  coding_requirements: {},
  integrity_settings: { require_fullscreen: false },
  recording_settings: { record_video: true, retention: { video_days: 14 } },
  candidate_report_mode: "immediate",
  allow_candidate_round_selection: false,
  created_by_id: null,
  created_at: NOW,
  updated_at: NOW,
};

describe("jobDraftFrom / toJobInput", () => {
  it("splits recording overrides from nested retention and round-trips them", () => {
    const draft = jobDraftFrom(job, [requirement("r1", "required_skill", "Kubernetes", "kubernetes")], [round("a", 1, "Screen")]);
    expect(draft.recording_overrides).toEqual({ record_video: true });
    expect(draft.retention_overrides).toEqual({ video_days: 14 });
    expect(draft.required_skills).toEqual(["kubernetes"]);
    const input = toJobInput(draft, "after_review");
    expect(input.recording_settings).toEqual({ record_video: true, retention: { video_days: 14 } });
    expect(input.integrity_settings).toEqual({ require_fullscreen: false });
    expect(input.candidate_report_mode).toBe("immediate");
  });

  it("sends blanks as null and falls back to the organization report mode", () => {
    const draft = { ...emptyJobDraft(), title: "  Data Engineer ", department: "  ", candidate_report_mode: null };
    const input = toJobInput(draft, "never");
    expect(input.title).toBe("Data Engineer");
    expect(input.department).toBeNull();
    expect(input.candidate_report_mode).toBe("never");
    expect(input.recording_settings).toEqual({});
  });

  it("derives the coding summary from active rounds with coding", () => {
    const a = newRoundDraft({ ...defaultRoundConfig("coding"), coding: { ...defaultRoundConfig("coding").coding, enabled: true, languages: ["python", "go"], difficulty: "easy" } });
    const b = newRoundDraft({ ...defaultRoundConfig("coding"), coding: { ...defaultRoundConfig("coding").coding, enabled: true, languages: ["go", "java"], difficulty: "hard" } });
    const off = { ...newRoundDraft(defaultRoundConfig("coding")), is_active: false };
    expect(deriveCodingRequirements([a, b, off])).toEqual({ enabled: true, languages: ["python", "go", "java"], difficulty: "hard" });
    expect(deriveCodingRequirements([])).toEqual({ enabled: false, languages: [], difficulty: "medium" });
  });
});

describe("planRequirements", () => {
  it("links catalog skills, keeps free text, and only touches skill rows", () => {
    const existing = [
      requirement("r1", "required_skill", "Kubernetes", "kubernetes", 0),
      requirement("r2", "required_skill", "Bash", null, 1),
      requirement("r3", "preferred_skill", "Go", null, 0),
      requirement("r4", "responsibility", "On-call", null, 0),
    ];
    const draft = { ...emptyJobDraft(), required_skills: ["aws", "kubernetes"], preferred_skills: ["go", "Cost optimisation"] };
    const plan = planRequirements(draft, existing, catalog);
    expect(plan.create).toEqual([
      { kind: "required_skill", name: "Amazon Web Services", competency_key: "aws", weight: 1, sort_order: 0 },
      { kind: "preferred_skill", name: "Cost optimisation", competency_key: null, weight: 0.5, sort_order: 1 },
    ]);
    // kubernetes moved from position 0 to 1; "go" matches "Go" case-insensitively
    expect(plan.update).toEqual([{ id: "r1", input: { sort_order: 1 } }]);
    expect(plan.remove).toEqual(["r2"]);
  });

  it("is a no-op when nothing changed", () => {
    const existing = [requirement("r1", "required_skill", "Kubernetes", "kubernetes", 0)];
    const plan = planRequirements({ ...emptyJobDraft(), required_skills: ["kubernetes"] }, existing, catalog);
    expect(plan).toEqual({ create: [], update: [], remove: [] });
  });
});

describe("planRounds", () => {
  const server = [round("a", 1, "Screen"), round("b", 2, "Deep dive"), round("c", 3, "Behavioral")];

  it("updates only changed rounds, deletes removed ones and creates new ones", () => {
    const draft = jobDraftFrom(job, [], server);
    draft.rounds[0] = { ...draft.rounds[0]!, config: { ...draft.rounds[0]!.config, duration_minutes: 45 } };
    draft.rounds.splice(2, 1);
    const extra = newRoundDraft(defaultRoundConfig("system_design", "Design"));
    draft.rounds.push(extra);
    const plan = planRounds(draft, server);
    expect(plan.update.map((r) => r.id)).toEqual(["a"]);
    expect(plan.remove).toEqual(["c"]);
    expect(plan.create.map((r) => r.uid)).toEqual([extra.uid]);
    expect(plan.reorder).toBe(false); // new rounds are appended, which matches the draft
  });

  it("reorders when the sequence changed or a new round sits before saved ones", () => {
    const draft = jobDraftFrom(job, [], server);
    draft.rounds.reverse();
    expect(planRounds(draft, server).reorder).toBe(true);
    const again = jobDraftFrom(job, [], server);
    again.rounds.unshift(newRoundDraft(defaultRoundConfig("behavioral")));
    expect(planRounds(again, server).reorder).toBe(true);
  });

  it("sends round columns in sync with the config", () => {
    const draft = { ...newRoundDraft({ ...defaultRoundConfig("coding", "  Live coding "), duration_minutes: 50, max_duration_minutes: 40 }, "tpl-1") };
    const payload = roundCreatePayload(draft);
    expect(payload.name).toBe("Live coding");
    expect(payload.duration_minutes).toBe(50);
    expect(payload.max_duration_minutes).toBe(50);
    expect(payload.config?.name).toBe("Live coding");
    expect(payload.round_type).toBe("coding");
    expect(payload.template_id).toBe("tpl-1");
  });

  it("replaces the whole config on update and never sends template_id (RoundUpdate forbids it)", () => {
    const payload = roundUpdatePayload(newRoundDraft(defaultRoundConfig("behavioral"), "tpl-1")) as Record<string, unknown>;
    expect(payload.replace_config).toBe(true);
    expect("template_id" in payload).toBe(false);
    expect(Object.keys(payload).sort()).toEqual(
      ["candidate_selectable", "config", "duration_minutes", "expected_difficulty", "instructions", "is_active", "max_duration_minutes", "name", "replace_config", "round_type"].sort(),
    );
  });
});

describe("validateJobDraft", () => {
  it("requires a title and consistent experience bounds", () => {
    const issues = validateJobDraft({ ...emptyJobDraft(), experience_min_years: 8, experience_max_years: 3 });
    const errors = issues.filter((i) => i.severity === "error").map((i) => i.path);
    expect(errors).toEqual(expect.arrayContaining(["title", "experience_max_years"]));
  });

  it("blocks opening a job without rounds and surfaces round problems with their round", () => {
    const bad = newRoundDraft({ ...defaultRoundConfig("technical_screening"), rubric: [{ key: "a", label: "A", weight: 70, competency_keys: [], dimensions: ["depth"], description: null }] });
    const open = validateJobDraft({ ...emptyJobDraft(), title: "SRE", status: "open" });
    expect(open.some((i) => i.path === "rounds" && i.severity === "error")).toBe(true);
    const withRound = validateJobDraft({ ...emptyJobDraft(), title: "SRE", rounds: [bad] });
    const rubric = withRound.find((i) => i.roundUid === bad.uid && i.path.endsWith(".rubric"));
    expect(rubric?.message).toMatch(/70%/);
  });

  it("rejects a skill listed as both required and preferred", () => {
    const issues = validateJobDraft({ ...emptyJobDraft(), title: "SRE", required_skills: ["aws"], preferred_skills: ["AWS"] });
    expect(issues.find((i) => i.path === "preferred_skills")?.severity).toBe("error");
  });
});
