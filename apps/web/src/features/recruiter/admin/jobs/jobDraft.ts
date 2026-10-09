/**
 * Job editor model: one draft object for the job, its skill requirements and
 * its rounds, plus pure diffing so a save sends only what changed.
 */

import type {
  CandidateReportMode,
  CodeLanguage,
  CodingRequirements,
  Competency,
  Difficulty,
  IntegrityPolicy,
  Job,
  JobInput,
  JobRequirement,
  JobRequirementInput,
  JobStatus,
  RecordingPolicy,
  RetentionPolicy,
  Round,
  RoundConfig,
  RoundInput,
} from "@/lib/types";

import { blankToNull, jsonEqual, localId } from "../lib/utils";
import { toRules } from "../settings/settingsModel";
import { roundColumns, toRoundConfig, validateRoundConfig, type ConfigIssue } from "../rounds/roundConfig";

export interface RoundDraft {
  /** Stable React key; equals the server id once saved. */
  uid: string;
  id: string | null;
  template_id: string | null;
  is_active: boolean;
  config: RoundConfig;
}

export interface JobDraft {
  title: string;
  department: string;
  location: string;
  employment_type: string;
  seniority: string;
  description: string;
  experience_min_years: number | null;
  experience_max_years: number | null;
  status: JobStatus;
  interview_instructions: string;
  /** Values are competency keys (linked) or free-text skill names. */
  required_skills: string[];
  preferred_skills: string[];
  integrity_overrides: Partial<IntegrityPolicy>;
  recording_overrides: Partial<RecordingPolicy>;
  retention_overrides: Partial<RetentionPolicy>;
  candidate_report_mode: CandidateReportMode | null;
  allow_candidate_round_selection: boolean;
  rounds: RoundDraft[];
}

export const EMPLOYMENT_TYPES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "full_time", label: "Full time" },
  { value: "part_time", label: "Part time" },
  { value: "contract", label: "Contract" },
  { value: "temporary", label: "Temporary" },
  { value: "internship", label: "Internship" },
];

export const SENIORITY_LEVELS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "intern", label: "Intern" },
  { value: "junior", label: "Junior" },
  { value: "mid", label: "Mid-level" },
  { value: "senior", label: "Senior" },
  { value: "staff", label: "Staff" },
  { value: "principal", label: "Principal" },
  { value: "lead", label: "Lead" },
  { value: "manager", label: "Manager" },
  { value: "director", label: "Director" },
];

export function emptyJobDraft(): JobDraft {
  return {
    title: "",
    department: "",
    location: "",
    employment_type: "full_time",
    seniority: "",
    description: "",
    experience_min_years: null,
    experience_max_years: null,
    status: "draft",
    interview_instructions: "",
    required_skills: [],
    preferred_skills: [],
    integrity_overrides: {},
    recording_overrides: {},
    retention_overrides: {},
    candidate_report_mode: null,
    allow_candidate_round_selection: false,
    rounds: [],
  };
}

export function roundDraftFrom(round: Round): RoundDraft {
  return { uid: round.id, id: round.id, template_id: round.template_id ?? null, is_active: round.is_active !== false, config: round.config };
}

export function newRoundDraft(config: RoundConfig, templateId: string | null = null): RoundDraft {
  return { uid: localId("round"), id: null, template_id: templateId, is_active: true, config };
}

/** Requirement -> MultiSelect value: competency key when linked, else the name. */
function skillValue(requirement: JobRequirement): string {
  return requirement.competency_key || requirement.name;
}

export function jobDraftFrom(job: Job, requirements: readonly JobRequirement[], rounds: readonly Round[]): JobDraft {
  const recording = { ...(job.recording_settings ?? {}) } as Partial<RecordingPolicy> & { retention?: Partial<RetentionPolicy> };
  const retention = recording.retention ?? {};
  delete recording.retention;
  return {
    title: job.title ?? "",
    department: job.department ?? "",
    location: job.location ?? "",
    employment_type: job.employment_type ?? "",
    seniority: job.seniority ?? "",
    description: job.description ?? "",
    experience_min_years: job.experience_min_years ?? null,
    experience_max_years: job.experience_max_years ?? null,
    status: job.status ?? "draft",
    interview_instructions: job.interview_instructions ?? "",
    required_skills: requirements.filter((r) => r.kind === "required_skill").map(skillValue),
    preferred_skills: requirements.filter((r) => r.kind === "preferred_skill").map(skillValue),
    integrity_overrides: { ...(job.integrity_settings ?? {}) },
    recording_overrides: recording,
    retention_overrides: { ...retention },
    candidate_report_mode: job.candidate_report_mode ?? null,
    allow_candidate_round_selection: Boolean(job.allow_candidate_round_selection),
    rounds: rounds.map(roundDraftFrom),
  };
}

/** Coding summary on the job row, derived from the rounds that include coding. */
export function deriveCodingRequirements(rounds: readonly RoundDraft[]): CodingRequirements {
  const coding = rounds.filter((r) => r.is_active && r.config.coding.enabled).map((r) => r.config.coding);
  if (coding.length === 0) return { enabled: false, languages: [], difficulty: "medium" };
  const languages: CodeLanguage[] = [];
  for (const section of coding) for (const language of section.languages) if (!languages.includes(language)) languages.push(language);
  const order: Difficulty[] = ["easy", "medium", "hard"];
  const difficulty = coding.map((c) => c.difficulty).sort((a, b) => order.indexOf(b) - order.indexOf(a))[0] ?? "medium";
  return { enabled: true, languages, difficulty };
}

/** The `POST/PATCH /jobs` body. Overrides are sent as-is (only customised fields). */
export function toJobInput(draft: JobDraft, fallbackReportMode: CandidateReportMode): JobInput {
  const recording: JobInput["recording_settings"] = { ...draft.recording_overrides };
  if (Object.keys(draft.retention_overrides).length > 0) recording.retention = { ...draft.retention_overrides };
  return {
    title: draft.title.trim(),
    department: blankToNull(draft.department),
    location: blankToNull(draft.location),
    employment_type: blankToNull(draft.employment_type),
    seniority: blankToNull(draft.seniority),
    description: draft.description.trim(),
    experience_min_years: draft.experience_min_years,
    experience_max_years: draft.experience_max_years,
    status: draft.status,
    interview_instructions: blankToNull(draft.interview_instructions),
    coding_requirements: deriveCodingRequirements(draft.rounds),
    integrity_settings: draft.integrity_overrides.rules ? { ...draft.integrity_overrides, rules: toRules(draft.integrity_overrides.rules) } : { ...draft.integrity_overrides },
    recording_settings: recording,
    candidate_report_mode: draft.candidate_report_mode ?? fallbackReportMode,
    allow_candidate_round_selection: draft.allow_candidate_round_selection,
  };
}

/**
 * Override keys whose value actually differs from the organization default.
 * Rules count when a job rule differs from the organization rule for the same signal.
 */
export function differingIntegrityKeys(overrides: Partial<IntegrityPolicy>, defaults: IntegrityPolicy): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(overrides)) {
    if (key === "rules") {
      const rules = (value as IntegrityPolicy["rules"] | undefined) ?? [];
      const changed = rules.some((rule) => !jsonEqual(rule, defaults.rules.find((r) => r.event_type === rule.event_type)));
      if (changed) out.push(key);
    } else if (!jsonEqual(value, (defaults as unknown as Record<string, unknown>)[key])) {
      out.push(key);
    }
  }
  return out;
}

// --------------------------------------------------------------- validation

export interface JobIssue {
  path: string;
  message: string;
  severity: "error" | "warning";
  /** For round issues: which round (uid) and the config issue. */
  roundUid?: string;
}

export function validateJobDraft(draft: JobDraft): JobIssue[] {
  const issues: JobIssue[] = [];
  const title = draft.title.trim();
  if (!title) issues.push({ path: "title", message: "Give the job a title.", severity: "error" });
  else if (title.length > 200) issues.push({ path: "title", message: "Keep the title under 200 characters.", severity: "error" });
  const { experience_min_years: min, experience_max_years: max } = draft;
  if (min !== null && (!Number.isFinite(min) || min < 0 || min > 50)) issues.push({ path: "experience_min_years", message: "Use 0 to 50 years.", severity: "error" });
  if (max !== null && (!Number.isFinite(max) || max < 0 || max > 60)) issues.push({ path: "experience_max_years", message: "Use 0 to 60 years.", severity: "error" });
  if (min !== null && max !== null && Number.isFinite(min) && Number.isFinite(max) && max < min) {
    issues.push({ path: "experience_max_years", message: "The maximum can't be below the minimum.", severity: "error" });
  }
  if (draft.description.length > 50_000) issues.push({ path: "description", message: "Keep the description under 50,000 characters.", severity: "error" });
  for (const [path, label, max] of [
    ["department", "Department", 120],
    ["location", "Location", 120],
    ["interview_instructions", "Notes for candidates", 8000],
  ] as const) {
    if (draft[path].length > max) issues.push({ path, message: `${label} is limited to ${max.toLocaleString("en")} characters.`, severity: "error" });
  }
  const overlap = draft.required_skills.filter((s) => draft.preferred_skills.some((p) => p.toLowerCase() === s.toLowerCase()));
  if (overlap.length) issues.push({ path: "preferred_skills", message: `Listed as both required and preferred: ${overlap.join(", ")}.`, severity: "error" });
  if (draft.required_skills.length === 0) issues.push({ path: "required_skills", message: "Add the skills this role needs; they drive resume matching.", severity: "warning" });

  const active = draft.rounds.filter((r) => r.is_active);
  if (draft.status === "open" && active.length === 0) {
    issues.push({ path: "rounds", message: "An open job needs at least one round before candidates can be invited.", severity: "error" });
  } else if (active.length === 0) {
    issues.push({ path: "rounds", message: "Add a round before inviting candidates.", severity: "warning" });
  }
  if (draft.allow_candidate_round_selection) {
    const selectable = active.filter((r) => r.config.candidate_selectable);
    if (selectable.length === 0) {
      issues.push({
        path: "allow_candidate_round_selection",
        message: "No round is marked “Candidates can pick this round” (round editor, Candidate tab), so invitations can't offer a choice yet.",
        severity: "warning",
      });
    }
  }
  const names = new Map<string, number>();
  for (const round of draft.rounds) {
    const key = round.config.name.trim().toLowerCase();
    names.set(key, (names.get(key) ?? 0) + 1);
    for (const issue of validateRoundConfig(round.config)) {
      issues.push({ path: `rounds.${round.uid}.${issue.path}`, message: issue.message, severity: issue.severity, roundUid: round.uid });
    }
  }
  for (const [name, count] of names) if (name && count > 1) issues.push({ path: "rounds", message: `Two rounds are called "${name}". Give each round a distinct name.`, severity: "warning" });
  return issues;
}

export function roundIssues(issues: readonly JobIssue[], uid: string): ConfigIssue[] {
  const prefix = `rounds.${uid}.`;
  return issues.filter((i) => i.roundUid === uid).map((i) => ({ path: i.path.slice(prefix.length), message: i.message, severity: i.severity }));
}

// ------------------------------------------------------------------ diffing

export interface RequirementPlan {
  create: JobRequirementInput[];
  update: Array<{ id: string; input: Partial<JobRequirementInput> }>;
  remove: string[];
}

/** Turns the skill tags into requirement creates/updates/deletes. Other kinds are left untouched. */
export function planRequirements(draft: JobDraft, existing: readonly JobRequirement[], catalog: readonly Competency[]): RequirementPlan {
  const byKey = new Map(catalog.map((c) => [c.key, c] as const));
  const plan: RequirementPlan = { create: [], update: [], remove: [] };
  const desired: Array<{ kind: "required_skill" | "preferred_skill"; value: string; order: number }> = [
    ...draft.required_skills.map((value, order) => ({ kind: "required_skill" as const, value, order })),
    ...draft.preferred_skills.map((value, order) => ({ kind: "preferred_skill" as const, value, order })),
  ];
  const skillRows = existing.filter((r) => r.kind === "required_skill" || r.kind === "preferred_skill");
  const matched = new Set<string>();
  for (const want of desired) {
    const competency = byKey.get(want.value);
    const match = skillRows.find(
      (r) => !matched.has(r.id) && r.kind === want.kind && (competency ? r.competency_key === competency.key : !r.competency_key && r.name.toLowerCase() === want.value.toLowerCase()),
    );
    if (match) {
      matched.add(match.id);
      if (match.sort_order !== want.order) plan.update.push({ id: match.id, input: { sort_order: want.order } });
      continue;
    }
    plan.create.push({
      kind: want.kind,
      name: competency ? competency.name : want.value.trim(),
      competency_key: competency ? competency.key : null,
      weight: want.kind === "required_skill" ? 1 : 0.5,
      sort_order: want.order,
    });
  }
  for (const row of skillRows) if (!matched.has(row.id)) plan.remove.push(row.id);
  return plan;
}

function roundBody(round: RoundDraft) {
  const config = toRoundConfig(round.config);
  return { ...roundColumns(config), is_active: round.is_active, config };
}

/** `POST /jobs/{id}/rounds`: the complete config is merged onto the template (or round-type default). */
export function roundCreatePayload(round: RoundDraft): RoundInput {
  return { ...roundBody(round), template_id: round.template_id };
}

/** `PATCH /rounds/{id}`: the complete config replaces the stored one (no template_id on update). */
export function roundUpdatePayload(round: RoundDraft): Omit<RoundInput, "template_id"> & { replace_config: true } {
  return { ...roundBody(round), replace_config: true };
}

export interface RoundPlan {
  create: RoundDraft[];
  update: RoundDraft[];
  remove: string[];
  /** True when the draft order differs from what the server will have after the creates. */
  reorder: boolean;
}

export function planRounds(draft: JobDraft, existing: readonly Round[]): RoundPlan {
  const byId = new Map(existing.map((r) => [r.id, r] as const));
  const keep = new Set(draft.rounds.filter((r) => r.id).map((r) => r.id!));
  const update = draft.rounds.filter((r) => {
    if (!r.id) return false;
    const server = byId.get(r.id);
    if (!server) return false;
    const before = roundUpdatePayload(roundDraftFrom(server));
    return !jsonEqual(before, roundUpdatePayload(r));
  });
  const create = draft.rounds.filter((r) => !r.id);
  // The API appends new rounds; reorder only when that order differs from the draft's.
  const serverOrder = [...existing].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0)).map((r) => r.id).filter((id) => keep.has(id));
  const expected = [...serverOrder, ...create.map((r) => r.uid)];
  const desired = draft.rounds.map((r) => r.id ?? r.uid);
  return {
    create,
    update,
    remove: existing.filter((r) => !keep.has(r.id)).map((r) => r.id),
    reorder: !jsonEqual(expected, desired),
  };
}

export function isDraftDirty(a: JobDraft, b: JobDraft): boolean {
  return !jsonEqual(a, b);
}
