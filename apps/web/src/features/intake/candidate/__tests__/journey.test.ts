import { describe, expect, it } from "vitest";

import { normalizeInterview, normalizeInvite, normalizeStepKey, type PortalInterviewView } from "@/features/candidate/api";
import { completedSteps, journeySteps, requiredRedirect, resumeStep, stepAfter, stepBefore } from "@/features/candidate/journey";

import { INTAKE_GUARD_STEPS, intakeRedirect, isIntakeInterview } from "../journey";

function intakeInterview(over: Partial<PortalInterviewView> = {}): PortalInterviewView {
  return {
    ...normalizeInterview({
      id: "i1",
      status: "invited",
      organization: { name: "TechSara Solutions" },
      job: { id: "j1", title: "AI/ML Training Program — Intake" },
      rounds_offered: [{ id: "r1", name: "Intake Interview", round_type: "intake", duration_minutes: 45, expected_difficulty: "medium" }],
      selected_round_id: "r1",
      allow_round_selection: false,
      policy: {},
      steps_completed: [],
      expires_at: "2026-10-16T00:00:00Z",
      intake: { intake_id: "in1", title: "Intake Interview", estimated_minutes: 45, presence: "orb" },
    }),
    ...over,
  };
}

function combinedInterview(): PortalInterviewView {
  return normalizeInterview({
    id: "i2",
    status: "invited",
    organization: { name: "TechSara Solutions" },
    job: { id: "j2", title: "Senior DevOps Engineer" },
    rounds_offered: [{ id: "r2", name: "Combined AI Round", round_type: "combined", duration_minutes: 30 }],
    selected_round_id: "r2",
    allow_round_selection: false,
    policy: {},
    steps_completed: [],
    expires_at: "2026-10-16T00:00:00Z",
  });
}

describe("intake journey", () => {
  it("adds the details form and the résumé review between résumé and system check, and never a round step", () => {
    expect(journeySteps(intakeInterview()).map((s) => s.key)).toEqual(["welcome", "instructions", "consent", "resume", "details", "resume-review", "system-check", "interview"]);
    expect(journeySteps(intakeInterview()).find((s) => s.key === "details")?.label).toBe("Your details");
    expect(journeySteps(intakeInterview()).find((s) => s.key === "resume-review")?.label).toBe("Check résumé");
    expect(stepAfter("resume", intakeInterview())).toBe("details");
    expect(stepAfter("details", intakeInterview())).toBe("resume-review");
    expect(stepAfter("resume-review", intakeInterview())).toBe("system-check");
    expect(stepBefore("system-check", intakeInterview())).toBe("resume-review");
  });

  it("leaves every other round type unchanged", () => {
    expect(journeySteps(combinedInterview()).map((s) => s.key)).toEqual(["welcome", "instructions", "consent", "resume", "system-check", "interview"]);
    expect(journeySteps(null).map((s) => s.key)).not.toContain("details");
    expect(stepAfter("resume", combinedInterview())).toBe("system-check");
    expect(requiredRedirect("system-check", { ...combinedInterview(), stepsCompleted: ["consent"] })).toBeNull();
  });

  it("recognises intake from the portal brief or the round type", () => {
    expect(isIntakeInterview(intakeInterview())).toBe(true);
    expect(isIntakeInterview({ ...intakeInterview(), intake: null })).toBe(true);
    expect(isIntakeInterview(combinedInterview())).toBe(false);
    expect(isIntakeInterview(null)).toBe(false);
    // Several rounds and none chosen yet: not decided by the client.
    expect(isIntakeInterview({ intake: null, rounds: [...intakeInterview().rounds, ...combinedInterview().rounds], selectedRoundId: null })).toBe(false);
  });

  it("resumes at the first intake step still to do", () => {
    expect(resumeStep(intakeInterview({ stepsCompleted: ["consent", "resume"] }))).toBe("details");
    expect(resumeStep(intakeInterview({ stepsCompleted: ["consent", "resume", "details"] }))).toBe("resume-review");
    expect(resumeStep(intakeInterview({ stepsCompleted: ["consent", "resume", "details", "resume-review"] }))).toBe("system-check");
    expect(completedSteps(intakeInterview({ stepsCompleted: ["consent", "details"] }))).toEqual(["welcome", "instructions", "consent", "details"]);
  });

  it("sends intake candidates to the missing step before the system check and the interview", () => {
    const base = intakeInterview();
    expect(requiredRedirect("details", { ...base, stepsCompleted: [] })).toBe("consent");
    expect(requiredRedirect("resume-review", { ...base, stepsCompleted: [] })).toBe("consent");
    expect(requiredRedirect("system-check", { ...base, stepsCompleted: ["consent", "resume"] })).toBe("details");
    expect(requiredRedirect("interview", { ...base, stepsCompleted: ["consent", "resume", "details"] })).toBe("resume-review");
    expect(requiredRedirect("system-check", { ...base, stepsCompleted: ["consent", "resume", "details", "resume-review"] })).toBeNull();
    expect(requiredRedirect("details", { ...base, stepsCompleted: ["consent"] })).toBeNull();
    expect(intakeRedirect("resume", new Set())).toBeNull();
  });

  it("maps the start-guard codes to the step that fixes them", () => {
    expect(INTAKE_GUARD_STEPS.intake_onboarding_required).toBe("details");
    expect(INTAKE_GUARD_STEPS.intake_resume_required).toBe("resume");
    expect(INTAKE_GUARD_STEPS.intake_resume_review_required).toBe("resume-review");
  });

  it("keeps the résumé review step key distinct from the résumé upload", () => {
    expect(["resume_review", "resume-review", "details", "onboarding_submitted", "resume_uploaded"].map(normalizeStepKey)).toEqual(["resume-review", "resume-review", "details", "details", "resume"]);
    expect(intakeInterview({}).stepsCompleted).toEqual([]);
    expect(normalizeInterview({ id: "x", steps_completed: ["consent", "resume", "resume_review", "details"] }).stepsCompleted).toEqual(["consent", "resume", "resume-review", "details"]);
  });

  it("reads the intake brief and the invite's round type", () => {
    expect(intakeInterview().intake).toEqual({ intakeId: "in1", title: "Intake Interview", estimatedMinutes: 45, presence: "orb" });
    expect(combinedInterview().intake).toBeNull();
    expect(normalizeInvite({ organization_name: "TechSara Solutions", job_title: "AI/ML Training Program", round_type: "intake", duration_minutes: 45 }).roundType).toBe("intake");
    expect(normalizeInvite({ organization_name: "TechSara Solutions", job_title: "Engineer" }).roundType).toBeNull();
  });
});
