/**
 * Candidate portal API (CONTRACTS §4 "Candidate portal"). Every response goes
 * through a normaliser so the UI depends on one stable view shape even where
 * the backend DTO evolves (the portal routes and the Wave A TypeScript mirrors
 * were written in parallel). Normalisers accept the documented shapes and the
 * obvious variants, and never throw on missing optional fields.
 */
import type { PortalIntakeBriefView } from "@/features/intake/candidate/types";
import { api, isApiError, type UploadProgress } from "@/lib/api";
import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";
import type {
  CandidateReportNarrative,
  CandidateVerifyRequest,
  CodeLanguage,
  ConsentRequest,
  Difficulty,
  InterviewStatus,
  PolicySnapshot,
  ResumeStatus,
  RoundSelectionRequest,
  RoundType,
  SessionStatus,
  SystemCheckRequest,
} from "@/lib/types";

// ----------------------------------------------------------------- helpers

type Rec = Record<string, unknown>;

export function isRec(value: unknown): value is Rec {
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
function strings(value: unknown): string[] {
  return arr(value)
    .map((v) => (typeof v === "string" ? v : str(rec(v).name) ?? str(rec(v).title) ?? str(rec(v).text)))
    .filter((v): v is string => Boolean(v));
}
function firstStr(...values: unknown[]): string | null {
  for (const v of values) {
    const s = str(v);
    if (s) return s;
  }
  return null;
}

// ----------------------------------------------------------------- invite

export interface InviteView {
  organizationName: string;
  jobTitle: string;
  roundName: string | null;
  candidateFirstName: string | null;
  requiresAccessCode: boolean;
  expiresAt: string | null;
  status: InterviewStatus | string;
  interviewerName: string | null;
  durationMinutes: number | null;
  /** Round type of the invited round (`intake` switches the copy); null when not sent. */
  roundType: RoundType | string | null;
}

export function normalizeInvite(raw: unknown): InviteView {
  const r = rec(raw);
  let status = str(r.status) ?? "invited";
  if (r.expired === true || r.is_expired === true) status = "expired";
  if (r.cancelled === true || r.is_cancelled === true) status = "cancelled";
  if (r.completed === true) status = "completed";
  return {
    organizationName: firstStr(r.organization_name, rec(r.organization).name) ?? "the hiring team",
    jobTitle: firstStr(r.job_title, rec(r.job).title) ?? "the role",
    roundName: firstStr(r.round_name, rec(r.round).name),
    candidateFirstName: firstStr(r.candidate_first_name, r.first_name, rec(r.candidate).first_name),
    requiresAccessCode: bool(r.requires_access_code, bool(r.require_access_code)),
    expiresAt: firstStr(r.expires_at, r.invite_expires_at),
    status,
    interviewerName: firstStr(r.interviewer_name),
    durationMinutes: num(r.duration_minutes) ?? num(rec(r.round).duration_minutes),
    roundType: firstStr(r.round_type, rec(r.round).round_type),
  };
}

export function getInvite(token: string): Promise<InviteView> {
  return api.get<unknown>(`/candidate-access/${encodeURIComponent(token)}`, { skipAuthRefresh: true }).then(normalizeInvite);
}

export interface VerifyResult {
  interviewId: string | null;
  nextStep: string | null;
}

export async function verifyInvite(token: string, body: CandidateVerifyRequest): Promise<VerifyResult> {
  const raw = rec(await api.post<unknown>(`/candidate-access/${encodeURIComponent(token)}/verify`, body, { skipAuthRefresh: true }));
  return {
    interviewId: firstStr(raw.interview_id, rec(raw.interview).id),
    nextStep: firstStr(raw.next_step),
  };
}

// ----------------------------------------------------------------- me / interview

export interface PortalMeView {
  candidateId: string | null;
  fullName: string | null;
  firstName: string | null;
  email: string | null;
  interviewId: string | null;
  organizationName: string | null;
}

export function normalizeMe(raw: unknown): PortalMeView {
  const r = rec(raw);
  const c = rec(r.candidate);
  return {
    candidateId: firstStr(c.id, r.candidate_id),
    fullName: firstStr(c.full_name, r.full_name, c.name),
    firstName: firstStr(c.first_name) ?? (firstStr(c.full_name, r.full_name)?.split(/\s+/)[0] ?? null),
    email: firstStr(c.email, r.email),
    interviewId: firstStr(r.interview_id, rec(r.interview).id),
    organizationName: firstStr(rec(r.organization).name, r.organization_name),
  };
}

export function getPortalMe(): Promise<PortalMeView> {
  return api.get<unknown>("/portal/me").then(normalizeMe);
}

export interface PortalRoundView {
  id: string;
  name: string;
  roundType: RoundType | string;
  durationMinutes: number;
  difficulty: Difficulty | string | null;
  instructions: string | null;
  codingEnabled: boolean;
  codingMinutes: number | null;
  codingLanguages: CodeLanguage[];
  competencies: string[];
  description: string | null;
}

function normalizeRound(raw: unknown): PortalRoundView | null {
  const r = rec(raw);
  const id = str(r.id);
  if (!id) return null;
  const config = rec(r.config);
  const coding = rec(config.coding ?? r.coding);
  const comps = arr(r.competencies ?? config.competencies)
    .map((c) => (typeof c === "string" ? c : firstStr(rec(c).name, rec(c).label, rec(c).key)))
    .filter((c): c is string => Boolean(c))
    .map((c) => (c.includes("_") && c === c.toLowerCase() ? c.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()) : c));
  return {
    id,
    name: str(r.name) ?? "Interview round",
    roundType: str(r.round_type) ?? "combined",
    durationMinutes: num(r.duration_minutes) ?? num(config.duration_minutes) ?? 30,
    difficulty: firstStr(r.expected_difficulty, config.expected_difficulty),
    instructions: firstStr(r.instructions, config.instructions),
    codingEnabled: bool(r.coding_enabled, bool(coding.enabled)),
    codingMinutes: num(r.coding_minutes) ?? num(coding.duration_minutes),
    codingLanguages: strings(r.coding_languages ?? coding.languages) as CodeLanguage[],
    competencies: comps,
    description: firstStr(r.description, r.summary),
  };
}

export interface PortalInterviewView {
  id: string;
  status: InterviewStatus | string;
  organizationName: string;
  /** The organization's own logo, when it has one (the shell shows the TechSara mark otherwise). */
  organizationLogoUrl: string | null;
  job: { id: string | null; title: string; department: string | null; location: string | null; description: string | null };
  rounds: PortalRoundView[];
  selectedRoundId: string | null;
  allowRoundSelection: boolean;
  policy: Partial<PolicySnapshot>;
  stepsCompleted: string[];
  expiresAt: string | null;
  interviewerName: string;
  latestSession: { id: string; status: SessionStatus | string } | null;
  candidateName: string | null;
  /** The résumé step can be skipped when false. */
  requiresResume: boolean;
  /**
   * Intake interviews (`PortalInterview.intake`); null for every other round type. Set by
   * `normalizeInterview` (optional so hand-built views in tests stay valid).
   */
  intake?: PortalIntakeBriefView | null;
}

function normalizeIntakeBrief(raw: unknown): PortalIntakeBriefView | null {
  if (!isRec(raw)) return null;
  return {
    intakeId: str(raw.intake_id),
    title: str(raw.title) ?? "Intake interview",
    estimatedMinutes: num(raw.estimated_minutes),
    presence: raw.presence === "none" ? "none" : "orb",
  };
}

/** Server step names vary ("system_check", "round_selected"...); map them onto route segments. */
export function normalizeStepKey(step: string): string {
  const s = step.toLowerCase().replace(/_/g, "-");
  if (s.startsWith("consent")) return "consent";
  // Intake steps: the résumé review must not collapse into "resume".
  if (s.startsWith("resume-review") || s === "resume-confirmed") return "resume-review";
  if (s === "details" || s.startsWith("onboarding")) return "details";
  if (s.startsWith("resume")) return "resume";
  if (s.startsWith("round")) return "round";
  if (s.startsWith("system")) return "system-check";
  if (s.startsWith("interview") || s.startsWith("session")) return "interview";
  if (s.startsWith("welcome")) return "welcome";
  if (s.startsWith("instruction")) return "instructions";
  return s;
}

export function normalizeInterview(raw: unknown, fallbackId = ""): PortalInterviewView {
  const r = rec(raw);
  const job = rec(r.job);
  const policy = rec(r.policy ?? r.policy_snapshot) as Partial<PolicySnapshot>;
  const rounds = arr(r.rounds_offered ?? r.rounds).map(normalizeRound).filter((x): x is PortalRoundView => Boolean(x));
  const single = normalizeRound(r.round);
  if (single && !rounds.some((x) => x.id === single.id)) rounds.push(single);
  let steps: string[] = [];
  if (Array.isArray(r.steps_completed)) steps = strings(r.steps_completed);
  else if (isRec(r.steps_completed)) steps = Object.entries(r.steps_completed).filter(([, v]) => Boolean(v)).map(([k]) => k);
  else if (isRec(r.steps)) steps = Object.entries(r.steps).filter(([, v]) => v === true || v === "done" || v === "complete").map(([k]) => k);
  const latest = rec(r.latest_session ?? r.session);
  const avatar = rec(policy.avatar);
  return {
    id: str(r.id) ?? fallbackId,
    status: str(r.status) ?? "invited",
    organizationName: firstStr(rec(r.organization).name, r.organization_name) ?? "",
    organizationLogoUrl: firstStr(rec(r.organization).logo_url, r.organization_logo_url),
    job: {
      id: str(job.id),
      title: firstStr(job.title, r.job_title) ?? "",
      department: str(job.department),
      location: str(job.location),
      description: str(job.description),
    },
    rounds,
    selectedRoundId: firstStr(r.selected_round_id, r.round_id, rec(r.round).id),
    allowRoundSelection: bool(r.allow_round_selection),
    policy,
    stepsCompleted: [...new Set(steps.map(normalizeStepKey))],
    expiresAt: firstStr(r.expires_at, r.invite_expires_at),
    interviewerName: firstStr(r.interviewer_name, avatar.interviewer_name) ?? "Maya",
    latestSession: str(latest.id) ? { id: str(latest.id)!, status: str(latest.status) ?? "created" } : null,
    candidateName: firstStr(rec(r.candidate).full_name, r.candidate_name),
    requiresResume: bool(r.requires_resume, true),
    intake: normalizeIntakeBrief(r.intake),
  };
}

export function getPortalInterview(id: string): Promise<PortalInterviewView> {
  return api.get<unknown>(`/portal/interviews/${encodeURIComponent(id)}`).then((raw) => normalizeInterview(raw, id));
}

// ----------------------------------------------------------------- disclosure + consent

export interface DisclosureItemView {
  key: string;
  title: string;
  description: string;
  required: boolean;
  enabled: boolean;
}

export interface DisclosureView {
  policyVersion: string;
  policyHash: string;
  title: string;
  intro: string;
  items: DisclosureItemView[];
  retentionSummary: string | null;
  purpose: string | null;
  humanReviewStatement: string | null;
  limitationsStatement: string | null;
  textMarkdown: string | null;
  retentionDays: number | null;
  /** Items came as a boolean map (older DTO); the consent body mirrors that shape. */
  mapShaped: boolean;
  /** Server's consent map template (`consent_items`): keys it expects back, with defaults. */
  consentTemplate: Record<string, boolean | number>;
  /** The candidate already accepted this exact policy. */
  consented: boolean;
}

const ITEM_TITLES: Record<string, { title: string; description: string }> = {
  microphone: { title: "Microphone", description: "Your voice is captured so your answers can be transcribed." },
  camera: { title: "Camera", description: "Your camera stays on during the interview." },
  camera_frames: { title: "Camera checks", description: "Still images from your camera are checked periodically for a human reviewer." },
  transcript: { title: "Transcript", description: "A written transcript of the conversation is created." },
  integrity_monitoring: { title: "Interview monitoring", description: "Full screen, tab and window changes, copy and paste are recorded as observations." },
  screen_share: { title: "Screen sharing", description: "You share your entire screen for the duration of the interview." },
  audio_recording: { title: "Audio recording", description: "Audio of your answers is kept for the hiring team to review." },
  video_recording: { title: "Video recording", description: "Video of the interview is kept for the hiring team to review." },
  ai_assessment: { title: "AI assessment", description: "An AI evaluates your answers; a person makes every hiring decision." },
  retention: { title: "Data retention", description: "Your data is kept only for the periods stated." },
};

export function normalizeDisclosure(raw: unknown): DisclosureView {
  const r = rec(raw);
  let items: DisclosureItemView[] = [];
  let mapShaped = false;
  let retentionDays: number | null = num(r.retention_days);
  if (Array.isArray(r.items)) {
    items = r.items
      .map((item) => {
        const i = rec(item);
        const key = str(i.key);
        if (!key) return null;
        return {
          key,
          title: firstStr(i.title, i.label) ?? ITEM_TITLES[key]?.title ?? key.replace(/_/g, " "),
          description: firstStr(i.description, i.text) ?? ITEM_TITLES[key]?.description ?? "",
          required: bool(i.required, true),
          enabled: bool(i.enabled, true),
        };
      })
      .filter((x): x is DisclosureItemView => Boolean(x));
  } else if (isRec(r.items)) {
    mapShaped = true;
    for (const [key, value] of Object.entries(r.items)) {
      if (key === "retention_days") {
        retentionDays = num(value);
        continue;
      }
      if (value !== true) continue;
      items.push({ key, title: ITEM_TITLES[key]?.title ?? key, description: ITEM_TITLES[key]?.description ?? "", required: true, enabled: true });
    }
  }
  // `required_items` (list of keys) is the authority on what must be acknowledged.
  if (Array.isArray(r.required_items)) {
    const required = new Set(strings(r.required_items));
    items = items.map((item) => ({ ...item, required: required.has(item.key) }));
  }
  const consentTemplate: Record<string, boolean | number> = {};
  if (isRec(r.consent_items)) {
    for (const [key, value] of Object.entries(r.consent_items)) {
      if (typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) consentTemplate[key] = value;
    }
  }
  return {
    policyVersion: str(r.policy_version) ?? "",
    policyHash: str(r.policy_hash) ?? "",
    title: str(r.title) ?? "Before you begin",
    intro: firstStr(r.intro, r.summary) ?? "",
    items: items.filter((i) => i.enabled),
    retentionSummary: str(r.retention_summary),
    purpose: str(r.purpose),
    humanReviewStatement: firstStr(r.human_review_statement, r.human_decision_statement),
    limitationsStatement: str(r.limitations_statement),
    textMarkdown: firstStr(r.text_markdown, r.text),
    retentionDays,
    mapShaped,
    consentTemplate,
    consented: bool(r.consented),
  };
}

export function getDisclosure(interviewId: string): Promise<DisclosureView> {
  return api.get<unknown>(`/portal/interviews/${encodeURIComponent(interviewId)}/disclosure`).then(normalizeDisclosure);
}

/**
 * Consent body: a map of every disclosed item key -> acknowledged, plus the
 * server's own template keys that were not shown as items (e.g. retention
 * days) passed through unchanged. Items the candidate saw are always taken
 * from the checkboxes, never from the template.
 */
export function consentBody(disclosure: DisclosureView, checked: Record<string, boolean>): ConsentRequest {
  const items: Record<string, boolean | number> = {};
  const shown = new Set(disclosure.items.map((i) => i.key));
  for (const [key, value] of Object.entries(disclosure.consentTemplate)) {
    if (!shown.has(key)) items[key] = typeof value === "boolean" ? false : value;
  }
  for (const item of disclosure.items) items[item.key] = Boolean(checked[item.key]);
  if (disclosure.mapShaped && disclosure.retentionDays !== null) items.retention_days = disclosure.retentionDays;
  return { policy_hash: disclosure.policyHash, items };
}

export function postConsent(interviewId: string, body: ConsentRequest): Promise<unknown> {
  return api.post(`/portal/interviews/${encodeURIComponent(interviewId)}/consent`, body);
}

// ----------------------------------------------------------------- resume

export type ResumeStepState = "pending" | "active" | "done" | "failed";

export interface ResumeStepView {
  key: string;
  label: string;
  state: ResumeStepState;
  /** When the step reached its current state (server time, ISO), if known. */
  at: string | null;
}

export interface ResumeSummaryView {
  name: string | null;
  headline: string | null;
  experienceYears: number | null;
  skills: string[];
  roles: Array<{ title: string; company: string; period: string | null }>;
  certifications: string[];
  /** Degrees and schools (never dates). */
  education?: Array<{ institution: string; degree: string | null; field: string | null }>;
  /** A scanned résumé read so far (page 1 of 2 ...) while the rest is still scanned. */
  partial?: { pagesRead: number | null; pagesTotal: number | null } | null;
}

/**
 * How far one part of the profile is: `quick` = what we read from the file
 * instantly, `active` = being checked, `done` = checked, `fallback` = could not
 * be checked (the quick read stays), `pending` = nothing to show yet.
 */
export type ResumeSectionState = "pending" | "quick" | "active" | "done" | "fallback";

export interface ResumeSectionView {
  key: string;
  label: string;
  state: ResumeSectionState;
}

/** `quick`: the instant read of the file; `full`: the checked profile; `none`: nothing yet. */
export type ResumeProfileStage = "none" | "quick" | "full";

export interface ResumeView {
  resumeId: string | null;
  /** Upload time (server, ISO). */
  createdAt: string | null;
  filename: string | null;
  sizeBytes: number | null;
  status: ResumeStatus | "none" | string;
  statusDetail: string | null;
  steps: ResumeStepView[];
  error: string | null;
  profileReady: boolean;
  /** Upload validated and text extracted: the candidate may move on while analysis continues. */
  extracted: boolean;
  summary: ResumeSummaryView | null;
  percent: number | null;
  /** Set by `normalizeResume` (optional so hand-built views in tests stay valid). */
  profileStage?: ResumeProfileStage;
  /** Per-section state of the profile (empty for older servers). */
  sections?: ResumeSectionView[];
}

const PIPELINE: Array<{ key: string; label: string; statuses: string[] }> = [
  { key: "upload", label: "Upload", statuses: ["uploaded"] },
  { key: "validate", label: "Security check", statuses: ["validating"] },
  { key: "extract", label: "Reading your résumé", statuses: ["extracting", "ocr"] },
  { key: "analyze", label: "Understanding your experience", statuses: ["analyzing"] },
  { key: "profile", label: "Tailoring your interview", statuses: ["profiling", "profile"] },
];

export const TERMINAL_RESUME_FAILURES = new Set(["failed", "rejected"]);

/** Candidate-facing names for the pipeline steps the server reports (by key). */
const STEP_LABELS: Record<string, string> = {
  upload: "Upload",
  uploaded: "Upload",
  validate: "Security check",
  validating: "Security check",
  extract: "Reading your résumé",
  extracting: "Reading your résumé",
  ocr: "Scanning the pages",
  analyze: "Understanding your experience",
  analyzing: "Understanding your experience",
  profile: "Tailoring your interview",
  profiling: "Tailoring your interview",
};

/** Steps that only mark completion: the status already says so, so they are not listed. */
const MARKER_STEPS = new Set(["ready", "done", "complete"]);

export function deriveSteps(status: string, profileReady: boolean): ResumeStepView[] {
  if (status === "none") return [];
  const failed = TERMINAL_RESUME_FAILURES.has(status);
  const ready = status === "ready";
  let activeIndex = PIPELINE.findIndex((p) => p.statuses.includes(status));
  if (ready) activeIndex = profileReady ? PIPELINE.length : PIPELINE.length - 1;
  if (activeIndex === -1) activeIndex = failed ? 1 : 0;
  return PIPELINE.map((p, i) => ({
    key: p.key,
    label: p.label,
    state: i < activeIndex ? "done" : i === activeIndex ? (failed ? "failed" : "active") : "pending",
    at: null,
  }));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2023-01" -> "Jan 2023", "2021" -> "2021", "present" -> "Present" (anything else as written). */
export function formatResumeDate(value: string | null): string | null {
  if (!value) return null;
  const text = value.trim();
  if (/^(present|current|now)$/i.test(text)) return "Present";
  const match = /^(\d{4})-(\d{2})$/.exec(text);
  if (match) {
    const month = Number(match[2]);
    return month >= 1 && month <= 12 ? `${MONTHS[month - 1]} ${match[1]}` : match[1]!;
  }
  return text;
}

function normalizeSummary(raw: unknown): ResumeSummaryView | null {
  if (!isRec(raw)) return null;
  const roles = arr(raw.roles).map((role) => {
    const r = rec(role);
    const start = formatResumeDate(str(r.start));
    const end = formatResumeDate(str(r.end));
    return { title: str(r.title) ?? "", company: str(r.company) ?? "", period: firstStr(r.period, r.dates) ?? (start ? `${start} – ${end ?? "Present"}` : null) };
  }).filter((r) => r.title || r.company);
  let skills = strings(raw.skills ?? raw.top_skills);
  if (!skills.length && isRec(raw.skills)) skills = Object.values(raw.skills).flatMap((v) => strings(v));
  const education = arr(raw.education)
    .map((item) => {
      const e = rec(item);
      return { institution: str(e.institution) ?? "", degree: str(e.degree), field: str(e.field) };
    })
    .filter((e) => e.institution || e.degree);
  const partial = raw.partial === true ? { pagesRead: num(raw.pages_read), pagesTotal: num(raw.pages_total) } : null;
  const summary: ResumeSummaryView = {
    name: firstStr(raw.name, raw.candidate_name, raw.full_name),
    headline: firstStr(raw.headline, raw.summary),
    experienceYears: num(raw.experience_years) ?? num(raw.total_experience_years),
    skills: skills.slice(0, 18),
    roles: roles.slice(0, 6),
    certifications: strings(raw.certifications).slice(0, 8),
    education: education.slice(0, 4),
    partial,
  };
  if (!summary.name && !summary.headline && !summary.skills.length && !summary.roles.length && summary.experienceYears === null) return null;
  return summary;
}

export function normalizeResume(raw: unknown): ResumeView {
  const r = rec(raw);
  const resume = isRec(r.resume) ? r.resume : r.resume === null ? {} : r;
  const progress = rec(resume.progress ?? r.progress);
  const status = firstStr(r.status, resume.status) ?? (r.resume === null ? "none" : str(resume.id) ? "uploaded" : "none");
  const summary = normalizeSummary(r.profile_summary ?? r.summary ?? r.profile ?? resume.extraction_summary);
  const explicitReady = typeof r.profile_ready === "boolean" ? r.profile_ready : typeof resume.profile_ready === "boolean" ? (resume.profile_ready as boolean) : null;
  const profileReady = explicitReady ?? (status === "ready");
  const rawSteps = arr(r.steps).length ? arr(r.steps) : arr(resume.steps).length ? arr(resume.steps) : arr(progress.steps);
  const parsed: ResumeStepView[] = rawSteps
    .map((s) => {
      const step = rec(s);
      const key = str(step.key) ?? str(step.label);
      const rawState = str(step.state) ?? "pending";
      // Skipped stages (e.g. page scanning for a text PDF) are not shown.
      if (!key || rawState === "skipped") return null;
      const state: ResumeStepState = rawState === "done" || rawState === "active" || rawState === "failed" ? rawState : rawState === "running" ? "active" : rawState === "complete" ? "done" : "pending";
      return { key, label: STEP_LABELS[key.toLowerCase()] ?? str(step.label) ?? key, state, at: str(step.at) };
    })
    .filter((s): s is ResumeStepView => Boolean(s));
  const pastExtraction =
    ["analyzing", "ready"].includes(status) ||
    parsed.some((s) => (s.key === "analyzing" || s.key === "ready" || s.key === "profile") && s.state !== "pending");
  let steps = parsed.filter((s) => !MARKER_STEPS.has(s.key.toLowerCase()));
  if (!parsed.length) steps = deriveSteps(status, profileReady);
  else if (status === "ready" && !steps.some((s) => s.key === "profile") && !profileReady) {
    // The résumé is read; the interview profile is generated next.
    steps = [...steps, { key: "profile", label: STEP_LABELS.profile!, state: "active", at: null }];
  }
  return {
    resumeId: firstStr(r.resume_id, resume.id),
    createdAt: firstStr(resume.created_at, r.created_at),
    filename: firstStr(resume.original_filename, r.filename),
    sizeBytes: num(resume.size_bytes),
    status,
    statusDetail: firstStr(resume.status_detail, r.status_detail, progress.message),
    steps,
    error: firstStr(r.error, resume.error),
    profileReady,
    extracted: !TERMINAL_RESUME_FAILURES.has(status) && (profileReady || pastExtraction),
    summary,
    percent: num(progress.percent),
    profileStage: normalizeStage(r.profile_stage, summary !== null),
    sections: normalizeSections(r.sections ?? progress.sections),
  };
}

const SECTION_STATES = new Set<ResumeSectionState>(["pending", "quick", "active", "done", "fallback"]);
/** Candidate wording for the profile sections the server reports. */
const SECTION_LABELS: Record<string, string> = {
  basics: "Name and headline",
  experience: "Work experience",
  skills: "Skills",
  education: "Education",
  interview: "Interview topics",
};

function normalizeStage(raw: unknown, hasSummary: boolean): ResumeProfileStage {
  const stage = str(raw);
  if (stage === "quick" || stage === "full" || stage === "none") return hasSummary || stage === "none" ? stage : "none";
  // Older servers only sent a summary once the profile was ready.
  return hasSummary ? "full" : "none";
}

function normalizeSections(raw: unknown): ResumeSectionView[] {
  return arr(raw)
    .map((item) => {
      const s = rec(item);
      const key = str(s.key);
      const state = str(s.state) as ResumeSectionState | null;
      if (!key || !(key in SECTION_LABELS) || !state || !SECTION_STATES.has(state)) return null;
      return { key, label: SECTION_LABELS[key]!, state };
    })
    .filter((s): s is ResumeSectionView => Boolean(s));
}

export function getResume(interviewId: string): Promise<ResumeView> {
  return api
    .get<unknown>(`/portal/interviews/${encodeURIComponent(interviewId)}/resume`, { params: { include_media_url: false } })
    .then(normalizeResume);
}

export function uploadResume(
  interviewId: string,
  file: File,
  options: { onProgress?: (p: UploadProgress) => void; signal?: AbortSignal } = {},
): Promise<ResumeView> {
  return api
    .upload<unknown>(`/portal/interviews/${encodeURIComponent(interviewId)}/resume`, file, { fieldName: "file", onProgress: options.onProgress, signal: options.signal })
    .then(normalizeResume);
}

// ----------------------------------------------------------------- round, system check, session

export function selectRound(interviewId: string, roundId: string): Promise<unknown> {
  const body: RoundSelectionRequest = { round_id: roundId };
  return api.post(`/portal/interviews/${encodeURIComponent(interviewId)}/round`, body);
}

export type SystemCheckBody = SystemCheckRequest;

/** Fields newer than some deployed portal schemas (which forbid unknown fields). */
const OPTIONAL_CHECK_FIELDS = ["avatar_mode", "avatar_fps"] as const;

/**
 * Records the system check. An API that predates the optional avatar fields
 * answers 422 naming them; the check is then re-sent without them, so the
 * candidate is never blocked by a schema rollout.
 */
export async function postSystemCheck(interviewId: string, body: SystemCheckBody): Promise<unknown> {
  const url = `/portal/interviews/${encodeURIComponent(interviewId)}/system-check`;
  try {
    return await api.post(url, body);
  } catch (error) {
    const sent = OPTIONAL_CHECK_FIELDS.filter((key) => body[key] !== undefined);
    const details = isApiError(error) && error.status === 422 ? JSON.stringify(error.details ?? {}) : "";
    if (!sent.length || !sent.some((key) => details.includes(key))) throw error;
    const legacy: SystemCheckBody = { ...body };
    for (const key of OPTIONAL_CHECK_FIELDS) delete legacy[key];
    return api.post(url, legacy);
  }
}

export interface SessionRef {
  sessionId: string;
  status: SessionStatus | string;
}

export async function createOrResumeSession(interviewId: string): Promise<SessionRef> {
  const raw = rec(await api.post<unknown>(`/portal/interviews/${encodeURIComponent(interviewId)}/session`, {}));
  const id = firstStr(raw.session_id, raw.id, rec(raw.session).id);
  if (!id) throw new Error("The server did not return a session.");
  return { sessionId: id, status: firstStr(raw.status, rec(raw.session).status) ?? "created" };
}

export interface PortalSessionView {
  id: string;
  status: SessionStatus | string;
  endReason: string | null;
  candidateReportAvailable: boolean;
  /** The session's candidate feedback policy (`never` hides every feedback element); null from older APIs. */
  candidateReportMode: string | null;
  reportStatus: string | null;
  roundName: string | null;
  startedAt: string | null;
  endedAt: string | null;
}

export function normalizeSession(raw: unknown, fallbackId = ""): PortalSessionView {
  const r = rec(raw);
  return {
    id: str(r.id) ?? str(r.session_id) ?? fallbackId,
    status: str(r.status) ?? "created",
    endReason: str(r.end_reason),
    candidateReportAvailable: bool(r.candidate_report_available, bool(r.report_available)),
    candidateReportMode: firstStr(r.candidate_report_mode),
    reportStatus: firstStr(r.report_status, rec(r.report).status),
    roundName: firstStr(r.round_name, rec(r.round).name),
    startedAt: str(r.started_at),
    endedAt: str(r.ended_at),
  };
}

export function getPortalSession(sessionId: string): Promise<PortalSessionView> {
  return api.get<unknown>(`/portal/sessions/${encodeURIComponent(sessionId)}`).then((raw) => normalizeSession(raw, sessionId));
}

/**
 * Ends the interview over REST when the WebSocket is down (S6). The server finishes it on whichever
 * instance holds the room, or offline a few seconds later when no room is attached anywhere.
 */
/** `ending` | `already_ended` | `not_started` (nothing to end yet: the candidate may come back to the link). */
export async function endPortalSession(sessionId: string, reason = "candidate_ended"): Promise<{ status: string }> {
  const raw = rec(await api.post<unknown>(`/portal/sessions/${encodeURIComponent(sessionId)}/end`, { reason }, { keepalive: true, timeoutMs: 8000 }));
  return { status: str(raw.status) ?? "ending" };
}

export async function getWsTicket(sessionId: string): Promise<{ ticket: string; ws_url: string }> {
  const raw = rec(await api.post<unknown>(`/portal/sessions/${encodeURIComponent(sessionId)}/ws-ticket`, {}));
  const ticket = str(raw.ticket);
  if (!ticket) throw new Error("No WebSocket ticket in the response.");
  // ws_path resolves against the page's own origin (the edge proxies /ws); the ticket is appended by the client.
  return { ticket, ws_url: str(raw.ws_path) ?? str(raw.ws_url) ?? "" };
}

/**
 * Face-check metadata of a frame upload: `trigger: "face_check"` marks the one snapshot of an
 * on-device face-check interval (`faceCheck` = reason, interval id, duration, start); periodic
 * frames may carry `faceCheck.static_boxes` (faces the device judged motionless).
 */
export interface FrameUploadOptions {
  trigger?: "periodic" | "face_check";
  faceCheck?: Record<string, unknown>;
}

export function uploadFrame(sessionId: string, image: Blob, kind: "camera" | "screen", capturedAt: string, options: FrameUploadOptions = {}): Promise<unknown> {
  const form = new FormData();
  form.append("file", image, `${kind}-${Date.now()}.jpg`);
  form.append("kind", kind);
  form.append("captured_at", capturedAt);
  if (options.trigger && options.trigger !== "periodic") form.append("trigger", options.trigger);
  if (options.faceCheck) form.append("face_check", JSON.stringify(options.faceCheck));
  return api.upload(`/portal/sessions/${encodeURIComponent(sessionId)}/frames`, form);
}

export interface IntegrityEventsResult {
  accepted: number;
  /** Gentle candidate-facing messages (same wording as integrity.warning over the socket). */
  warnings: Array<{ eventType: string | null; message: string }>;
}

export async function postIntegrityEvents(sessionId: string, events: ClientIntegrityEvent[], options: { keepalive?: boolean } = {}): Promise<IntegrityEventsResult> {
  const raw = rec(await api.post<unknown>(`/portal/sessions/${encodeURIComponent(sessionId)}/integrity-events`, { events }, { keepalive: options.keepalive, timeoutMs: 15_000 }));
  return {
    accepted: num(raw.accepted) ?? events.length,
    warnings: arr(raw.warnings)
      .map((w) => ({ eventType: str(rec(w).event_type), message: str(rec(w).message) ?? "" }))
      .filter((w) => w.message),
  };
}

/** Recording part + timing of one segment (see features/integrity/recorder.ts). */
export interface RecordingUploadMeta {
  partId: string;
  partStartedAt: string;
  mediaStartMs: number;
  mediaEndMs: number;
  /** Interview clock at the segment end (null while unknown). */
  sessionMs: number | null;
  audioOnly: boolean;
}

export function uploadRecording(
  sessionId: string,
  segment: Blob,
  index: number,
  startedAt: string,
  kind: "camera" | "screen" = "camera",
  meta?: RecordingUploadMeta,
): Promise<unknown> {
  const form = new FormData();
  const extension = segment.type.includes("mp4") ? "mp4" : "webm";
  form.append("file", segment, `${kind === "screen" ? "screen" : "segment"}-${String(index).padStart(5, "0")}.${extension}`);
  form.append("segment", String(index));
  form.append("kind", kind);
  void startedAt; // kept for older callers: timing travels in `meta`
  if (meta) {
    form.append("part_id", meta.partId);
    form.append("part_started_at", meta.partStartedAt);
    form.append("media_start_ms", String(Math.max(0, Math.round(meta.mediaStartMs))));
    form.append("media_end_ms", String(Math.max(0, Math.round(meta.mediaEndMs))));
    if (meta.sessionMs !== null) form.append("session_ms", String(Math.max(0, Math.round(meta.sessionMs))));
    // Lets the server correct this browser's clock (server receive time minus this).
    form.append("client_sent_at", String(Date.now()));
    form.append("audio_only", meta.audioOnly ? "true" : "false");
  }
  return api.upload(`/portal/sessions/${encodeURIComponent(sessionId)}/recording`, form);
}

/** Every kept segment of a recording part was uploaded: the server processes the part now. */
export function completeRecording(sessionId: string, info: { partId: string; kind: "camera" | "screen"; segments: number }): Promise<unknown> {
  return api.post(`/portal/sessions/${encodeURIComponent(sessionId)}/recording/complete`, {
    part_id: info.partId,
    kind: info.kind,
    segments: info.segments,
  });
}

// ----------------------------------------------------------------- candidate report

export interface TopicScoreView {
  key: string;
  label: string;
  /** 0-10 */
  score: number;
  /** Expected topics the answers didn't cover (learning pointers, not penalties). */
  unanswered: string[];
}

export interface CodingFeedbackView {
  title: string;
  language: string | null;
  testsPassed: number | null;
  testsTotal: number | null;
  hintsUsed: number | null;
  summary: string | null;
  timeComplexity: string | null;
  strengths: string[];
  improvements: string[];
}

export interface QuestionFeedbackView {
  id: string;
  sequence: number | null;
  text: string;
  summary: string | null;
  missing: string[];
  followUp: boolean;
}

export interface CandidateReportView {
  status: "ready" | "released" | "pending" | "generating" | "failed" | string;
  /** 0-100 */
  overallScore: number | null;
  generatedAt: string | null;
  narrative: Partial<CandidateReportNarrative>;
  topics: TopicScoreView[];
  coding: CodingFeedbackView[];
  questionText: Record<string, string>;
  questions: QuestionFeedbackView[];
  roundName: string | null;
  jobTitle: string | null;
  durationMinutes: number | null;
  /** Signed link to the printable HTML copy. */
  artifactUrl: string | null;
  note: string | null;
}

function scoreTo10(value: number | null): number | null {
  if (value === null) return null;
  return value > 10 ? value / 10 : value;
}

function humanKey(key: string): string {
  return key.replace(/[_-]+/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

/**
 * Candidate report (GET /portal/sessions/{id}/report -> CandidateReportOut,
 * content = reports.assembly.candidate_view). The endpoint only answers once
 * the report is released, so a body with a report id is ready by definition.
 */
export function normalizeCandidateReport(raw: unknown): CandidateReportView {
  const r = rec(raw);
  const content = rec(r.content ?? r.report ?? r);
  const narrative = rec(content.narrative) as Partial<CandidateReportNarrative>;
  const scores = rec(content.scores);
  // overall_score is on the 0-100 scale everywhere (Report.overall_score); never rescale.
  const overall = num(content.overall_score) ?? num(r.overall_score) ?? num(rec(scores.overall).score) ?? num(scores.overall);

  const topicSource = arr(content.competencies ?? content.topics ?? content.competency_scores ?? scores.competencies);
  const topics: TopicScoreView[] = topicSource
    .map((t) => {
      const item = rec(t);
      const key = firstStr(item.key, item.competency_key, item.name) ?? "";
      const score = scoreTo10(num(item.score) ?? num(item.value));
      if (!key || score === null) return null;
      return { key, label: firstStr(item.name, item.label, item.title) ?? humanKey(key), score, unanswered: strings(item.unanswered_areas).slice(0, 4) };
    })
    .filter((x): x is TopicScoreView => Boolean(x));

  const coding = arr(content.coding ?? content.coding_sessions).map((c) => {
    const item = rec(c);
    const evaluation = rec(item.evaluation);
    return {
      title: firstStr(item.title, rec(item.question).title) ?? "Coding exercise",
      language: firstStr(item.language),
      testsPassed: num(item.tests_passed) ?? num(evaluation.tests_passed),
      testsTotal: num(item.tests_total) ?? num(evaluation.tests_total),
      hintsUsed: num(item.hints_used),
      summary: firstStr(item.correctness_summary, item.feedback, item.summary, evaluation.correctness_summary),
      timeComplexity: firstStr(item.time_complexity, evaluation.time_complexity),
      strengths: strings(item.strengths ?? evaluation.strengths),
      improvements: strings(item.weaknesses ?? item.improvements ?? evaluation.weaknesses),
    };
  });

  const questionText: Record<string, string> = {};
  const questions: QuestionFeedbackView[] = [];
  for (const q of arr(content.questions)) {
    const item = rec(q);
    const id = str(item.id) ?? str(item.question_id);
    const text = firstStr(item.text, item.question);
    if (!id || !text) continue;
    questionText[id] = text;
    const feedback = rec(item.feedback);
    questions.push({
      id,
      sequence: num(item.sequence),
      text,
      summary: firstStr(feedback.summary),
      missing: strings(feedback.missing_details).slice(0, 4),
      followUp: Boolean(str(item.parent_question_id)),
    });
  }

  const session = rec(content.session);
  const elapsed = num(session.elapsed_seconds) ?? num(content.duration_seconds);
  const released = Boolean(str(r.report_id) || str(r.released_at));
  return {
    status: firstStr(r.status, content.status) ?? (released || content.report_type === "candidate" ? "released" : Object.keys(narrative).length ? "ready" : "pending"),
    overallScore: overall,
    generatedAt: firstStr(r.generated_at, content.generated_at),
    narrative,
    topics,
    coding,
    questionText,
    questions,
    roundName: firstStr(rec(content.round).name, content.round_name),
    jobTitle: firstStr(rec(content.job).title, content.job_title),
    durationMinutes: num(content.duration_minutes) ?? (elapsed !== null ? Math.max(1, Math.round(elapsed / 60)) : null),
    artifactUrl: firstStr(r.artifact_url),
    note: firstStr(content.note),
  };
}

export function getCandidateReport(sessionId: string): Promise<CandidateReportView> {
  return api.get<unknown>(`/portal/sessions/${encodeURIComponent(sessionId)}/report`).then(normalizeCandidateReport);
}
