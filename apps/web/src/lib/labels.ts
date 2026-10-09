/**
 * Human-readable labels for backend enum values. Copy is sentence case and
 * written from the reader's point of view. Recommendation labels are
 * deliberately assistive: they describe the evidence, never the person.
 */

import type {
  AnswerQuality,
  CandidateReportMode,
  ClaimStatus,
  CodeLanguage,
  CodingSessionStatus,
  CompetencyCategory,
  Difficulty,
  EvidenceSignal,
  EvidenceSource,
  IntegrityAction,
  IntegrityEventType,
  IntegrityReviewStatus,
  IntegritySeverity,
  IntegritySource,
  InterviewStatus,
  JobStatus,
  QuestionKind,
  Recommendation,
  ReportStatus,
  RequirementKind,
  ResumeStatus,
  ReviewDecisionValue,
  Role,
  RoundType,
  RunStatus,
  SegmentKind,
  SessionEndReason,
  SessionStatus,
  TurnAction,
} from "@/lib/types";
import { humanize } from "@/lib/format";

export const ROLE_LABELS: Record<Role, string> = {
  SUPER_ADMIN: "Super admin",
  ADMIN: "Admin",
  RECRUITER: "Recruiter",
  INTERVIEWER: "Interviewer",
  CANDIDATE: "Candidate",
};

export const ROUND_TYPE_LABELS: Record<RoundType, string> = {
  resume_screening: "Resume screening",
  recruiter_screening: "Recruiter screening",
  technical_screening: "Technical screening",
  deep_technical: "Deep technical",
  coding: "Coding",
  system_design: "System design",
  problem_solving: "Problem solving",
  behavioral: "Behavioral",
  managerial: "Managerial",
  leadership: "Leadership",
  custom: "Custom",
  combined: "Combined",
  intake: "Intake interview",
};

export const DIFFICULTY_LABELS: Record<Difficulty, string> = {
  easy: "Easy",
  medium: "Medium",
  hard: "Hard",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  draft: "Draft",
  open: "Open",
  closed: "Closed",
  archived: "Archived",
};

export const REQUIREMENT_KIND_LABELS: Record<RequirementKind, string> = {
  required_skill: "Required skill",
  preferred_skill: "Preferred skill",
  responsibility: "Responsibility",
  qualification: "Qualification",
};

export const COMPETENCY_CATEGORY_LABELS: Record<CompetencyCategory, string> = {
  technical: "Technical",
  problem_solving: "Problem solving",
  system_design: "System design",
  behavioral: "Behavioral",
  leadership: "Leadership",
  communication: "Communication",
  domain: "Domain",
  coding: "Coding",
};

export const INTERVIEW_STATUS_LABELS: Record<InterviewStatus, string> = {
  invited: "Invited",
  in_progress: "In progress",
  completed: "Completed",
  expired: "Expired",
  cancelled: "Cancelled",
};

export const SESSION_STATUS_LABELS: Record<SessionStatus, string> = {
  created: "Created",
  preparing: "Preparing",
  ready: "Ready to start",
  live: "Live",
  paused: "Paused",
  completing: "Wrapping up",
  completed: "Completed",
  aborted: "Ended early",
  failed: "Failed",
};

export const SESSION_END_REASON_LABELS: Record<SessionEndReason, string> = {
  sufficient_evidence: "Enough evidence gathered",
  time_limit: "Time limit reached",
  stopping_condition: "Stopping condition met",
  candidate_ended: "Ended by the candidate",
  disconnect_timeout: "Candidate did not reconnect",
  policy_termination: "Ended by interview policy",
  technical_failure: "Technical problem",
  cancelled: "Cancelled",
};

export const RESUME_STATUS_LABELS: Record<ResumeStatus, string> = {
  uploaded: "Uploaded",
  validating: "Checking file",
  extracting: "Reading text",
  ocr: "Scanning pages",
  analyzing: "Analyzing",
  ready: "Ready",
  failed: "Failed",
  rejected: "Rejected",
};

export const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  pending: "Pending",
  generating: "Generating",
  ready: "Ready",
  failed: "Failed",
};

export const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  strong_proceed: "Strong evidence to proceed",
  proceed: "Evidence supports proceeding",
  human_review: "Needs a closer human review",
  borderline_evidence: "Borderline evidence",
  insufficient_evidence: "Not enough evidence",
};

export const RECOMMENDATION_SHORT_LABELS: Record<Recommendation, string> = {
  strong_proceed: "Strong evidence",
  proceed: "Supports proceeding",
  human_review: "Closer review",
  borderline_evidence: "Borderline",
  insufficient_evidence: "Not enough evidence",
};

export const REVIEW_DECISION_LABELS: Record<ReviewDecisionValue, string> = {
  advance: "Advance",
  hold: "Hold",
  reject: "Do not advance",
  needs_more_information: "Needs more information",
};

export const ANSWER_QUALITY_LABELS: Record<AnswerQuality, string> = {
  strong: "Strong",
  adequate: "Adequate",
  partial: "Partial",
  vague: "Vague",
  incorrect: "Incorrect",
  no_answer: "No answer",
  off_topic: "Off topic",
  clarification_request: "Asked for clarification",
  candidate_question: "Asked a question",
  wants_time: "Asked for time",
  wants_hint: "Asked for a hint",
};

export const EVIDENCE_SIGNAL_LABELS: Record<EvidenceSignal, string> = {
  positive: "Supporting evidence",
  negative: "Gap or concern",
  neutral: "Neutral observation",
};

export const EVIDENCE_SOURCE_LABELS: Record<EvidenceSource, string> = {
  answer: "Answer",
  coding: "Coding",
  resume_claim: "Resume claim",
  hint_usage: "Hint usage",
};

export const CLAIM_STATUS_LABELS: Record<ClaimStatus, string> = {
  unverified: "Not yet discussed",
  in_progress: "Being verified",
  verified: "Supported",
  partially_verified: "Partly supported",
  contradicted: "Contradicted",
  not_applicable: "Not applicable",
};

export const QUESTION_KIND_LABELS: Record<QuestionKind, string> = {
  intro: "Introduction",
  technical: "Technical",
  scenario: "Scenario",
  behavioral: "Behavioral",
  resume_claim: "Resume claim",
  system_design: "System design",
  coding: "Coding",
  recruiter: "Recruiter",
  leadership: "Leadership",
  followup: "Follow-up",
  closing: "Closing",
};

export const SEGMENT_KIND_LABELS: Record<SegmentKind, string> = {
  intro: "Introduction",
  resume_validation: "Resume discussion",
  technical: "Technical",
  coding: "Coding",
  system_design: "System design",
  problem_solving: "Problem solving",
  behavioral: "Behavioral",
  leadership: "Leadership",
  recruiter: "Recruiter questions",
  candidate_questions: "Candidate questions",
  closing: "Closing",
};

export const TURN_ACTION_LABELS: Record<TurnAction, string> = {
  introduce: "Introduction",
  ask_question: "Question",
  ask_followup: "Follow-up",
  request_specificity: "Asked for specifics",
  clarify_question: "Clarified the question",
  simplify: "Simplified",
  increase_difficulty: "Raised difficulty",
  verify_claim: "Verified a resume claim",
  verify_contradiction: "Checked a contradiction",
  change_topic: "Changed topic",
  start_coding: "Started coding",
  coding_comment: "Coding comment",
  give_hint: "Hint",
  acknowledge_and_wait: "Acknowledged",
  answer_candidate_question: "Answered a question",
  redirect: "Redirected",
  invite_candidate_questions: "Invited questions",
  close_interview: "Closing",
};

export const CODE_LANGUAGE_LABELS: Record<CodeLanguage, string> = {
  javascript: "JavaScript",
  typescript: "TypeScript",
  python: "Python",
  java: "Java",
  go: "Go",
  cpp: "C++",
  sql: "SQL",
};

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  passed: "Passed",
  failed: "Failed",
  error: "Runtime error",
  compile_error: "Compile error",
  timeout: "Timed out",
  memory_exceeded: "Memory limit exceeded",
  rejected: "Rejected",
};

export const CODING_SESSION_STATUS_LABELS: Record<CodingSessionStatus, string> = {
  active: "In progress",
  submitted: "Submitted",
  timed_out: "Timed out",
  abandoned: "Not finished",
};

export const INTEGRITY_EVENT_LABELS: Record<IntegrityEventType, string> = {
  fullscreen_exited: "Left full screen",
  fullscreen_declined: "Declined full screen",
  tab_hidden: "Switched away from the tab",
  focus_lost: "Interview window lost focus",
  rapid_focus_switching: "Frequent focus switching",
  window_minimized: "Window minimized",
  copy_event: "Copied text",
  paste_event: "Pasted text",
  large_paste_in_coding: "Large paste into the code editor",
  prohibited_shortcut: "Restricted shortcut used",
  context_menu: "Opened the context menu",
  page_reload: "Reloaded the page",
  disconnect: "Disconnected",
  reconnect: "Reconnected",
  unusual_disconnect_pattern: "Unusual disconnect pattern",
  multiple_displays_detected: "More than one display detected",
  camera_disabled: "Camera turned off",
  camera_blocked: "Camera view blocked",
  microphone_disabled: "Microphone turned off",
  no_person_visible: "No one visible on camera",
  multiple_persons_visible: "More than one person visible",
  person_left_frame: "Left the camera frame",
  another_person_entered: "Another person entered the frame",
  possible_phone_visible: "Possible phone in view",
  possible_photo_taking: "Possible photo of the screen",
  looking_away_with_other_signals: "Looking away alongside other signals",
  screen_share_started: "Screen sharing started",
  screen_share_stopped: "Screen sharing stopped",
  screen_share_not_full_screen: "Shared a window instead of the full screen",
  screen_context_change: "Shared screen content changed",
  face_not_visible: "No face visible on camera",
  multiple_faces_visible: "More than one face visible",
  static_face_ignored: "Motionless face set aside (photo, poster or screen)",
  face_check_summary: "Face check summary",
};

export const INTEGRITY_SOURCE_LABELS: Record<IntegritySource, string> = {
  browser: "Browser",
  vision: "Camera",
  screen: "Screen share",
  server: "Server",
};

export const INTEGRITY_SEVERITY_LABELS: Record<IntegritySeverity, string> = {
  info: "Info",
  low: "Low",
  medium: "Medium",
  high: "High",
};

export const INTEGRITY_ACTION_LABELS: Record<IntegrityAction, string> = {
  log_only: "Logged",
  warn_candidate: "Candidate warned",
  flag_for_human_review: "Flagged for review",
  pause_interview: "Interview paused",
  terminate_interview: "Interview ended by policy",
};

export const INTEGRITY_REVIEW_STATUS_LABELS: Record<IntegrityReviewStatus, string> = {
  unreviewed: "Not reviewed",
  dismissed: "Dismissed",
  noted: "Noted",
  confirmed_concern: "Confirmed concern",
};

export const CANDIDATE_REPORT_MODE_LABELS: Record<CandidateReportMode, string> = {
  immediate: "Right after the interview",
  after_review: "After a reviewer releases it",
  never: "Not shared with candidates",
};

/** Label lookup with a readable fallback for values added after this file. */
export function labelFor<T extends string>(labels: Partial<Record<T, string>>, value: T | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return labels[value] ?? humanize(value);
}
