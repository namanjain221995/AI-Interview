/**
 * Question helpers: follow-up chains, primary answers and the time segments
 * used by the interview tape.
 */

import type { AnswerEvaluationRecord, AnswerQuality, DimensionScores, SessionAnswer, SessionQuestion } from "@/lib/types";
import type { BadgeTone } from "@/components/ui/Badge";

export interface QuestionThread {
  question: SessionQuestion;
  depth: number;
  children: QuestionThread[];
}

/**
 * Builds follow-up chains from `parent_question_id`. Roots and children are
 * ordered by sequence. A follow-up whose parent is missing (or that would
 * form a cycle) is promoted to a root so nothing disappears.
 */
export function buildQuestionThreads(questions: readonly SessionQuestion[]): QuestionThread[] {
  const byId = new Map(questions.map((q) => [q.id, q]));
  const children = new Map<string, SessionQuestion[]>();
  const roots: SessionQuestion[] = [];

  const reachesSelf = (start: SessionQuestion): boolean => {
    const visited = new Set<string>([start.id]);
    let current = start.parent_question_id ? byId.get(start.parent_question_id) : undefined;
    while (current) {
      if (visited.has(current.id)) return true;
      visited.add(current.id);
      current = current.parent_question_id ? byId.get(current.parent_question_id) : undefined;
    }
    return false;
  };

  for (const question of questions) {
    const parentId = question.parent_question_id;
    if (parentId && parentId !== question.id && byId.has(parentId) && !reachesSelf(question)) {
      const list = children.get(parentId) ?? [];
      list.push(question);
      children.set(parentId, list);
    } else {
      roots.push(question);
    }
  }

  const build = (question: SessionQuestion, depth: number): QuestionThread => ({
    question,
    depth,
    children: (children.get(question.id) ?? []).sort((a, b) => a.sequence - b.sequence).map((child) => build(child, depth + 1)),
  });
  return roots.sort((a, b) => a.sequence - b.sequence).map((root) => build(root, 0));
}

/** Depth-first flattening (parent before its follow-ups). */
export function flattenThreads(threads: readonly QuestionThread[]): Array<{ question: SessionQuestion; depth: number; parentId: string | null }> {
  const out: Array<{ question: SessionQuestion; depth: number; parentId: string | null }> = [];
  const walk = (thread: QuestionThread, parentId: string | null) => {
    out.push({ question: thread.question, depth: thread.depth, parentId });
    for (const child of thread.children) walk(child, thread.question.id);
  };
  for (const thread of threads) walk(thread, null);
  return out;
}

/** Number of follow-ups below a thread (all depths). */
export function followUpCount(thread: QuestionThread): number {
  return thread.children.reduce((sum, child) => sum + 1 + followUpCount(child), 0);
}

export function questionLabel(question: Pick<SessionQuestion, "sequence">): string {
  return `Q${question.sequence}`;
}

/** The answer that carries the evaluation (else the longest), for one-line summaries. */
export function primaryAnswer(question: Pick<SessionQuestion, "answers">): SessionAnswer | null {
  const answers = [...(question.answers ?? [])].sort((a, b) => a.sequence - b.sequence);
  if (answers.length === 0) return null;
  return answers.find((a) => a.evaluation) ?? answers.reduce((best, a) => ((a.word_count ?? 0) > (best.word_count ?? 0) ? a : best), answers[0]!);
}

export const DIMENSION_LABELS: Record<keyof DimensionScores, string> = {
  correctness: "Correctness",
  relevance: "Relevance",
  completeness: "Completeness",
  depth: "Depth",
  specificity: "Specificity",
  practical_experience: "Practical experience",
  reasoning: "Reasoning",
  consistency: "Consistency",
  architecture_awareness: "Architecture awareness",
  tradeoff_understanding: "Trade-off understanding",
  communication_clarity: "Clarity",
};

const DIMENSION_KEYS = Object.keys(DIMENSION_LABELS) as Array<keyof DimensionScores>;

/** Dimensions that apply to an evaluation (non-null), in a stable order. */
export function evaluationDimensions(evaluation: Partial<AnswerEvaluationRecord> | null | undefined): Array<{ key: keyof DimensionScores; label: string; value: number }> {
  if (!evaluation) return [];
  const nested = evaluation.evaluation?.scores;
  return DIMENSION_KEYS.map((key) => {
    const direct = evaluation[key];
    const value = typeof direct === "number" ? direct : typeof nested?.[key] === "number" ? (nested[key] as number) : null;
    return value === null ? null : { key, label: DIMENSION_LABELS[key], value };
  }).filter((d): d is { key: keyof DimensionScores; label: string; value: number } => d !== null);
}

export const QUALITY_TONE: Record<AnswerQuality, BadgeTone> = {
  strong: "success",
  adequate: "accent",
  partial: "warning",
  vague: "warning",
  incorrect: "danger",
  no_answer: "neutral",
  off_topic: "neutral",
  clarification_request: "outline",
  candidate_question: "outline",
  wants_time: "outline",
  wants_hint: "outline",
};

export interface QuestionSpan {
  question: SessionQuestion;
  /** Seconds from interview start. */
  start: number;
  end: number;
}

/**
 * Time span of each question: from when it was asked to the next question
 * (or the end of its last answer, or the interview end for the last one).
 */
export function questionSpans(questions: readonly SessionQuestion[], durationSeconds: number): QuestionSpan[] {
  const ordered = [...questions].sort((a, b) => a.elapsed_seconds - b.elapsed_seconds || a.sequence - b.sequence);
  return ordered.map((question, index) => {
    const start = Math.max(0, question.elapsed_seconds ?? 0);
    const next = ordered[index + 1];
    const answerEnd = Math.max(0, ...question.answers.map((a) => (a.ended_ms ?? 0) / 1000));
    const end = next ? Math.max(start, next.elapsed_seconds) : Math.max(start + 1, answerEnd, durationSeconds);
    return { question, start, end: Math.max(end, start) };
  });
}
