/**
 * Shared domain types.
 *
 * Part 1 mirrors apps/api/app/domain/enums.py exactly (a unit test parses the
 * Python file and fails on any drift). Part 2 mirrors policies/rounds/AI
 * schemas. Part 3 holds REST DTOs for CONTRACTS §4. IDs are UUID strings and
 * timestamps are ISO-8601 UTC strings.
 */

import type * as Protocol from "@/lib/realtime/protocol";

// =============================================================================
// Part 1 — backend enums (apps/api/app/domain/enums.py)
// =============================================================================

type ValueOf<T> = T[keyof T];

export const Role = {
  SUPER_ADMIN: "SUPER_ADMIN",
  ADMIN: "ADMIN",
  RECRUITER: "RECRUITER",
  INTERVIEWER: "INTERVIEWER",
  CANDIDATE: "CANDIDATE",
} as const;
export type Role = ValueOf<typeof Role>;
export type StaffRole = Exclude<Role, "CANDIDATE">;

export const SubjectType = { USER: "user", CANDIDATE: "candidate" } as const;
export type SubjectType = ValueOf<typeof SubjectType>;

export const RoundType = {
  RESUME_SCREENING: "resume_screening",
  RECRUITER_SCREENING: "recruiter_screening",
  TECHNICAL_SCREENING: "technical_screening",
  DEEP_TECHNICAL: "deep_technical",
  CODING: "coding",
  SYSTEM_DESIGN: "system_design",
  PROBLEM_SOLVING: "problem_solving",
  BEHAVIORAL: "behavioral",
  MANAGERIAL: "managerial",
  LEADERSHIP: "leadership",
  CUSTOM: "custom",
  COMBINED: "combined",
  INTAKE: "intake",
} as const;
export type RoundType = ValueOf<typeof RoundType>;

export const Difficulty = { EASY: "easy", MEDIUM: "medium", HARD: "hard" } as const;
export type Difficulty = ValueOf<typeof Difficulty>;
export const DIFFICULTY_ORDER: readonly Difficulty[] = ["easy", "medium", "hard"];

export const JobStatus = { DRAFT: "draft", OPEN: "open", CLOSED: "closed", ARCHIVED: "archived" } as const;
export type JobStatus = ValueOf<typeof JobStatus>;

export const RequirementKind = {
  REQUIRED_SKILL: "required_skill",
  PREFERRED_SKILL: "preferred_skill",
  RESPONSIBILITY: "responsibility",
  QUALIFICATION: "qualification",
} as const;
export type RequirementKind = ValueOf<typeof RequirementKind>;

export const CompetencyCategory = {
  TECHNICAL: "technical",
  PROBLEM_SOLVING: "problem_solving",
  SYSTEM_DESIGN: "system_design",
  BEHAVIORAL: "behavioral",
  LEADERSHIP: "leadership",
  COMMUNICATION: "communication",
  DOMAIN: "domain",
  CODING: "coding",
} as const;
export type CompetencyCategory = ValueOf<typeof CompetencyCategory>;

export const InterviewStatus = {
  INVITED: "invited",
  IN_PROGRESS: "in_progress",
  COMPLETED: "completed",
  EXPIRED: "expired",
  CANCELLED: "cancelled",
} as const;
export type InterviewStatus = ValueOf<typeof InterviewStatus>;

export const SessionStatus = {
  CREATED: "created",
  PREPARING: "preparing",
  READY: "ready",
  LIVE: "live",
  PAUSED: "paused",
  COMPLETING: "completing",
  COMPLETED: "completed",
  ABORTED: "aborted",
  FAILED: "failed",
} as const;
export type SessionStatus = ValueOf<typeof SessionStatus>;

export const SessionEndReason = {
  SUFFICIENT_EVIDENCE: "sufficient_evidence",
  TIME_LIMIT: "time_limit",
  STOPPING_CONDITION: "stopping_condition",
  CANDIDATE_ENDED: "candidate_ended",
  DISCONNECT_TIMEOUT: "disconnect_timeout",
  POLICY_TERMINATION: "policy_termination",
  TECHNICAL_FAILURE: "technical_failure",
  CANCELLED: "cancelled",
} as const;
export type SessionEndReason = ValueOf<typeof SessionEndReason>;

export const ResumeStatus = {
  UPLOADED: "uploaded",
  VALIDATING: "validating",
  EXTRACTING: "extracting",
  OCR: "ocr",
  ANALYZING: "analyzing",
  READY: "ready",
  FAILED: "failed",
  REJECTED: "rejected",
} as const;
export type ResumeStatus = ValueOf<typeof ResumeStatus>;

export const ExtractionMethod = { TEXT: "text", OCR: "ocr", MIXED: "mixed" } as const;
export type ExtractionMethod = ValueOf<typeof ExtractionMethod>;

export const TurnState = {
  IDLE: "idle",
  AI_SPEAKING: "ai_speaking",
  LISTENING: "listening",
  CANDIDATE_SPEAKING: "candidate_speaking",
  PROCESSING: "processing",
  CODING: "coding",
  PAUSED: "paused",
  ENDED: "ended",
} as const;
export type TurnState = ValueOf<typeof TurnState>;

export const AvatarState = {
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  ACKNOWLEDGING: "ACKNOWLEDGING",
  THINKING: "THINKING",
  SPEAKING: "SPEAKING",
  QUESTIONING: "QUESTIONING",
  ENCOURAGING: "ENCOURAGING",
  TRANSITION: "TRANSITION",
  INTERRUPTED: "INTERRUPTED",
  CLOSING: "CLOSING",
} as const;
export type AvatarState = ValueOf<typeof AvatarState>;

export const AvatarExpression = {
  NEUTRAL: "neutral",
  WARM: "warm",
  CURIOUS: "curious",
  THOUGHTFUL: "thoughtful",
  ENCOURAGING: "encouraging",
  SERIOUS: "serious",
  PLEASED: "pleased",
} as const;
export type AvatarExpression = ValueOf<typeof AvatarExpression>;

export const AvatarGesture = {
  NONE: "none",
  SMALL_OPEN_PALM: "small_open_palm",
  ONE_HAND_EXPLAIN: "one_hand_explain",
  TWO_HAND_EXPLAIN: "two_hand_explain",
  SMALL_EMPHASIS: "small_emphasis",
  QUESTION_GESTURE: "question_gesture",
  NOD: "nod",
  HEAD_TILT: "head_tilt",
  GOODBYE: "goodbye",
} as const;
export type AvatarGesture = ValueOf<typeof AvatarGesture>;

export const GestureIntensity = { LOW: "low", MEDIUM: "medium", HIGH: "high" } as const;
export type GestureIntensity = ValueOf<typeof GestureIntensity>;

export const SpeechTone = {
  PROFESSIONAL: "professional",
  FRIENDLY: "friendly",
  SUPPORTIVE: "supportive",
  NEUTRAL: "neutral",
} as const;
export type SpeechTone = ValueOf<typeof SpeechTone>;

export const SegmentKind = {
  INTRO: "intro",
  RESUME_VALIDATION: "resume_validation",
  TECHNICAL: "technical",
  CODING: "coding",
  SYSTEM_DESIGN: "system_design",
  PROBLEM_SOLVING: "problem_solving",
  BEHAVIORAL: "behavioral",
  LEADERSHIP: "leadership",
  RECRUITER: "recruiter",
  CANDIDATE_QUESTIONS: "candidate_questions",
  CLOSING: "closing",
} as const;
export type SegmentKind = ValueOf<typeof SegmentKind>;

export const TurnAction = {
  INTRODUCE: "introduce",
  ASK_QUESTION: "ask_question",
  ASK_FOLLOWUP: "ask_followup",
  REQUEST_SPECIFICITY: "request_specificity",
  CLARIFY_QUESTION: "clarify_question",
  SIMPLIFY: "simplify",
  INCREASE_DIFFICULTY: "increase_difficulty",
  VERIFY_CLAIM: "verify_claim",
  VERIFY_CONTRADICTION: "verify_contradiction",
  CHANGE_TOPIC: "change_topic",
  START_CODING: "start_coding",
  CODING_COMMENT: "coding_comment",
  GIVE_HINT: "give_hint",
  ACKNOWLEDGE_AND_WAIT: "acknowledge_and_wait",
  ANSWER_CANDIDATE_QUESTION: "answer_candidate_question",
  REDIRECT: "redirect",
  INVITE_CANDIDATE_QUESTIONS: "invite_candidate_questions",
  CLOSE_INTERVIEW: "close_interview",
} as const;
export type TurnAction = ValueOf<typeof TurnAction>;

export const AnswerQuality = {
  STRONG: "strong",
  ADEQUATE: "adequate",
  PARTIAL: "partial",
  VAGUE: "vague",
  INCORRECT: "incorrect",
  NO_ANSWER: "no_answer",
  OFF_TOPIC: "off_topic",
  CLARIFICATION_REQUEST: "clarification_request",
  CANDIDATE_QUESTION: "candidate_question",
  WANTS_TIME: "wants_time",
  WANTS_HINT: "wants_hint",
} as const;
export type AnswerQuality = ValueOf<typeof AnswerQuality>;

/** Scored answer qualities (non-answers that only change the flow are absent). */
export const ANSWER_QUALITY_VALUE: Partial<Record<AnswerQuality, number>> = {
  strong: 0.9,
  adequate: 0.7,
  partial: 0.5,
  vague: 0.35,
  incorrect: 0.15,
  no_answer: 0.0,
  off_topic: 0.2,
};

export const QuestionKind = {
  INTRO: "intro",
  TECHNICAL: "technical",
  SCENARIO: "scenario",
  BEHAVIORAL: "behavioral",
  RESUME_CLAIM: "resume_claim",
  SYSTEM_DESIGN: "system_design",
  CODING: "coding",
  RECRUITER: "recruiter",
  LEADERSHIP: "leadership",
  FOLLOWUP: "followup",
  CLOSING: "closing",
} as const;
export type QuestionKind = ValueOf<typeof QuestionKind>;

export const QuestionSource = { CURATED: "curated", GENERATED: "generated", FOLLOWUP: "followup" } as const;
export type QuestionSource = ValueOf<typeof QuestionSource>;

export const BankStatus = { ACTIVE: "active", DRAFT: "draft", RETIRED: "retired" } as const;
export type BankStatus = ValueOf<typeof BankStatus>;

export const EvidenceSignal = { POSITIVE: "positive", NEGATIVE: "negative", NEUTRAL: "neutral" } as const;
export type EvidenceSignal = ValueOf<typeof EvidenceSignal>;

export const EvidenceSource = {
  ANSWER: "answer",
  CODING: "coding",
  RESUME_CLAIM: "resume_claim",
  HINT_USAGE: "hint_usage",
} as const;
export type EvidenceSource = ValueOf<typeof EvidenceSource>;

export const ClaimStatus = {
  UNVERIFIED: "unverified",
  IN_PROGRESS: "in_progress",
  VERIFIED: "verified",
  PARTIALLY_VERIFIED: "partially_verified",
  CONTRADICTED: "contradicted",
  NOT_APPLICABLE: "not_applicable",
} as const;
export type ClaimStatus = ValueOf<typeof ClaimStatus>;

export const ScoreCategory = { COMPETENCY: "competency", RUBRIC_CATEGORY: "rubric_category", OVERALL: "overall" } as const;
export type ScoreCategory = ValueOf<typeof ScoreCategory>;

/** Assistive only. A human reviewer always owns the employment decision. */
export const Recommendation = {
  STRONG_PROCEED: "strong_proceed",
  PROCEED: "proceed",
  HUMAN_REVIEW: "human_review",
  BORDERLINE_EVIDENCE: "borderline_evidence",
  INSUFFICIENT_EVIDENCE: "insufficient_evidence",
} as const;
export type Recommendation = ValueOf<typeof Recommendation>;

export const ReviewDecisionValue = {
  ADVANCE: "advance",
  HOLD: "hold",
  REJECT: "reject",
  NEEDS_MORE_INFORMATION: "needs_more_information",
} as const;
export type ReviewDecisionValue = ValueOf<typeof ReviewDecisionValue>;

export const ReportType = { CANDIDATE: "candidate", RECRUITER: "recruiter" } as const;
export type ReportType = ValueOf<typeof ReportType>;

export const ReportStatus = { PENDING: "pending", GENERATING: "generating", READY: "ready", FAILED: "failed" } as const;
export type ReportStatus = ValueOf<typeof ReportStatus>;

export const CandidateReportMode = { IMMEDIATE: "immediate", AFTER_REVIEW: "after_review", NEVER: "never" } as const;
export type CandidateReportMode = ValueOf<typeof CandidateReportMode>;

export const CodeLanguage = {
  JAVASCRIPT: "javascript",
  TYPESCRIPT: "typescript",
  PYTHON: "python",
  JAVA: "java",
  GO: "go",
  CPP: "cpp",
  SQL: "sql",
} as const;
export type CodeLanguage = ValueOf<typeof CodeLanguage>;

export const RunKind = { RUN: "run", TEST: "test", SUBMIT: "submit" } as const;
export type RunKind = ValueOf<typeof RunKind>;

export const RunStatus = {
  QUEUED: "queued",
  RUNNING: "running",
  PASSED: "passed",
  FAILED: "failed",
  ERROR: "error",
  COMPILE_ERROR: "compile_error",
  TIMEOUT: "timeout",
  MEMORY_EXCEEDED: "memory_exceeded",
  REJECTED: "rejected",
} as const;
export type RunStatus = ValueOf<typeof RunStatus>;

export const CodingSessionStatus = {
  ACTIVE: "active",
  SUBMITTED: "submitted",
  TIMED_OUT: "timed_out",
  ABANDONED: "abandoned",
} as const;
export type CodingSessionStatus = ValueOf<typeof CodingSessionStatus>;

export const SnapshotTrigger = {
  AUTOSAVE: "autosave",
  RUN: "run",
  SUBMIT: "submit",
  LANGUAGE_CHANGE: "language_change",
  HINT: "hint",
} as const;
export type SnapshotTrigger = ValueOf<typeof SnapshotTrigger>;

export const IntegrityEventType = {
  FULLSCREEN_EXITED: "fullscreen_exited",
  FULLSCREEN_DECLINED: "fullscreen_declined",
  TAB_HIDDEN: "tab_hidden",
  FOCUS_LOST: "focus_lost",
  RAPID_FOCUS_SWITCHING: "rapid_focus_switching",
  WINDOW_MINIMIZED: "window_minimized",
  COPY_EVENT: "copy_event",
  PASTE_EVENT: "paste_event",
  LARGE_PASTE_IN_CODING: "large_paste_in_coding",
  PROHIBITED_SHORTCUT: "prohibited_shortcut",
  CONTEXT_MENU: "context_menu",
  PAGE_RELOAD: "page_reload",
  DISCONNECT: "disconnect",
  RECONNECT: "reconnect",
  UNUSUAL_DISCONNECT_PATTERN: "unusual_disconnect_pattern",
  MULTIPLE_DISPLAYS_DETECTED: "multiple_displays_detected",
  CAMERA_DISABLED: "camera_disabled",
  CAMERA_BLOCKED: "camera_blocked",
  MICROPHONE_DISABLED: "microphone_disabled",
  NO_PERSON_VISIBLE: "no_person_visible",
  MULTIPLE_PERSONS_VISIBLE: "multiple_persons_visible",
  PERSON_LEFT_FRAME: "person_left_frame",
  ANOTHER_PERSON_ENTERED: "another_person_entered",
  POSSIBLE_PHONE_VISIBLE: "possible_phone_visible",
  POSSIBLE_PHOTO_TAKING: "possible_photo_taking",
  LOOKING_AWAY_WITH_OTHER_SIGNALS: "looking_away_with_other_signals",
  SCREEN_SHARE_STARTED: "screen_share_started",
  SCREEN_SHARE_STOPPED: "screen_share_stopped",
  SCREEN_SHARE_NOT_FULL_SCREEN: "screen_share_not_full_screen",
  SCREEN_CONTEXT_CHANGE: "screen_context_change",
  FACE_NOT_VISIBLE: "face_not_visible",
  MULTIPLE_FACES_VISIBLE: "multiple_faces_visible",
  STATIC_FACE_IGNORED: "static_face_ignored",
  FACE_CHECK_SUMMARY: "face_check_summary",
} as const;
export type IntegrityEventType = ValueOf<typeof IntegrityEventType>;

export const IntegritySource = { BROWSER: "browser", VISION: "vision", SCREEN: "screen", SERVER: "server" } as const;
export type IntegritySource = ValueOf<typeof IntegritySource>;

export const IntegritySeverity = { INFO: "info", LOW: "low", MEDIUM: "medium", HIGH: "high" } as const;
export type IntegritySeverity = ValueOf<typeof IntegritySeverity>;

/** Never an automatic guilt decision — at most a flag for a human reviewer. */
export const IntegrityAction = {
  LOG_ONLY: "log_only",
  WARN_CANDIDATE: "warn_candidate",
  FLAG_FOR_HUMAN_REVIEW: "flag_for_human_review",
  PAUSE_INTERVIEW: "pause_interview",
  TERMINATE_INTERVIEW: "terminate_interview",
} as const;
export type IntegrityAction = ValueOf<typeof IntegrityAction>;

export const IntegrityReviewStatus = {
  UNREVIEWED: "unreviewed",
  DISMISSED: "dismissed",
  NOTED: "noted",
  CONFIRMED_CONCERN: "confirmed_concern",
} as const;
export type IntegrityReviewStatus = ValueOf<typeof IntegrityReviewStatus>;

export const MediaKind = {
  RESUME: "resume",
  ANSWER_AUDIO: "answer_audio",
  SESSION_AUDIO: "session_audio",
  VIDEO_SEGMENT: "video_segment",
  SCREEN_SEGMENT: "screen_segment",
  CAMERA_FRAME: "camera_frame",
  SCREEN_FRAME: "screen_frame",
  REPORT_ARTIFACT: "report_artifact",
  /** A finalized recording part (camera + both voices, audio only, or the shared screen). */
  SESSION_RECORDING: "session_recording",
} as const;
export type MediaKind = ValueOf<typeof MediaKind>;

export const ModelTask = {
  RESUME_EXTRACTION: "resume_extraction",
  PROFILE_ANALYSIS: "profile_analysis",
  JOB_MATCH: "job_match",
  INTERVIEW_PLANNING: "interview_planning",
  OPENING: "opening",
  TURN_DECISION: "turn_decision",
  ANSWER_ASSESSMENT: "answer_assessment",
  ANSWER_EVALUATION: "answer_evaluation",
  CODE_OBSERVATION: "code_observation",
  HINT_GENERATION: "hint_generation",
  CODING_EVALUATION: "coding_evaluation",
  QUESTION_GENERATION: "question_generation",
  COMPETENCY_SUMMARY: "competency_summary",
  REPORT_GENERATION: "report_generation",
  MEMORY_SUMMARY: "memory_summary",
  FRAME_ANALYSIS: "frame_analysis",
  SCREEN_ANALYSIS: "screen_analysis",
  ROUTING_CLASSIFICATION: "routing_classification",
  DOCUMENT_CLASSIFICATION: "document_classification",
  EMBEDDING: "embedding",
  RERANK: "rerank",
  STT: "stt",
  OCR: "ocr",
  TTS: "tts",
} as const;
export type ModelTask = ValueOf<typeof ModelTask>;

export const ModelCallStatus = {
  OK: "ok",
  REPAIRED: "repaired",
  RETRIED: "retried",
  FALLBACK: "fallback",
  ERROR: "error",
  TIMEOUT: "timeout",
  CIRCUIT_OPEN: "circuit_open",
  CANCELLED: "cancelled",
} as const;
export type ModelCallStatus = ValueOf<typeof ModelCallStatus>;

export const JobState = {
  QUEUED: "queued",
  RUNNING: "running",
  SUCCEEDED: "succeeded",
  FAILED: "failed",
  DEAD: "dead",
} as const;
export type JobState = ValueOf<typeof JobState>;

export const ActorType = { USER: "user", CANDIDATE: "candidate", SYSTEM: "system" } as const;
export type ActorType = ValueOf<typeof ActorType>;

/** Every mirrored enum by its Python class name (used by the drift test). */
export const BACKEND_ENUMS = {
  Role,
  SubjectType,
  RoundType,
  Difficulty,
  JobStatus,
  RequirementKind,
  CompetencyCategory,
  InterviewStatus,
  SessionStatus,
  SessionEndReason,
  ResumeStatus,
  ExtractionMethod,
  TurnState,
  AvatarState,
  AvatarExpression,
  AvatarGesture,
  GestureIntensity,
  SpeechTone,
  SegmentKind,
  TurnAction,
  AnswerQuality,
  QuestionKind,
  QuestionSource,
  BankStatus,
  EvidenceSignal,
  EvidenceSource,
  ClaimStatus,
  ScoreCategory,
  Recommendation,
  ReviewDecisionValue,
  ReportType,
  ReportStatus,
  CandidateReportMode,
  CodeLanguage,
  RunKind,
  RunStatus,
  CodingSessionStatus,
  SnapshotTrigger,
  IntegrityEventType,
  IntegritySource,
  IntegritySeverity,
  IntegrityAction,
  IntegrityReviewStatus,
  MediaKind,
  ModelTask,
  ModelCallStatus,
  JobState,
  ActorType,
} as const satisfies Record<string, Record<string, string>>;

// Compile-time guard: the realtime protocol's literal unions must match these enums.
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Expect<T extends true> = T;
export type __ProtocolEnumSync = [
  Expect<Equal<AvatarState, Protocol.AvatarState>>,
  Expect<Equal<AvatarExpression, Protocol.AvatarExpression>>,
  Expect<Equal<AvatarGesture, Protocol.AvatarGesture>>,
  Expect<Equal<GestureIntensity, Protocol.GestureIntensity>>,
  Expect<Equal<SpeechTone, Protocol.SpeechTone>>,
  Expect<Equal<TurnState, Protocol.TurnState>>,
  Expect<Equal<SessionStatus, Protocol.SessionStatus>>,
  Expect<Equal<CodeLanguage, Protocol.CodeLanguage>>,
  Expect<Equal<RunKind, Protocol.RunKind>>,
  Expect<Equal<RunStatus, Protocol.RunStatus>>,
  Expect<Equal<IntegrityEventType, Protocol.IntegrityEventType>>,
];

// =============================================================================
// RBAC (CONTRACTS §3) — the matrix itself lives in lib/auth.ts
// =============================================================================

export const PERMISSIONS = [
  "organizations:manage",
  "users:manage",
  "settings:manage",
  "jobs:read",
  "jobs:write",
  "templates:write",
  "question_bank:write",
  "candidates:read",
  "candidates:write",
  "interviews:invite",
  "sessions:read",
  "reports:read",
  "transcripts:read",
  "reports:release",
  "reviews:write",
  "integrity:review",
  "analytics:read",
  "audit:read",
  "portal:self",
] as const;
export type Permission = (typeof PERMISSIONS)[number];

// =============================================================================
// Part 2 — policies (app/schemas/policies.py), rounds (app/schemas/rounds.py)
// =============================================================================

export interface IntegrityRule {
  event_type: IntegrityEventType;
  enabled: boolean;
  min_confidence: number;
  min_duration_seconds: number;
  min_occurrences: number;
  window_seconds: number;
  action: IntegrityAction;
  candidate_message: string | null;
}

export interface IntegrityPolicy {
  enabled: boolean;
  require_fullscreen: boolean;
  monitor_focus: boolean;
  monitor_clipboard: boolean;
  block_paste_in_coding: boolean;
  monitor_shortcuts: boolean;
  camera_required: boolean;
  camera_frame_analysis: boolean;
  camera_frame_interval_seconds: number;
  store_evidence_frames: boolean;
  /** On-device face check (default on while the camera is used); optional for older snapshots. */
  face_presence_check?: boolean;
  screen_share_required: boolean;
  screen_share_analysis: boolean;
  screen_frame_interval_seconds: number;
  terminate_on_required_screen_share_stop: boolean;
  warn_candidate: boolean;
  allow_page_reload_resume: boolean;
  max_reconnects: number;
  rules: IntegrityRule[];
}

export interface RecordingPolicy {
  record_answer_audio: boolean;
  record_video: boolean;
  record_screen: boolean;
  show_live_subtitles: boolean;
  allow_typed_answers: boolean;
}

export interface RetentionPolicy {
  resume_days: number;
  audio_days: number;
  video_days: number;
  frame_days: number;
  transcript_days: number;
  report_days: number;
}

export interface AvatarSettings {
  model_url: string;
  /** "orb" (default animated presence), "glb"/"vrm" (optional 3D avatar), "procedural"; "cartoon" is retired (treated as orb). */
  model_format: "orb" | "cartoon" | "glb" | "vrm" | "procedural";
  interviewer_name: string;
  voice: string;
  background: string;
}

export interface OrganizationSettings {
  integrity: IntegrityPolicy;
  recording: RecordingPolicy;
  retention: RetentionPolicy;
  avatar: AvatarSettings;
  candidate_report_mode: CandidateReportMode;
  require_access_code: boolean;
  data_processing_notice: string;
}

export interface PolicySnapshot {
  policy_version: string;
  integrity: IntegrityPolicy;
  recording: RecordingPolicy;
  retention: RetentionPolicy;
  avatar: AvatarSettings;
  candidate_report_mode: CandidateReportMode;
}

export interface RoundCompetency {
  key: string;
  name: string | null;
  weight: number;
  required: boolean;
  min_evidence: number | null;
  target_difficulty: Difficulty | null;
  focus_areas: string[];
}

export interface RubricCategory {
  key: string;
  label: string;
  /** Percentage; categories sum to 100. */
  weight: number;
  competency_keys: string[];
  dimensions: string[];
  description: string | null;
}

export interface DifficultyDistribution {
  easy: number;
  medium: number;
  hard: number;
}

export interface QuestionStrategy {
  style: string[];
  max_followup_depth: number;
  prefer_curated_ratio: number;
  allow_generated_questions: boolean;
  adaptive_difficulty: boolean;
  allow_candidate_questions_at_end: boolean;
}

export interface StoppingConditions {
  stop_on_sufficient_evidence: boolean;
  min_duration_minutes: number;
  max_questions: number | null;
  min_questions: number;
}

export interface CodingSection {
  enabled: boolean;
  duration_minutes: number;
  languages: CodeLanguage[];
  difficulty: Difficulty;
  question_ids: string[];
  allow_generated: boolean;
  max_problems: number;
  hints_enabled: boolean;
  max_hints: number;
  hint3_may_reveal_solution: boolean;
  restrict_paste: boolean;
  large_paste_threshold_chars: number;
  proactive_comments: boolean;
  min_seconds_between_comments: number;
  start_after_minutes: number | null;
}

export interface ResumeValidation {
  enabled: boolean;
  max_claims: number;
  min_priority: number;
}

/** Thresholds the recommendation engine uses. Assistive only. */
export interface PassingGuidance {
  strong_proceed_min_score: number;
  proceed_min_score: number;
  borderline_min_score: number;
  min_confidence: number;
  min_required_coverage: number;
  reviewer_notes: string;
}

export interface RoundConfig {
  round_type: RoundType;
  name: string;
  duration_minutes: number;
  max_duration_minutes: number;
  expected_difficulty: Difficulty;
  difficulty_distribution: DifficultyDistribution;
  competencies: RoundCompetency[];
  rubric: RubricCategory[];
  question_strategy: QuestionStrategy;
  min_evidence_per_competency: number;
  confidence_threshold: number;
  stopping: StoppingConditions;
  coding: CodingSection;
  resume_validation: ResumeValidation;
  passing_guidance: PassingGuidance;
  instructions: string;
  interviewer_notes: string | null;
  candidate_selectable: boolean;
}

// ----------------------------------------------------------- AI documents

export interface SkillItem {
  name: string;
  category: string;
  claimed_level: string;
  years: number | null;
  last_used_year: number | null;
  evidence: string | null;
}

export interface Achievement {
  text: string;
  metric: string | null;
  value: string | null;
  measurable: boolean;
}

export interface RoleEntry {
  title: string;
  company: string;
  start: string | null;
  end: string | null;
  duration_months: number | null;
  location: string | null;
  responsibilities: string[];
  achievements: Achievement[];
  technologies: string[];
  leadership_scope: string | null;
}

export interface ProjectEntry {
  name: string;
  description: string;
  role: string | null;
  technologies: string[];
  outcomes: string[];
  scale_indicators: string[];
}

export interface EducationEntry {
  institution: string;
  degree: string | null;
  field: string | null;
  start: string | null;
  end: string | null;
}

export interface CertificationEntry {
  name: string;
  issuer: string | null;
  year: string | null;
}

export interface Inconsistency {
  description: string;
  severity: "low" | "medium" | "high";
}

export interface ClaimToVerify {
  id: string;
  claim: string;
  source_quote: string | null;
  category: string;
  competency_keys: string[];
  evidence_topics: string[];
  /** 1-5, 5 = most important to verify. */
  priority: number;
}

export interface RequirementMatch {
  requirement: string;
  evidence: string | null;
  strength: "strong" | "partial" | "weak" | "none";
}

export interface JobMatch {
  overall_fit: number;
  relevant_experience_years: number | null;
  matched_requirements: RequirementMatch[];
  missing_requirements: string[];
  strengths: string[];
  gaps: string[];
  notes: string | null;
}

export interface RecommendedTopic {
  topic: string;
  competency_key: string | null;
  reason: string;
  priority: number;
}

/** Normalized candidate profile document (candidate_profiles.profile). */
export interface CandidateProfileDocument {
  candidate_id: string;
  candidate_name: string | null;
  headline: string | null;
  summary: string | null;
  experience_years: number | null;
  relevant_experience_years: number | null;
  roles: RoleEntry[];
  companies: string[];
  skills: Record<string, SkillItem[]>;
  projects: ProjectEntry[];
  achievements: Achievement[];
  education: EducationEntry[];
  certifications: CertificationEntry[];
  leadership_experience: string[];
  career_progression: string | null;
  inconsistencies: Inconsistency[];
  claims_to_verify: ClaimToVerify[];
  job_match: JobMatch | null;
  recommended_topics: RecommendedTopic[];
  potential_skill_gaps: string[];
  areas_requiring_verification: string[];
  job_relevant_strengths: string[];
  job_relevant_missing_areas: string[];
}

export interface PlanSegment {
  key: string;
  kind: SegmentKind;
  competency_keys: string[];
  time_budget_minutes: number;
  goals: string[];
}

export interface CompetencyTarget {
  key: string;
  priority: number;
  starting_difficulty: Difficulty;
  focus_areas: string[];
  rationale: string | null;
}

export interface InterviewPlan {
  opening_focus: string;
  segments: PlanSegment[];
  competency_targets: CompetencyTarget[];
  claims_priority: string[];
  initial_difficulty: Difficulty;
  difficulty_strategy: string;
  coding_plan: string | null;
  risks_to_probe: string[];
}

/** 0-10 per dimension; null when the dimension does not apply. */
export interface DimensionScores {
  correctness: number | null;
  relevance: number | null;
  completeness: number | null;
  depth: number | null;
  specificity: number | null;
  practical_experience: number | null;
  reasoning: number | null;
  consistency: number | null;
  architecture_awareness: number | null;
  tradeoff_understanding: number | null;
  /** Clarity of content only. */
  communication_clarity: number | null;
}

export interface EvidenceItem {
  competency_key: string;
  dimension: string;
  signal: EvidenceSignal;
  evidence: string;
  quote: string | null;
  strength: number;
  confidence: number;
}

export interface ContradictionFinding {
  statement: string;
  conflicts_with: string;
  severity: "low" | "medium" | "high";
}

export interface ClaimVerification {
  claim_id: string;
  status: ClaimStatus;
  note: string;
}

export interface SuggestedFollowup {
  text: string;
  reason: string;
  priority: number;
}

export interface AnswerEvaluationDocument {
  overall_quality: AnswerQuality;
  scores: DimensionScores;
  summary: string;
  technical_evidence: string[];
  measurable_outcomes: string[];
  missing_details: string[];
  unsupported_claims: string[];
  contradictions: ContradictionFinding[];
  evidence: EvidenceItem[];
  claim_verifications: ClaimVerification[];
  suggested_followups: SuggestedFollowup[];
  evaluation_confidence: number;
}

export interface CodingEvaluationDocument {
  correctness_summary: string;
  tests_passed: number;
  tests_total: number;
  time_complexity: string | null;
  space_complexity: string | null;
  complexity_assessment: string;
  code_quality: number;
  problem_solving: number;
  edge_case_handling: number;
  testing_approach: number | null;
  communication: number | null;
  process_observations: string[];
  hints_impact: string;
  strengths: string[];
  weaknesses: string[];
  evidence: EvidenceItem[];
  score_estimate: number;
  confidence: number;
}

export interface EvidenceBackedPoint {
  title: string;
  detail: string;
  evidence_ids: string[];
  competency_key: string | null;
}

export interface ImprovementArea {
  title: string;
  detail: string;
  learning_guidance: string;
  evidence_ids: string[];
  competency_key: string | null;
}

export interface QuestionHighlight {
  question_id: string;
  note: string;
}

export interface StudyArea {
  topic: string;
  why: string;
  next_steps: string[];
}

export interface CandidateReportNarrative {
  overall_summary: string;
  technical_strengths: EvidenceBackedPoint[];
  areas_to_improve: ImprovementArea[];
  coding_feedback: string | null;
  problem_solving_feedback: string | null;
  communication_feedback: string | null;
  strong_answers: QuestionHighlight[];
  incomplete_answers: QuestionHighlight[];
  recommended_study_areas: StudyArea[];
  next_steps: string[];
}

export interface CompetencyFinding {
  competency_key: string;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  unanswered_areas: string[];
  evidence_ids: string[];
}

export interface RecruiterReportNarrative {
  executive_summary: string;
  competency_findings: CompetencyFinding[];
  claims_summary: string;
  contradictions_summary: string;
  coding_summary: string | null;
  areas_for_human_followup: string[];
  recommendation_rationale: string;
  suggested_recommendation: Recommendation;
  risks: string[];
}

// =============================================================================
// Part 3 — REST DTOs (CONTRACTS §4, prefix /api/v1)
// =============================================================================

/** Every list endpoint returns this envelope. */
export interface Page<T> {
  items: T[];
  total: number;
  /** Present on cursor-paginated endpoints. */
  next_cursor?: string | null;
}

export interface ListParams {
  limit?: number;
  offset?: number;
  cursor?: string;
  /** SUPER_ADMIN only: act on another organization. */
  organization_id?: string;
}

/** Backend error envelope. */
export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id?: string;
    details?: Record<string, unknown>;
  };
}

interface Timestamps {
  created_at: string;
  updated_at: string;
}

export interface PersonRef {
  id: string;
  full_name: string;
  email?: string;
}

export interface JobRef {
  id: string;
  title: string;
}

export interface RoundRef {
  id: string;
  name: string;
  round_type: RoundType;
}

// ------------------------------------------------------- auth, users, orgs

export interface LoginRequest {
  email: string;
  password: string;
}

export interface OrganizationRef {
  id: string;
  name: string;
  slug: string;
}

export interface Organization extends OrganizationRef, Timestamps {
  is_active: boolean;
  settings?: Partial<OrganizationSettings>;
}

export interface OrganizationCreate {
  name: string;
  slug: string;
}

export interface OrganizationUpdate {
  name?: string;
  is_active?: boolean;
}

export interface StaffUser extends Timestamps {
  id: string;
  organization_id: string | null;
  email: string;
  full_name: string;
  role: StaffRole;
  is_active: boolean;
  last_login_at: string | null;
}

/** GET /auth/me and POST /auth/login (normalised by lib/auth.ts). */
export interface MeResponse {
  user: StaffUser;
  organization: OrganizationRef | null;
  permissions?: Permission[];
  /** The account still has an administrator-issued password: only the account page is usable (S13). */
  mustChangePassword?: boolean;
}

export interface UserCreate {
  email: string;
  full_name: string;
  role: StaffRole;
  password: string;
  organization_id?: string;
}

export interface UserUpdate {
  full_name?: string;
  role?: StaffRole;
  is_active?: boolean;
}

export interface AuditLogEntry {
  id: number;
  organization_id: string | null;
  actor_type: ActorType;
  actor_id: string | null;
  actor_name?: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export type HealthState = "ok" | "degraded" | "down" | "unknown";

export interface HealthComponent {
  name: string;
  status: HealthState;
  latency_ms?: number | null;
  detail?: string | null;
  model?: string | null;
}

/** GET /system/health (model endpoints, DB, Redis, storage, tts, sandbox). */
export interface SystemHealth {
  status: HealthState;
  checked_at: string;
  components: HealthComponent[];
}

// -------------------------------------------------------------------- media

export interface MediaUrl {
  url: string;
  expires_at: string;
}

// ------------------------------------------------------------------ catalog

export interface CompetencyRubric {
  levels?: Record<string, string>;
  dimensions?: string[];
}

export interface Competency extends Timestamps {
  id: string;
  organization_id: string | null;
  key: string;
  name: string;
  category: CompetencyCategory;
  description: string | null;
  rubric: CompetencyRubric;
  is_active: boolean;
}

export interface CompetencyInput {
  key: string;
  name: string;
  category: CompetencyCategory;
  description?: string | null;
  rubric?: CompetencyRubric;
  is_active?: boolean;
}

export interface CodingRequirements {
  enabled: boolean;
  languages: CodeLanguage[];
  difficulty: Difficulty;
}

export interface Job extends Timestamps {
  id: string;
  organization_id: string;
  title: string;
  department: string | null;
  location: string | null;
  employment_type: string | null;
  seniority: string | null;
  description: string;
  experience_min_years: number | null;
  experience_max_years: number | null;
  status: JobStatus;
  interview_instructions: string | null;
  coding_requirements: Partial<CodingRequirements>;
  integrity_settings: Partial<IntegrityPolicy>;
  recording_settings: Partial<RecordingPolicy> & { retention?: Partial<RetentionPolicy> };
  candidate_report_mode: CandidateReportMode;
  allow_candidate_round_selection: boolean;
  created_by_id: string | null;
  /** List-view aggregates (optional). */
  rounds_count?: number;
  requirements_count?: number;
  candidates_count?: number;
  sessions_count?: number;
}

export interface JobInput {
  title: string;
  department?: string | null;
  location?: string | null;
  employment_type?: string | null;
  seniority?: string | null;
  description?: string;
  experience_min_years?: number | null;
  experience_max_years?: number | null;
  status?: JobStatus;
  interview_instructions?: string | null;
  coding_requirements?: Partial<CodingRequirements>;
  integrity_settings?: Partial<IntegrityPolicy>;
  recording_settings?: Partial<RecordingPolicy> & { retention?: Partial<RetentionPolicy> };
  candidate_report_mode?: CandidateReportMode;
  allow_candidate_round_selection?: boolean;
}

export interface JobListParams extends ListParams {
  status?: JobStatus;
  q?: string;
}

export interface JobRequirement extends Timestamps {
  id: string;
  job_id: string;
  kind: RequirementKind;
  name: string;
  competency_key: string | null;
  description: string | null;
  weight: number;
  min_years: number | null;
  sort_order: number;
}

export interface JobRequirementInput {
  kind: RequirementKind;
  name: string;
  competency_key?: string | null;
  description?: string | null;
  weight?: number;
  min_years?: number | null;
  sort_order?: number;
}

export interface Round extends Timestamps {
  id: string;
  job_id: string;
  template_id: string | null;
  name: string;
  round_type: RoundType;
  sequence: number;
  duration_minutes: number;
  max_duration_minutes: number;
  expected_difficulty: Difficulty;
  candidate_selectable: boolean;
  is_active: boolean;
  instructions: string | null;
  config: RoundConfig;
}

export interface RoundInput {
  name: string;
  round_type: RoundType;
  template_id?: string | null;
  duration_minutes?: number;
  max_duration_minutes?: number;
  expected_difficulty?: Difficulty;
  candidate_selectable?: boolean;
  is_active?: boolean;
  instructions?: string | null;
  config?: Partial<RoundConfig>;
}

export interface RoundReorderRequest {
  round_ids: string[];
}

/** GET /round-types — defaults per RoundType. */
export interface RoundTypeDefault {
  round_type: RoundType;
  label?: string;
  description?: string;
  config: RoundConfig;
}

export interface InterviewTemplate extends Timestamps {
  id: string;
  organization_id: string | null;
  name: string;
  description: string | null;
  round_type: RoundType;
  config: RoundConfig;
  is_default: boolean;
  created_by_id: string | null;
}

export interface InterviewTemplateInput {
  name: string;
  description?: string | null;
  round_type: RoundType;
  config: Partial<RoundConfig>;
  is_default?: boolean;
}

export interface BankQuestionRubric {
  excellent?: string;
  good?: string;
  weak?: string;
  dimensions?: Record<string, string>;
}

export interface BankQuestion extends Timestamps {
  id: string;
  organization_id: string | null;
  external_key: string | null;
  role_tags: string[];
  round_types: RoundType[];
  competency_key: string;
  topic: string | null;
  difficulty: Difficulty;
  kind: QuestionKind;
  text: string;
  expected_concepts: string[];
  rubric: BankQuestionRubric;
  followups: string[];
  common_errors: string[];
  source: QuestionSource;
  status: BankStatus;
  usage_count: number;
  created_by_id: string | null;
  /** Relevance from /question-bank/search (reranker score). */
  score?: number;
}

export interface BankQuestionInput {
  competency_key: string;
  difficulty: Difficulty;
  kind: QuestionKind;
  text: string;
  topic?: string | null;
  role_tags?: string[];
  round_types?: RoundType[];
  expected_concepts?: string[];
  rubric?: BankQuestionRubric;
  followups?: string[];
  common_errors?: string[];
  status?: BankStatus;
}

export interface QuestionBankListParams extends ListParams {
  competency_key?: string;
  difficulty?: Difficulty;
  kind?: QuestionKind;
  status?: BankStatus;
  q?: string;
}

export interface QuestionBankSearchRequest {
  query: string;
  competency_key?: string;
  difficulty?: Difficulty;
  limit?: number;
}

export interface QuestionGenerateRequest {
  competency_key: string;
  difficulty: Difficulty;
  count: number;
  role: string;
}

export type CodingValueType =
  | "int"
  | "long"
  | "float"
  | "bool"
  | "string"
  | "int[]"
  | "long[]"
  | "float[]"
  | "string[]"
  | "bool[]"
  | "int[][]"
  | "string[][]";

export interface CodingSignature {
  function_name: string;
  params: Array<{ name: string; type: CodingValueType }>;
  return_type: CodingValueType;
  comparison: "exact" | "unordered" | "float_tolerance";
}

export interface CodingTestCase {
  id: string;
  hidden: boolean;
  description?: string;
  args?: unknown[];
  expected?: unknown;
  /** SQL problems. */
  setup_sql?: string;
  expected_rows?: unknown[][];
  ordered?: boolean;
}

export interface CodingQuestion extends Timestamps {
  id: string;
  organization_id: string | null;
  slug: string;
  title: string;
  difficulty: Difficulty;
  competency_keys: string[];
  role_tags: string[];
  prompt_markdown: string;
  languages: CodeLanguage[];
  signature: CodingSignature | Record<string, never>;
  starter_code: Partial<Record<CodeLanguage, string>>;
  reference_solutions?: Partial<Record<CodeLanguage, string>>;
  tests: CodingTestCase[];
  sql_setup: string | null;
  expected_concepts: string[];
  optimal_complexity: { time?: string; space?: string };
  rubric: Record<string, unknown>;
  /** Exactly three levels. */
  hints: string[];
  followups: string[];
  common_errors: string[];
  time_limit_minutes: number;
  source: QuestionSource;
  status: BankStatus;
}

export type CodingQuestionInput = Partial<
  Omit<CodingQuestion, "id" | "organization_id" | "created_at" | "updated_at">
> & { title: string; slug: string; difficulty: Difficulty; prompt_markdown: string };

export interface CodingQuestionValidation {
  ok: boolean;
  results: Array<{
    language: CodeLanguage;
    status: RunStatus;
    passed: number;
    total: number;
    runtime_ms?: number | null;
    error?: string | null;
  }>;
}

// --------------------------------------------------------------- candidates

export interface Candidate extends Timestamps {
  id: string;
  organization_id: string;
  full_name: string;
  email: string;
  phone: string | null;
  location: string | null;
  source: string | null;
  external_ref: string | null;
  notes: string | null;
  created_by_id: string | null;
  /** List-view aggregates (optional). */
  latest_resume_status?: ResumeStatus | null;
  interviews_count?: number;
  latest_interview_status?: InterviewStatus | null;
  /** Set when the candidate is archived (hidden from lists; no new invitations or résumés). */
  archived_at?: string | null;
}

/** DELETE /candidates/{id} (data-subject erasure): what was removed. Counts only. */
export interface CandidateDeletionResult {
  candidate_id: string;
  deleted_at: string;
  interviews: number;
  sessions: number;
  resumes: number;
  profiles: number;
  media_files_deleted: number;
  media_bytes_deleted: number;
  media_by_kind?: Record<string, number>;
  media_records_anonymized?: number;
  unregistered_files_deleted?: number;
  model_calls_scrubbed?: number;
  audit_entries_scrubbed?: number;
  auth_sessions_revoked?: number;
  intake_interviews?: number;
  reports?: number;
  link_registrations?: number;
  /** True when the candidate was archived and deleted in one request (`archive_first`). */
  archived_in_request?: boolean;
}

export interface CandidateInput {
  full_name: string;
  email: string;
  phone?: string | null;
  location?: string | null;
  source?: string | null;
  external_ref?: string | null;
  notes?: string | null;
}

export interface CandidateListParams extends ListParams {
  q?: string;
  job_id?: string;
}

export interface ResumeProgress {
  stage?: ResumeStatus | string;
  percent?: number;
  message?: string;
  pages_done?: number;
  pages_total?: number;
}

export interface ResumeExtractionSummary {
  candidate_name?: string | null;
  headline?: string | null;
  total_experience_years?: number | null;
  roles_count?: number;
  skills_count?: number;
  top_skills?: string[];
  warnings?: string[];
}

export interface CandidateResume extends Timestamps {
  id: string;
  candidate_id: string;
  organization_id: string;
  interview_id: string | null;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: ResumeStatus;
  status_detail: string | null;
  progress: ResumeProgress;
  page_count: number | null;
  extraction_method: ExtractionMethod | null;
  error: string | null;
  uploaded_by_candidate: boolean;
  processing_started_at: string | null;
  processing_finished_at: string | null;
  extraction_summary?: ResumeExtractionSummary | null;
  media_id?: string | null;
}

export interface CandidateProfileRecord {
  id: string;
  candidate_id: string;
  resume_id: string;
  job_id: string | null;
  version: number;
  status: string;
  profile: CandidateProfileDocument;
  summary: string | null;
  experience_years: number | null;
  relevant_experience_years: number | null;
  /** 0-100 */
  job_match_score: number | null;
  claims_count: number;
  created_at: string;
  /** Structured job-match arithmetic (resume pipeline); absent on older profiles. */
  match_breakdown?: MatchBreakdown | null;
}

export interface MatchBreakdown {
  score: number | null;
  coverage: number | null;
  model_fit: number | null;
  experience_fit: number | null;
  /** Keys coverage / model / experience, 0-1. */
  weights: Record<string, number>;
  /** Keys strong / partial / weak / none. */
  bands: Record<string, number>;
  requirements: Array<Record<string, unknown>>;
}

// --------------------------------------------------------------- interviews

export interface Interview extends Timestamps {
  id: string;
  organization_id: string;
  candidate_id: string;
  job_id: string;
  round_id: string | null;
  allow_round_selection: boolean;
  status: InterviewStatus;
  invite_expires_at: string;
  invite_opened_at: string | null;
  verified_at: string | null;
  requires_access_code?: boolean;
  policy_snapshot: Partial<PolicySnapshot>;
  system_check: Record<string, unknown>;
  invited_by_id: string | null;
  scheduled_for: string | null;
  completed_at: string | null;
  notes: string | null;
  candidate?: PersonRef;
  job?: JobRef;
  round?: RoundRef | null;
  latest_session?: { id: string; status: SessionStatus } | null;
}

export interface InterviewCreate {
  candidate_id: string;
  job_id: string;
  round_id?: string | null;
  allow_round_selection: boolean;
  expires_in_hours: number;
  require_access_code: boolean;
}

/** The invite link and access code are shown exactly once. */
export interface InterviewCreateResponse {
  interview: Interview;
  invite_url: string;
  access_code?: string | null;
}

export interface InterviewListParams extends ListParams {
  status?: InterviewStatus;
  job_id?: string;
  candidate_id?: string;
}

// ------------------------------------------------------ sessions & reports

export interface SessionSummary extends Timestamps {
  id: string;
  interview_id: string;
  organization_id: string;
  candidate_id: string;
  job_id: string;
  round_id: string;
  status: SessionStatus;
  started_at: string | null;
  ended_at: string | null;
  end_reason: SessionEndReason | null;
  elapsed_seconds: number;
  reconnect_count: number;
  candidate?: PersonRef;
  job?: JobRef;
  round?: RoundRef;
  /** 0-100 */
  overall_score?: number | null;
  confidence?: number | null;
  recommendation?: Recommendation | null;
  report_status?: ReportStatus | null;
  review_decision?: ReviewDecisionValue | null;
  integrity_flags?: number;
  question_count?: number;
}

export interface SessionListParams extends ListParams {
  status?: SessionStatus | SessionStatus[];
  job_id?: string;
  candidate_id?: string;
}

export interface CompetencyDigest {
  key: string;
  name?: string | null;
  weight?: number;
  required?: boolean;
  /** `null` = nothing observed yet (the engine's neutral starting point is not a score). */
  score_estimate: number | null;
  confidence: number;
  evidence_count: number;
  sufficient_evidence: boolean;
}

export interface SessionStateDigest {
  turn_state: TurnState;
  avatar_state: AvatarState;
  current_segment?: string | null;
  question_count: number;
  elapsed_s: number;
  remaining_s: number;
  competencies: CompetencyDigest[];
  claims?: Array<{ id: string; claim: string; status: ClaimStatus }>;
  integrity?: { counts: Record<string, number>; warnings_issued: number; flagged_for_review: number };
}

export interface SessionDetail extends SessionSummary {
  round_config: RoundConfig;
  plan: InterviewPlan | null;
  state_digest?: SessionStateDigest | null;
  client_info?: Record<string, unknown>;
  error?: string | null;
}

export type TranscriptSpeaker = "candidate" | "interviewer" | "system";

export interface TranscriptSegment {
  id: string;
  session_id: string;
  seq: number;
  speaker: TranscriptSpeaker;
  kind: "final" | "partial";
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  confidence: number | null;
  question_id: string | null;
  answer_id: string | null;
  interrupted: boolean;
  created_at: string;
}

export interface EvidenceRecord {
  id: string;
  session_id: string;
  competency_key: string;
  question_id: string | null;
  answer_id: string | null;
  evaluation_id: string | null;
  coding_session_id: string | null;
  source: EvidenceSource;
  signal: EvidenceSignal;
  dimension: string;
  evidence: string;
  /** 0-1 */
  strength: number;
  /** 0-1 */
  confidence: number;
  difficulty: Difficulty | null;
  elapsed_seconds: number | null;
  transcript_ref: { answer_id?: string; start_ms?: number; end_ms?: number; quote?: string };
  created_at: string;
}

export interface AnswerEvaluationRecord extends DimensionScores {
  id: string;
  answer_id: string;
  question_id: string | null;
  session_id: string;
  overall_quality: AnswerQuality;
  confidence: number;
  is_fallback: boolean;
  evaluation: Partial<AnswerEvaluationDocument>;
  created_at: string;
}

export interface SessionAnswer {
  id: string;
  question_id: string | null;
  sequence: number;
  transcript_text: string;
  typed: boolean;
  started_ms: number | null;
  ended_ms: number | null;
  duration_ms: number | null;
  word_count: number;
  stt_confidence: number | null;
  interrupted_ai: boolean;
  status: string;
  audio_media_id?: string | null;
  evaluation?: AnswerEvaluationRecord | null;
  evidence?: EvidenceRecord[];
}

export interface SessionQuestion {
  id: string;
  session_id: string;
  sequence: number;
  parent_question_id: string | null;
  bank_question_id: string | null;
  coding_question_id: string | null;
  kind: QuestionKind;
  action: TurnAction;
  source: QuestionSource;
  segment: SegmentKind | null;
  topic: string | null;
  competency_keys: string[];
  difficulty: Difficulty;
  text: string;
  spoken_text: string | null;
  question_focus: string | null;
  reason: string | null;
  expected_evidence: string[];
  claim_id: string | null;
  interrupted: boolean;
  asked_at: string;
  elapsed_seconds: number;
  answers: SessionAnswer[];
}

export interface ScoreRecord {
  id: string;
  session_id: string;
  category: ScoreCategory;
  key: string;
  label: string | null;
  /** Competency 0-10; rubric category / overall 0-100. `null` = not enough evidence to score (never a made-up midpoint). */
  score: number | null;
  confidence: number;
  weight: number | null;
  evidence_count: number;
  positive_count: number;
  negative_count: number;
  sufficient_evidence: boolean;
  unanswered_areas: string[];
  supporting_question_ids: string[];
  positive_evidence_ids: string[];
  negative_evidence_ids: string[];
  transcript_refs: Array<Record<string, unknown>>;
  details: Record<string, unknown>;
  is_final: boolean;
  created_at: string;
}

/** GET /sessions/{id}/scores */
export interface SessionScores {
  overall: ScoreRecord | null;
  competencies: ScoreRecord[];
  rubric: ScoreRecord[];
  recommendation?: Recommendation | null;
}

export interface ScoreSnapshot {
  elapsed_seconds: number;
  competency_key: string;
  /** 0-10 */
  score: number;
  confidence: number;
  evidence_count: number;
  trigger: string | null;
}

export interface TimelineEvent {
  seq: number;
  type: string;
  elapsed_seconds: number | null;
  payload: Record<string, unknown>;
  created_at: string;
}

/** GET /sessions/{id}/timeline */
export interface SessionTimeline {
  snapshots: ScoreSnapshot[];
  events: TimelineEvent[];
}

export interface CodeSnapshotRecord {
  id: string;
  coding_session_id: string;
  seq: number;
  language: CodeLanguage;
  code: string;
  diff_from_previous: string | null;
  lines_added: number;
  lines_removed: number;
  char_count: number;
  trigger: SnapshotTrigger;
  elapsed_seconds: number | null;
  created_at: string;
}

export interface CodeTestResult {
  id: string;
  passed: boolean;
  hidden: boolean;
  args?: unknown;
  expected?: unknown;
  actual?: unknown;
  runtime_ms?: number | null;
  error?: string | null;
}

export interface CodeRunRecord {
  id: string;
  coding_session_id: string;
  snapshot_id: string | null;
  kind: RunKind;
  language: CodeLanguage;
  status: RunStatus;
  stdout: string | null;
  stderr: string | null;
  compile_output: string | null;
  exit_code: number | null;
  test_results: CodeTestResult[];
  passed_count: number;
  total_count: number;
  runtime_ms: number | null;
  memory_kb: number | null;
  elapsed_seconds: number | null;
  created_at: string;
}

export interface CodingHintRecord {
  id: string;
  coding_session_id: string;
  level: number;
  content: string;
  source: "curated" | "generated";
  requested_via: "button" | "voice";
  elapsed_seconds: number | null;
  delivered_at: string | null;
  created_at: string;
}

export interface CodingSessionDetail extends Timestamps {
  id: string;
  session_id: string;
  coding_question_id: string;
  question_id: string | null;
  language: CodeLanguage;
  status: CodingSessionStatus;
  started_at: string;
  ended_at: string | null;
  time_limit_seconds: number;
  hints_available: number;
  hints_used: number;
  paste_event_count: number;
  final_code: string | null;
  final_run_id: string | null;
  evaluation: Partial<CodingEvaluationDocument>;
  question?: Pick<CodingQuestion, "id" | "title" | "difficulty" | "prompt_markdown">;
  snapshots: CodeSnapshotRecord[];
  runs: CodeRunRecord[];
  hints: CodingHintRecord[];
}

export interface RecruiterReportContent {
  narrative?: RecruiterReportNarrative;
  [key: string]: unknown;
}

export interface CandidateReportContent {
  narrative?: CandidateReportNarrative;
  [key: string]: unknown;
}

export interface Report extends Timestamps {
  id: string;
  session_id: string;
  type: ReportType;
  status: ReportStatus;
  version: number;
  content: RecruiterReportContent | CandidateReportContent;
  /** Recruiter reports only; advisory. */
  recommendation: Recommendation | null;
  /** 0-100 */
  overall_score: number | null;
  confidence: number | null;
  generated_at: string | null;
  released_to_candidate: boolean;
  released_at: string | null;
  released_by_id: string | null;
  error: string | null;
  artifact_media_id?: string | null;
}

/** The human decision. The AI recommendation is advisory only. */
export interface ReviewDecision {
  id: string;
  session_id: string;
  reviewer_id: string;
  reviewer?: PersonRef;
  decision: ReviewDecisionValue;
  notes: string | null;
  ai_recommendation_at_review: Recommendation | null;
  overrides_ai: boolean;
  created_at: string;
}

export interface ReviewCreate {
  decision: ReviewDecisionValue;
  notes?: string | null;
}

// ---------------------------------------------------------------- analytics

export interface AnalyticsOverview {
  period_days?: number;
  interviews_invited: number;
  interviews_completed: number;
  sessions_live: number;
  sessions_completed: number;
  awaiting_review: number;
  /** 0-100 */
  average_overall_score: number | null;
  /** 0-1 */
  completion_rate: number | null;
  integrity_unreviewed: number;
  recommendation_distribution?: Partial<Record<Recommendation, number>>;
  sessions_by_day?: Array<{ date: string; count: number }>;
}

export interface CompetencyAnalyticsItem {
  competency_key: string;
  name?: string | null;
  /** 0-10 */
  average_score: number | null;
  sessions: number;
  evidence_count: number;
}

export interface CompetencyAnalytics {
  items: CompetencyAnalyticsItem[];
}

export interface FunnelStage {
  stage: string;
  label?: string;
  count: number;
}

export interface AnalyticsFunnel {
  stages: FunnelStage[];
}

export interface ModelUsageItem {
  task: ModelTask;
  endpoint: string;
  calls: number;
  errors: number;
  p50_ms: number | null;
  p95_ms: number | null;
  avg_ttft_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
}

export interface ModelUsage {
  items: ModelUsageItem[];
}

// ---------------------------------------------------------------- integrity

/** An observation for human review — never a verdict. */
export interface IntegrityEvent {
  id: string;
  session_id: string;
  organization_id: string;
  type: IntegrityEventType;
  source: IntegritySource;
  severity: IntegritySeverity;
  confidence: number | null;
  occurred_at: string;
  elapsed_seconds: number | null;
  duration_ms: number | null;
  count: number;
  details: Record<string, unknown>;
  evidence_media_id: string | null;
  action: IntegrityAction;
  review_status: IntegrityReviewStatus;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
  candidate?: PersonRef;
  job?: JobRef;
}

export interface IntegrityEventListParams extends ListParams {
  status?: IntegrityReviewStatus;
  session_id?: string;
  type?: IntegrityEventType;
}

export interface IntegrityReviewUpdate {
  review_status: IntegrityReviewStatus;
  review_note?: string | null;
}

/** GET /sessions/{id}/integrity */
export interface SessionIntegrity {
  events: IntegrityEvent[];
  counts?: Partial<Record<IntegrityEventType, number>>;
  policy?: Partial<IntegrityPolicy>;
}

// ---------------------------------------------------------- candidate portal

/**
 * Candidate journey steps, in order; each maps to /candidate/:interviewId/<key>.
 * `details` and `resume-review` exist only for intake interviews (features/candidate/journey.ts filters them).
 */
export const CANDIDATE_STEPS = [
  { key: "welcome", label: "Welcome" },
  { key: "instructions", label: "Instructions" },
  { key: "consent", label: "Consent" },
  { key: "resume", label: "Résumé" },
  { key: "details", label: "Your details" },
  { key: "resume-review", label: "Check résumé" },
  { key: "round", label: "Round" },
  { key: "system-check", label: "System check" },
  { key: "interview", label: "Interview" },
] as const;
export type CandidateJourneyStep = (typeof CANDIDATE_STEPS)[number]["key"];
export type CandidateStepKey = CandidateJourneyStep | "complete" | "report";

// Mirrors of the portal DTOs in apps/api/app/schemas/api/portal.py. The candidate
// feature parses responses through tolerant normalisers (features/candidate/api.ts);
// these types document the wire shapes and type the request bodies.

/** GET /candidate-access/{token} — public invite info (`InviteInfo`). */
export interface InviteInfo {
  organization_name: string;
  job_title: string;
  round_name?: string | null;
  candidate_first_name?: string | null;
  requires_access_code: boolean;
  expires_at: string;
  expired: boolean;
  cancelled: boolean;
  completed?: boolean;
  status: InterviewStatus;
  interviewer_name?: string | null;
  /** Planned round length (null when the candidate picks the round later). */
  duration_minutes?: number | null;
}

/** POST /candidate-access/{token}/verify */
export interface CandidateVerifyRequest {
  email: string;
  access_code?: string | null;
}

export interface CandidateVerifyResponse {
  interview_id: string;
  next_step: CandidateStepKey;
  csrf_token: string;
}

export interface PortalCandidate {
  id: string;
  full_name: string;
  first_name?: string | null;
  email: string;
}

export interface PortalOrganization {
  name: string;
  slug?: string | null;
}

/** GET /portal/me */
export interface PortalMe {
  candidate: PortalCandidate;
  interview_id: string;
  organization: PortalOrganization;
}

export interface PortalRound {
  id: string;
  name: string;
  round_type: RoundType;
  duration_minutes: number;
  expected_difficulty: Difficulty;
  instructions?: string | null;
  coding_enabled?: boolean;
  candidate_selectable?: boolean;
}

export interface PortalJob {
  id: string;
  title: string;
  department?: string | null;
  location?: string | null;
  description?: string | null;
}

/** GET /portal/interviews/{id} */
export interface PortalInterview {
  id: string;
  status: InterviewStatus;
  organization: PortalOrganization;
  job: PortalJob;
  rounds_offered: PortalRound[];
  selected_round_id?: string | null;
  allow_round_selection: boolean;
  policy: Partial<PolicySnapshot>;
  steps_completed: CandidateStepKey[];
  expires_at: string;
  interviewer_name?: string | null;
  latest_session?: { id: string; status: SessionStatus } | null;
  requires_resume?: boolean;
}

/** One acknowledgeable item of the disclosure (`DisclosureItemOut`). */
export interface DisclosureItem {
  key: string;
  title: string;
  description: string;
  required: boolean;
  enabled: boolean;
}

/** Values stored with the consent, keyed by item (booleans) plus numeric extras such as `retention_days`. */
export type ConsentItems = Record<string, boolean | number>;

/** GET /portal/interviews/{id}/disclosure — exact consent text + policy hash (`DisclosureOut`). */
export interface Disclosure {
  policy_version: string;
  policy_hash: string;
  title: string;
  intro: string;
  items: DisclosureItem[];
  retention_summary: string;
  purpose: string;
  human_review_statement: string;
  limitations_statement: string;
  /** Exactly the text that was hashed into `policy_hash`. */
  text_markdown: string;
  /** Template of the map stored with the consent (every key the server expects back). */
  consent_items: ConsentItems;
  /** Keys the candidate must acknowledge. */
  required_items: string[];
  /** The candidate already accepted this exact policy. */
  consented: boolean;
}

/** POST /portal/interviews/{id}/consent (`ConsentRequest`, at most 40 items). */
export interface ConsentRequest {
  policy_hash: string;
  items: ConsentItems;
}

export interface ConsentResponse {
  consent_id: string;
  policy_version: string;
  policy_hash: string;
  accepted_at: string;
  items: ConsentItems;
}

/** A pipeline step of the résumé progress (`progress.steps[]`). */
export interface PortalResumeStep {
  key: string;
  label: string;
  state: "pending" | "active" | "done" | "failed" | "skipped";
  at?: string | null;
}

export interface PortalResumeInfo {
  id: string;
  original_filename: string;
  size_bytes: number;
  /** A ResumeStatus value (the DTO types it as a plain string). */
  status: ResumeStatus | (string & {});
  status_detail?: string | null;
  progress: Partial<ResumeProgress> & { steps?: PortalResumeStep[] };
  steps: PortalResumeStep[];
  error?: string | null;
  profile_ready: boolean;
  created_at?: string | null;
}

/** GET|POST /portal/interviews/{id}/resume — status + candidate-safe profile summary (`PortalResume`). */
export interface PortalResume {
  resume: PortalResumeInfo | null;
  profile_summary?: {
    name?: string | null;
    headline?: string | null;
    experience_years?: number | null;
    skills?: string[];
    roles?: Array<{ title: string; company: string; start?: string | null; end?: string | null }>;
    certifications?: string[];
  } | null;
}

/** POST /portal/interviews/{id}/round */
export interface RoundSelectionRequest {
  round_id: string;
}

/** POST /portal/interviews/{id}/system-check (`SystemCheckRequest`, extra fields forbidden). */
export interface SystemCheckRequest {
  camera: boolean;
  microphone: boolean;
  speakers: boolean;
  webgl: boolean;
  fullscreen?: boolean | null;
  screen_share?: boolean | null;
  network?: { rtt_ms?: number | null; downlink_mbps?: number | null } | null;
  browser?: { name?: string | null; version?: string | null; user_agent?: string | null } | null;
  displays?: number | null;
  /** -160..10 dBFS. */
  microphone_level_dbfs?: number | null;
  /** Interviewer render mode chosen by the check (accepted once the portal schema adds it). */
  avatar_mode?: "3d" | "2d" | "none" | null;
  /** Measured 3D frame rate (accepted once the portal schema adds it). */
  avatar_fps?: number | null;
}

export interface SystemCheckResponse {
  passed: boolean;
  issues: string[];
  recorded_at: string;
}

/** POST /portal/interviews/{id}/session */
export interface SessionCreateRequest {
  round_id?: string | null;
}

export interface PortalSessionCreateResponse {
  session_id: string;
  status: SessionStatus;
}

/** GET /portal/sessions/{id} */
export interface PortalSession {
  id: string;
  interview_id: string;
  status: SessionStatus;
  round_name?: string | null;
  round_type?: string | null;
  duration_minutes?: number | null;
  started_at: string | null;
  ended_at: string | null;
  end_reason: SessionEndReason | string | null;
  candidate_report_available: boolean;
  details?: Record<string, unknown>;
}

/** POST /portal/sessions/{id}/ws-ticket — `ws_path` resolves against the page's own origin. */
export interface WsTicketResponse {
  ticket: string;
  ws_url: string;
  ws_path: string;
  expires_in: number;
}

export interface FrameUploadAccepted {
  accepted: boolean;
  media_id?: string | null;
}

export interface IntegrityEventsResponse {
  accepted: number;
  warnings: Array<{ event_type: IntegrityEventType; message: string }>;
}

export interface RecordingChunkResponse {
  media_id: string;
  segment: number;
  kind: "camera" | "screen";
  size_bytes: number;
  duplicate: boolean;
}

/** GET /portal/sessions/{id}/report — answers only once the candidate report is released. */
export interface CandidateReportOut {
  session_id: string;
  report_id: string;
  version: number;
  generated_at?: string | null;
  released_at?: string | null;
  content: Record<string, unknown>;
  artifact_url?: string | null;
  artifact_expires_at?: string | null;
}
