/**
 * Intake labels and tones. Copy is sentence case and describes what happened or
 * what the evidence shows, never the person. Server labels (band_label,
 * outcome_label, competency labels) win when present; these are fallbacks.
 */

import type { BadgeTone } from "@/components/ui/Badge";

import type {
  ActivityKind,
  Availability,
  CompetencyKey,
  CountedBucket,
  CoverageStatus,
  DecisionBand,
  DecisionOutcome,
  EvidenceSupport,
  ExperienceComfort,
  IntakeBucket,
  IntakeSort,
  ItemStatus,
  OpenToAiMl,
  PrimaryLanguage,
  ReviewItemKind,
  ReviewItemSeverity,
  ReviewResolution,
  ScriptingLevel,
  StageKey,
  StageStatus,
  TargetRole,
  TrainingCommitment,
  TranscriptStatus,
} from "./types";

export const INTAKE_NAME = "Intake interview";

/** Fairness copy, fixed wording (design 11.1). */
export const AI_RECOMMENDATION_NOTE = "AI recommendation — a human reviewer decides";
export const DECISION_ADVISORY_NOTE = "The AI recommendation is advisory. Your decision is recorded with your name and rationale.";
export const ELIGIBILITY_NOTE = "For human review only — never used by the AI interviewer or in any score";
export const OBSERVATIONS_NOTE = "Signals are observations, not determinations.";
export const PRONUNCIATION_NOTE = "Pronunciation: not assessed";
export const TECHNICAL_READING_FORMULA = "Technical Reading & Relay = 0.4 × Drill A + 0.6 × Drill B";
export const DISABLED_MESSAGE = "Intake interviews are turned off for this workspace";

// ------------------------------------------------------------------ buckets

export const BUCKET_LABELS: Record<IntakeBucket, string> = {
  all: "All",
  drafts: "Drafts",
  active: "Active invitations",
  scheduled: "Scheduled",
  in_progress: "In progress",
  completed: "Completed",
  awaiting_review: "Awaiting review",
  accepted: "Accepted",
  borderline: "Borderline",
  rejected: "Rejected",
  training: "Training recommendations",
  expired: "Expired",
  cancelled: "Cancelled",
};

/** Single-row badge for `IntakeSummaryOut.bucket`. */
export const BUCKET_BADGE: Record<IntakeBucket, { label: string; tone: BadgeTone }> = {
  all: { label: "—", tone: "neutral" },
  drafts: { label: "Draft", tone: "outline" },
  active: { label: "Invited", tone: "accent" },
  scheduled: { label: "Scheduled", tone: "accent" },
  in_progress: { label: "In progress", tone: "signal" },
  completed: { label: "Completed", tone: "success" },
  awaiting_review: { label: "Awaiting review", tone: "warning" },
  accepted: { label: "Accepted", tone: "success" },
  borderline: { label: "Borderline", tone: "warning" },
  rejected: { label: "Rejected", tone: "neutral" },
  training: { label: "Training", tone: "accent" },
  expired: { label: "Expired", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

/** Tabs on the list page, in order. Expired and cancelled appear only when they hold rows. */
export const BUCKET_TABS: readonly IntakeBucket[] = [
  "all",
  "drafts",
  "active",
  "scheduled",
  "in_progress",
  "completed",
  "awaiting_review",
  "accepted",
  "borderline",
  "rejected",
  "training",
  "expired",
  "cancelled",
];
export const OPTIONAL_BUCKETS: ReadonlySet<CountedBucket> = new Set(["expired", "cancelled"]);

export const BUCKET_EMPTY: Record<IntakeBucket, string> = {
  all: "No intake interviews yet.",
  drafts: "No drafts. Save an intake as a draft to finish it later.",
  active: "No open invitations. Invitations stay here until the candidate starts.",
  scheduled: "Nothing scheduled ahead.",
  in_progress: "No one is in an intake interview right now.",
  completed: "No completed intake interviews yet.",
  awaiting_review: "Nothing waiting for a decision.",
  accepted: "No one accepted yet.",
  borderline: "No borderline results.",
  rejected: "No rejections recorded.",
  training: "No training recommendations yet. They appear for accepted candidates and strong AI recommendations.",
  expired: "No expired invitations.",
  cancelled: "No cancelled intakes.",
};

export const SORT_OPTIONS: ReadonlyArray<{ value: IntakeSort; label: string }> = [
  { value: "-created_at", label: "Newest first" },
  { value: "created_at", label: "Oldest first" },
  { value: "-completed_at", label: "Recently completed" },
  { value: "-score", label: "Highest weighted total" },
  { value: "score", label: "Lowest weighted total" },
  { value: "candidate", label: "Candidate name" },
];

// --------------------------------------------------------- bands, outcomes

export const BAND_LABELS: Record<DecisionBand, string> = {
  strong_accept: "Strong accept",
  accept_with_notes: "Accept with notes",
  borderline: "Borderline",
  reject: "Reject",
  insufficient_evidence: "Insufficient evidence",
};

/** Bands are recommendations: "reject" stays neutral so a list never reads as a verdict. */
export const BAND_TONES: Record<DecisionBand, BadgeTone> = {
  strong_accept: "success",
  accept_with_notes: "accent",
  borderline: "warning",
  reject: "neutral",
  insufficient_evidence: "outline",
};

export const BAND_GUIDANCE: Record<DecisionBand, string> = {
  strong_accept: "Fast-track into training.",
  accept_with_notes: "Flag weak areas for the trainer team.",
  borderline: "Second interview or a conditional offer.",
  reject: "Recommend not proceeding; a human reviewer decides.",
  insufficient_evidence: "At least one competency lacks enough evidence, so no band is recommended.",
};

/** Half-open band edges on the 1-5 total (scoring.BAND_THRESHOLDS). */
export const BAND_EDGES: ReadonlyArray<{ band: DecisionBand; from: number }> = [
  { band: "reject", from: 1 },
  { band: "borderline", from: 2.5 },
  { band: "accept_with_notes", from: 3.3 },
  { band: "strong_accept", from: 4.2 },
];

export const OUTCOME_LABELS: Record<DecisionOutcome, string> = {
  accept: "Accept",
  accept_with_notes: "Accept with notes",
  second_interview: "Second interview",
  reject: "Reject",
  request_evidence: "Request evidence",
};

export const OUTCOME_DESCRIPTIONS: Record<DecisionOutcome, string> = {
  accept: "Admit to the training program.",
  accept_with_notes: "Admit, with notes for the trainer team on the areas to strengthen.",
  second_interview: "Hold the decision and invite the candidate to a second conversation.",
  reject: "Do not admit to this program.",
  request_evidence: "Ask the candidate for more information before deciding.",
};

export const OUTCOME_TONES: Record<DecisionOutcome, BadgeTone> = {
  accept: "success",
  accept_with_notes: "success",
  second_interview: "warning",
  reject: "danger",
  request_evidence: "accent",
};

export const RESOLUTION_LABELS: Record<ReviewResolution, string> = {
  confirmed: "Confirmed",
  dismissed: "Dismissed",
  follow_up: "Needs follow-up",
};

// -------------------------------------------------------------- competencies

export const COMPETENCY_LABELS: Record<CompetencyKey, string> = {
  communication: "Communication & Presentation",
  technical_reading: "Technical Reading & Relay",
  structured_answer: "Structured Answer / Gap-Filling",
  technical: "Actual Technical Ability",
  trainability: "Trainability & Learning Attitude",
  composure: "Confidence & Composure",
  adaptability: "Niche-Fit & Adaptability",
};

/** Display fallback only; the API sends the weight with every competency row. */
export const COMPETENCY_WEIGHTS: Record<CompetencyKey, string> = {
  communication: "0.20",
  technical_reading: "0.20",
  structured_answer: "0.15",
  technical: "0.15",
  trainability: "0.15",
  composure: "0.10",
  adaptability: "0.05",
};

export function competencyLabel(key: string): string {
  return COMPETENCY_LABELS[key as CompetencyKey] ?? key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

// ------------------------------------------------------------------- stages

export const STAGE_LABELS: Record<StageKey, string> = {
  greeting: "Greetings & self-presentation",
  resume: "Résumé-related questions",
  mandatory: "Mandatory screening questions",
  reading: "Technical reading & relay",
  topic: "Structured answer / gap-filling",
  learning: "Niche-fit & trainability probe",
  close: "Candidate questions & close",
};

/** Short stage names for dense strips. */
export const STAGE_SHORT_LABELS: Record<StageKey, string> = {
  greeting: "Introduction",
  resume: "Résumé",
  mandatory: "Standard questions",
  reading: "Reading",
  topic: "Topic card",
  learning: "Learning",
  close: "Close",
};

export const STAGE_STATUS_LABELS: Record<StageStatus, string> = {
  pending: "Not started",
  in_progress: "In progress",
  completed: "Completed",
  incomplete: "Incomplete",
};

export const STAGE_STATUS_TONES: Record<StageStatus, BadgeTone> = {
  pending: "outline",
  in_progress: "signal",
  completed: "success",
  incomplete: "warning",
};

export const ITEM_STATUS_LABELS: Record<ItemStatus, string> = {
  pending: "Not asked",
  asked: "Asked",
  answered: "Answered",
  prefilled: "From the form",
  skipped: "Skipped",
  declined: "Declined",
  not_reached: "Not reached",
  interrupted: "Interrupted",
  not_applicable: "Not applicable",
};

export const SKIP_REASON_LABELS: Record<string, string> = {
  time_budget: "Skipped to stay on time",
  policy_disabled: "Turned off by policy",
  prefilled: "Answered on the form",
  declined: "The candidate preferred to skip it",
  not_hands_on: "Skipped after a team-level answer",
  unclear: "Still unclear after a clarification",
  activity_interrupted: "The activity was interrupted",
  time_up: "Time ran out",
};

export const ACTIVITY_LABELS: Record<ActivityKind, string> = {
  reading_aloud: "Drill A · read aloud",
  read_relay: "Drill B · read and relay",
  topic_card: "Topic card",
};

export const TRANSCRIPT_SOURCE_LABELS: Record<string, string> = {
  final_pass_live: "Final transcript (full pass after the reading)",
  final_pass_worker: "Final transcript (background pass)",
  live_segments: "Live transcript (the final pass was not available)",
  typed: "Typed by the candidate",
};

export const TRANSCRIPT_STATUS_LABELS: Record<TranscriptStatus, string> = {
  pending: "Transcribing",
  final: "Final",
  pending_worker: "Transcribing in the background",
  live_only: "Live transcript only",
  unavailable: "No transcript",
};

export const ACTIVITY_END_LABELS: Record<string, string> = {
  finish_button: "Candidate pressed finish",
  deadline: "Time limit reached",
  silence: "Ended after silence",
  disconnect: "Disconnected",
  max_audio: "Maximum length reached",
  session_end: "Interview ended",
  session_ended: "Interview ended",
  skipped: "Skipped",
};

// -------------------------------------------------------------- review items

export const REVIEW_ITEM_LABELS: Record<ReviewItemKind, string> = {
  claims_not_supported: "Résumé claim or self-rating not supported",
  reading_not_completed: "Passage not read aloud",
  work_authorization_review: "Work authorisation answer for review",
  mandatory_mismatch: "Hard mismatch on role, training or experience level",
  communication_unclear: "Communication clarity concern",
  stage_incomplete: "Stage not completed",
  mandatory_item_missing: "Mandatory question not covered",
  activity_interrupted: "Activity interrupted",
  asr_quality_low: "Speech recognition quality was low; listen to the recording",
  integrity_observation: "Integrity observations to review",
  evaluator_fallback: "An evaluator could not run",
  evaluator_disagreement: "Evaluators disagree",
};

export const SEVERITY_LABELS: Record<ReviewItemSeverity, string> = {
  important: "Important",
  attention: "Check",
  info: "Info",
};

export const SEVERITY_TONES: Record<ReviewItemSeverity, BadgeTone> = {
  important: "warning",
  attention: "neutral",
  info: "outline",
};

export const SUPPORT_LABELS: Record<EvidenceSupport, string> = {
  positive: "Supports",
  negative: "Gap",
  context: "Context",
};

export const COVERAGE_LABELS: Record<CoverageStatus, string> = {
  covered: "Covered",
  partial: "Partly covered",
  missing: "Missing",
};

export const COVERAGE_TONES: Record<CoverageStatus, BadgeTone> = {
  covered: "success",
  partial: "warning",
  missing: "neutral",
};

export const PARAPHRASE_LABELS: Record<string, string> = {
  own_words: "In their own words",
  mixed: "Partly in their own words",
  recited: "Mostly recited from the passage",
  insufficient: "Too little to judge",
};

export const FLOW_LABELS: Record<string, string> = {
  natural: "Natural flow between points",
  mechanical: "Points covered one by one",
  disjointed: "Points not connected",
};

export const GAP_HANDLING_LABELS: Record<string, string> = {
  honest: "Gaps acknowledged honestly",
  bluffed: "Gaps covered with unsupported claims",
  not_needed: "No gaps to handle",
};

export const AI_ML_OPENNESS_LABELS: Record<string, string> = {
  eager: "Eager to work in AI/ML",
  open: "Open to AI/ML",
  hesitant: "Hesitant about AI/ML",
  refuses: "Not interested in AI/ML",
  unknown: "Not discussed",
};

export const VERDICT_LABELS: Record<string, string> = {
  hands_on: "Hands-on, supported",
  team_level: "Team-level involvement",
  conceptual: "Conceptual understanding",
  unsupported: "Not supported by the answers",
  uncertain: "Unclear",
  inconsistent: "Inconsistent with other answers",
};

export const VERDICT_TONES: Record<string, BadgeTone> = {
  hands_on: "success",
  team_level: "accent",
  conceptual: "neutral",
  unsupported: "warning",
  uncertain: "outline",
  inconsistent: "warning",
};

export const RATING_SUPPORTED_LABELS: Record<string, string> = {
  supported: "Self-rating supported",
  partially: "Self-rating partly supported",
  not_supported: "Self-rating not supported",
  unknown: "Self-rating not checked",
};

export const VERIFICATION_LABELS: Record<string, string> = {
  correct: "Correct",
  partially_correct: "Partly correct",
  incorrect: "Incorrect",
  no_answer: "No answer",
  not_asked: "Not asked",
};

export const COMPOSURE_LABELS: Record<string, string> = {
  kept_going_when_unsure: "Kept going when unsure",
  handled_followup: "Handled a follow-up",
  recovered_from_mistake: "Recovered from a mistake",
  stalled_when_unsure: "Stalled when unsure",
  abandoned_answer: "Left an answer unfinished",
};

// --------------------------------------------------------------- onboarding

export const TARGET_ROLE_LABELS: Record<TargetRole, string> = {
  ai_ml: "AI/ML engineer",
  data_analyst: "Data analyst",
  product_manager_technical: "Technical product manager",
  product_manager_growth: "Growth product manager",
  other: "Other role",
};

export const OPEN_TO_AI_ML_LABELS: Record<OpenToAiMl, string> = { yes: "Yes", maybe: "Maybe", no: "No" };
export const SCRIPTING_LABELS: Record<ScriptingLevel, string> = { none: "None", some: "Some", regular: "Regularly" };
export const COMFORT_LABELS: Record<ExperienceComfort, string> = { yes: "Yes", unsure: "Unsure", no: "No" };
export const COMMITMENT_LABELS: Record<TrainingCommitment, string> = { full: "Full commitment", partial: "Partial commitment", cannot: "Cannot commit" };
export const AVAILABILITY_LABELS: Record<Availability, string> = {
  immediately: "Immediately",
  within_2_weeks: "Within 2 weeks",
  within_1_month: "Within a month",
  later: "Later",
};
export const LANGUAGE_LABELS: Record<PrimaryLanguage, string> = {
  python: "Python",
  java: "Java",
  javascript: "JavaScript",
  csharp: "C#",
  sql: "SQL",
  other: "Other",
};

export const TRAINING_MODULE_LABELS: Record<string, string> = {
  programming_fundamentals: "Programming fundamentals",
  python_fundamentals: "Python fundamentals",
  sql_fundamentals: "SQL fundamentals",
  ai_ml_fundamentals: "AI/ML fundamentals",
  technical_reading_practice: "Technical reading practice",
  technical_explanations: "Technical explanations",
  structured_speaking: "Structured speaking",
  communication_practice: "Communication practice",
  interview_preparation: "Interview preparation",
  learning_habits: "Learning habits",
};

/** Label lookup with a readable fallback for values added after this file. */
export function lookup(labels: Record<string, string>, value: string | null | undefined, empty = "—"): string {
  if (value === null || value === undefined || value === "") return empty;
  return labels[value] ?? value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
