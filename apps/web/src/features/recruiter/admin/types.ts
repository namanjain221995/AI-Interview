/**
 * Admin-module refinements of the shared DTOs in `@/lib/types`.
 *
 * The backend owners build these endpoints in parallel against CONTRACTS §4;
 * everything here is tolerant of optional fields so the UI keeps working when
 * an aggregate or a nested reference is missing.
 */

import type {
  BankQuestion,
  BankStatus,
  Candidate,
  CandidateResume,
  CodeLanguage,
  Difficulty,
  Interview,
  QuestionSource,
  RunStatus,
  StaffUser,
} from "@/lib/types";

/** `GET /users` item (UserOut): StaffUser plus lockout/password metadata. */
export interface AdminUser extends StaffUser {
  is_locked?: boolean;
  password_changed_at?: string | null;
}

/** One selectable interviewer voice (Kokoro). */
export interface VoiceOption {
  id: string;
  language_name: string;
  gender: "female" | "male" | string;
  default?: boolean;
}

/** A generated question the server dropped (near-duplicate or invalid). */
export interface SkippedQuestion {
  text: string;
  reason: string;
  duplicate_of?: string | null;
  similarity?: number | null;
}

/** `POST /question-bank/generate` ({created, skipped, requested, generated}), normalised. */
export interface QuestionGenerateResult {
  items: BankQuestion[];
  /** Near-duplicates of existing questions the server skipped. */
  skipped: number;
  skippedItems: SkippedQuestion[];
}

/** `POST /question-bank/search` ({items, total, mode, reranked}). */
export interface QuestionSearchResult {
  items: BankQuestion[];
  /** "semantic", or "lexical" when embeddings aren't available yet. */
  mode: string;
  reranked: boolean;
}

export interface ValidationFailure {
  id: string;
  hidden: boolean;
  error: string | null;
  expected?: unknown;
  actual?: unknown;
}

/**
 * `GET /coding-questions` list row (`view=summary`, the API default): no prompt,
 * signature, starters, solutions, tests, rubric or hints; test counts instead.
 * `GET /coding-questions/{id}` returns the full problem.
 */
export interface CodingQuestionSummary {
  id: string;
  organization_id: string | null;
  is_global?: boolean;
  slug: string;
  title: string;
  difficulty: Difficulty;
  competency_keys: string[];
  role_tags: string[];
  languages: CodeLanguage[];
  time_limit_minutes: number;
  source: QuestionSource;
  status: BankStatus;
  has_embedding?: boolean;
  visible_test_count: number;
  hidden_test_count: number;
  created_at: string;
  updated_at: string;
}

/** One language of `POST /coding-questions/{id}/validate`, normalised. */
export interface CodingValidationRow {
  language: CodeLanguage;
  status: RunStatus | "skipped";
  passed: number;
  total: number;
  runtime_ms: number | null;
  error: string | null;
  failures: ValidationFailure[];
}

export interface CodingValidationSummary {
  ok: boolean;
  results: CodingValidationRow[];
  /** Starter code compiled against the visible tests (include_starters=true). */
  starters: CodingValidationRow[];
  duration_ms: number | null;
  validated_at: string | null;
}

/** `POST /candidates/{id}/resumes` returns the resume row (status uploaded/validating). */
export type ResumeUploadResult = CandidateResume;

/** `POST /interviews/{id}/regenerate-link` — same shape as the create response. */
export interface InviteSecrets {
  interview: Interview;
  invite_url: string;
  access_code?: string | null;
}

/** `GET /candidates` item (CandidateOut): Candidate plus profile highlights. */
export interface AdminCandidate extends Candidate {
  headline?: string | null;
  experience_years?: number | null;
  top_skills?: string[];
  latest_resume_id?: string | null;
}
