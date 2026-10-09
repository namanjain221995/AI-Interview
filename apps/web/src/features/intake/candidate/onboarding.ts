/**
 * The intake details form (INTAKE_INTERVIEW.md §15.3): draft state, client
 * validation that mirrors the server schema, and the request body. Pure.
 *
 * Wording rules: ask only what the training program needs (no age, nationality,
 * citizenship or visa type); the work-authorisation questions appear only when the
 * organisation turned them on, and their answers never reach the AI interviewer.
 */
import type {
  Availability,
  EligibilityView,
  ExperienceComfort,
  IntakeOnboarding,
  IntakeOnboardingIn,
  OpenToAiMl,
  PrimaryLanguage,
  ScriptingLevel,
  TargetRole,
  TrainingCommitment,
} from "./types";

export type Choice3 = "yes" | "no" | "discuss";

export interface OnboardingDraft {
  full_name: string;
  target_role: TargetRole | "";
  target_role_other: string;
  open_to_ai_ml: OpenToAiMl | "";
  technical_background: string;
  primary_language: PrimaryLanguage | "";
  primary_language_other: string;
  self_rating: number | null;
  scripting: ScriptingLevel | "";
  scripting_details: string;
  data_ml_libraries: string;
  years_hands_on: string;
  experience_level_comfort: ExperienceComfort | "";
  training_commitment: TrainingCommitment | "";
  weekly_hours: string;
  availability: Availability | "";
  available_from: string;
  accuracy_confirmed: boolean;
  work_authorized: Choice3 | "";
  needs_sponsorship: Choice3 | "";
}

/** Every field with a test id `intake-field-<name>` (UI_TEST_IDS.md, candidate block). */
export type OnboardingField = keyof OnboardingDraft;

export const ONBOARDING_FIELDS: readonly OnboardingField[] = [
  "full_name",
  "target_role",
  "target_role_other",
  "open_to_ai_ml",
  "technical_background",
  "primary_language",
  "primary_language_other",
  "self_rating",
  "scripting",
  "scripting_details",
  "data_ml_libraries",
  "years_hands_on",
  "experience_level_comfort",
  "training_commitment",
  "weekly_hours",
  "availability",
  "available_from",
  "accuracy_confirmed",
  "work_authorized",
  "needs_sponsorship",
];

export const LIMITS = {
  full_name: 200,
  target_role_other: 80,
  technical_background: 1500,
  primary_language_other: 40,
  scripting_details: 500,
  data_ml_libraries: 300,
} as const;

function fromBool(value: boolean | null | undefined, answered: boolean): Choice3 | "" {
  if (value === true) return "yes";
  if (value === false) return "no";
  return answered ? "discuss" : "";
}

export function draftFrom(
  prefill: Partial<IntakeOnboarding> | null | undefined,
  fallbackName: string | null,
  eligibility?: EligibilityView | null,
  /** The hiring team's preset role wins over a saved choice (the server stores the preset). */
  targetRolePreset?: TargetRole | null,
): OnboardingDraft {
  const p = prefill ?? {};
  return {
    full_name: p.full_name ?? fallbackName ?? "",
    target_role: targetRolePreset ?? p.target_role ?? "",
    target_role_other: p.target_role_other ?? "",
    open_to_ai_ml: p.open_to_ai_ml ?? "",
    technical_background: p.technical_background ?? "",
    primary_language: p.primary_language ?? "",
    primary_language_other: p.primary_language_other ?? "",
    self_rating: p.self_rating ?? null,
    scripting: p.scripting ?? "",
    scripting_details: p.scripting_details ?? "",
    data_ml_libraries: p.data_ml_libraries ?? "",
    years_hands_on: p.years_hands_on === null || p.years_hands_on === undefined ? "" : String(p.years_hands_on),
    experience_level_comfort: p.experience_level_comfort ?? "",
    training_commitment: p.training_commitment ?? "",
    weekly_hours: p.weekly_hours === null || p.weekly_hours === undefined ? "" : String(p.weekly_hours),
    availability: p.availability ?? "",
    available_from: p.available_from ?? "",
    // Re-confirmed on every save.
    accuracy_confirmed: false,
    work_authorized: eligibility?.enabled ? fromBool(eligibility.authorized, eligibility.answered) : "",
    needs_sponsorship: eligibility?.enabled && eligibility.askSponsorship ? fromBool(eligibility.needsSponsorship, eligibility.answered) : "",
  };
}

/** Someone aiming for AI/ML is open to it by definition: the question is not asked. */
export function asksOpenToAiMl(draft: Pick<OnboardingDraft, "target_role">): boolean {
  return draft.target_role !== "ai_ml";
}

function parseNumber(value: string): number | null {
  const text = value.trim().replace(",", ".");
  if (!text) return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : Number.NaN;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Client validation, mirroring the server's IntakeOnboardingIn rules. Field -> message. */
export function validateOnboarding(draft: OnboardingDraft, eligibility: Pick<EligibilityView, "enabled" | "askSponsorship"> | null): Partial<Record<OnboardingField, string>> {
  const errors: Partial<Record<OnboardingField, string>> = {};
  const name = draft.full_name.trim();
  if (name.length < 2) errors.full_name = "Enter your full name.";
  else if (name.length > LIMITS.full_name) errors.full_name = `Keep your name under ${LIMITS.full_name} characters.`;

  if (!draft.target_role) errors.target_role = "Choose the role you're aiming for.";
  if (draft.target_role === "other") {
    const other = draft.target_role_other.trim();
    if (!other) errors.target_role_other = "Name the role you're aiming for.";
    else if (other.length > LIMITS.target_role_other) errors.target_role_other = `Keep it under ${LIMITS.target_role_other} characters.`;
  }
  if (asksOpenToAiMl(draft) && !draft.open_to_ai_ml) errors.open_to_ai_ml = "Choose an answer.";

  const background = draft.technical_background.trim();
  if (!background) errors.technical_background = "Describe your technical background in a sentence or two. If you're new to tech, say so.";
  else if (background.length > LIMITS.technical_background) errors.technical_background = `Keep it under ${LIMITS.technical_background.toLocaleString("en")} characters.`;

  if (!draft.primary_language) errors.primary_language = "Choose the language you know best.";
  if (draft.primary_language === "other") {
    const other = draft.primary_language_other.trim();
    if (!other) errors.primary_language_other = "Name the language.";
    else if (other.length > LIMITS.primary_language_other) errors.primary_language_other = `Keep it under ${LIMITS.primary_language_other} characters.`;
  }
  if (draft.self_rating === null || !Number.isInteger(draft.self_rating) || draft.self_rating < 1 || draft.self_rating > 10) {
    errors.self_rating = "Choose a number from 1 to 10.";
  }
  if (!draft.scripting) errors.scripting = "Choose an answer.";
  if (draft.scripting !== "none" && draft.scripting_details.trim().length > LIMITS.scripting_details) {
    errors.scripting_details = `Keep it under ${LIMITS.scripting_details} characters.`;
  }
  if (draft.data_ml_libraries.trim().length > LIMITS.data_ml_libraries) errors.data_ml_libraries = `Keep it under ${LIMITS.data_ml_libraries} characters.`;

  const years = parseNumber(draft.years_hands_on);
  if (years !== null && (Number.isNaN(years) || years < 0 || years > 50)) errors.years_hands_on = "Enter a number of years from 0 to 50, or leave it empty.";

  if (!draft.experience_level_comfort) errors.experience_level_comfort = "Choose an answer.";
  if (!draft.training_commitment) errors.training_commitment = "Choose an answer.";

  const hours = parseNumber(draft.weekly_hours);
  if (hours !== null && (Number.isNaN(hours) || !Number.isInteger(hours) || hours < 0 || hours > 80)) {
    errors.weekly_hours = "Enter whole hours from 0 to 80, or leave it empty.";
  }
  if (!draft.availability) errors.availability = "Choose when you could start.";
  if (draft.availability === "later" && draft.available_from.trim() && !DATE.test(draft.available_from.trim())) {
    errors.available_from = "Enter a date, or leave it empty.";
  }

  if (eligibility?.enabled) {
    if (!draft.work_authorized) errors.work_authorized = "Choose an answer, or say you'd rather discuss it with the hiring team.";
    if (eligibility.askSponsorship && !draft.needs_sponsorship) errors.needs_sponsorship = "Choose an answer, or say you'd rather discuss it with the hiring team.";
  }
  if (!draft.accuracy_confirmed) errors.accuracy_confirmed = "Confirm that your answers are accurate.";
  return errors;
}

function toBool(value: Choice3 | ""): boolean | null {
  return value === "yes" ? true : value === "no" ? false : null;
}

function optional(value: string): string | null {
  const text = value.trim();
  return text ? text : null;
}

/** The PUT body. Call only after `validateOnboarding` returned no errors. */
export function toOnboardingBody(draft: OnboardingDraft, eligibility: Pick<EligibilityView, "enabled" | "askSponsorship"> | null): IntakeOnboardingIn {
  const years = parseNumber(draft.years_hands_on);
  const hours = parseNumber(draft.weekly_hours);
  return {
    full_name: draft.full_name.trim(),
    target_role: draft.target_role as TargetRole,
    target_role_other: draft.target_role === "other" ? optional(draft.target_role_other) : null,
    open_to_ai_ml: asksOpenToAiMl(draft) ? (draft.open_to_ai_ml as OpenToAiMl) : "yes",
    technical_background: draft.technical_background.trim(),
    primary_language: draft.primary_language as PrimaryLanguage,
    primary_language_other: draft.primary_language === "other" ? optional(draft.primary_language_other) : null,
    self_rating: draft.self_rating ?? 1,
    scripting: draft.scripting as ScriptingLevel,
    scripting_details: draft.scripting === "none" ? null : optional(draft.scripting_details),
    data_ml_libraries: optional(draft.data_ml_libraries),
    years_hands_on: years === null || Number.isNaN(years) ? null : years,
    experience_level_comfort: draft.experience_level_comfort as ExperienceComfort,
    training_commitment: draft.training_commitment as TrainingCommitment,
    weekly_hours: hours === null || Number.isNaN(hours) ? null : hours,
    availability: draft.availability as Availability,
    available_from: draft.availability === "later" ? optional(draft.available_from) : null,
    accuracy_confirmed: true,
    eligibility: eligibility?.enabled
      ? { authorized: toBool(draft.work_authorized), needs_sponsorship: eligibility.askSponsorship ? toBool(draft.needs_sponsorship) : null }
      : null,
  };
}

/** Server field names (`eligibility.authorized`) onto form fields. */
export function formFieldFor(serverField: string): OnboardingField | null {
  if (serverField === "eligibility" || serverField === "eligibility.authorized") return "work_authorized";
  if (serverField === "eligibility.needs_sponsorship") return "needs_sponsorship";
  return (ONBOARDING_FIELDS as readonly string[]).includes(serverField) ? (serverField as OnboardingField) : null;
}

// ------------------------------------------------------------------ option copy

export const TARGET_ROLE_OPTIONS: ReadonlyArray<{ value: TargetRole; label: string; hint?: string }> = [
  { value: "ai_ml", label: "AI/ML", hint: "Machine learning and AI engineering" },
  { value: "data_analyst", label: "Data analyst", hint: "Analysis, reporting and SQL" },
  { value: "product_manager_technical", label: "Product manager, technical", hint: "Works closely with engineering" },
  { value: "product_manager_growth", label: "Product manager, growth", hint: "Users, experiments and metrics" },
  { value: "other", label: "Something else" },
];

export const OPEN_OPTIONS: ReadonlyArray<{ value: OpenToAiMl; label: string }> = [
  { value: "yes", label: "Yes" },
  { value: "maybe", label: "Maybe" },
  { value: "no", label: "No" },
];

export const LANGUAGE_OPTIONS: ReadonlyArray<{ value: PrimaryLanguage; label: string }> = [
  { value: "python", label: "Python" },
  { value: "java", label: "Java" },
  { value: "javascript", label: "JavaScript or TypeScript" },
  { value: "csharp", label: "C#" },
  { value: "sql", label: "SQL" },
  { value: "other", label: "Another language" },
];

export const SCRIPTING_OPTIONS: ReadonlyArray<{ value: ScriptingLevel; label: string }> = [
  { value: "none", label: "No" },
  { value: "some", label: "A little" },
  { value: "regular", label: "Regularly" },
];

export const COMFORT_OPTIONS: ReadonlyArray<{ value: ExperienceComfort; label: string }> = [
  { value: "yes", label: "Yes" },
  { value: "unsure", label: "Not sure yet" },
  { value: "no", label: "No" },
];

export const COMMITMENT_OPTIONS: ReadonlyArray<{ value: TrainingCommitment; label: string }> = [
  { value: "full", label: "Yes, fully" },
  { value: "partial", label: "Partly" },
  { value: "cannot", label: "Not at the moment" },
];

export const AVAILABILITY_OPTIONS: ReadonlyArray<{ value: Availability; label: string }> = [
  { value: "immediately", label: "Straight away" },
  { value: "within_2_weeks", label: "Within 2 weeks" },
  { value: "within_1_month", label: "Within a month" },
  { value: "later", label: "Later" },
];

export const ELIGIBILITY_OPTIONS: ReadonlyArray<{ value: Choice3; label: string }> = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "discuss", label: "I'd rather discuss this with the hiring team" },
];
