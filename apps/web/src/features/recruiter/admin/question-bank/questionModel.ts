/** Question bank editor model: draft <-> BankQuestionInput, validation. */

import type { BankQuestion, BankQuestionInput, BankQuestionRubric, BankStatus, Difficulty, QuestionKind, RoundType } from "@/lib/types";

import { blankToNull, cleanList } from "../lib/utils";

export interface QuestionDraft {
  competency_key: string;
  difficulty: Difficulty;
  kind: QuestionKind;
  topic: string;
  text: string;
  role_tags: string[];
  round_types: RoundType[];
  expected_concepts: string[];
  rubric_excellent: string;
  rubric_good: string;
  rubric_weak: string;
  /** Dimension name -> what to look for. */
  rubric_dimensions: Array<{ key: string; value: string }>;
  followups: string[];
  common_errors: string[];
  status: BankStatus;
}

/** Kinds that make sense for a bank question (follow-ups/intro/closing are generated live). */
export const BANK_KINDS: QuestionKind[] = ["technical", "scenario", "behavioral", "system_design", "coding", "leadership", "recruiter", "resume_claim"];

export function emptyQuestionDraft(): QuestionDraft {
  return {
    competency_key: "",
    difficulty: "medium",
    kind: "technical",
    topic: "",
    text: "",
    role_tags: [],
    round_types: [],
    expected_concepts: [""],
    rubric_excellent: "",
    rubric_good: "",
    rubric_weak: "",
    rubric_dimensions: [],
    followups: [],
    common_errors: [],
    status: "active",
  };
}

export function questionDraftFrom(q: BankQuestion): QuestionDraft {
  const rubric: BankQuestionRubric = q.rubric ?? {};
  return {
    competency_key: q.competency_key ?? "",
    difficulty: q.difficulty ?? "medium",
    kind: q.kind ?? "technical",
    topic: q.topic ?? "",
    text: q.text ?? "",
    role_tags: [...(q.role_tags ?? [])],
    round_types: [...(q.round_types ?? [])],
    expected_concepts: q.expected_concepts?.length ? [...q.expected_concepts] : [""],
    rubric_excellent: rubric.excellent ?? "",
    rubric_good: rubric.good ?? "",
    rubric_weak: rubric.weak ?? "",
    rubric_dimensions: Object.entries(rubric.dimensions ?? {}).map(([key, value]) => ({ key, value: String(value) })),
    followups: [...(q.followups ?? [])],
    common_errors: [...(q.common_errors ?? [])],
    status: q.status ?? "active",
  };
}

export function toQuestionInput(draft: QuestionDraft): BankQuestionInput {
  const rubric: BankQuestionRubric = {};
  if (draft.rubric_excellent.trim()) rubric.excellent = draft.rubric_excellent.trim();
  if (draft.rubric_good.trim()) rubric.good = draft.rubric_good.trim();
  if (draft.rubric_weak.trim()) rubric.weak = draft.rubric_weak.trim();
  const dims = draft.rubric_dimensions.filter((d) => d.key.trim() && d.value.trim());
  if (dims.length) rubric.dimensions = Object.fromEntries(dims.map((d) => [d.key.trim(), d.value.trim()]));
  return {
    competency_key: draft.competency_key,
    difficulty: draft.difficulty,
    kind: draft.kind,
    text: draft.text.trim(),
    topic: blankToNull(draft.topic),
    role_tags: cleanList(draft.role_tags),
    round_types: [...draft.round_types],
    expected_concepts: cleanList(draft.expected_concepts),
    rubric,
    followups: cleanList(draft.followups),
    common_errors: cleanList(draft.common_errors),
    status: draft.status,
  };
}

export function validateQuestion(draft: QuestionDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!draft.competency_key) errors.competency_key = "Choose the competency this question assesses.";
  const text = draft.text.trim();
  if (!text) errors.text = "Write the question.";
  else if (text.length < 10) errors.text = "Make the question specific enough to answer (at least 10 characters).";
  else if (text.length > 2000) errors.text = "Keep the question under 2,000 characters.";
  if (draft.topic.trim().length > 120) errors.topic = "Keep the topic under 120 characters.";
  const concepts = cleanList(draft.expected_concepts);
  if (concepts.length === 0) errors.expected_concepts = "List at least one concept a strong answer covers; evaluation relies on it.";
  else if (concepts.length > 20) errors.expected_concepts = "List at most 20 concepts.";
  else if (concepts.some((c) => c.length > 300)) errors.expected_concepts = "Keep each concept under 300 characters.";
  for (const [key, label] of [
    ["followups", "follow-ups"],
    ["common_errors", "common errors"],
  ] as const) {
    const items = cleanList(draft[key]);
    if (items.length > 10) errors[key] = `List at most 10 ${label}.`;
    else if (items.some((c) => c.length > 300)) errors[key] = "Keep each entry under 300 characters.";
  }
  for (const key of ["rubric_excellent", "rubric_good", "rubric_weak"] as const) {
    if (draft[key].length > 1000) errors[key] = "Keep this under 1,000 characters.";
  }
  if (draft.role_tags.length > 20) errors.role_tags = "Use at most 20 role tags.";
  const keys = new Set<string>();
  draft.rubric_dimensions.forEach((d, i) => {
    const key = d.key.trim();
    if (!key && d.value.trim()) errors[`rubric_dimensions.${i}`] = "Name the dimension.";
    if (key && keys.has(key)) errors[`rubric_dimensions.${i}`] = "Each dimension once.";
    keys.add(key);
  });
  return errors;
}

/** Server field paths -> draft paths (rubric.excellent -> rubric_excellent). */
export function mapQuestionServerErrors(errors: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [path, message] of Object.entries(errors)) {
    if (path.startsWith("rubric.dimensions")) out.rubric_dimensions = message;
    else if (path.startsWith("rubric.")) out[`rubric_${path.split(".")[1]}`] = message;
    else out[path.split(".")[0] ?? path] = message;
  }
  return out;
}
