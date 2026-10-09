/**
 * Candidate intake portal API (INTAKE_INTERVIEW.md §10.4, DTOs §15.3/15.4/15.14).
 * Every response goes through a tolerant normaliser, like features/candidate/api.ts:
 * missing optional fields never throw, unknown enum values fall back safely.
 */
import { api, isApiError, type ApiError } from "@/lib/api";

import {
  AVAILABILITY,
  EXPERIENCE_COMFORT,
  INTAKE_DURATION,
  INTAKE_STAGE_KEYS,
  INTAKE_STAGE_MINUTES,
  INTAKE_STAGE_TITLES,
  OPEN_TO_AI_ML,
  PRIMARY_LANGUAGES,
  SCRIPTING_LEVELS,
  TARGET_ROLES,
  TRAINING_COMMITMENT,
  type IntakeOnboarding,
  type IntakeOnboardingIn,
  type IntakePartView,
  type PortalIntakeView,
  type ResumeReviewDoc,
  type ResumeReviewIn,
  type ResumeReviewView,
  type ReviewEducation,
  type ReviewProject,
  type ReviewRole,
  type ReviewSectionState,
} from "./types";

type Rec = Record<string, unknown>;

function isRec(value: unknown): value is Rec {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function rec(value: unknown): Rec {
  return isRec(value) ? value : {};
}
function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
function num(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}
function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}
function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
function strings(value: unknown, limit = 60): string[] {
  return arr(value)
    .map((v) => (typeof v === "string" ? v.trim() : ""))
    .filter(Boolean)
    .slice(0, limit);
}
function oneOf<T extends string>(values: readonly T[], value: unknown): T | undefined {
  return typeof value === "string" && (values as readonly string[]).includes(value) ? (value as T) : undefined;
}
function nullableBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** Query keys under the portal interview key, so invalidating the interview refreshes these too. */
export const intakeKeys = {
  portal: (interviewId: string) => ["portal", "interview", interviewId, "intake"] as const,
  resumeReview: (interviewId: string) => ["portal", "interview", interviewId, "intake", "resume-review"] as const,
};

// ------------------------------------------------------------------ portal intake (§15.14)

function minutesRange(value: unknown): { min: number; max: number } | null {
  const single = num(value);
  if (single !== null) return { min: single, max: single };
  const r = rec(value);
  const min = num(r.min);
  const max = num(r.max);
  if (min === null && max === null) return null;
  return { min: min ?? max!, max: max ?? min! };
}

/** The seven parts in order, from the server or from the kit defaults. */
export function normalizeParts(raw: unknown): IntakePartView[] {
  const parts = arr(raw)
    .map((item, i): IntakePartView | null => {
      const p = rec(item);
      const key = str(p.key);
      if (!key) return null;
      return {
        number: num(p.number) ?? i + 1,
        key,
        title: str(p.title) ?? INTAKE_STAGE_TITLES[key as keyof typeof INTAKE_STAGE_TITLES] ?? key,
        minutes: minutesRange(p.minutes) ?? INTAKE_STAGE_MINUTES[key as keyof typeof INTAKE_STAGE_MINUTES] ?? null,
      };
    })
    .filter((p): p is IntakePartView => Boolean(p))
    .sort((a, b) => a.number - b.number);
  if (parts.length) return parts;
  return INTAKE_STAGE_KEYS.map((key, i) => ({ number: i + 1, key, title: INTAKE_STAGE_TITLES[key], minutes: INTAKE_STAGE_MINUTES[key] }));
}

function normalizeOnboarding(raw: unknown): Partial<IntakeOnboarding> | null {
  if (!isRec(raw)) return null;
  const out: Partial<IntakeOnboarding> = {};
  const fullName = str(raw.full_name);
  if (fullName) out.full_name = fullName;
  const role = oneOf(TARGET_ROLES, raw.target_role);
  if (role) out.target_role = role;
  out.target_role_other = str(raw.target_role_other);
  const open = oneOf(OPEN_TO_AI_ML, raw.open_to_ai_ml);
  if (open) out.open_to_ai_ml = open;
  out.technical_background = typeof raw.technical_background === "string" ? raw.technical_background : "";
  const language = oneOf(PRIMARY_LANGUAGES, raw.primary_language);
  if (language) out.primary_language = language;
  out.primary_language_other = str(raw.primary_language_other);
  const rating = num(raw.self_rating);
  if (rating !== null && rating >= 1 && rating <= 10) out.self_rating = Math.round(rating);
  const scripting = oneOf(SCRIPTING_LEVELS, raw.scripting);
  if (scripting) out.scripting = scripting;
  out.scripting_details = str(raw.scripting_details);
  out.data_ml_libraries = str(raw.data_ml_libraries);
  out.years_hands_on = num(raw.years_hands_on);
  const comfort = oneOf(EXPERIENCE_COMFORT, raw.experience_level_comfort);
  if (comfort) out.experience_level_comfort = comfort;
  const commitment = oneOf(TRAINING_COMMITMENT, raw.training_commitment);
  if (commitment) out.training_commitment = commitment;
  out.weekly_hours = num(raw.weekly_hours);
  const availability = oneOf(AVAILABILITY, raw.availability);
  if (availability) out.availability = availability;
  out.available_from = str(raw.available_from);
  return out;
}

export function normalizePortalIntake(raw: unknown, interviewId: string): PortalIntakeView {
  const r = rec(raw);
  const eligibility = rec(r.eligibility);
  const range = rec(r.duration_range);
  const review = rec(r.resume_review);
  return {
    intakeId: str(r.intake_id),
    interviewId: str(r.interview_id) ?? interviewId,
    title: str(r.title) ?? "Intake interview",
    programName: str(r.program_name),
    organizationName: str(r.organization_name),
    estimatedMinutes: num(r.estimated_minutes) ?? INTAKE_DURATION.target,
    durationRange: { min: num(range.min) ?? INTAKE_DURATION.min, max: num(range.max) ?? INTAKE_DURATION.max },
    parts: normalizeParts(r.parts),
    presence: r.presence === "none" ? "none" : "orb",
    targetRolePreset: oneOf(TARGET_ROLES, r.target_role_preset) ?? null,
    onboarding: normalizeOnboarding(r.onboarding),
    onboardingSubmitted: bool(r.onboarding_submitted),
    onboardingLocked: bool(r.onboarding_locked),
    eligibility: {
      enabled: bool(eligibility.enabled),
      country: str(eligibility.country),
      askSponsorship: bool(eligibility.ask_sponsorship, true),
      answered: bool(eligibility.answered),
      authorized: nullableBool(eligibility.authorized),
      needsSponsorship: nullableBool(eligibility.needs_sponsorship),
    },
    resumeRequired: bool(r.resume_required, true),
    resumeUploaded: bool(r.resume_uploaded),
    resumeReview: { required: bool(review.required, true), confirmed: bool(review.confirmed), skipped: bool(review.skipped) },
    readyToStart: bool(r.ready_to_start),
    missing: strings(r.missing, 8),
  };
}

export function getPortalIntake(interviewId: string): Promise<PortalIntakeView> {
  return api.get<unknown>(`/portal/intake/${encodeURIComponent(interviewId)}`).then((raw) => normalizePortalIntake(raw, interviewId));
}

export function saveOnboarding(interviewId: string, body: IntakeOnboardingIn): Promise<PortalIntakeView> {
  return api.put<unknown>(`/portal/intake/${encodeURIComponent(interviewId)}/onboarding`, body).then((raw) => normalizePortalIntake(raw, interviewId));
}

/**
 * Field errors of a failed onboarding save: pydantic `details.errors[{loc, msg}]`
 * (via ApiError.fieldErrors) and app errors naming one field (`details.field`).
 * Keys are top-level form fields (`eligibility.authorized` stays dotted).
 */
export function onboardingFieldErrors(error: ApiError): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [field, message] of Object.entries(error.fieldErrors())) {
    const key = field.startsWith("eligibility.") ? field : (field.split(".")[0] ?? field);
    if (!(key in out)) out[key] = message;
  }
  const single = error.details.field;
  if (typeof single === "string" && single && !(single in out)) out[single] = error.message || "Check this answer.";
  return out;
}

// ------------------------------------------------------------------ résumé review (§15.4)

let newRefCounter = 0;
/** Client-side key for an item the candidate adds; sent to the server as ref "new". */
export function draftRef(): string {
  newRefCounter += 1;
  return `new-${newRefCounter}`;
}

/** Refs the server re-keys: anything the candidate added in this browser. */
export function isDraftRef(ref: string): boolean {
  return ref === "new" || ref.startsWith("new-");
}

function role(raw: unknown, i: number): ReviewRole | null {
  const r = rec(raw);
  const title = str(r.title) ?? "";
  const company = str(r.company) ?? "";
  if (!title && !company) return null;
  return {
    ref: str(r.ref) ?? `r${i + 1}`,
    title,
    company,
    start: str(r.start),
    end: str(r.end),
    responsibilities: strings(r.responsibilities, 6),
    technologies: strings(r.technologies, 15),
    achievements: strings(r.achievements, 4),
  };
}

function project(raw: unknown, i: number): ReviewProject | null {
  const r = rec(raw);
  const name = str(r.name) ?? "";
  if (!name && !str(r.description)) return null;
  return {
    ref: str(r.ref) ?? `p${i + 1}`,
    name,
    role: str(r.role),
    description: str(r.description) ?? "",
    technologies: strings(r.technologies, 15),
    outcomes: strings(r.outcomes, 4),
  };
}

function education(raw: unknown, i: number): ReviewEducation | null {
  const r = rec(raw);
  const institution = str(r.institution) ?? "";
  if (!institution && !str(r.degree)) return null;
  return { ref: str(r.ref) ?? `e${i + 1}`, institution, degree: str(r.degree), field: str(r.field) };
}

export function normalizeReviewDoc(raw: unknown): ResumeReviewDoc | null {
  if (!isRec(raw)) return null;
  return {
    version: 1,
    headline: str(raw.headline),
    experience_years: num(raw.experience_years),
    roles: arr(raw.roles).map(role).filter((x): x is ReviewRole => Boolean(x)).slice(0, 12),
    projects: arr(raw.projects).map(project).filter((x): x is ReviewProject => Boolean(x)).slice(0, 10),
    skills: strings(raw.skills, 40),
    languages: strings(raw.languages, 15),
    tools: strings(raw.tools, 30),
    education: arr(raw.education).map(education).filter((x): x is ReviewEducation => Boolean(x)).slice(0, 5),
    certifications: strings(raw.certifications, 10),
  };
}

const SECTION_STATES = new Set<ReviewSectionState>(["pending", "quick", "active", "done", "fallback"]);

export function normalizeResumeReview(raw: unknown): ResumeReviewView {
  const r = rec(raw);
  const document = normalizeReviewDoc(r.document);
  const stage = r.profile_stage === "quick" || r.profile_stage === "full" ? r.profile_stage : document ? "quick" : "none";
  return {
    status: r.status === "ready" || (r.status !== "waiting" && document) ? "ready" : "waiting",
    profileStage: stage,
    sections: arr(r.sections)
      .map((item) => {
        const s = rec(item);
        const key = str(s.key);
        const state = str(s.state) as ReviewSectionState | null;
        if (!key || !state || !SECTION_STATES.has(state)) return null;
        return { key, label: str(s.label) ?? key, state };
      })
      .filter((s): s is ResumeReviewView["sections"][number] => Boolean(s)),
    document,
    confirmed: bool(r.confirmed),
    skipped: bool(r.skipped),
    confirmedAt: str(r.confirmed_at),
    canSkip: bool(r.can_skip),
    skipAvailableInS: num(r.skip_available_in_s),
  };
}

export function getResumeReview(interviewId: string): Promise<ResumeReviewView> {
  return api.get<unknown>(`/portal/intake/${encodeURIComponent(interviewId)}/resume-review`).then(normalizeResumeReview);
}

/** Items the candidate added carry ref "new" on the wire (the server re-keys them n1, n2...). */
export function toWireDoc(doc: ResumeReviewDoc): ResumeReviewDoc {
  const wire = <T extends { ref: string }>(items: T[]): T[] => items.map((item) => (isDraftRef(item.ref) ? { ...item, ref: "new" } : item));
  return { ...doc, roles: wire(doc.roles), projects: wire(doc.projects), education: wire(doc.education) };
}

export function saveResumeReview(interviewId: string, body: ResumeReviewIn): Promise<ResumeReviewView> {
  const payload: ResumeReviewIn = { action: body.action, document: body.document ? toWireDoc(body.document) : null };
  return api.put<unknown>(`/portal/intake/${encodeURIComponent(interviewId)}/resume-review`, payload).then(normalizeResumeReview);
}

/** The intake routes are switched off or not deployed: pages fall back to defaults. */
export function intakeUnavailable(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.code === "intake_disabled" || error.code === "intake_not_applicable");
}
