/**
 * Candidate journey: which steps apply, which are complete, where to resume.
 * Pure functions over the portal interview view (+ locally visited
 * informational steps), so resumable progress is unit-tested.
 */
import type { StepperStep } from "@/components/ui/Stepper";
import { INTAKE_ONLY_STEPS, intakeRedirect, isIntakeInterview, type IntakeShape } from "@/features/intake/candidate/journey";
import { CANDIDATE_STEPS } from "@/lib/types";

import type { PortalInterviewView } from "./api";

export type StepKey = (typeof CANDIDATE_STEPS)[number]["key"];

const INFORMATIONAL: readonly StepKey[] = ["welcome", "instructions"];

/** What the journey needs to know about an interview (intake fields optional for hand-built views). */
type JourneyShape = Pick<PortalInterviewView, "allowRoundSelection" | "rounds"> & IntakeShape;

/** Round selection appears only when the candidate may choose between rounds. */
export function roundSelectionApplies(interview: Pick<PortalInterviewView, "allowRoundSelection" | "rounds">): boolean {
  return interview.allowRoundSelection && interview.rounds.length > 1;
}

export function journeySteps(interview: JourneyShape | null): StepperStep[] {
  // Intake interviews add the details form and the résumé review, and never offer a round choice.
  const intake = isIntakeInterview(interview);
  const steps = CANDIDATE_STEPS.map((s) => ({ key: s.key, label: s.label })).filter((s) => intake || !INTAKE_ONLY_STEPS.includes(s.key));
  if (!interview || (!intake && roundSelectionApplies(interview))) return steps;
  return steps.filter((s) => s.key !== "round");
}

export function completedSteps(
  interview: Pick<PortalInterviewView, "stepsCompleted" | "allowRoundSelection" | "rounds" | "selectedRoundId"> & Partial<Pick<PortalInterviewView, "requiresResume" | "intake">>,
  visited: readonly string[] = [],
): StepKey[] {
  const done = new Set<string>(interview.stepsCompleted);
  for (const v of visited) done.add(v);
  // An optional résumé never blocks resuming the journey.
  if (interview.requiresResume === false && done.has("consent")) done.add("resume");
  // Anyone who consented has read the welcome and instructions.
  if (done.has("consent")) INFORMATIONAL.forEach((s) => done.add(s));
  if (roundSelectionApplies(interview) && interview.selectedRoundId) done.add("round");
  return journeySteps(interview)
    .map((s) => s.key as StepKey)
    .filter((key) => done.has(key));
}

/** The first step the candidate still has to do. */
export function resumeStep(interview: PortalInterviewView, visited: readonly string[] = []): StepKey | "complete" {
  if (interview.status === "completed") return "complete";
  const session = interview.latestSession?.status;
  if (session && ["completing", "completed"].includes(session)) return "complete";
  if (session && ["live", "paused"].includes(session)) return "interview";
  const done = new Set(completedSteps(interview, visited));
  for (const step of journeySteps(interview)) {
    if (!done.has(step.key as StepKey)) return step.key as StepKey;
  }
  return "interview";
}

export function stepAfter(step: StepKey, interview: JourneyShape | null): StepKey {
  const steps = journeySteps(interview);
  const index = steps.findIndex((s) => s.key === step);
  return (steps[Math.min(steps.length - 1, index + 1)]?.key ?? "interview") as StepKey;
}

export function stepBefore(step: StepKey, interview: JourneyShape | null): StepKey | null {
  const steps = journeySteps(interview);
  const index = steps.findIndex((s) => s.key === step);
  return index > 0 ? (steps[index - 1]!.key as StepKey) : null;
}

/**
 * Steps that need consent first (intake: also the details form and the résumé
 * review before the system check and the interview). The server enforces this
 * too; redirecting early just saves the candidate a confusing error.
 */
export function requiredRedirect(step: StepKey, interview: Pick<PortalInterviewView, "stepsCompleted"> & IntakeShape): StepKey | null {
  const needsConsent: StepKey[] = ["resume", "round", "details", "resume-review", "system-check", "interview"];
  if (needsConsent.includes(step) && !interview.stepsCompleted.includes("consent")) return "consent";
  if (isIntakeInterview(interview)) return intakeRedirect(step, new Set(interview.stepsCompleted));
  return null;
}

// ------------------------------------------------------- local journey memory

function key(interviewId: string, name: string): string {
  return `interview:${interviewId}:${name}`;
}

export function readLocal<T>(interviewId: string, name: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key(interviewId, name));
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writeLocal(interviewId: string, name: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(key(interviewId, name), JSON.stringify(value));
  } catch {
    /* storage unavailable: progress still comes from the server */
  }
}

export function markVisited(interviewId: string, step: StepKey): void {
  const visited = readLocal<string[]>(interviewId, "visited", []);
  if (!visited.includes(step)) writeLocal(interviewId, "visited", [...visited, step]);
}

export interface ClientPrefs {
  avatarMode: "3d" | "2d" | "none";
  avatarFps: number | null;
  ttsMode: "server" | "browser" | "text";
}

export const DEFAULT_PREFS: ClientPrefs = { avatarMode: "3d", avatarFps: null, ttsMode: "server" };
