/**
 * Candidate-side mirrors of the intake portal DTOs (INTAKE_INTERVIEW.md §15.1,
 * 15.3, 15.4, 15.14). Responses go through the tolerant normalisers in `api.ts`;
 * these types describe the view the pages render and the bodies they send.
 */

// ------------------------------------------------------------------ enumerations (§15.1)

export const TARGET_ROLES = ["ai_ml", "data_analyst", "product_manager_technical", "product_manager_growth", "other"] as const;
export type TargetRole = (typeof TARGET_ROLES)[number];

export const OPEN_TO_AI_ML = ["yes", "maybe", "no"] as const;
export type OpenToAiMl = (typeof OPEN_TO_AI_ML)[number];

export const PRIMARY_LANGUAGES = ["python", "java", "javascript", "csharp", "sql", "other"] as const;
export type PrimaryLanguage = (typeof PRIMARY_LANGUAGES)[number];

export const SCRIPTING_LEVELS = ["none", "some", "regular"] as const;
export type ScriptingLevel = (typeof SCRIPTING_LEVELS)[number];

export const EXPERIENCE_COMFORT = ["yes", "unsure", "no"] as const;
export type ExperienceComfort = (typeof EXPERIENCE_COMFORT)[number];

export const TRAINING_COMMITMENT = ["full", "partial", "cannot"] as const;
export type TrainingCommitment = (typeof TRAINING_COMMITMENT)[number];

export const AVAILABILITY = ["immediately", "within_2_weeks", "within_1_month", "later"] as const;
export type Availability = (typeof AVAILABILITY)[number];

export type PresenceMode = "orb" | "none";

/** The seven stages, in order (kit STAGE_KEYS). */
export const INTAKE_STAGE_KEYS = ["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"] as const;
export type IntakeStageKey = (typeof INTAKE_STAGE_KEYS)[number];

/** Candidate-safe stage titles (server `stages.CANDIDATE_TITLES`); used when the server sends none. */
export const INTAKE_STAGE_TITLES: Record<IntakeStageKey, string> = {
  greeting: "Introduction",
  resume: "Your experience",
  mandatory: "Standard questions",
  reading: "Reading exercise",
  topic: "Speaking exercise",
  learning: "Learning and growth",
  close: "Your questions",
};

/** Kit minutes per stage, the fallback for the overview when the portal sends none. */
export const INTAKE_STAGE_MINUTES: Record<IntakeStageKey, { min: number; max: number }> = {
  greeting: { min: 5, max: 5 },
  resume: { min: 8, max: 10 },
  mandatory: { min: 3, max: 5 },
  reading: { min: 7, max: 8 },
  topic: { min: 7, max: 8 },
  learning: { min: 5, max: 5 },
  close: { min: 3, max: 5 },
};

export const INTAKE_DURATION = { min: 35, max: 50, target: 45 } as const;

// ------------------------------------------------------------------ onboarding (§15.3)

export interface IntakeOnboarding {
  full_name: string;
  target_role: TargetRole;
  target_role_other: string | null;
  open_to_ai_ml: OpenToAiMl;
  technical_background: string;
  primary_language: PrimaryLanguage;
  primary_language_other: string | null;
  self_rating: number;
  scripting: ScriptingLevel;
  scripting_details: string | null;
  data_ml_libraries: string | null;
  years_hands_on: number | null;
  experience_level_comfort: ExperienceComfort;
  training_commitment: TrainingCommitment;
  weekly_hours: number | null;
  availability: Availability;
  available_from: string | null;
  accuracy_confirmed: true;
}

/** Answers to the optional work-authorisation questions; `null` = "I'd rather discuss this with the hiring team". */
export interface EligibilityAnswers {
  authorized: boolean | null;
  needs_sponsorship: boolean | null;
}

/** PUT /portal/intake/{interview_id}/onboarding body. */
export interface IntakeOnboardingIn extends IntakeOnboarding {
  eligibility: EligibilityAnswers | null;
}

export interface EligibilityView {
  enabled: boolean;
  country: string | null;
  askSponsorship: boolean;
  answered: boolean;
  authorized: boolean | null;
  needsSponsorship: boolean | null;
}

export interface IntakePartView {
  number: number;
  key: string;
  title: string;
  minutes: { min: number; max: number } | null;
}

/** GET /portal/intake/{interview_id} (`PortalIntakeOut`). */
export interface PortalIntakeView {
  intakeId: string | null;
  interviewId: string;
  title: string;
  programName: string | null;
  organizationName: string | null;
  estimatedMinutes: number;
  durationRange: { min: number; max: number };
  parts: IntakePartView[];
  presence: PresenceMode;
  /** A role the hiring team set for this interview: shown read-only, and the server stores it on submit. */
  targetRolePreset: TargetRole | null;
  onboarding: Partial<IntakeOnboarding> | null;
  onboardingSubmitted: boolean;
  onboardingLocked: boolean;
  eligibility: EligibilityView;
  resumeRequired: boolean;
  resumeUploaded: boolean;
  resumeReview: { required: boolean; confirmed: boolean; skipped: boolean };
  readyToStart: boolean;
  missing: string[];
}

/** `PortalInterview.intake` (`PortalIntakeBrief`). */
export interface PortalIntakeBriefView {
  intakeId: string | null;
  title: string;
  estimatedMinutes: number | null;
  presence: PresenceMode;
}

// ------------------------------------------------------------------ résumé review (§15.4)

export interface ReviewRole {
  ref: string;
  title: string;
  company: string;
  start: string | null;
  end: string | null;
  responsibilities: string[];
  technologies: string[];
  achievements: string[];
}

export interface ReviewProject {
  ref: string;
  name: string;
  role: string | null;
  description: string;
  technologies: string[];
  outcomes: string[];
}

export interface ReviewEducation {
  ref: string;
  institution: string;
  degree: string | null;
  field: string | null;
}

export interface ResumeReviewDoc {
  version: 1;
  headline: string | null;
  experience_years: number | null;
  roles: ReviewRole[];
  projects: ReviewProject[];
  skills: string[];
  languages: string[];
  tools: string[];
  education: ReviewEducation[];
  certifications: string[];
}

export type ReviewSectionKey = "roles" | "projects" | "skills" | "languages" | "tools" | "education" | "certifications";
export type ReviewSectionState = "pending" | "quick" | "active" | "done" | "fallback";

export interface ReviewSectionStatus {
  key: string;
  label: string;
  state: ReviewSectionState;
}

/** GET/PUT /portal/intake/{interview_id}/resume-review (`ResumeReviewOut`). */
export interface ResumeReviewView {
  status: "waiting" | "ready";
  profileStage: "none" | "quick" | "full";
  sections: ReviewSectionStatus[];
  document: ResumeReviewDoc | null;
  confirmed: boolean;
  skipped: boolean;
  confirmedAt: string | null;
  canSkip: boolean;
  skipAvailableInS: number | null;
}

export interface ResumeReviewIn {
  action: "confirm" | "skip";
  document: ResumeReviewDoc | null;
}

/** Server limits per list (§15.4). */
export const REVIEW_LIMITS = {
  roles: 12,
  projects: 10,
  skills: 40,
  languages: 15,
  tools: 30,
  education: 5,
  certifications: 10,
  responsibilities: 6,
  technologies: 15,
  achievements: 4,
  outcomes: 4,
} as const;
