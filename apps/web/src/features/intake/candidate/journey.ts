/**
 * Intake journey rules (INTAKE_INTERVIEW.md §11.2): welcome → instructions →
 * consent → résumé → details → résumé review → system check → interview, with
 * the round step hidden. Pure functions over the portal interview view.
 */
import type { PortalInterviewView } from "@/features/candidate/api";

/** Journey steps that exist only for intake interviews. */
export const INTAKE_ONLY_STEPS: readonly string[] = ["details", "resume-review"];

export type IntakeShape = Partial<Pick<PortalInterviewView, "intake" | "rounds" | "selectedRoundId">>;

/**
 * The interview runs an intake round: the portal sends `intake`, or the round the
 * candidate will run (the selected one, else the only one offered) is of type intake.
 */
export function isIntakeInterview(interview: IntakeShape | null | undefined): boolean {
  if (!interview) return false;
  if (interview.intake) return true;
  const rounds = interview.rounds ?? [];
  const selected = rounds.find((r) => r.id === interview.selectedRoundId) ?? (rounds.length === 1 ? rounds[0] : undefined);
  return selected?.roundType === "intake";
}

/**
 * The step an intake candidate still owes before the system check and the
 * interview (the server's start guard enforces the same: 409
 * `intake_onboarding_required` / `intake_resume_review_required`).
 */
export function intakeRedirect(step: string, done: ReadonlySet<string>): "details" | "resume-review" | null {
  if (step !== "system-check" && step !== "interview") return null;
  if (!done.has("details")) return "details";
  if (!done.has("resume-review")) return "resume-review";
  return null;
}

/** Start-guard error codes of `POST /portal/interviews/{id}/session` and the step that fixes each. */
export const INTAKE_GUARD_STEPS: Record<string, "details" | "resume" | "resume-review"> = {
  intake_onboarding_required: "details",
  intake_resume_required: "resume",
  intake_resume_review_required: "resume-review",
};
