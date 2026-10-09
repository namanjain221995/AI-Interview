/**
 * Staff-side mirror of the Intake Interview contract (docs/design/INTAKE_INTERVIEW.md
 * section 15). Field names follow the backend DTOs exactly; `api.ts` normalises every
 * response so a partial or older payload never crashes a page.
 *
 * Decimals (scores, weights, totals) travel as strings ("3.6700"). The web formats them
 * for display only and never recomputes totals or bands.
 */

// ---------------------------------------------------------------- enumerations

export const TARGET_ROLES = ["ai_ml", "data_analyst", "product_manager_technical", "product_manager_growth", "other"] as const;
export type TargetRole = (typeof TARGET_ROLES)[number];

export type OpenToAiMl = "yes" | "maybe" | "no";
export type PrimaryLanguage = "python" | "java" | "javascript" | "csharp" | "sql" | "other";
export type ScriptingLevel = "none" | "some" | "regular";
export type ExperienceComfort = "yes" | "unsure" | "no";
export type TrainingCommitment = "full" | "partial" | "cannot";
export type Availability = "immediately" | "within_2_weeks" | "within_1_month" | "later";
export type PresenceMode = "orb" | "none";
export type IntakeStatus = "draft" | "active" | "cancelled";

export const INTAKE_BUCKETS = [
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
] as const;
export type IntakeBucket = (typeof INTAKE_BUCKETS)[number];
/** Buckets with a count in `IntakeStatsOut.buckets` (every bucket except `all`). */
export type CountedBucket = Exclude<IntakeBucket, "all">;

export const STAGE_KEYS = ["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"] as const;
export type StageKey = (typeof STAGE_KEYS)[number];
export type StageStatus = "pending" | "in_progress" | "completed" | "incomplete";
export type ItemStatus = "pending" | "asked" | "answered" | "prefilled" | "skipped" | "declined" | "not_reached" | "interrupted" | "not_applicable";
export type ActivityKind = "reading_aloud" | "read_relay" | "topic_card";
export type ActivityPhase = "read" | "relay" | "prepare" | "speak";
export type ActivityStatus = "pending" | "armed" | "running" | "completed" | "interrupted" | "skipped" | "failed";
export type TranscriptStatus = "pending" | "final" | "pending_worker" | "live_only" | "unavailable";
export type EvaluationStatus = "pending" | "running" | "ready" | "failed";

export const DECISION_BANDS = ["strong_accept", "accept_with_notes", "borderline", "reject", "insufficient_evidence"] as const;
export type DecisionBand = (typeof DECISION_BANDS)[number];

export const DECISION_OUTCOMES = ["accept", "accept_with_notes", "second_interview", "reject", "request_evidence"] as const;
export type DecisionOutcome = (typeof DECISION_OUTCOMES)[number];

export type ReviewItemKind =
  | "claims_not_supported"
  | "reading_not_completed"
  | "work_authorization_review"
  | "mandatory_mismatch"
  | "communication_unclear"
  | "stage_incomplete"
  | "mandatory_item_missing"
  | "activity_interrupted"
  | "asr_quality_low"
  | "integrity_observation"
  | "evaluator_fallback"
  | "evaluator_disagreement";
export type ReviewItemSeverity = "important" | "attention" | "info";
export type ReviewResolution = "confirmed" | "dismissed" | "follow_up";

export const COMPETENCY_KEYS = ["communication", "technical_reading", "structured_answer", "technical", "trainability", "composure", "adaptability"] as const;
export type CompetencyKey = (typeof COMPETENCY_KEYS)[number];

export type EvidenceSupport = "positive" | "negative" | "context";
export type CoverageStatus = "covered" | "partial" | "missing";

/** A decimal serialised as a string, e.g. "3.6700". */
export type DecimalString = string;

export interface PersonRef {
  id: string;
  full_name: string;
  email?: string;
}

// ---------------------------------------------------------- settings (15.2)

export interface WorkAuthorizationPolicy {
  enabled: boolean;
  country: string | null;
  ask_sponsorship: boolean;
  variant: "default";
}

export interface IntakeInviteSettings {
  duration_minutes: number;
  presence: PresenceMode;
  max_reconnects: number;
  expires_in_hours: number;
  scheduled_for: string | null;
  require_access_code: boolean;
  resume_required: boolean;
  assigned_reviewer_id: string | null;
  notify_on_completion: boolean;
  work_authorization: WorkAuthorizationPolicy | null;
  program_name: string;
  kit_version: string;
  kit_sha256: string;
}

/** What staff send: no program name or kit pins; `work_authorization: null` inherits the organisation default. */
export type IntakeInviteSettingsIn = Omit<IntakeInviteSettings, "program_name" | "kit_version" | "kit_sha256">;

export interface IntakeDefaults {
  duration_minutes: number;
  max_reconnects: number;
  expires_in_hours: number;
  presence: PresenceMode;
  resume_required: boolean;
}

// ------------------------------------------------------- onboarding (15.3)

export interface IntakeOnboarding {
  version: number;
  full_name: string;
  target_role: TargetRole | null;
  target_role_other: string | null;
  open_to_ai_ml: OpenToAiMl | null;
  technical_background: string;
  primary_language: PrimaryLanguage | null;
  primary_language_other: string | null;
  self_rating: number | null;
  scripting: ScriptingLevel | null;
  scripting_details: string | null;
  data_ml_libraries: string | null;
  years_hands_on: number | null;
  experience_level_comfort: ExperienceComfort | null;
  training_commitment: TrainingCommitment | null;
  weekly_hours: number | null;
  availability: Availability | null;
  available_from: string | null;
  accuracy_confirmed: boolean;
  submitted_at: string | null;
}

/** Staff view of the MQ1 answers (never sent to a model, never in reports). */
export interface IntakeEligibilityView {
  enabled: boolean;
  country: string | null;
  ask_sponsorship: boolean;
  authorized: boolean | null;
  needs_sponsorship: boolean | null;
  answered_at: string | null;
}

// ---------------------------------------------------------- résumé (15.4)

export interface ResumeReviewRole {
  ref: string;
  title: string;
  company: string;
  start: string | null;
  end: string | null;
  responsibilities: string[];
  technologies: string[];
  achievements: string[];
}

export interface ResumeReviewProject {
  ref: string;
  name: string;
  role: string | null;
  description: string;
  technologies: string[];
  outcomes: string[];
}

export interface ResumeReviewDoc {
  version: number;
  headline: string | null;
  experience_years: number | null;
  roles: ResumeReviewRole[];
  projects: ResumeReviewProject[];
  skills: string[];
  languages: string[];
  tools: string[];
  education: Array<{ ref: string; institution: string; degree: string | null; field: string | null }>;
  certifications: string[];
}

export interface ResumeCorrection {
  path: string;
  op: "changed" | "added" | "removed";
  before: unknown;
  after: unknown;
}

export interface ResumeReviewRecord {
  action: "confirmed" | "skipped";
  source: { resume_id: string | null; profile_id: string | null; profile_status: string | null };
  original: ResumeReviewDoc | null;
  document: ResumeReviewDoc | null;
  corrections: ResumeCorrection[];
  confirmed_at: string | null;
}

// --------------------------------------------------------- stages (15.5)

export interface StageItemResult {
  item_key: string;
  title: string;
  mandatory: boolean;
  compressible: boolean;
  status: ItemStatus;
  question_ids: string[];
  answer_ids: string[];
  followups_used: number;
  followup_limit: number;
  clarifications: number;
  skip_reason: string | null;
  started_s: number | null;
  ended_s: number | null;
}

export interface IntakeStage {
  key: StageKey;
  number: number;
  title: string;
  status: StageStatus;
  started_s: number | null;
  ended_s: number | null;
  budget_s: number | null;
  items: StageItemResult[];
  extra: Record<string, unknown>;
}

// ------------------------------------------------------ evidence (15.7, 15.8)

export interface EvidenceRef {
  source: "answer" | "activity" | "onboarding" | "resume";
  stage: StageKey | null;
  item_key: string | null;
  question_id: string | null;
  answer_id: string | null;
  activity_id: string | null;
  start_ms: number | null;
  end_ms: number | null;
  quote: string | null;
  supports: EvidenceSupport;
}

export interface ReviewItemResolution {
  resolution: ReviewResolution;
  note: string;
  reviewer: PersonRef | null;
  at: string | null;
}

export interface ReviewItem {
  id: string;
  kind: ReviewItemKind | string;
  severity: ReviewItemSeverity;
  title: string;
  detail: string;
  competency_keys: string[];
  evidence: EvidenceRef[];
  source: "rule" | "evaluator" | "reading" | "validator" | string;
  kit_flag: string | null;
  status: string;
  resolution: ReviewItemResolution | null;
}

// ----------------------------------------------------- training plan (15.10)

export interface TrainingModule {
  key: string;
  title: string;
  priority: number;
  competencies: string[];
  why: string;
  objectives: string[];
  milestones: Array<{ week: number; milestone: string; measure: string }>;
}

export interface TrainingPlan {
  generated_by: "model" | "deterministic" | string;
  summary: string;
  modules: TrainingModule[];
}

// ------------------------------------------------------------ staff DTOs (15.12)

export interface IntakeConfigStage {
  key: StageKey;
  number: number;
  title: string;
  candidate_title: string;
  minutes: { min: number; max: number };
}

export interface IntakeConfigPassage {
  key: string;
  title: string;
  level: string;
  drills: string[];
}

export interface IntakeConfigCard {
  number: number;
  title: string;
  type: string;
  type_label: string;
}

export interface IntakeConfigJob {
  job_id: string;
  title: string;
  status: string;
  round_id: string | null;
  round_name: string | null;
}

export interface IntakeConfig {
  enabled: boolean;
  kit: {
    version: string;
    sha256: string;
    title: string;
    duration: { min: number; max: number; target: number };
    stages: IntakeConfigStage[];
    passages: IntakeConfigPassage[];
    topic_cards: IntakeConfigCard[];
  };
  defaults: IntakeDefaults;
  work_authorization_default: WorkAuthorizationPolicy;
  jobs: IntakeConfigJob[];
}

export interface ApprovedInfoItem {
  topic: string;
  answer: string;
}

export interface IntakeOrgSettingsIn {
  program_name: string;
  work_authorization: WorkAuthorizationPolicy;
  approved_info: ApprovedInfoItem[];
  defaults: IntakeDefaults;
}

export interface IntakeOrgSettings extends IntakeOrgSettingsIn {
  updated_at: string | null;
  updated_by: PersonRef | null;
}

export interface NewCandidateIn {
  full_name: string;
  email: string;
  phone?: string | null;
}

export interface IntakeCreate {
  candidate_id: string | null;
  candidate: NewCandidateIn | null;
  job_id: string;
  round_id: string | null;
  target_role: TargetRole | null;
  title: string | null;
  settings: IntakeInviteSettingsIn;
  notes: string | null;
  send: boolean;
}

export interface IntakeSummary {
  id: string;
  status: IntakeStatus;
  bucket: IntakeBucket;
  title: string;
  batch_id: string | null;
  candidate: { id: string; full_name: string; email: string };
  job: { id: string; title: string };
  round: { id: string; name: string } | null;
  target_role: TargetRole | null;
  interview: { id: string; status: string; invite_expires_at: string | null; scheduled_for: string | null; verified_at: string | null } | null;
  session: { id: string; status: string; started_at: string | null; ended_at: string | null; elapsed_seconds: number } | null;
  onboarding_submitted: boolean;
  resume_confirmed: boolean;
  evaluation: { id: string; status: EvaluationStatus; weighted_total: DecimalString | null; band: DecisionBand | null; evidence_sufficiency: string | null } | null;
  decision: { outcome: DecisionOutcome; created_at: string; reviewer: PersonRef | null } | null;
  assigned_reviewer: PersonRef | null;
  has_training_plan: boolean;
  review_items_open: number;
  integrity_flags: number;
  created_at: string;
  updated_at: string;
}

export interface IntakeCreateResponse {
  intake: IntakeSummary;
  invite_url: string | null;
  access_code: string | null;
  expires_at: string | null;
}

export interface IntakeBatchRowIn {
  full_name: string;
  email: string;
  target_role?: TargetRole | null;
  notes?: string;
}

export interface IntakeBatchCreate {
  job_id: string;
  round_id: string | null;
  settings: IntakeInviteSettingsIn;
  send: boolean;
  rows: IntakeBatchRowIn[];
}

export interface IntakeBatchCreated {
  row: number;
  intake_id: string;
  candidate_id: string;
  email: string;
  candidate_reused: boolean;
  invite_url: string | null;
  access_code: string | null;
  expires_at: string | null;
}

export interface IntakeBatchFailed {
  row: number;
  email: string;
  code: string;
  message: string;
}

export interface IntakeBatchResponse {
  batch_id: string;
  created: IntakeBatchCreated[];
  failed: IntakeBatchFailed[];
}

export interface IntakeUpdate {
  title?: string;
  target_role?: TargetRole | null;
  settings?: IntakeInviteSettingsIn;
  assigned_reviewer_id?: string | null;
  notify_on_completion?: boolean;
  notes?: string | null;
  job_id?: string;
  round_id?: string | null;
  candidate?: NewCandidateIn;
}

export interface IntakeStats {
  buckets: Record<CountedBucket, number>;
  totals: {
    invitations: number;
    started: number;
    completed: number;
    incomplete: number;
    awaiting_review: number;
    decided: number;
    average_weighted_total: DecimalString | null;
    average_duration_s: number | null;
  };
}

// --------------------------------------------------------- review DTOs (15.13)

export interface ActivityMaterial {
  key: string;
  title: string;
  level?: string | null;
  type?: string | null;
  type_label?: string | null;
  paragraphs?: string[];
  bullets?: string[];
  instruction?: string | null;
}

export interface IntakeActivity {
  id: string;
  kind: ActivityKind;
  stage_key: StageKey;
  status: ActivityStatus;
  attempt: number;
  material: ActivityMaterial;
  phases: Array<{ phase: string; status: string; planned_ms: number | null; started_elapsed_s: number | null; ended_elapsed_s: number | null; end_reason: string | null }>;
  reveal: { shown_ms: number | null; windows: Array<{ from_elapsed_s: number; to_elapsed_s: number }>; interrupted: boolean } | null;
  capture: { started_ms: number | null; ended_ms: number | null; voiced_ms: number | null; end_reason: string | null; typed: boolean; pauses_count: number | null; segments_count: number | null } | null;
  transcript: { final: string | null; live: string | null; status: TranscriptStatus; source: string | null };
  question_id: string | null;
  answer_id: string | null;
  audio_media_id: string | null;
}

export interface CompetencyScore {
  key: string;
  label: string;
  kit_label: string;
  weight: DecimalString;
  status: "scored" | "insufficient";
  score: DecimalString | null;
  weighted: DecimalString | null;
  evaluator: string;
  reasoning: string;
  evidence: EvidenceRef[];
  missing_evidence: string[];
  strengths: string[];
  improvements: string[];
  anchors: Partial<Record<"1" | "3" | "5", string>>;
  sub_scores: Record<string, unknown>;
}

/** ReadingMetrics (F0 `reading.py`) without `events`. */
export interface ReadingMetricsView {
  method: string | null;
  reference_words: number | null;
  hypothesis_words: number | null;
  matched: number | null;
  asr_variants: number | null;
  substitutions: number | null;
  omissions: number | null;
  insertions: number | null;
  repetitions: number | null;
  reordered: number | null;
  fillers: number | null;
  wer: number | null;
  strict_wer: number | null;
  completeness: number | null;
  token_coverage: number | null;
  sentences: Array<{ index: number; text: string; status: "read" | "partial" | "skipped"; coverage: number; omitted: number; substituted: number; reordered: boolean }>;
  sentences_read: number | null;
  sentences_partial: number | null;
  sentences_skipped: number | null;
  duration_seconds: number | null;
  words_per_minute: number | null;
  correct_words_per_minute: number | null;
  pause_source: string | null;
  pause_count: number | null;
  long_pause_count: number | null;
  longest_pause_seconds: number | null;
  total_pause_seconds: number | null;
  delivery_score: number | null;
  delivery_basis: string[];
  pronunciation: string;
  review_flags: string[];
}

export type ReadingEventKind = "omission" | "substitution" | "insertion" | "repetition" | "asr_variant" | "reordered";

/** One non-matching alignment step (F0 `ReadingEvent`). */
export interface ReadingEvent {
  kind: ReadingEventKind;
  ref_index: number | null;
  ref_word_index: number | null;
  ref_start: number | null;
  ref_end: number | null;
  ref_word: string | null;
  hyp_index: number | null;
  hyp_word: string | null;
  sentence: number | null;
}

export interface DrillASummary {
  passage: { key: string | null; title: string | null };
  metrics: ReadingMetricsView | null;
  events: ReadingEvent[] | null;
  score: number | null;
  basis: string[];
  /** "insufficient" with a `reason` when the reading couldn't be assessed (additive, design 10.8). */
  status: string | null;
  reason: string | null;
  transcript_source: string | null;
}

export interface RelayConcept {
  key: string;
  text: string;
  status: CoverageStatus;
  quote: string | null;
  evidence: EvidenceRef | null;
}

export interface DrillBSummary {
  passage: { key: string | null; title: string | null };
  coverage: number | null;
  concepts: RelayConcept[];
  paraphrase: string | null;
  misstatements: EvidenceRef[];
  followups: Array<{ question: string; answer: string | null; note: string; evidence: EvidenceRef | null }>;
  score: number | null;
  basis: string[];
  status: string | null;
  reason: string | null;
  shown_ms: number | null;
}

export interface BulletCoverage {
  index: number;
  text: string | null;
  status: CoverageStatus;
  quote: string | null;
  evidence: EvidenceRef | null;
}

export interface ClaimVerdictView {
  /** Claim reference ("C1"). */
  claim: string;
  text: string | null;
  probe: string | null;
  source: string | null;
  verdict: string;
  note: string;
  unresolved: string[];
  evidence: EvidenceRef[];
}

export interface EvaluationSummary {
  language: string | null;
  strengths: string[];
  improvements: string[];
  drill_a: DrillASummary | null;
  drill_b: DrillBSummary | null;
  claims: ClaimVerdictView[];
  verification: { quality: string | null; note: string | null; answer: EvidenceRef | null } | null;
  rating_supported: string | null;
  bullets: BulletCoverage[];
  flow: string | null;
  examples_used: boolean | null;
  gap_handling: string | null;
  followup_analysis: string | null;
  learning_strategy: string | null;
  teach_back: string | null;
  ai_ml_openness: string | null;
  composure_observations: Array<{ behaviour: string; note: string; evidence: EvidenceRef | null }>;
  evaluator_notes: Array<{ evaluator: string; note: string }>;
}

export interface ValidatorIssue {
  code: string;
  competency: string | null;
  detail: string;
}

export interface IntakeEvaluation {
  id: string;
  version: number;
  status: EvaluationStatus;
  reason: string;
  created_at: string;
  finished_at: string | null;
  kit_version: string;
  scoring_version: string;
  weighted_total: DecimalString | null;
  partial_total: DecimalString | null;
  partial_label: string | null;
  normalized_100: DecimalString | null;
  normalized_label: string;
  band: DecisionBand | null;
  band_label: string | null;
  evidence_sufficiency: string | null;
  competencies: CompetencyScore[];
  summary: EvaluationSummary;
  validator_issues: ValidatorIssue[];
  review_items: ReviewItem[];
  training_plan: TrainingPlan | null;
  /** Set when the evaluation failed (additive, design 10.8). */
  error: string | null;
}

export interface IntakeDecision {
  id: string;
  outcome: DecisionOutcome;
  outcome_label: string;
  rationale: string;
  reviewer: PersonRef | null;
  ai_band_at_decision: DecisionBand | null;
  ai_total_at_decision: DecimalString | null;
  overrides_ai: boolean;
  review_items_resolution: Array<{ item_id: string; resolution: ReviewResolution; note: string }>;
  competency_notes: Record<string, string>;
  requested_evidence: string[];
  review_decision_id: string | null;
  evaluation_id: string | null;
  created_at: string;
}

export interface IntakeDecisionIn {
  outcome: DecisionOutcome;
  rationale: string;
  review_items: Array<{ item_id: string; resolution: ReviewResolution; note?: string }>;
  competency_notes: Record<string, string>;
  requested_evidence: string[];
}

export interface IntakeDetail extends IntakeSummary {
  settings: IntakeInviteSettings;
  /** Staff notes; undefined when the server doesn't send them (the detail DTO has no notes). */
  notes?: string | null;
  onboarding: IntakeOnboarding | null;
  eligibility: IntakeEligibilityView | null;
  resume_review: ResumeReviewRecord | null;
  resume: { id: string; original_filename: string; status: string } | null;
  stages: IntakeStage[];
  activities: IntakeActivity[];
  evaluation: IntakeEvaluation | null;
  review_items: ReviewItem[];
  decisions: IntakeDecision[];
  report: { version: number; status: string; artifact_media_id: string | null; generated_at: string | null } | null;
  integrity: { flags: number; unreviewed: number };
  recordings: { parts: number; ready: boolean };
  /** Newest evaluation of any status (design 10.8); `evaluation` is the newest ready one. */
  evaluation_ref: IntakeSummary["evaluation"];
}

export interface TimelineAnswer {
  answer_id: string;
  text: string;
  started_ms: number | null;
  ended_ms: number | null;
  duration_ms: number | null;
  typed: boolean;
  audio_media_id: string | null;
}

export interface TimelineExchange {
  question_id: string;
  text: string;
  asked_ms: number | null;
  template: boolean;
  item_kind: string | null;
  answer: TimelineAnswer | null;
}

export interface TimelineItem {
  item_key: string;
  title: string;
  mandatory: boolean;
  status: ItemStatus;
  skip_reason: string | null;
  exchanges: TimelineExchange[];
}

export interface IntakeTimeline {
  intake_id: string;
  session_id: string | null;
  stages: Array<{ key: StageKey; number: number; title: string; status: StageStatus; items: TimelineItem[] }>;
}

export interface IntakeAnalytics {
  period: { since: string | null; until: string | null; days: number | null; job_id: string | null; target_role: string | null; reviewer_id: string | null };
  funnel: { invitations: number; onboarded: number; started: number; completed: number; incomplete: number; evaluated: number; decided: number };
  average_weighted_total: DecimalString | null;
  average_duration_s: number | null;
  stage_completion: Array<{ stage: string; title: string; completed: number; incomplete: number; rate: number | null }>;
  bands: Array<{ band: string; label: string; count: number }>;
  decisions: Array<{ outcome: string; label: string; count: number }>;
  overrides: { count: number; rate: number | null };
  competency_averages: Array<{ key: string; label: string; average: DecimalString | null; scored: number; insufficient: number }>;
  technical_weaknesses: Array<{ key: string; label: string; count: number }>;
  training_needs: Array<{ module: string; label: string; count: number }>;
  reviewers: Array<{ id: string; full_name: string; decisions: number }>;
  by_target_role: Array<{ target_role: string; label: string | null; count: number; average_weighted_total: DecimalString | null }>;
}

// -------------------------------------------------------------- report (15.11)

export interface IntakeReportContent {
  kind: "intake";
  schema_version: number;
  kit_version: string | null;
  scoring_version: string | null;
  reading_method: string | null;
  generated_at: string | null;
  session_id: string | null;
  intake_id: string | null;
  evaluation_id: string | null;
  evaluation_version: number | null;
  A_candidate_summary: {
    candidate_name: string;
    target_role: string | null;
    interview_date: string | null;
    duration_s: number | null;
    resume_summary: string | null;
    technical_background: string | null;
    years_hands_on: number | null;
    resume_experience_years: number | null;
    completed_stages: Array<{ key: string; title: string; status: string }>;
  };
  B_overall: {
    weighted_total: DecimalString | null;
    normalized_100: DecimalString | null;
    normalized_label: string;
    band: DecisionBand | null;
    band_label: string | null;
    band_guidance: string | null;
    recommendation_note: string;
    human_review_status: "pending" | "decided";
    latest_decision: IntakeDecision | null;
    evidence_sufficiency: string | null;
    partial_total: DecimalString | null;
    partial_label: string | null;
    strengths: string[];
    improvements: string[];
  };
  C_competencies: Array<{
    key: string;
    label: string;
    weight: DecimalString;
    score: DecimalString | null;
    weighted: DecimalString | null;
    status: string;
    observations: string[];
    explanation: string;
    improvements: string[];
    evidence: EvidenceRef[];
    sub_scores: Record<string, unknown>;
  }>;
  D_resume_verification: Array<{
    claim_ref: string;
    claim: string;
    probe: string | null;
    source: string | null;
    questions: Array<{ rung: string | null; question_id: string | null; text: string }>;
    responses: Array<{ answer_id: string | null; excerpt: string }>;
    verdict: string | null;
    supporting_evidence: EvidenceRef[];
    unresolved: string[];
  }>;
  E_technical: {
    language: string | null;
    self_rating: number | null;
    scripting: string | null;
    data_ml_libraries: string | null;
    verification: Array<{ question: string; answer_excerpt: string | null; quality: string | null; matched_key_points: unknown[] }>;
    rating_supported: string | null;
    technical_understanding: string | null;
    accuracy_notes: string | null;
  };
  F_reading: {
    drill_a: {
      passage_key: string | null;
      passage_title: string | null;
      duration_s: number | null;
      transcript: string | null;
      transcript_source: string | null;
      metrics: ReadingMetricsView | null;
      events: ReadingEvent[];
      score: number | null;
      basis: string[];
      pronunciation: string;
    } | null;
    drill_b: {
      passage_key: string | null;
      passage_title: string | null;
      shown_seconds: number | null;
      explanation: string | null;
      concepts: Array<{ key: string; text: string; status: CoverageStatus; quote: string | null }>;
      coverage: number | null;
      paraphrase: string | null;
      misstatements: EvidenceRef[];
      followups: Array<{ question: string; answer_excerpt: string | null; note: string }>;
      score: number | null;
      basis: string[];
    } | null;
    technical_reading: { score: DecimalString | null; formula: string };
  };
  G_structured: {
    card_number: number | null;
    card_title: string | null;
    bullets: Array<{ index: number; text: string; status: CoverageStatus; excerpt: string | null }>;
    flow: string | null;
    examples_used: boolean | null;
    gap_handling: string | null;
    followup: { question: string | null; answer_excerpt: string | null; analysis: string | null } | null;
    score: DecimalString | null;
  };
  H_trainability: {
    learning_strategy: string | null;
    teaching_exercise: string | null;
    problem_solving: string | null;
    training_readiness: string | null;
    ai_ml_openness: string | null;
    development_areas: string[];
  };
  I_integrity: { observations: Array<{ type: string; title: string; count: number; first_at: string | null; review_status: string | null }>; unreviewed: number; note: string };
  J_training_plan: TrainingPlan | null;
  K_human_review: { review_items: ReviewItem[]; decisions: IntakeDecision[]; status: "pending" | "decided" };
  eligibility_note: string;
}

// ------------------------------------------------------------------ queries

export type IntakeSort = "-created_at" | "created_at" | "-completed_at" | "-score" | "score" | "candidate";

export interface IntakeListFilters {
  bucket: IntakeBucket;
  q?: string;
  job_id?: string;
  target_role?: TargetRole;
  band?: DecisionBand;
  outcome?: DecisionOutcome;
  reviewer_id?: string;
  batch_id?: string;
  created_from?: string;
  created_to?: string;
  sort: IntakeSort;
}

export interface IntakeListQuery extends IntakeListFilters {
  limit: number;
  offset: number;
}

export interface IntakeAnalyticsQuery {
  days?: number;
  job_id?: string;
  target_role?: TargetRole;
  reviewer_id?: string;
}
