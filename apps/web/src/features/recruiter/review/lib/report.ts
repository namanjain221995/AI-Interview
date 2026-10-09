/**
 * Builds the printable recruiter report view. The report row's `content` is
 * the frozen assembly made at generation time and wins wherever it has a
 * section; sections it lacks are filled from the live session APIs so the
 * report is always complete. Field names are probed tolerantly because the
 * content document is versioned on the backend.
 */

import type {
  ClaimStatus,
  CodingSessionDetail,
  ContradictionFinding,
  EvidenceRecord,
  IntegritySeverity,
  JobRef,
  PersonRef,
  Recommendation,
  RecruiterReportNarrative,
  Report,
  ReviewDecision,
  RoundRef,
  SessionDetail,
  SessionEndReason,
  SessionQuestion,
  SessionScores,
} from "@/lib/types";

import { asList, isRecord, numberOrNull, stringList, stringOrNull } from "../api/normalize";
import type {
  RecruiterReportView,
  ReportClaimView,
  ReportCodingView,
  ReportCompetencyView,
  ReportEvidenceView,
  ReportIntegrityView,
  ReportQuestionView,
  SessionIntegrityView,
  TranscriptEntry,
} from "../api/types";
import type { CompetencyIndex } from "./competencies";
import { assistanceFactor } from "./coding";
import { quoteOf } from "./evidence";
import { evaluationDimensions, primaryAnswer } from "./questions";

export interface ReportSources {
  report?: Report | null;
  session?: SessionDetail | null;
  scores?: SessionScores | null;
  questions?: readonly SessionQuestion[];
  evidence?: readonly EvidenceRecord[];
  coding?: readonly CodingSessionDetail[];
  integrity?: SessionIntegrityView | null;
  transcript?: readonly TranscriptEntry[];
  reviews?: readonly ReviewDecision[];
  index: CompetencyIndex;
}

type Obj = Record<string, unknown>;

function obj(value: unknown): Obj {
  return isRecord(value) ? value : {};
}

function first<T>(...values: Array<T | null | undefined>): T | null {
  for (const value of values) if (value !== null && value !== undefined) return value;
  return null;
}

function firstList(...values: unknown[]): unknown[] | null {
  for (const value of values) if (Array.isArray(value) && value.length > 0) return value;
  return null;
}

const RECOMMENDATIONS: readonly Recommendation[] = ["strong_proceed", "proceed", "human_review", "borderline_evidence", "insufficient_evidence"];

function asRecommendation(value: unknown): Recommendation | null {
  const raw = isRecord(value) ? (stringOrNull(value.value) ?? stringOrNull(value.recommendation)) : stringOrNull(value);
  return raw && (RECOMMENDATIONS as readonly string[]).includes(raw) ? (raw as Recommendation) : null;
}

function personRef(value: unknown): PersonRef | null {
  if (!isRecord(value)) return null;
  const name = stringOrNull(value.full_name) ?? stringOrNull(value.name);
  if (!name) return null;
  return { id: stringOrNull(value.id) ?? "", full_name: name, email: stringOrNull(value.email) ?? undefined };
}

function jobRef(value: unknown): JobRef | null {
  if (!isRecord(value)) return null;
  const title = stringOrNull(value.title) ?? stringOrNull(value.name);
  return title ? { id: stringOrNull(value.id) ?? "", title } : null;
}

function roundRef(value: unknown): RoundRef | null {
  if (!isRecord(value)) return null;
  const name = stringOrNull(value.name);
  return name ? { id: stringOrNull(value.id) ?? "", name, round_type: (stringOrNull(value.round_type) ?? "custom") as RoundRef["round_type"] } : null;
}

function toEvidenceView(raw: unknown): ReportEvidenceView | null {
  if (!isRecord(raw)) return null;
  const text = stringOrNull(raw.evidence) ?? stringOrNull(raw.text) ?? stringOrNull(raw.detail);
  const key = stringOrNull(raw.competency_key) ?? stringOrNull(raw.competency);
  if (!text || !key) return null;
  const signal = stringOrNull(raw.signal);
  return {
    id: stringOrNull(raw.id) ?? `${key}:${text.slice(0, 24)}`,
    competency_key: key,
    signal: signal === "positive" || signal === "negative" ? signal : "neutral",
    dimension: stringOrNull(raw.dimension) ?? undefined,
    evidence: text,
    quote: stringOrNull(raw.quote) ?? (isRecord(raw.transcript_ref) ? stringOrNull(raw.transcript_ref.quote) : null),
    strength: numberOrNull(raw.strength) ?? undefined,
    confidence: numberOrNull(raw.confidence) ?? undefined,
    question_id: stringOrNull(raw.question_id),
  };
}

function fromRecord(e: EvidenceRecord): ReportEvidenceView {
  return {
    id: e.id,
    competency_key: e.competency_key,
    signal: e.signal,
    dimension: e.dimension,
    evidence: e.evidence,
    quote: quoteOf(e),
    strength: e.strength,
    confidence: e.confidence,
    question_id: e.question_id,
  };
}

// ------------------------------------------------------------ competencies

function buildCompetencies(content: Obj, narrative: Obj, src: ReportSources, evidenceIndex: Map<string, ReportEvidenceView>): ReportCompetencyView[] {
  const findings = new Map<string, Obj>();
  for (const f of asList<Obj>(narrative.competency_findings)) {
    const key = stringOrNull(f.competency_key);
    if (key) findings.set(key, f);
  }
  const contentList = firstList(content.competencies, content.competency_scores, obj(content.scores).competencies);
  const rows: Array<{ key: string; raw: Obj }> = [];
  if (contentList) {
    for (const raw of contentList) {
      if (!isRecord(raw)) continue;
      const key = stringOrNull(raw.key) ?? stringOrNull(raw.competency_key);
      if (key) rows.push({ key, raw });
    }
  } else {
    for (const s of src.scores?.competencies ?? []) rows.push({ key: s.key, raw: s as unknown as Obj });
    for (const key of src.index.order) if (!rows.some((r) => r.key === key)) rows.push({ key, raw: {} });
  }
  const evidenceByKey = new Map<string, ReportEvidenceView[]>();
  for (const e of evidenceIndex.values()) {
    const list = evidenceByKey.get(e.competency_key) ?? [];
    list.push(e);
    evidenceByKey.set(e.competency_key, list);
  }
  return rows.map(({ key, raw }) => {
    const finding = findings.get(key) ?? {};
    const findingIds = stringList(finding.evidence_ids);
    const own = asList(raw.evidence).map(toEvidenceView).filter((e): e is ReportEvidenceView => !!e);
    const listed = [...stringList(raw.positive_evidence_ids), ...stringList(raw.negative_evidence_ids), ...findingIds]
      .map((id) => evidenceIndex.get(id))
      .filter((e): e is ReportEvidenceView => !!e);
    const evidence = dedupe(own.length ? own : listed.length ? listed : (evidenceByKey.get(key) ?? []));
    return {
      key,
      label: stringOrNull(raw.label) ?? stringOrNull(raw.name) ?? src.index.nameOf(key),
      score: numberOrNull(raw.score) ?? numberOrNull(raw.score_estimate),
      confidence: numberOrNull(raw.confidence),
      evidenceCount: numberOrNull(raw.evidence_count),
      sufficient: typeof raw.sufficient_evidence === "boolean" ? raw.sufficient_evidence : null,
      weight: numberOrNull(raw.weight) ?? src.index.byKey.get(key)?.weight ?? null,
      summary: stringOrNull(finding.summary) ?? stringOrNull(raw.summary),
      strengths: stringList(finding.strengths).length ? stringList(finding.strengths) : stringList(raw.strengths),
      weaknesses: stringList(finding.weaknesses).length ? stringList(finding.weaknesses) : stringList(raw.weaknesses),
      unansweredAreas: [...new Set([...stringList(raw.unanswered_areas), ...stringList(finding.unanswered_areas)])],
      evidence: sortEvidence(evidence),
    };
  });
}

function dedupe(items: ReportEvidenceView[]): ReportEvidenceView[] {
  const seen = new Set<string>();
  return items.filter((e) => (seen.has(e.id) ? false : (seen.add(e.id), true)));
}

/** Strongest first (strength x confidence; unknown values count as 0.5), stable for ties. */
function sortEvidence(items: ReportEvidenceView[]): ReportEvidenceView[] {
  const weight = (e: ReportEvidenceView) => (e.strength ?? 0.5) * (e.confidence ?? 0.5);
  return items
    .map((e, i) => ({ e, i }))
    .sort((a, b) => weight(b.e) - weight(a.e) || a.i - b.i)
    .map(({ e }) => e);
}

// --------------------------------------------------------------- questions

function buildQuestions(content: Obj, src: ReportSources, evidenceIndex: Map<string, ReportEvidenceView>): ReportQuestionView[] {
  const contentList = firstList(content.questions, content.question_evaluations);
  if (contentList) {
    return contentList
      .filter(isRecord)
      .map((raw, position) => {
        // Report documents list answers per question; the evaluated (else longest) one represents it.
        const answers = asList<Obj>(raw.answers).filter(isRecord);
        const answer =
          answers.find((a) => isRecord(a.evaluation)) ??
          answers.reduce<Obj | null>((best, a) => (!best || String(a.transcript_text ?? "").length > String(best.transcript_text ?? "").length ? a : best), null) ??
          obj(raw.answer);
        const evaluation = obj(raw.evaluation ?? answer.evaluation);
        const scores = obj(evaluation.dimensions ?? evaluation.scores ?? raw.dimensions);
        const dimensions = Object.entries(scores)
          .map(([key, value]) => ({ key, value: numberOrNull(value) }))
          .filter((d): d is { key: string; value: number } => d.value !== null);
        const evidenceRefs = [...asList(raw.evidence ?? evaluation.evidence), ...answers.flatMap((a) => asList(a.evidence_ids))];
        return {
          id: stringOrNull(raw.id) ?? stringOrNull(raw.question_id) ?? `q-${position}`,
          sequence: numberOrNull(raw.sequence) ?? position + 1,
          text: stringOrNull(raw.text) ?? stringOrNull(raw.question) ?? "",
          competencyKeys: stringList(raw.competency_keys),
          difficulty: stringOrNull(raw.difficulty),
          parentId: stringOrNull(raw.parent_question_id),
          answer: stringOrNull(answer.transcript_text) ?? stringOrNull(answer.text) ?? stringOrNull(raw.answer_text) ?? stringOrNull(raw.answer),
          quality: stringOrNull(evaluation.overall_quality) ?? stringOrNull(raw.quality),
          summary: stringOrNull(evaluation.summary) ?? stringOrNull(raw.summary),
          confidence: numberOrNull(evaluation.confidence) ?? numberOrNull(evaluation.evaluation_confidence),
          dimensions,
          evidence: sortEvidence(
            dedupe(
              evidenceRefs
                .map((e) => (typeof e === "string" ? (evidenceIndex.get(e) ?? null) : toEvidenceView(e)))
                .filter((e): e is ReportEvidenceView => !!e),
            ),
          ),
          isFallback: raw.is_fallback === true || evaluation.is_fallback === true,
        };
      })
      .filter((q) => q.text);
  }
  return (src.questions ?? []).map((q) => {
    const answer = primaryAnswer(q);
    const evaluation = answer?.evaluation ?? null;
    const evidence = (src.evidence ?? []).filter((e) => e.question_id === q.id).map(fromRecord);
    return {
      id: q.id,
      sequence: q.sequence,
      text: q.text,
      competencyKeys: q.competency_keys,
      difficulty: q.difficulty,
      parentId: q.parent_question_id,
      answer: answer?.transcript_text ?? null,
      quality: evaluation?.overall_quality ?? null,
      summary: evaluation?.evaluation?.summary ?? null,
      confidence: evaluation?.confidence ?? null,
      dimensions: evaluationDimensions(evaluation).map((d) => ({ key: d.label, value: d.value })),
      evidence: sortEvidence(evidence),
      isFallback: evaluation?.is_fallback ?? false,
    };
  });
}

// ------------------------------------------------------------------ coding

function buildCoding(content: Obj, src: ReportSources): ReportCodingView[] {
  const contentList = firstList(content.coding, content.coding_sessions, content.coding_results);
  const fromContent = (raw: Obj, position: number): ReportCodingView => {
    const evaluation = obj(raw.evaluation);
    const question = obj(raw.question);
    const hints = asList<Obj>(raw.hints).map((h) => ({ level: numberOrNull(h.level) ?? 0, content: stringOrNull(h.content) ?? stringOrNull(h.text) ?? "", elapsedSeconds: numberOrNull(h.elapsed_seconds) }));
    const hintsUsed = numberOrNull(raw.hints_used) ?? hints.length;
    const finalResult = obj(raw.final_result);
    return {
      id: stringOrNull(raw.id) ?? `coding-${position}`,
      title: stringOrNull(raw.title) ?? stringOrNull(question.title) ?? "Coding exercise",
      language: stringOrNull(raw.language),
      status: stringOrNull(raw.status),
      testsPassed: numberOrNull(raw.tests_passed) ?? numberOrNull(finalResult.passed) ?? numberOrNull(evaluation.tests_passed),
      testsTotal: numberOrNull(raw.tests_total) ?? numberOrNull(finalResult.total) ?? numberOrNull(evaluation.tests_total),
      hintsUsed,
      hints,
      scoreEstimate: numberOrNull(raw.score_estimate) ?? numberOrNull(evaluation.raw_score_estimate) ?? numberOrNull(evaluation.score_estimate),
      assistanceFactor: numberOrNull(raw.assistance_factor) ?? numberOrNull(evaluation.assistance_factor) ?? assistanceFactor(hintsUsed),
      timeComplexity: stringOrNull(raw.time_complexity) ?? stringOrNull(evaluation.time_complexity),
      spaceComplexity: stringOrNull(raw.space_complexity) ?? stringOrNull(evaluation.space_complexity),
      correctnessSummary: stringOrNull(raw.correctness_summary) ?? stringOrNull(evaluation.correctness_summary),
      hintsImpact: stringOrNull(raw.hints_impact) ?? stringOrNull(evaluation.hints_impact),
      strengths: stringList(raw.strengths).length ? stringList(raw.strengths) : stringList(evaluation.strengths),
      weaknesses: stringList(raw.weaknesses).length ? stringList(raw.weaknesses) : stringList(evaluation.weaknesses),
      finalCode: stringOrNull(raw.final_code),
    };
  };
  if (contentList) return contentList.filter(isRecord).map(fromContent);
  return (src.coding ?? []).map((c) => {
    const finalRun = c.runs.find((r) => r.id === c.final_run_id) ?? [...c.runs].reverse().find((r) => r.kind === "submit") ?? c.runs[c.runs.length - 1];
    return fromContent(
      {
        id: c.id,
        title: c.question?.title,
        language: c.language,
        status: c.status,
        tests_passed: c.evaluation.tests_passed ?? finalRun?.passed_count,
        tests_total: c.evaluation.tests_total ?? finalRun?.total_count,
        hints_used: c.hints_used,
        hints: c.hints,
        evaluation: c.evaluation,
        final_code: c.final_code,
      },
      0,
    );
  });
}

// ------------------------------------------------------------------ claims

function buildClaims(content: Obj, src: ReportSources): ReportClaimView[] {
  const toClaim = (raw: unknown, fallbackStatus?: ClaimStatus): ReportClaimView | null => {
    if (!isRecord(raw)) return typeof raw === "string" ? { id: raw, claim: raw, status: fallbackStatus ?? "unverified", note: null } : null;
    const claim = stringOrNull(raw.claim) ?? stringOrNull(raw.text);
    if (!claim) return null;
    return { id: stringOrNull(raw.id) ?? stringOrNull(raw.claim_id) ?? claim, claim, status: stringOrNull(raw.status) ?? fallbackStatus ?? "unverified", note: stringOrNull(raw.note) };
  };
  const listed = firstList(content.claims);
  if (listed) return listed.map((c) => toClaim(c)).filter((c): c is ReportClaimView => !!c);
  const verified = asList(content.claims_verified).map((c) => toClaim(c, "verified"));
  const unverified = asList(content.claims_unverified ?? content.claims_not_verified).map((c) => toClaim(c, "unverified"));
  const combined = [...verified, ...unverified].filter((c): c is ReportClaimView => !!c);
  if (combined.length) return combined;

  const notes = new Map<string, { status: ClaimStatus; note: string }>();
  for (const q of src.questions ?? []) {
    for (const a of q.answers) {
      for (const v of a.evaluation?.evaluation?.claim_verifications ?? []) notes.set(v.claim_id, { status: v.status, note: v.note });
    }
  }
  return (src.session?.state_digest?.claims ?? []).map((c) => ({
    id: c.id,
    claim: c.claim,
    status: notes.get(c.id)?.status ?? c.status,
    note: notes.get(c.id)?.note ?? null,
  }));
}

function buildContradictions(content: Obj, src: ReportSources): ContradictionFinding[] {
  const listed = firstList(content.contradictions);
  const parse = (raw: unknown): ContradictionFinding | null => {
    if (!isRecord(raw)) return null;
    let statement = stringOrNull(raw.statement);
    let conflicts = stringOrNull(raw.conflicts_with);
    // Report documents carry one description: "statement — conflicts with: other".
    const description = stringOrNull(raw.description);
    if (!statement && description) {
      const [said, other] = description.split(/\s+—\s+conflicts with:\s+/);
      statement = said?.trim() || description;
      conflicts = conflicts ?? (other?.trim() || null);
    }
    if (!statement) return null;
    const severity = stringOrNull(raw.severity);
    return { statement, conflicts_with: conflicts ?? "", severity: severity === "high" || severity === "medium" ? severity : "low" };
  };
  if (listed) return listed.map(parse).filter((c): c is ContradictionFinding => !!c);
  const out: ContradictionFinding[] = [];
  for (const q of src.questions ?? []) for (const a of q.answers) for (const c of a.evaluation?.evaluation?.contradictions ?? []) out.push(c);
  return out;
}

// --------------------------------------------------------------- integrity

function buildIntegrity(content: Obj, src: ReportSources): ReportIntegrityView | null {
  const raw = isRecord(content.integrity) ? content.integrity : isRecord(content.integrity_summary) ? content.integrity_summary : null;
  const countsList = (value: unknown): Array<{ type: string; count: number }> => {
    if (Array.isArray(value)) {
      return value
        .filter(isRecord)
        .map((v) => ({ type: stringOrNull(v.type) ?? "", count: numberOrNull(v.count) ?? 0 }))
        .filter((v) => v.type && v.count > 0);
    }
    if (isRecord(value)) {
      return Object.entries(value)
        .map(([type, entry]) => ({ type, count: isRecord(entry) ? (numberOrNull(entry.events) ?? numberOrNull(entry.count) ?? 0) : (numberOrNull(entry) ?? 0) }))
        .filter((v) => v.count > 0);
    }
    return [];
  };
  if (raw) {
    const byType = countsList(raw.by_type ?? raw.counts_by_type ?? raw.counts).sort((a, b) => b.count - a.count);
    const bySeverity: Partial<Record<IntegritySeverity, number>> = {};
    for (const { type, count } of countsList(raw.by_severity ?? raw.counts_by_severity)) bySeverity[type as IntegritySeverity] = count;
    const reviewed: Partial<Record<string, number>> = {};
    for (const { type, count } of countsList(raw.reviewed ?? raw.by_review_status ?? raw.review_status_counts)) reviewed[type] = count;
    return {
      total: numberOrNull(raw.total_events) ?? numberOrNull(raw.total) ?? byType.reduce((sum, t) => sum + t.count, 0),
      unreviewed: numberOrNull(raw.unreviewed_flags) ?? numberOrNull(raw.unreviewed) ?? reviewed.unreviewed ?? 0,
      byType,
      bySeverity,
      reviewed,
    };
  }
  if (!src.integrity) return null;
  const events = src.integrity.events;
  const reviewed: Partial<Record<string, number>> = {};
  for (const e of events) reviewed[e.review_status] = (reviewed[e.review_status] ?? 0) + 1;
  return {
    total: events.length,
    unreviewed: reviewed.unreviewed ?? 0,
    byType: Object.entries(src.integrity.countsByType)
      .map(([type, count]) => ({ type, count: count ?? 0 }))
      .filter((t) => t.count > 0)
      .sort((a, b) => b.count - a.count),
    bySeverity: src.integrity.countsBySeverity,
    reviewed,
  };
}

// ---------------------------------------------------------------- excerpts

/**
 * Excerpts worth reading in full: the strongest quoted evidence, one per
 * question (answers themselves already appear under each question). Falls
 * back to the document's transcript excerpts, then to answer openings.
 */
function buildExcerpts(content: Obj, evidenceIndex: Map<string, ReportEvidenceView>, questions: ReportQuestionView[], src: ReportSources): RecruiterReportView["excerpts"] {
  const out: RecruiterReportView["excerpts"] = [];
  const used = new Set<string>();
  const startOf = new Map((src.evidence ?? []).map((e) => [e.id, numberOrNull(e.transcript_ref?.start_ms)]));
  const quoted = sortEvidence([...evidenceIndex.values()].filter((e) => e.quote && e.signal !== "neutral"));
  for (const e of quoted) {
    const key = e.question_id ?? e.id;
    if (used.has(key)) continue;
    used.add(key);
    out.push({ id: e.id, speaker: "candidate", text: e.quote!, startMs: startOf.get(e.id) ?? null, questionId: e.question_id ?? null });
    if (out.length >= 8) break;
  }
  if (out.length) return out;
  const listed = firstList(content.transcript_excerpts, content.excerpts);
  if (listed) {
    return listed
      .filter(isRecord)
      .map((raw, i) => ({
        id: stringOrNull(raw.id) ?? stringOrNull(raw.answer_id) ?? `excerpt-${i}`,
        speaker: stringOrNull(raw.speaker) ?? "candidate",
        text: stringOrNull(raw.text) ?? stringOrNull(raw.quote) ?? stringOrNull(raw.answer_excerpt) ?? "",
        startMs: numberOrNull(raw.start_ms),
        questionId: stringOrNull(raw.question_id),
      }))
      .filter((e) => e.text)
      .slice(0, 8);
  }
  for (const q of questions.slice(0, 4)) if (q.answer) out.push({ id: `answer-${q.id}`, speaker: "candidate", text: q.answer.length > 320 ? `${q.answer.slice(0, 319)}…` : q.answer, startMs: null, questionId: q.id });
  return out;
}

// -------------------------------------------------------------------- main

export function buildReportView(src: ReportSources): RecruiterReportView {
  const content = obj(src.report?.content);
  const narrative = obj(content.narrative) as Partial<RecruiterReportNarrative> & Obj;
  const sessionInfo = obj(content.session);
  const scoresDoc = obj(content.scores);
  const overall = obj(content.overall ?? scoresDoc.overall);
  const recommendationObj = obj(content.recommendation);

  const evidenceIndex = new Map<string, ReportEvidenceView>();
  for (const e of src.evidence ?? []) evidenceIndex.set(e.id, fromRecord(e));
  for (const raw of asList(content.evidence)) {
    const e = toEvidenceView(raw);
    if (e) evidenceIndex.set(e.id, e);
  }

  const rubricList = firstList(content.rubric, content.rubric_categories, scoresDoc.rubric);
  const rubric = rubricList
    ? rubricList.filter(isRecord).map((r) => ({
        key: stringOrNull(r.key) ?? "",
        label: stringOrNull(r.label) ?? stringOrNull(r.key) ?? "",
        weight: numberOrNull(r.weight),
        score: numberOrNull(r.score),
        confidence: numberOrNull(r.confidence),
      }))
    : (src.scores?.rubric ?? []).map((r) => ({ key: r.key, label: r.label ?? r.key, weight: r.weight, score: r.score, confidence: r.confidence }));

  const questions = buildQuestions(content, src, evidenceIndex);
  const review = src.reviews?.[0] ?? null;

  return {
    candidate: first(personRef(content.candidate), src.session?.candidate ?? null),
    job: first(jobRef(content.job), src.session?.job ?? null),
    round: first(roundRef(content.round), src.session?.round ?? null),
    startedAt: first(stringOrNull(sessionInfo.started_at), stringOrNull(content.started_at), src.session?.started_at ?? null),
    endedAt: first(stringOrNull(sessionInfo.ended_at), stringOrNull(content.ended_at), src.session?.ended_at ?? null),
    durationSeconds: first(
      numberOrNull(content.duration_seconds),
      numberOrNull(sessionInfo.duration_seconds),
      numberOrNull(sessionInfo.elapsed_seconds),
      src.session?.elapsed_seconds ?? null,
    ),
    endReason: first(stringOrNull(content.end_reason) as SessionEndReason | null, stringOrNull(sessionInfo.end_reason) as SessionEndReason | null, src.session?.end_reason ?? null),
    overallScore: first(src.report?.overall_score ?? null, numberOrNull(overall.score), numberOrNull(content.overall_score), src.scores?.overall?.score ?? null, src.session?.overall_score ?? null),
    confidence: first(src.report?.confidence ?? null, numberOrNull(overall.confidence), numberOrNull(content.confidence), src.scores?.overall?.confidence ?? null, src.session?.confidence ?? null),
    recommendation: first(
      src.report?.recommendation ?? null,
      asRecommendation(content.recommendation),
      asRecommendation(narrative.suggested_recommendation),
      src.scores?.recommendation ?? null,
      src.session?.recommendation ?? null,
    ),
    recommendationRationale: first(stringOrNull(recommendationObj.rationale), stringOrNull(content.recommendation_rationale), stringOrNull(narrative.recommendation_rationale)),
    executiveSummary: first(stringOrNull(narrative.executive_summary), stringOrNull(content.executive_summary), stringOrNull(content.summary)),
    narrative: Object.keys(narrative).length ? narrative : null,
    rubric,
    competencies: buildCompetencies(content, narrative, src, evidenceIndex),
    questions,
    excerpts: buildExcerpts(content, evidenceIndex, questions, src),
    coding: buildCoding(content, src),
    codingSummary: first(stringOrNull(narrative.coding_summary), stringOrNull(content.coding_summary)),
    claims: buildClaims(content, src),
    claimsSummary: first(stringOrNull(narrative.claims_summary), stringOrNull(content.claims_summary)),
    contradictions: buildContradictions(content, src),
    contradictionsSummary: first(stringOrNull(narrative.contradictions_summary), stringOrNull(content.contradictions_summary)),
    integrity: buildIntegrity(content, src),
    followUps: stringList(narrative.areas_for_human_followup).length
      ? stringList(narrative.areas_for_human_followup)
      : stringList(content.areas_for_human_followup ?? content.follow_ups),
    risks: stringList(narrative.risks).length ? stringList(narrative.risks) : stringList(content.risks),
    humanDecision: review
      ? { decision: review.decision, reviewer: review.reviewer?.full_name ?? null, at: review.created_at, overridesAi: review.overrides_ai }
      : null,
  };
}

/** File name for the JSON export: "interview-report-priya-shah-2026-10-06.json". */
export function reportFileName(view: Pick<RecruiterReportView, "candidate" | "startedAt">, extension = "json"): string {
  const name = (view.candidate?.full_name ?? "candidate")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  const date = (view.startedAt ?? new Date().toISOString()).slice(0, 10);
  return `interview-report-${name || "candidate"}-${date}.${extension}`;
}

/** Triggers a browser download of `data` as pretty-printed JSON. */
export function downloadJson(data: unknown, fileName: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1_000);
}
