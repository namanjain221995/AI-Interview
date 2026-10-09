/**
 * Round configuration model for the editor.
 *
 * `RoundConfig` (app/schemas/rounds.py) is validated with `extra="forbid"`, so
 * everything sent to the API goes through `toRoundConfig`, which emits exactly
 * the schema's keys. `withDefaults` accepts partial or older configs (from
 * templates, round-type defaults or saved rounds) and fills every field with
 * the backend's defaults, so the editor always works on a complete object.
 */

import type {
  CodeLanguage,
  CodingSection,
  Difficulty,
  DifficultyDistribution,
  PassingGuidance,
  QuestionStrategy,
  ResumeValidation,
  RoundCompetency,
  RoundConfig,
  RoundType,
  RubricCategory,
  StoppingConditions,
} from "@/lib/types";
import { ROUND_TYPE_LABELS } from "@/lib/labels";

import { readIntakeRound, validateIntakeRound } from "@/features/intake/staff/lib/roundIntake";

import { blankToNull, cleanList } from "../lib/utils";
import { normalizeDistribution } from "./distribution";
import { isRubricBalanced, rubricBalanceMessage } from "./rubric";

// ---------------------------------------------------------------- vocabulary

export const ALL_CODE_LANGUAGES: readonly CodeLanguage[] = ["python", "javascript", "typescript", "java", "go", "cpp", "sql"];
export const FUNCTION_LANGUAGES: readonly CodeLanguage[] = ["python", "javascript", "typescript", "java", "go", "cpp"];

/** question_strategy.style vocabulary (samples/catalog/round_templates.yaml). */
export const QUESTION_STYLES: ReadonlyArray<{ value: string; label: string; description: string }> = [
  { value: "experience_deep_dive", label: "Experience deep dive", description: "Walk through real work in detail" },
  { value: "scenario", label: "Scenario", description: "What would you do if…" },
  { value: "conceptual", label: "Conceptual", description: "Explain how something works" },
  { value: "resume_claim_verification", label: "Resume claim check", description: "Probe specific resume statements" },
  { value: "tradeoff_probe", label: "Trade-off probe", description: "Compare options and justify a choice" },
  { value: "failure_mode_probe", label: "Failure modes", description: "What breaks and how to recover" },
  { value: "scale_probe", label: "Scale probe", description: "How it changes at 10x or 100x" },
  { value: "debugging_walkthrough", label: "Debugging walkthrough", description: "Diagnose a described problem" },
  { value: "design_exercise", label: "Design exercise", description: "Design a system step by step" },
  { value: "estimation", label: "Estimation", description: "Back-of-the-envelope numbers" },
  { value: "coding_exercise", label: "Coding exercise", description: "Live problem in the editor" },
  { value: "complexity_probe", label: "Complexity probe", description: "Time and space analysis" },
  { value: "edge_case_probe", label: "Edge cases", description: "Inputs that break naive solutions" },
  { value: "star_behavioral", label: "Behavioral (STAR)", description: "Situation, task, action, result" },
  { value: "conversational", label: "Conversational", description: "Open, relaxed questions" },
  { value: "motivation", label: "Motivation", description: "Why this role and team" },
  { value: "logistics", label: "Logistics", description: "Availability, location, notice period" },
  { value: "strategy", label: "Strategy", description: "Direction-setting and prioritisation" },
];

/** Evaluation dimensions a rubric category can roll up (DimensionScores + coding criteria). */
export const RUBRIC_DIMENSIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "correctness", label: "Correctness" },
  { value: "relevance", label: "Relevance" },
  { value: "completeness", label: "Completeness" },
  { value: "depth", label: "Depth" },
  { value: "specificity", label: "Specificity" },
  { value: "practical_experience", label: "Practical experience" },
  { value: "reasoning", label: "Reasoning" },
  { value: "consistency", label: "Consistency" },
  { value: "architecture_awareness", label: "Architecture awareness" },
  { value: "tradeoff_understanding", label: "Trade-off understanding" },
  { value: "communication_clarity", label: "Clarity of content" },
  { value: "code_quality", label: "Code quality" },
  { value: "problem_solving", label: "Problem solving (coding)" },
  { value: "edge_case_handling", label: "Edge-case handling" },
  { value: "testing_approach", label: "Testing approach" },
];

export const ROUND_TYPES = Object.keys(ROUND_TYPE_LABELS) as RoundType[];

// ------------------------------------------------------------------ defaults

export function defaultQuestionStrategy(): QuestionStrategy {
  return {
    style: ["experience_deep_dive", "scenario", "conceptual"],
    max_followup_depth: 4,
    prefer_curated_ratio: 0.4,
    allow_generated_questions: true,
    adaptive_difficulty: true,
    allow_candidate_questions_at_end: true,
  };
}

export function defaultStopping(): StoppingConditions {
  return { stop_on_sufficient_evidence: true, min_duration_minutes: 8, max_questions: null, min_questions: 4 };
}

export function defaultCoding(): CodingSection {
  return {
    enabled: false,
    duration_minutes: 20,
    languages: [...FUNCTION_LANGUAGES],
    difficulty: "medium",
    question_ids: [],
    allow_generated: false,
    max_problems: 1,
    hints_enabled: true,
    max_hints: 3,
    hint3_may_reveal_solution: false,
    restrict_paste: true,
    large_paste_threshold_chars: 200,
    proactive_comments: true,
    min_seconds_between_comments: 75,
    start_after_minutes: null,
  };
}

export function defaultResumeValidation(): ResumeValidation {
  return { enabled: true, max_claims: 4, min_priority: 2 };
}

export const HUMAN_REVIEW_NOTE = "Recommendations are advisory. A human reviewer owns the hiring decision.";

export function defaultPassingGuidance(): PassingGuidance {
  return {
    strong_proceed_min_score: 80,
    proceed_min_score: 65,
    borderline_min_score: 50,
    min_confidence: 0.6,
    min_required_coverage: 0.75,
    reviewer_notes: HUMAN_REVIEW_NOTE,
  };
}

export const DEFAULT_INSTRUCTIONS =
  "Answer naturally, as you would with a human interviewer. Take a moment to think when you need it.";

/**
 * A round config that may carry the intake section (`RoundConfig.intake`, intake rounds
 * only; docs/design/INTAKE_INTERVIEW.md 4.1). The editor keeps it as an opaque object.
 */
export type RoundConfigWithIntake = RoundConfig & { intake?: Record<string, unknown> | null };

export const INTAKE_DURATION = { min: 35, max: 50, default: 45 } as const;

/** Intake rounds start from the default intake template (samples/catalog/round_templates.yaml). */
function intakeRoundConfig(name?: string): RoundConfigWithIntake {
  const competencies: Array<[string, number, string]> = [
    ["communication", 2.0, "Communication & Presentation"],
    ["technical_reading", 2.0, "Technical Reading & Relay"],
    ["structured_answer", 1.5, "Structured Answer / Gap-Filling"],
    ["technical", 1.5, "Actual Technical Ability"],
    ["trainability", 1.5, "Trainability & Learning Attitude"],
    ["composure", 1.0, "Confidence & Composure"],
    ["adaptability", 0.5, "Niche-Fit & Adaptability"],
  ];
  return {
    round_type: "intake",
    name: name ?? "Intake Interview",
    duration_minutes: INTAKE_DURATION.default,
    max_duration_minutes: INTAKE_DURATION.max,
    expected_difficulty: "medium",
    difficulty_distribution: { easy: 0.3, medium: 0.4, hard: 0.3 },
    competencies: competencies.map(([key, weight]) => ({ key, name: null, weight, required: true, min_evidence: null, target_difficulty: null, focus_areas: [] })),
    rubric: competencies.map(([key, weight, label]) => ({ key, label, weight: weight * 10, competency_keys: [key], dimensions: [], description: null })),
    question_strategy: { style: ["experience_deep_dive", "resume_claim_verification"], max_followup_depth: 2, prefer_curated_ratio: 0, allow_generated_questions: true, adaptive_difficulty: false, allow_candidate_questions_at_end: true },
    min_evidence_per_competency: 3,
    confidence_threshold: 0.7,
    stopping: { stop_on_sufficient_evidence: false, min_duration_minutes: 30, max_questions: null, min_questions: 10 },
    coding: { ...defaultCoding(), enabled: false },
    resume_validation: { enabled: true, max_claims: 6, min_priority: 1 },
    passing_guidance: defaultPassingGuidance(),
    instructions:
      "This intake interview has seven short parts: an introduction, your experience, a few standard questions, a reading exercise, a short speaking exercise, how you learn, and your questions. It takes about 45 minutes. Answer in your own words; it's fine to say when you don't know something.",
    interviewer_notes: "Intake mode is driven by the deterministic stage machine; these notes are not used.",
    candidate_selectable: false,
    intake: {},
  };
}

export function defaultRoundConfig(roundType: RoundType = "technical_screening", name?: string): RoundConfig {
  if (roundType === "intake") return intakeRoundConfig(name);
  return {
    round_type: roundType,
    name: name ?? ROUND_TYPE_LABELS[roundType] ?? "Interview round",
    duration_minutes: 30,
    max_duration_minutes: 40,
    expected_difficulty: "medium",
    difficulty_distribution: { easy: 0.3, medium: 0.4, hard: 0.3 },
    competencies: [],
    rubric: [],
    question_strategy: defaultQuestionStrategy(),
    min_evidence_per_competency: 3,
    confidence_threshold: 0.7,
    stopping: defaultStopping(),
    coding: { ...defaultCoding(), enabled: roundType === "coding" },
    resume_validation: defaultResumeValidation(),
    passing_guidance: defaultPassingGuidance(),
    instructions: DEFAULT_INSTRUCTIONS,
    interviewer_notes: null,
    candidate_selectable: false,
  };
}

// --------------------------------------------------------------- tolerance

type Loose<T> = { [K in keyof T]?: T[K] | null } | null | undefined;

function num(value: unknown, fallback: number): number {
  const n = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
}

function numOrNull(value: unknown, fallback: number | null): number | null {
  if (value === null) return null;
  if (value === undefined) return fallback;
  const n = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : fallback;
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function str(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function strOrNull(value: unknown, fallback: string | null): string | null {
  if (value === null) return null;
  return typeof value === "string" ? value : fallback;
}

function strList(value: unknown, fallback: string[]): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : fallback;
}

function difficulty(value: unknown, fallback: Difficulty): Difficulty {
  return value === "easy" || value === "medium" || value === "hard" ? value : fallback;
}

function difficultyOrNull(value: unknown): Difficulty | null {
  return value === "easy" || value === "medium" || value === "hard" ? value : null;
}

function languages(value: unknown, fallback: CodeLanguage[]): CodeLanguage[] {
  if (!Array.isArray(value)) return fallback;
  return value.filter((v): v is CodeLanguage => ALL_CODE_LANGUAGES.includes(v as CodeLanguage));
}

function competency(raw: unknown): RoundCompetency | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  if (typeof c.key !== "string") return null;
  return {
    key: c.key,
    name: strOrNull(c.name, null),
    weight: num(c.weight, 1),
    required: bool(c.required, true),
    min_evidence: numOrNull(c.min_evidence, null),
    target_difficulty: difficultyOrNull(c.target_difficulty),
    focus_areas: strList(c.focus_areas, []),
  };
}

function rubricCategory(raw: unknown): RubricCategory | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  if (typeof c.key !== "string" && typeof c.label !== "string") return null;
  return {
    key: str(c.key, ""),
    label: str(c.label, ""),
    weight: num(c.weight, 0),
    competency_keys: strList(c.competency_keys, []),
    dimensions: strList(c.dimensions, []),
    description: strOrNull(c.description, null),
  };
}

/**
 * A complete RoundConfig from anything config-like. Unknown keys are dropped;
 * missing or malformed values take the backend defaults. The difficulty mix is
 * normalised to whole percentages.
 */
export function withDefaults(partial: Loose<RoundConfig> | Record<string, unknown>, overrides?: { round_type?: RoundType; name?: string }): RoundConfigWithIntake {
  const p = (partial ?? {}) as Record<string, unknown>;
  const roundType = (overrides?.round_type ?? (ROUND_TYPES.includes(p.round_type as RoundType) ? p.round_type : undefined) ?? "technical_screening") as RoundType;
  const base = defaultRoundConfig(roundType, overrides?.name ?? (typeof p.name === "string" && p.name ? p.name : undefined));
  const qs = (p.question_strategy ?? {}) as Record<string, unknown>;
  const st = (p.stopping ?? {}) as Record<string, unknown>;
  const cd = (p.coding ?? {}) as Record<string, unknown>;
  const rv = (p.resume_validation ?? {}) as Record<string, unknown>;
  const pg = (p.passing_guidance ?? {}) as Record<string, unknown>;
  const dd = (p.difficulty_distribution ?? null) as Partial<DifficultyDistribution> | null;

  return {
    round_type: roundType,
    name: base.name,
    duration_minutes: num(p.duration_minutes, base.duration_minutes),
    max_duration_minutes: num(p.max_duration_minutes, Math.max(base.max_duration_minutes, num(p.duration_minutes, base.duration_minutes))),
    expected_difficulty: difficulty(p.expected_difficulty, base.expected_difficulty),
    difficulty_distribution: normalizeDistribution(dd ?? base.difficulty_distribution),
    competencies: Array.isArray(p.competencies) ? p.competencies.map(competency).filter((c): c is RoundCompetency => c !== null) : [],
    rubric: Array.isArray(p.rubric) ? p.rubric.map(rubricCategory).filter((c): c is RubricCategory => c !== null) : [],
    question_strategy: {
      style: strList(qs.style, base.question_strategy.style),
      max_followup_depth: num(qs.max_followup_depth, base.question_strategy.max_followup_depth),
      prefer_curated_ratio: num(qs.prefer_curated_ratio, base.question_strategy.prefer_curated_ratio),
      allow_generated_questions: bool(qs.allow_generated_questions, base.question_strategy.allow_generated_questions),
      adaptive_difficulty: bool(qs.adaptive_difficulty, base.question_strategy.adaptive_difficulty),
      allow_candidate_questions_at_end: bool(qs.allow_candidate_questions_at_end, base.question_strategy.allow_candidate_questions_at_end),
    },
    min_evidence_per_competency: num(p.min_evidence_per_competency, base.min_evidence_per_competency),
    confidence_threshold: num(p.confidence_threshold, base.confidence_threshold),
    stopping: {
      stop_on_sufficient_evidence: bool(st.stop_on_sufficient_evidence, base.stopping.stop_on_sufficient_evidence),
      min_duration_minutes: num(st.min_duration_minutes, base.stopping.min_duration_minutes),
      max_questions: numOrNull(st.max_questions, base.stopping.max_questions),
      min_questions: num(st.min_questions, base.stopping.min_questions),
    },
    coding: {
      enabled: bool(cd.enabled, base.coding.enabled),
      duration_minutes: num(cd.duration_minutes, base.coding.duration_minutes),
      languages: languages(cd.languages, base.coding.languages),
      difficulty: difficulty(cd.difficulty, base.coding.difficulty),
      question_ids: strList(cd.question_ids, []),
      allow_generated: bool(cd.allow_generated, base.coding.allow_generated),
      max_problems: num(cd.max_problems, base.coding.max_problems),
      hints_enabled: bool(cd.hints_enabled, base.coding.hints_enabled),
      max_hints: num(cd.max_hints, base.coding.max_hints),
      hint3_may_reveal_solution: bool(cd.hint3_may_reveal_solution, base.coding.hint3_may_reveal_solution),
      restrict_paste: bool(cd.restrict_paste, base.coding.restrict_paste),
      large_paste_threshold_chars: num(cd.large_paste_threshold_chars, base.coding.large_paste_threshold_chars),
      proactive_comments: bool(cd.proactive_comments, base.coding.proactive_comments),
      min_seconds_between_comments: num(cd.min_seconds_between_comments, base.coding.min_seconds_between_comments),
      start_after_minutes: numOrNull(cd.start_after_minutes, base.coding.start_after_minutes),
    },
    resume_validation: {
      enabled: bool(rv.enabled, base.resume_validation.enabled),
      max_claims: num(rv.max_claims, base.resume_validation.max_claims),
      min_priority: num(rv.min_priority, base.resume_validation.min_priority),
    },
    passing_guidance: {
      strong_proceed_min_score: num(pg.strong_proceed_min_score, base.passing_guidance.strong_proceed_min_score),
      proceed_min_score: num(pg.proceed_min_score, base.passing_guidance.proceed_min_score),
      borderline_min_score: num(pg.borderline_min_score, base.passing_guidance.borderline_min_score),
      min_confidence: num(pg.min_confidence, base.passing_guidance.min_confidence),
      min_required_coverage: num(pg.min_required_coverage, base.passing_guidance.min_required_coverage),
      reviewer_notes: str(pg.reviewer_notes, base.passing_guidance.reviewer_notes),
    },
    instructions: str(p.instructions, base.instructions),
    interviewer_notes: strOrNull(p.interviewer_notes, base.interviewer_notes),
    candidate_selectable: bool(p.candidate_selectable, base.candidate_selectable),
    // The intake section travels untouched (opaque) and only on intake rounds.
    ...(roundType === "intake" ? { intake: p.intake && typeof p.intake === "object" && !Array.isArray(p.intake) ? { ...(p.intake as Record<string, unknown>) } : {} } : {}),
  };
}

// ---------------------------------------------------------------- outbound

/**
 * The exact RoundConfig JSON the API accepts: schema keys only, trimmed
 * strings, blank optional text as null, list entries cleaned.
 */
export function toRoundConfig(config: RoundConfigWithIntake): RoundConfigWithIntake {
  return {
    round_type: config.round_type,
    name: config.name.trim(),
    duration_minutes: config.duration_minutes,
    max_duration_minutes: Math.max(config.max_duration_minutes, config.duration_minutes),
    expected_difficulty: config.expected_difficulty,
    difficulty_distribution: normalizeDistribution(config.difficulty_distribution),
    competencies: config.competencies.map((c) => ({
      key: c.key.trim(),
      name: blankToNull(c.name),
      weight: c.weight,
      required: c.required,
      min_evidence: c.min_evidence ?? null,
      target_difficulty: c.target_difficulty ?? null,
      focus_areas: cleanList(c.focus_areas),
    })),
    rubric: config.rubric.map((c) => ({
      key: c.key.trim(),
      label: c.label.trim(),
      weight: c.weight,
      competency_keys: cleanList(c.competency_keys),
      dimensions: cleanList(c.dimensions),
      description: blankToNull(c.description),
    })),
    question_strategy: {
      style: cleanList(config.question_strategy.style),
      max_followup_depth: config.question_strategy.max_followup_depth,
      prefer_curated_ratio: config.question_strategy.prefer_curated_ratio,
      allow_generated_questions: config.question_strategy.allow_generated_questions,
      adaptive_difficulty: config.question_strategy.adaptive_difficulty,
      allow_candidate_questions_at_end: config.question_strategy.allow_candidate_questions_at_end,
    },
    min_evidence_per_competency: config.min_evidence_per_competency,
    confidence_threshold: config.confidence_threshold,
    stopping: {
      stop_on_sufficient_evidence: config.stopping.stop_on_sufficient_evidence,
      min_duration_minutes: config.stopping.min_duration_minutes,
      max_questions: config.stopping.max_questions ?? null,
      min_questions: config.stopping.min_questions,
    },
    coding: {
      enabled: config.coding.enabled,
      duration_minutes: config.coding.duration_minutes,
      languages: [...config.coding.languages],
      difficulty: config.coding.difficulty,
      question_ids: cleanList(config.coding.question_ids),
      allow_generated: config.coding.allow_generated,
      max_problems: config.coding.max_problems,
      hints_enabled: config.coding.hints_enabled,
      max_hints: config.coding.max_hints,
      hint3_may_reveal_solution: config.coding.hint3_may_reveal_solution,
      restrict_paste: config.coding.restrict_paste,
      large_paste_threshold_chars: config.coding.large_paste_threshold_chars,
      proactive_comments: config.coding.proactive_comments,
      min_seconds_between_comments: config.coding.min_seconds_between_comments,
      start_after_minutes: config.coding.start_after_minutes ?? null,
    },
    resume_validation: { ...config.resume_validation },
    passing_guidance: { ...config.passing_guidance, reviewer_notes: config.passing_guidance.reviewer_notes.trim() || HUMAN_REVIEW_NOTE },
    instructions: config.instructions.trim() || DEFAULT_INSTRUCTIONS,
    interviewer_notes: blankToNull(config.interviewer_notes),
    candidate_selectable: config.candidate_selectable,
    // `intake` exists in the schema only for intake rounds; other rounds never send it.
    ...(config.round_type === "intake" ? { intake: JSON.parse(JSON.stringify(config.intake ?? {})) as Record<string, unknown> } : {}),
  };
}

// --------------------------------------------------------------- validation

export type IssueSeverity = "error" | "warning";

export interface ConfigIssue {
  /** Dotted RoundConfig path, e.g. "rubric" or "competencies.2.weight". */
  path: string;
  message: string;
  severity: IssueSeverity;
}

function isInt(value: number): boolean {
  return Number.isFinite(value) && Number.isInteger(value);
}

function rangeIssue(path: string, label: string, value: number | null | undefined, min: number, max: number, integer = true): ConfigIssue | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return { path, message: `${label} is required.`, severity: "error" };
  if (integer && !isInt(value)) return { path, message: `${label} must be a whole number.`, severity: "error" };
  if (value < min || value > max) return { path, message: `${label} must be between ${min} and ${max}.`, severity: "error" };
  return null;
}

/**
 * Mirrors the backend RoundConfig validators (ranges, rubric total) and adds
 * consistency checks the schema cannot express (warnings never block saving).
 */
export function validateRoundConfig(config: RoundConfigWithIntake): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  const push = (issue: ConfigIssue | null) => {
    if (issue) issues.push(issue);
  };
  const intake = config.round_type === "intake";

  if (!config.name.trim()) push({ path: "name", message: "Give the round a name.", severity: "error" });
  else if (config.name.trim().length > 200) push({ path: "name", message: "Keep the name under 200 characters.", severity: "error" });

  if (intake) {
    // Server rules for intake rounds (app/schemas/rounds.py intake branch).
    if (!isInt(config.duration_minutes) || config.duration_minutes < INTAKE_DURATION.min || config.duration_minutes > INTAKE_DURATION.max) {
      push({ path: "duration_minutes", message: `Intake rounds last ${INTAKE_DURATION.min}-${INTAKE_DURATION.max} minutes.`, severity: "error" });
    } else if (config.max_duration_minutes > config.duration_minutes + 5) {
      push({ path: "max_duration_minutes", message: `For intake rounds the hard stop is at most 5 minutes past the duration (${config.duration_minutes + 5} min); it's capped when saved.`, severity: "warning" });
    }
    if (config.coding.enabled) push({ path: "intake", message: "Intake rounds don't include coding. Turn coding off.", severity: "error" });
    if (config.candidate_selectable) push({ path: "candidate_selectable", message: "Candidates can't pick an intake round; it's assigned by invitation.", severity: "error" });
    if (!config.resume_validation.enabled) push({ path: "intake", message: "Intake rounds always check résumé claims; this turns back on when saved.", severity: "warning" });
    for (const issue of validateIntakeRound(readIntakeRound(config.intake))) push(issue);
  } else {
    push(rangeIssue("duration_minutes", "Duration", config.duration_minutes, 5, 240));
  }
  push(rangeIssue("max_duration_minutes", "Maximum duration", config.max_duration_minutes, 5, 300));
  if (isInt(config.duration_minutes) && isInt(config.max_duration_minutes) && config.max_duration_minutes < config.duration_minutes) {
    push({ path: "max_duration_minutes", message: "The maximum can't be shorter than the planned duration.", severity: "error" });
  }

  const dist = config.difficulty_distribution;
  if (dist.easy + dist.medium + dist.hard <= 0) push({ path: "difficulty_distribution", message: "The difficulty mix needs at least one non-zero share.", severity: "error" });

  // Competencies
  const keys = new Set<string>();
  config.competencies.forEach((c, i) => {
    const key = c.key.trim();
    if (!key) push({ path: `competencies.${i}.key`, message: "Choose a competency.", severity: "error" });
    else if (keys.has(key)) push({ path: `competencies.${i}.key`, message: "This competency is already in the round.", severity: "error" });
    keys.add(key);
    push(rangeIssue(`competencies.${i}.weight`, "Weight", c.weight, 0, 10, false));
    if (c.min_evidence !== null && c.min_evidence !== undefined) push(rangeIssue(`competencies.${i}.min_evidence`, "Minimum evidence", c.min_evidence, 1, 20));
  });
  if (config.competencies.length === 0 && !config.coding.enabled) {
    push({ path: "competencies", message: "Add at least one competency so the interviewer knows what to assess.", severity: "warning" });
  } else if (config.competencies.length > 0 && !config.competencies.some((c) => c.required)) {
    push({ path: "competencies", message: "No competency is marked required, so the coverage check has nothing to measure.", severity: "warning" });
  }

  // Rubric
  if (!isRubricBalanced(config.rubric)) push({ path: "rubric", message: rubricBalanceMessage(config.rubric) ?? "Rubric weights must sum to 100.", severity: "error" });
  const rubricKeys = new Set<string>();
  config.rubric.forEach((c, i) => {
    if (!c.label.trim()) push({ path: `rubric.${i}.label`, message: "Name this category.", severity: "error" });
    if (!c.key.trim()) push({ path: `rubric.${i}.key`, message: "Each category needs a key.", severity: "error" });
    else if (rubricKeys.has(c.key.trim())) push({ path: `rubric.${i}.key`, message: "Category keys must be unique.", severity: "error" });
    rubricKeys.add(c.key.trim());
    push(rangeIssue(`rubric.${i}.weight`, "Weight", c.weight, 0, 100, false));
    const stray = c.competency_keys.filter((k) => !keys.has(k));
    if (stray.length) push({ path: `rubric.${i}.competency_keys`, message: `Not in this round: ${stray.join(", ")}. Add them as competencies or remove them here.`, severity: "warning" });
    if (c.competency_keys.length === 0 && c.dimensions.length === 0) {
      push({ path: `rubric.${i}.competency_keys`, message: "Link at least one competency or dimension, or this category can't be scored.", severity: "warning" });
    }
  });

  // Evidence + strategy
  push(rangeIssue("min_evidence_per_competency", "Evidence per competency", config.min_evidence_per_competency, 1, 20));
  push(rangeIssue("confidence_threshold", "Confidence threshold", config.confidence_threshold, 0, 1, false));
  push(rangeIssue("question_strategy.max_followup_depth", "Follow-up depth", config.question_strategy.max_followup_depth, 0, 8));
  push(rangeIssue("question_strategy.prefer_curated_ratio", "Curated share", config.question_strategy.prefer_curated_ratio, 0, 1, false));
  if (config.question_strategy.style.length === 0) push({ path: "question_strategy.style", message: "Pick at least one question style.", severity: "warning" });

  // Stopping
  push(rangeIssue("stopping.min_duration_minutes", "Minimum duration", config.stopping.min_duration_minutes, 0, 300, false));
  push(rangeIssue("stopping.min_questions", "Minimum questions", config.stopping.min_questions, 0, 100));
  if (config.stopping.max_questions !== null) {
    push(rangeIssue("stopping.max_questions", "Maximum questions", config.stopping.max_questions, 1, 200));
    if (isInt(config.stopping.max_questions) && config.stopping.max_questions < config.stopping.min_questions) {
      push({ path: "stopping.max_questions", message: "The maximum can't be lower than the minimum number of questions.", severity: "error" });
    }
  }
  if (config.stopping.min_duration_minutes > config.duration_minutes) {
    push({ path: "stopping.min_duration_minutes", message: "The minimum runs past the planned duration; the round will always use the full time.", severity: "warning" });
  }

  // Coding
  const coding = config.coding;
  if (coding.enabled) {
    if (coding.languages.length === 0) push({ path: "coding.languages", message: "Allow at least one language.", severity: "error" });
    push(rangeIssue("coding.duration_minutes", "Coding time", coding.duration_minutes, 5, 120));
    push(rangeIssue("coding.max_problems", "Problems", coding.max_problems, 1, 5));
    push(rangeIssue("coding.max_hints", "Hints", coding.max_hints, 0, 3));
    push(rangeIssue("coding.large_paste_threshold_chars", "Large paste threshold", coding.large_paste_threshold_chars, 20, 100_000));
    push(rangeIssue("coding.min_seconds_between_comments", "Seconds between comments", coding.min_seconds_between_comments, 20, 3_600));
    if (coding.start_after_minutes !== null) push(rangeIssue("coding.start_after_minutes", "Start after", coding.start_after_minutes, 0, 300, false));
    if (coding.duration_minutes > config.duration_minutes) {
      push({ path: "coding.duration_minutes", message: "Coding time is longer than the whole round.", severity: "error" });
    } else if (coding.duration_minutes > config.duration_minutes * 0.8 && config.competencies.length > 0) {
      push({ path: "coding.duration_minutes", message: "Coding leaves little time for the conversation.", severity: "warning" });
    }
    if (coding.question_ids.length === 0 && !coding.allow_generated) {
      // The engine picks from the curated bank by difficulty and language — fine, just say so.
    }
    const hasSql = coding.languages.includes("sql");
    if (hasSql && coding.languages.length > 1 && coding.question_ids.length === 0) {
      push({ path: "coding.languages", message: "SQL problems and function problems use different editors; pin a problem or keep SQL on its own.", severity: "warning" });
    }
  }

  // Resume validation
  push(rangeIssue("resume_validation.max_claims", "Claims to verify", config.resume_validation.max_claims, 0, 20));
  push(rangeIssue("resume_validation.min_priority", "Minimum priority", config.resume_validation.min_priority, 1, 5));

  // Passing guidance
  const g = config.passing_guidance;
  push(rangeIssue("passing_guidance.strong_proceed_min_score", "Strong evidence threshold", g.strong_proceed_min_score, 0, 100, false));
  push(rangeIssue("passing_guidance.proceed_min_score", "Proceed threshold", g.proceed_min_score, 0, 100, false));
  push(rangeIssue("passing_guidance.borderline_min_score", "Borderline threshold", g.borderline_min_score, 0, 100, false));
  if (g.strong_proceed_min_score < g.proceed_min_score || g.proceed_min_score < g.borderline_min_score) {
    push({ path: "passing_guidance", message: "Thresholds must step down: strong ≥ proceed ≥ borderline.", severity: "error" });
  }
  push(rangeIssue("passing_guidance.min_confidence", "Minimum confidence", g.min_confidence, 0, 1, false));
  push(rangeIssue("passing_guidance.min_required_coverage", "Required coverage", g.min_required_coverage, 0, 1, false));

  if (config.instructions.length > 4000) push({ path: "instructions", message: "Keep candidate instructions under 4,000 characters.", severity: "error" });
  return issues;
}

export function hasBlockingIssues(issues: readonly ConfigIssue[]): boolean {
  return issues.some((i) => i.severity === "error");
}

/** path -> message for errors only (first per path). */
export function issuesToErrors(issues: readonly ConfigIssue[], severity: IssueSeverity = "error"): Record<string, string> {
  const out: Record<string, string> = {};
  for (const issue of issues) if (issue.severity === severity && !(issue.path in out)) out[issue.path] = issue.message;
  return out;
}

// ----------------------------------------------------------------- sections

export type RoundSection = "setup" | "scoring" | "flow" | "coding" | "intake" | "candidate";

export const ROUND_SECTIONS: ReadonlyArray<{ key: RoundSection; label: string }> = [
  { key: "setup", label: "Setup" },
  { key: "scoring", label: "Scoring" },
  { key: "flow", label: "Flow" },
  { key: "coding", label: "Coding" },
  { key: "candidate", label: "Candidate" },
];

/** Editor tabs for a round type: intake rounds swap Coding for their Intake settings. */
export function sectionsFor(roundType: RoundType): ReadonlyArray<{ key: RoundSection; label: string }> {
  if (roundType !== "intake") return ROUND_SECTIONS;
  return ROUND_SECTIONS.map((section) => (section.key === "coding" ? { key: "intake" as const, label: "Intake" } : section));
}

/**
 * Changing the round type: intake rounds get their fixed rules (35-50 minutes, no
 * coding, not candidate-selectable, résumé claims on, an `intake` section); leaving
 * intake drops the section.
 */
export function applyRoundTypeChange(config: RoundConfigWithIntake, roundType: RoundType): RoundConfigWithIntake {
  if (roundType === config.round_type) return config;
  if (roundType === "intake") {
    const duration = config.duration_minutes >= INTAKE_DURATION.min && config.duration_minutes <= INTAKE_DURATION.max ? config.duration_minutes : INTAKE_DURATION.default;
    return {
      ...config,
      round_type: roundType,
      duration_minutes: duration,
      max_duration_minutes: Math.min(Math.max(config.max_duration_minutes, duration), duration + 5),
      coding: { ...config.coding, enabled: false },
      candidate_selectable: false,
      resume_validation: { ...config.resume_validation, enabled: true },
      intake: config.intake ?? {},
    };
  }
  const { intake: _intake, ...rest } = config;
  return { ...rest, round_type: roundType };
}

/** Which editor tab owns a config path. */
export function sectionForPath(path: string): RoundSection {
  const head = path.split(".")[0] ?? path;
  switch (head) {
    case "rubric":
    case "passing_guidance":
    case "confidence_threshold":
    case "min_evidence_per_competency":
      return "scoring";
    case "question_strategy":
    case "stopping":
    case "resume_validation":
      return "flow";
    case "coding":
      return "coding";
    case "intake":
      return "intake";
    case "instructions":
    case "interviewer_notes":
    case "candidate_selectable":
      return "candidate";
    default:
      return "setup";
  }
}

export function countBySection(paths: Iterable<string>): Record<RoundSection, number> {
  const counts: Record<RoundSection, number> = { setup: 0, scoring: 0, flow: 0, coding: 0, intake: 0, candidate: 0 };
  for (const path of paths) counts[sectionForPath(path)] += 1;
  return counts;
}

// --------------------------------------------------------------- summaries

/** Round row columns derived from the config (the API keeps them in sync too). */
export function roundColumns(config: RoundConfig) {
  return {
    name: config.name.trim(),
    round_type: config.round_type,
    duration_minutes: config.duration_minutes,
    max_duration_minutes: Math.max(config.max_duration_minutes, config.duration_minutes),
    expected_difficulty: config.expected_difficulty,
    candidate_selectable: config.candidate_selectable,
    instructions: config.instructions.trim() || null,
  };
}
