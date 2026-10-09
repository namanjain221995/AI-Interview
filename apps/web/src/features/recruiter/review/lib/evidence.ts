/**
 * Evidence grouping and the "why this score" assembly. Evidence records are
 * atomic, attributable observations (competency, signal, strength,
 * confidence, quote, question/answer, transcript span); every score the
 * reviewer sees is explained from them.
 */

import type { CompetencyDigest, Difficulty, EvidenceRecord, EvidenceSignal, ScoreRecord, SessionQuestion } from "@/lib/types";
import { clamp } from "@/lib/utils";

import { isRecord, numberOrNull, stringOrNull } from "../api/normalize";
import type { ScoreWhy } from "../api/types";

/** Weight of one item: strength x confidence, both clamped to 0-1. */
export function evidenceWeight(item: Pick<EvidenceRecord, "strength" | "confidence">): number {
  const strength = Number.isFinite(item.strength) ? clamp(item.strength, 0, 1) : 0;
  const confidence = Number.isFinite(item.confidence) ? clamp(item.confidence, 0, 1) : 0;
  return strength * confidence;
}

/** Strongest first; ties keep chronological order. */
export function sortByWeight<T extends Pick<EvidenceRecord, "strength" | "confidence"> & Partial<Pick<EvidenceRecord, "elapsed_seconds" | "created_at">>>(items: readonly T[]): T[] {
  return items
    .map((item, order) => ({ item, order }))
    .sort((a, b) => evidenceWeight(b.item) - evidenceWeight(a.item) || (a.item.elapsed_seconds ?? 0) - (b.item.elapsed_seconds ?? 0) || a.order - b.order)
    .map(({ item }) => item);
}

/** Merges evidence from the evidence endpoint and the questions payload (deduplicated by id). */
export function collectEvidence(fromEndpoint: readonly EvidenceRecord[] | undefined, questions: readonly SessionQuestion[] | undefined): EvidenceRecord[] {
  const byId = new Map<string, EvidenceRecord>();
  for (const item of fromEndpoint ?? []) byId.set(item.id, item);
  for (const question of questions ?? []) {
    for (const answer of question.answers ?? []) {
      for (const item of answer.evidence ?? []) {
        if (!byId.has(item.id)) {
          byId.set(item.id, { ...item, question_id: item.question_id ?? question.id, answer_id: item.answer_id ?? answer.id });
        }
      }
    }
  }
  return [...byId.values()];
}

export interface EvidenceGroup {
  key: string;
  positive: EvidenceRecord[];
  negative: EvidenceRecord[];
  neutral: EvidenceRecord[];
  /** Non-neutral items (what the scoring engine counts). */
  counted: number;
  positiveWeight: number;
  negativeWeight: number;
  /** -1 (all concerns) .. 1 (all support), weighted by strength x confidence. */
  balance: number;
}

function emptyGroup(key: string): EvidenceGroup {
  return { key, positive: [], negative: [], neutral: [], counted: 0, positiveWeight: 0, negativeWeight: 0, balance: 0 };
}

function finishGroup(group: EvidenceGroup): EvidenceGroup {
  group.positive = sortByWeight(group.positive);
  group.negative = sortByWeight(group.negative);
  group.neutral = sortByWeight(group.neutral);
  group.counted = group.positive.length + group.negative.length;
  group.positiveWeight = round3(group.positive.reduce((sum, e) => sum + evidenceWeight(e), 0));
  group.negativeWeight = round3(group.negative.reduce((sum, e) => sum + evidenceWeight(e), 0));
  const total = group.positiveWeight + group.negativeWeight;
  group.balance = total > 0 ? round3((group.positiveWeight - group.negativeWeight) / total) : 0;
  return group;
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/**
 * Groups evidence by an arbitrary key, splitting by signal and sorting each
 * side strongest-first. `order` fixes the group order (unknown keys follow in
 * first-seen order); keys listed in `order` without evidence still get an
 * (empty) group so every competency in the round is represented.
 */
export function groupEvidence(evidence: readonly EvidenceRecord[], keyOf: (item: EvidenceRecord) => string | null | undefined, order: readonly string[] = []): EvidenceGroup[] {
  const groups = new Map<string, EvidenceGroup>();
  for (const key of order) groups.set(key, emptyGroup(key));
  for (const item of evidence) {
    const key = keyOf(item);
    if (!key) continue;
    let group = groups.get(key);
    if (!group) {
      group = emptyGroup(key);
      groups.set(key, group);
    }
    bucketFor(group, item.signal).push(item);
  }
  return [...groups.values()].map(finishGroup);
}

function bucketFor(group: EvidenceGroup, signal: EvidenceSignal): EvidenceRecord[] {
  if (signal === "positive") return group.positive;
  if (signal === "negative") return group.negative;
  return group.neutral;
}

export function groupEvidenceByCompetency(evidence: readonly EvidenceRecord[], order: readonly string[] = []): EvidenceGroup[] {
  return groupEvidence(evidence, (e) => e.competency_key, order);
}

/** Evidence by answer id (answer-less items fall back to their question id). */
export function evidenceByAnswer(evidence: readonly EvidenceRecord[]): Map<string, EvidenceRecord[]> {
  const out = new Map<string, EvidenceRecord[]>();
  for (const item of evidence) {
    const key = item.answer_id ?? (item.question_id ? `q:${item.question_id}` : null);
    if (!key) continue;
    const list = out.get(key) ?? [];
    list.push(item);
    out.set(key, list);
  }
  for (const [key, list] of out) out.set(key, sortByWeight(list));
  return out;
}

export function evidenceByQuestion(evidence: readonly EvidenceRecord[]): Map<string, EvidenceRecord[]> {
  const out = new Map<string, EvidenceRecord[]>();
  for (const item of evidence) {
    if (!item.question_id) continue;
    const list = out.get(item.question_id) ?? [];
    list.push(item);
    out.set(item.question_id, list);
  }
  for (const [key, list] of out) out.set(key, sortByWeight(list));
  return out;
}

/** The strongest items of one signal across all competencies (overview strengths / gaps). */
export function topEvidence(evidence: readonly EvidenceRecord[], signal: EvidenceSignal, limit = 3): EvidenceRecord[] {
  return sortByWeight(evidence.filter((e) => e.signal === signal)).slice(0, limit);
}

export function quoteOf(item: Pick<EvidenceRecord, "transcript_ref">): string | null {
  const ref = item.transcript_ref;
  return isRecord(ref) ? stringOrNull(ref.quote) : null;
}

// ------------------------------------------------------------- why this score

export interface TranscriptReference {
  answerId: string | null;
  questionId: string | null;
  startMs: number | null;
  endMs: number | null;
  quote: string | null;
}

export interface CompetencyExplanation {
  key: string;
  label: string;
  /** 0-10 */
  score: number | null;
  confidence: number | null;
  evidenceCount: number;
  sufficient: boolean | null;
  weight: number | null;
  required: boolean | null;
  positives: EvidenceRecord[];
  negatives: EvidenceRecord[];
  neutrals: EvidenceRecord[];
  balance: number;
  unansweredAreas: string[];
  supportingQuestions: SessionQuestion[];
  transcriptRefs: TranscriptReference[];
  difficultyMix: Partial<Record<Difficulty, number>>;
  /** The scoring engine's own breakdown, when the scores API provides it. */
  calculation: ScoreWhy["calculation"];
  observations: ScoreWhy["observations"];
}

export interface ExplainInput {
  key: string;
  label: string;
  score?: (ScoreRecord & { why?: ScoreWhy }) | null;
  digest?: CompetencyDigest | null;
  evidence: readonly EvidenceRecord[];
  questions: readonly SessionQuestion[];
  weight?: number | null;
  required?: boolean | null;
}

function resolveIds(ids: readonly string[] | undefined, byId: Map<string, EvidenceRecord>): EvidenceRecord[] {
  if (!ids?.length) return [];
  return ids.map((id) => byId.get(id)).filter((e): e is EvidenceRecord => !!e);
}

function toReference(raw: unknown): TranscriptReference | null {
  if (!isRecord(raw)) return null;
  const ref: TranscriptReference = {
    answerId: stringOrNull(raw.answer_id),
    questionId: stringOrNull(raw.question_id),
    startMs: numberOrNull(raw.start_ms),
    endMs: numberOrNull(raw.end_ms),
    quote: stringOrNull(raw.quote) ?? stringOrNull(raw.text),
  };
  return ref.answerId || ref.questionId || ref.startMs !== null || ref.quote ? ref : null;
}

/**
 * Assembles the explanation of one competency score. Score records carry the
 * engine's own lists (positive/negative evidence ids, supporting questions,
 * transcript refs, unanswered areas); when a list is missing it is derived
 * from the raw evidence so the explanation never comes up empty.
 */
export function explainCompetency(input: ExplainInput): CompetencyExplanation {
  const { key, score, digest } = input;
  const why = score?.why ?? null;
  const own = input.evidence.filter((e) => e.competency_key === key);
  const byId = new Map(input.evidence.map((e) => [e.id, e]));
  // The scores API may embed the full evidence records ("why"); prefer the shared copies when loaded.
  const fromWhy = (items: readonly EvidenceRecord[] | undefined) => (items ?? []).map((e) => byId.get(e.id) ?? e);

  const listedPositive = why?.positive_evidence?.length ? fromWhy(why.positive_evidence) : resolveIds(score?.positive_evidence_ids, byId);
  const listedNegative = why?.negative_evidence?.length ? fromWhy(why.negative_evidence) : resolveIds(score?.negative_evidence_ids, byId);
  const positives = sortByWeight(listedPositive.length ? listedPositive : own.filter((e) => e.signal === "positive"));
  const negatives = sortByWeight(listedNegative.length ? listedNegative : own.filter((e) => e.signal === "negative"));
  const neutrals = sortByWeight(own.filter((e) => e.signal === "neutral"));

  const questionById = new Map(input.questions.map((q) => [q.id, q]));
  const supportingIds = new Set<string>();
  for (const ref of why?.supporting_questions ?? []) if (questionById.has(ref.id)) supportingIds.add(ref.id);
  for (const id of score?.supporting_question_ids ?? []) if (questionById.has(id)) supportingIds.add(id);
  if (supportingIds.size === 0) {
    for (const item of [...positives, ...negatives, ...neutrals]) if (item.question_id && questionById.has(item.question_id)) supportingIds.add(item.question_id);
    for (const question of input.questions) if (question.competency_keys.includes(key) && question.answers.length > 0) supportingIds.add(question.id);
  }
  const supportingQuestions = [...supportingIds].map((id) => questionById.get(id)!).sort((a, b) => a.sequence - b.sequence);

  const refs: TranscriptReference[] = [];
  const seen = new Set<string>();
  const pushRef = (ref: TranscriptReference | null) => {
    if (!ref) return;
    const id = `${ref.answerId ?? ""}|${ref.startMs ?? ""}|${ref.quote ?? ""}`;
    if (seen.has(id)) return;
    seen.add(id);
    refs.push(ref);
  };
  for (const raw of why?.transcript_refs ?? score?.transcript_refs ?? []) pushRef(toReference(raw));
  for (const item of [...positives, ...negatives]) {
    const ref = toReference({ ...(isRecord(item.transcript_ref) ? item.transcript_ref : {}), question_id: item.question_id, answer_id: item.transcript_ref?.answer_id ?? item.answer_id });
    if (ref?.quote) pushRef(ref);
  }
  refs.sort((a, b) => (a.startMs ?? Number.MAX_SAFE_INTEGER) - (b.startMs ?? Number.MAX_SAFE_INTEGER));

  const difficultyMix: Partial<Record<Difficulty, number>> = {};
  for (const item of [...positives, ...negatives]) if (item.difficulty) difficultyMix[item.difficulty] = (difficultyMix[item.difficulty] ?? 0) + 1;

  const positiveWeight = positives.reduce((sum, e) => sum + evidenceWeight(e), 0);
  const negativeWeight = negatives.reduce((sum, e) => sum + evidenceWeight(e), 0);
  const total = positiveWeight + negativeWeight;

  return {
    key,
    label: input.label,
    score: score?.score ?? digest?.score_estimate ?? null,
    confidence: score?.confidence ?? digest?.confidence ?? null,
    evidenceCount: score?.evidence_count ?? digest?.evidence_count ?? positives.length + negatives.length,
    sufficient: score?.sufficient_evidence ?? digest?.sufficient_evidence ?? null,
    weight: input.weight ?? score?.weight ?? digest?.weight ?? null,
    required: input.required ?? digest?.required ?? null,
    positives,
    negatives,
    neutrals,
    balance: total > 0 ? round3((positiveWeight - negativeWeight) / total) : 0,
    unansweredAreas: [...new Set(why?.unanswered_areas ?? score?.unanswered_areas ?? [])],
    supportingQuestions,
    transcriptRefs: refs,
    difficultyMix,
    calculation: why?.calculation ?? (isRecord(score?.details) && isRecord(score.details.calculation) ? (score.details.calculation as ScoreWhy["calculation"]) : null),
    observations: why?.observations ?? [],
  };
}

/** One-sentence plain-language summary of why a competency scored what it did. */
export function explanationSentence(explanation: CompetencyExplanation): string {
  const { positives, negatives, supportingQuestions, unansweredAreas } = explanation;
  if (positives.length + negatives.length === 0) {
    return supportingQuestions.length
      ? `Asked about in ${supportingQuestions.length === 1 ? "one question" : `${supportingQuestions.length} questions`}, but no scored evidence yet.`
      : "No evidence collected for this competency.";
  }
  const parts = [
    positives.length ? `${positives.length} supporting ${positives.length === 1 ? "observation" : "observations"}` : null,
    negatives.length ? `${negatives.length} ${negatives.length === 1 ? "gap" : "gaps"}` : null,
  ].filter(Boolean);
  const across = supportingQuestions.length ? ` across ${supportingQuestions.length === 1 ? "one question" : `${supportingQuestions.length} questions`}` : "";
  const open = unansweredAreas.length ? `; ${unansweredAreas.length === 1 ? "one area" : `${unansweredAreas.length} areas`} not yet covered` : "";
  return `Based on ${parts.join(" and ")}${across}${open}.`;
}
