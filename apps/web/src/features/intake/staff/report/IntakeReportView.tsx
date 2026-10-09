/**
 * The printable intake report (design 9.1, 15.11): sections A-K of `reports.content`
 * (kind "intake"), in reading order, styled for screen and paper by the recruiter
 * report's print stylesheet. The band is shown as an AI recommendation next to the
 * human review status; eligibility answers are never part of the report.
 */

import { CircleCheck, CircleDashed, CircleMinus, UserRound } from "lucide-react";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/brand/Logo";
import { formatDate, formatDateTime, formatMinutes, formatPercent, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import {
  AI_ML_OPENNESS_LABELS,
  BAND_GUIDANCE,
  competencyLabel,
  COVERAGE_LABELS,
  FLOW_LABELS,
  GAP_HANDLING_LABELS,
  lookup,
  OUTCOME_LABELS,
  PARAPHRASE_LABELS,
  RATING_SUPPORTED_LABELS,
  REVIEW_ITEM_LABELS,
  SEVERITY_LABELS,
  TARGET_ROLE_LABELS,
  TRANSCRIPT_SOURCE_LABELS,
  VERDICT_LABELS,
  VERIFICATION_LABELS,
} from "../labels";
import { formatHundred, formatScore5, formatTotal, formatWeightPercent } from "../lib/decimals";
import type { CoverageStatus, IntakeReportContent, TargetRole } from "../types";
import { BandBadge, OutcomeBadge } from "../components/IntakeBadges";
import { ScoreNotches } from "../components/review/ScorecardTab";

function Section({ id, letter, title, children, description, pageBreak }: { id: string; letter: string; title: string; children: ReactNode; description?: ReactNode; pageBreak?: boolean }) {
  return (
    <section aria-labelledby={id} className="grid grid-cols-1 gap-4 border-t border-line pt-7" data-print-flow data-print-page-break={pageBreak || undefined} data-section={letter}>
      <div className="grid grid-cols-1 gap-1">
        <h2 id={id} className="flex items-baseline gap-2.5 text-[17px] leading-6 font-semibold tracking-[-0.01em] text-fg">
          <span className="text-fg-subtle tabular-nums" aria-hidden="true">
            {letter}
          </span>
          {title}
        </h2>
        {description ? <p className="text-[13px] text-fg-muted">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Facts({ items }: { items: Array<[string, ReactNode]> }) {
  const shown = items.filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-[13.5px] sm:grid-cols-2">
      {shown.map(([label, value]) => (
        <div key={label} className="grid grid-cols-1 gap-0.5" data-print-avoid-break>
          <dt className="text-xs text-fg-muted">{label}</dt>
          <dd className="text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Bullets({ items, className }: { items: readonly string[]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={cn("grid grid-cols-1 list-disc gap-1 pl-5 text-[13.5px] leading-[1.45rem] text-fg", className)}>
      {items.map((item, i) => (
        <li key={`${item}-${i}`}>{item}</li>
      ))}
    </ul>
  );
}

function Quote({ children }: { children: ReactNode }) {
  return <span className="text-[13px] text-fg-muted italic">“{children}”</span>;
}

const COVERAGE_ICON: Record<CoverageStatus, typeof CircleCheck> = { covered: CircleCheck, partial: CircleDashed, missing: CircleMinus };
const COVERAGE_TONE: Record<CoverageStatus, string> = { covered: "text-success", partial: "text-warning", missing: "text-fg-subtle" };

function CoverageLine({ status, children }: { status: CoverageStatus; children: ReactNode }) {
  const Icon = COVERAGE_ICON[status];
  return (
    <li className="flex gap-2.5" data-print-avoid-break>
      <Icon className={cn("mt-0.5 size-4 shrink-0", COVERAGE_TONE[status])} role="img" aria-label={COVERAGE_LABELS[status]} />
      <span className="grid grid-cols-1 gap-0.5 text-[13.5px] leading-[1.4rem] text-fg">{children}</span>
    </li>
  );
}

function roleText(role: string | null): string {
  if (!role) return "Not chosen";
  return TARGET_ROLE_LABELS[role as TargetRole] ?? role;
}

export interface IntakeReportViewProps {
  content: IntakeReportContent;
  version?: number | null;
  generatedAt?: string | null;
}

export function IntakeReportView({ content, version, generatedAt }: IntakeReportViewProps) {
  const a = content.A_candidate_summary;
  const b = content.B_overall;
  const fa = content.F_reading.drill_a;
  const fb = content.F_reading.drill_b;
  const g = content.G_structured;
  const h = content.H_trainability;
  const e = content.E_technical;
  const insufficient = b.band === "insufficient_evidence" || !b.weighted_total;
  const omitted = (fa?.events ?? []).filter((ev) => ev.kind === "omission").map((ev) => ev.ref_word).filter(Boolean) as string[];
  const changed = (fa?.events ?? []).filter((ev) => ev.kind === "substitution" && ev.ref_word).map((ev) => `${ev.ref_word} → ${ev.hyp_word ?? "?"}`);
  const extra = (fa?.events ?? []).filter((ev) => ev.kind === "insertion" || ev.kind === "repetition").map((ev) => ev.hyp_word).filter(Boolean) as string[];

  return (
    <article
      data-testid="intake-report"
      data-report-root
      data-print-flow
      className="mx-auto grid w-full max-w-[900px] grid-cols-1 gap-8 rounded-xl border border-line bg-surface px-5 py-7 shadow-[0_1px_3px_rgb(16_24_40/0.06)] sm:px-10 sm:py-10"
    >
      {/* ------------------------------------------------------------ header */}
      <header className="grid grid-cols-1 gap-5">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="grid grid-cols-1 min-w-0 gap-1.5">
            <p className="flex items-center gap-2 text-[13px] text-fg-muted">
              <BrandMark size={22} />
              Intake interview report
            </p>
            <h1 className="text-[1.875rem] leading-9 font-semibold tracking-[-0.022em] text-fg">{a.candidate_name}</h1>
            <p className="text-[15px] text-fg">{roleText(a.target_role)}</p>
          </div>
          <div className="grid grid-cols-1 justify-items-start gap-1.5 sm:justify-items-end">
            <span className="flex items-baseline gap-1.5">
              <span className="text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-fg">{insufficient ? formatTotal(b.partial_total) : formatTotal(b.weighted_total)}</span>
              <span className="text-sm text-fg-subtle">/ 5{insufficient && b.partial_total ? ", partial" : ""}</span>
            </span>
            <BandBadge band={b.band} label={b.band_label} size="md" />
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg bg-surface-subtle px-5 py-4 text-[13px] ring-1 ring-line-subtle ring-inset sm:grid-cols-4">
          <div className="grid grid-cols-1 gap-0.5">
            <dt className="text-xs text-fg-muted">Interview date</dt>
            <dd className="font-medium text-fg">{a.interview_date ? formatDate(a.interview_date) : "—"}</dd>
          </div>
          <div className="grid grid-cols-1 gap-0.5">
            <dt className="text-xs text-fg-muted">Duration</dt>
            <dd className="font-medium text-fg">{a.duration_s ? formatMinutes(a.duration_s) : "—"}</dd>
          </div>
          <div className="grid grid-cols-1 gap-0.5">
            <dt className="text-xs text-fg-muted">Stages completed</dt>
            <dd className="font-medium text-fg">{a.completed_stages.length ? `${a.completed_stages.filter((s) => s.status === "completed").length} of ${a.completed_stages.length}` : "—"}</dd>
          </div>
          <div className="grid grid-cols-1 gap-0.5">
            <dt className="text-xs text-fg-muted">Human review</dt>
            <dd className="font-medium text-fg">{b.human_review_status === "decided" ? "Decided" : "Pending"}</dd>
          </div>
        </dl>
        <p className="flex items-center gap-1.5 text-[13px] font-medium text-fg">
          <UserRound className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
          {b.recommendation_note}
        </p>
      </header>

      {/* --------------------------------------------------------------- A */}
      <Section id="intake-report-a" letter="A" title="Candidate summary">
        <Facts
          items={[
            ["Target role", roleText(a.target_role)],
            ["Hands-on experience (as stated)", a.years_hands_on !== null ? pluralize(a.years_hands_on, "year") : null],
            ["Experience on the résumé", a.resume_experience_years !== null ? pluralize(a.resume_experience_years, "year") : null],
            ["Interview date", a.interview_date ? formatDateTime(a.interview_date) : null],
          ]}
        />
        {a.resume_summary ? <p className="text-[14px] leading-6 text-fg">{a.resume_summary}</p> : null}
        {a.technical_background ? (
          <div className="grid grid-cols-1 gap-1">
            <span className="text-xs text-fg-muted">Technical background, in the candidate's words</span>
            <p className="text-[13.5px] leading-6 whitespace-pre-wrap text-fg">{a.technical_background}</p>
          </div>
        ) : null}
        {a.completed_stages.length ? (
          <ol className="flex flex-wrap gap-x-4 gap-y-1.5 text-[13px]">
            {a.completed_stages.map((s, i) => (
              <li key={s.key} className={cn("flex items-center gap-1.5", s.status === "completed" ? "text-fg" : "text-fg-muted")}>
                <span className="text-fg-subtle tabular-nums">{i + 1}.</span>
                {s.title}
                {s.status !== "completed" ? <span className="text-xs">({s.status === "incomplete" ? "incomplete" : s.status})</span> : null}
              </li>
            ))}
          </ol>
        ) : null}
      </Section>

      {/* --------------------------------------------------------------- B */}
      <Section id="intake-report-b" letter="B" title="Overall result">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2" data-print-avoid-break>
          <div className="grid grid-cols-1 content-start gap-2 rounded-lg border border-line p-4">
            <p className="text-xs font-medium text-fg-muted">AI recommendation</p>
            <BandBadge band={b.band} label={b.band_label} size="md" />
            <p className="text-[13px] text-fg">
              {insufficient ? (
                <>
                  {b.partial_label ?? "Partial total"}: {formatTotal(b.partial_total)} / 5. No band without full evidence.
                </>
              ) : (
                <>
                  Weighted total {formatTotal(b.weighted_total)} / 5{b.normalized_100 ? `, ${formatHundred(b.normalized_100)} ${b.normalized_label ? b.normalized_label.charAt(0).toLowerCase() + b.normalized_label.slice(1) : "out of 100 (conversion)"}` : ""}.
                </>
              )}
            </p>
            {b.band_guidance || b.band ? <p className="text-xs text-fg-muted">{b.band_guidance ?? (b.band ? BAND_GUIDANCE[b.band] : "")}</p> : null}
          </div>
          <div className="grid grid-cols-1 content-start gap-2 rounded-lg border border-line p-4">
            <p className="text-xs font-medium text-fg-muted">Human decision</p>
            {b.latest_decision ? (
              <>
                <OutcomeBadge outcome={b.latest_decision.outcome} label={b.latest_decision.outcome_label} size="md" />
                <p className="text-xs text-fg-muted">
                  {b.latest_decision.reviewer?.full_name ?? "A reviewer"}, {formatDateTime(b.latest_decision.created_at)}
                  {b.latest_decision.overrides_ai ? ". Differs from the AI recommendation." : ""}
                </p>
                <p className="text-[13px] leading-5 whitespace-pre-wrap text-fg">{b.latest_decision.rationale}</p>
              </>
            ) : (
              <p className="flex items-center gap-1.5 text-sm text-fg">
                <CircleDashed className="size-4 text-fg-subtle" aria-hidden="true" />
                No decision recorded yet
              </p>
            )}
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {b.strengths.length ? (
            <div className="grid grid-cols-1 content-start gap-1.5">
              <h3 className="text-sm font-semibold text-fg">Strengths</h3>
              <Bullets items={b.strengths} />
            </div>
          ) : null}
          {b.improvements.length ? (
            <div className="grid grid-cols-1 content-start gap-1.5">
              <h3 className="text-sm font-semibold text-fg">Areas to develop</h3>
              <Bullets items={b.improvements} />
            </div>
          ) : null}
        </div>
      </Section>

      {/* --------------------------------------------------------------- C */}
      <Section id="intake-report-c" letter="C" title="Competencies" description="Scores 1-5 from the kit's rubric. Each one rests on the cited answers.">
        <ol className="grid grid-cols-1 gap-5">
          {content.C_competencies.map((c) => {
            const scored = c.status === "scored" && c.score !== null;
            return (
              <li key={c.key} className="grid grid-cols-1 gap-2 border-b border-line-subtle pb-4 last:border-b-0 last:pb-0" data-print-avoid-break>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-[14.5px] font-semibold text-fg">
                    {c.label || competencyLabel(c.key)}
                    <span className="ml-2 text-xs font-normal text-fg-muted">weight {formatWeightPercent(c.weight)}</span>
                  </h3>
                  <span className="flex items-center gap-3">
                    <ScoreNotches score={scored ? c.score : null} label={c.label || competencyLabel(c.key)} />
                    <span className="text-sm text-fg tabular-nums">{scored ? `${formatScore5(c.score)} / 5` : "Insufficient evidence"}</span>
                    {scored && c.weighted ? <span className="text-xs text-fg-muted tabular-nums">+{formatTotal(c.weighted)}</span> : null}
                  </span>
                </div>
                {c.explanation ? <p className="text-[13.5px] leading-6 text-fg">{c.explanation}</p> : null}
                <Bullets items={c.observations} className="text-fg-muted" />
                {c.evidence.length ? (
                  <ul className="grid grid-cols-1 gap-1">
                    {c.evidence.slice(0, 4).map((ev, i) =>
                      ev.quote ? (
                        <li key={i}>
                          <Quote>{ev.quote}</Quote>
                        </li>
                      ) : null,
                    )}
                  </ul>
                ) : null}
                {c.improvements.length ? <p className="text-[13px] text-fg-muted">To develop: {c.improvements.join("; ")}</p> : null}
              </li>
            );
          })}
        </ol>
      </Section>

      {/* --------------------------------------------------------------- D */}
      <Section id="intake-report-d" letter="D" title="Résumé verification" description="Two or three claims from the résumé, each probed for ownership, mechanics and stress.">
        {content.D_resume_verification.length ? (
          <ol className="grid grid-cols-1 gap-4">
            {content.D_resume_verification.map((claim) => (
              <li key={claim.claim_ref} className="grid grid-cols-1 gap-1.5" data-print-avoid-break>
                <p className="text-[14px] font-medium text-fg">
                  <span className="mr-2 text-fg-subtle">{claim.claim_ref}</span>
                  {claim.claim}
                </p>
                <p className="text-xs text-fg-muted">
                  {claim.verdict ? lookup(VERDICT_LABELS, claim.verdict) : "Not judged"}
                  {claim.source === "candidate_correction" ? ". Added by the candidate in the résumé review." : ""}
                </p>
                {claim.responses.slice(0, 3).map((r, i) => (
                  <Quote key={i}>{r.excerpt}</Quote>
                ))}
                {claim.unresolved.length ? <p className="text-[13px] text-fg-muted">Still open: {claim.unresolved.join("; ")}</p> : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-fg-muted">No résumé claims were discussed.</p>
        )}
      </Section>

      {/* --------------------------------------------------------------- E */}
      <Section id="intake-report-e" letter="E" title="Technical ability">
        <Facts
          items={[
            ["Strongest language", e.language],
            ["Self-rating", e.self_rating !== null ? `${e.self_rating} / 10` : null],
            ["Scripting", e.scripting],
            ["Data and ML libraries", e.data_ml_libraries],
            ["Self-rating check", e.rating_supported ? lookup(RATING_SUPPORTED_LABELS, e.rating_supported) : null],
          ]}
        />
        {e.verification.map((v, i) => (
          <div key={i} className="grid grid-cols-1 gap-1" data-print-avoid-break>
            <p className="text-[13.5px] font-medium text-fg">{v.question}</p>
            {v.answer_excerpt ? <Quote>{v.answer_excerpt}</Quote> : null}
            <p className="text-xs text-fg-muted">{v.quality ? lookup(VERIFICATION_LABELS, v.quality) : "Not graded"}</p>
          </div>
        ))}
        {e.technical_understanding ? <p className="text-[13.5px] leading-6 text-fg">{e.technical_understanding}</p> : null}
        {e.accuracy_notes ? <p className="text-[13px] text-fg-muted">{e.accuracy_notes}</p> : null}
      </Section>

      {/* --------------------------------------------------------------- F */}
      <Section id="intake-report-f" letter="F" title="Technical reading and relay" description={`Technical Reading & Relay = ${content.F_reading.technical_reading.formula}${content.F_reading.technical_reading.score ? ` = ${formatScore5(content.F_reading.technical_reading.score)}` : ""}.`} pageBreak>
        <div className="grid grid-cols-1 gap-3" data-print-avoid-break>
          <h3 className="text-sm font-semibold text-fg">
            Drill A, read aloud{fa?.passage_title ? `: ${fa.passage_title}` : ""}
            <span className="ml-2 font-normal text-fg-muted">{fa?.score !== null && fa?.score !== undefined ? `${fa.score} / 5` : "Not scored"}</span>
          </h3>
          {fa?.metrics ? (
            <Facts
              items={[
                ["Completeness", fa.metrics.completeness !== null ? formatPercent(fa.metrics.completeness) : null],
                ["Word error rate", fa.metrics.wer !== null ? `${formatPercent(fa.metrics.wer)}${fa.metrics.strict_wer !== null ? ` (strict ${formatPercent(fa.metrics.strict_wer)})` : ""}` : null],
                ["Pace", fa.metrics.words_per_minute !== null ? `${Math.round(fa.metrics.words_per_minute)} words per minute` : null],
                ["Long pauses", fa.metrics.long_pause_count !== null ? String(fa.metrics.long_pause_count) : null],
                ["Sentences", fa.metrics.sentences_read !== null ? `${fa.metrics.sentences_read} read, ${fa.metrics.sentences_partial ?? 0} partial, ${fa.metrics.sentences_skipped ?? 0} skipped` : null],
                ["Pronunciation", "Not assessed"],
              ]}
            />
          ) : null}
          {omitted.length || changed.length || extra.length ? (
            <ul className="grid grid-cols-1 gap-1 text-[13px] text-fg-muted">
              {omitted.length ? <li>Not read: {omitted.join(", ")}</li> : null}
              {changed.length ? <li>Read differently: {changed.join(", ")}</li> : null}
              {extra.length ? <li>Extra or repeated: {extra.join(", ")}</li> : null}
            </ul>
          ) : null}
          <Bullets items={fa?.basis ?? []} className="text-fg-muted" />
          {fa?.transcript ? (
            <p className="text-[13px] leading-6 text-fg-muted">
              <span className="text-xs">{fa.transcript_source ? lookup(TRANSCRIPT_SOURCE_LABELS, fa.transcript_source) : "Transcript"}: </span>
              {fa.transcript}
            </p>
          ) : null}
        </div>
        <div className="grid grid-cols-1 gap-3" data-print-avoid-break>
          <h3 className="text-sm font-semibold text-fg">
            Drill B, read and relay{fb?.passage_title ? `: ${fb.passage_title}` : ""}
            <span className="ml-2 font-normal text-fg-muted">{fb?.score !== null && fb?.score !== undefined ? `${fb.score} / 5` : "Not scored"}</span>
          </h3>
          <p className="text-xs text-fg-muted">
            Passage shown for {fb?.shown_seconds ?? 30} seconds{fb?.coverage !== null && fb?.coverage !== undefined ? `; ${formatPercent(fb.coverage)} of the key concepts relayed` : ""}
            {fb?.paraphrase ? `; ${lookup(PARAPHRASE_LABELS, fb.paraphrase).toLowerCase()}` : ""}.
          </p>
          {fb?.concepts.length ? (
            <ul className="grid grid-cols-1 gap-1.5">
              {fb.concepts.map((concept) => (
                <CoverageLine key={concept.key} status={concept.status}>
                  {concept.text}
                  {concept.quote ? <Quote>{concept.quote}</Quote> : null}
                </CoverageLine>
              ))}
            </ul>
          ) : null}
          {fb?.misstatements.length ? <p className="text-[13px] text-fg-muted">Factual errors: {fb.misstatements.map((m) => (m.quote ? `“${m.quote}”` : "")).filter(Boolean).join(", ")}</p> : null}
          {fb?.followups.map((f, i) => (
            <div key={i} className="grid grid-cols-1 gap-0.5">
              <p className="text-[13px] font-medium text-fg">{f.question}</p>
              {f.answer_excerpt ? <Quote>{f.answer_excerpt}</Quote> : null}
              {f.note ? <p className="text-xs text-fg-muted">{f.note}</p> : null}
            </div>
          ))}
          <Bullets items={fb?.basis ?? []} className="text-fg-muted" />
        </div>
      </Section>

      {/* --------------------------------------------------------------- G */}
      <Section id="intake-report-g" letter="G" title="Structured answer" description={g.card_title ? `Topic card${g.card_number ? ` ${g.card_number}` : ""}: ${g.card_title}` : undefined}>
        {g.bullets.length ? (
          <ul className="grid grid-cols-1 gap-1.5">
            {g.bullets.map((bullet) => (
              <CoverageLine key={bullet.index} status={bullet.status}>
                {bullet.text}
                {bullet.excerpt ? <Quote>{bullet.excerpt}</Quote> : null}
              </CoverageLine>
            ))}
          </ul>
        ) : null}
        <Facts
          items={[
            ["Flow", g.flow ? lookup(FLOW_LABELS, g.flow) : null],
            ["Examples", g.examples_used === null ? null : g.examples_used ? "Used a concrete example" : "No concrete example"],
            ["Gaps", g.gap_handling ? lookup(GAP_HANDLING_LABELS, g.gap_handling) : null],
            ["Score", g.score ? `${formatScore5(g.score)} / 5` : null],
          ]}
        />
        {g.followup?.analysis ? <p className="text-[13.5px] leading-6 text-fg">{g.followup.analysis}</p> : null}
      </Section>

      {/* --------------------------------------------------------------- H */}
      <Section id="intake-report-h" letter="H" title="Trainability and learning">
        <Facts
          items={[
            ["Learning strategy", h.learning_strategy],
            ["Teaching exercise", h.teaching_exercise],
            ["Problem solving", h.problem_solving],
            ["Training readiness", h.training_readiness],
            ["AI/ML openness", h.ai_ml_openness ? lookup(AI_ML_OPENNESS_LABELS, h.ai_ml_openness) : null],
          ]}
        />
        {h.development_areas.length ? (
          <div className="grid grid-cols-1 gap-1.5">
            <h3 className="text-sm font-semibold text-fg">Development areas</h3>
            <Bullets items={h.development_areas} />
          </div>
        ) : null}
      </Section>

      {/* --------------------------------------------------------------- I */}
      <Section id="intake-report-i" letter="I" title="Integrity observations" description={content.I_integrity.note}>
        {content.I_integrity.observations.length ? (
          <ul className="grid grid-cols-1 gap-1.5 text-[13.5px]">
            {content.I_integrity.observations.map((o) => (
              <li key={o.type} className="flex flex-wrap items-baseline justify-between gap-2" data-print-avoid-break>
                <span className="text-fg">{o.title}</span>
                <span className="text-xs text-fg-muted">
                  {pluralize(o.count, "time")}
                  {o.review_status ? `, ${o.review_status.replace(/_/g, " ")}` : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-fg-muted">No observations recorded.</p>
        )}
        {content.I_integrity.unreviewed ? <p className="text-xs text-fg-muted">{pluralize(content.I_integrity.unreviewed, "observation")} not reviewed yet.</p> : null}
      </Section>

      {/* --------------------------------------------------------------- J */}
      <Section id="intake-report-j" letter="J" title="Training recommendation">
        {content.J_training_plan ? (
          <div className="grid grid-cols-1 gap-4">
            {content.J_training_plan.summary ? <p className="text-[14px] leading-6 text-fg">{content.J_training_plan.summary}</p> : null}
            <ol className="grid grid-cols-1 gap-4">
              {[...content.J_training_plan.modules].sort((x, y) => x.priority - y.priority).map((module) => (
                <li key={module.key} className="grid grid-cols-1 gap-1.5" data-print-avoid-break>
                  <h3 className="text-[14px] font-semibold text-fg">
                    {module.title}
                    <span className="ml-2 text-xs font-normal text-fg-muted">priority {module.priority}</span>
                  </h3>
                  {module.why ? <p className="text-[13px] leading-5 text-fg-muted">{module.why}</p> : null}
                  <Bullets items={module.objectives} />
                  {module.milestones.length ? (
                    <ul className="grid grid-cols-1 gap-1 text-[13px]">
                      {module.milestones.map((m, i) => (
                        <li key={i} className="grid grid-cols-[4rem_minmax(0,1fr)] gap-2">
                          <span className="text-fg-subtle">Week {m.week}</span>
                          <span className="text-fg">
                            {m.milestone}
                            {m.measure ? <span className="text-fg-muted">. Measure: {m.measure}</span> : null}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No training plan in this report.</p>
        )}
      </Section>

      {/* --------------------------------------------------------------- K */}
      <Section id="intake-report-k" letter="K" title="Human review">
        {content.K_human_review.review_items.length ? (
          <ul className="grid grid-cols-1 gap-2">
            {content.K_human_review.review_items.map((item) => (
              <li key={item.id} className="grid grid-cols-1 gap-0.5" data-print-avoid-break>
                <span className="text-[13.5px] font-medium text-fg">
                  {item.title || lookup(REVIEW_ITEM_LABELS, item.kind)}
                  <span className="ml-2 text-xs font-normal text-fg-muted">{SEVERITY_LABELS[item.severity]}</span>
                </span>
                {item.detail ? <span className="text-[13px] text-fg-muted">{item.detail}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-fg-muted">No review items.</p>
        )}
        {content.K_human_review.decisions.length ? (
          <ol className="grid grid-cols-1 gap-2">
            {content.K_human_review.decisions.map((d) => (
              <li key={d.id} className="text-[13px] text-fg" data-print-avoid-break>
                <span className="font-medium">{d.outcome_label || OUTCOME_LABELS[d.outcome]}</span>, {d.reviewer?.full_name ?? "a reviewer"}, {formatDateTime(d.created_at)}
                {d.overrides_ai ? " (differs from the AI recommendation)" : ""}: {d.rationale}
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-sm text-fg-muted">Awaiting a human decision.</p>
        )}
      </Section>

      <footer className="grid grid-cols-1 gap-1 border-t border-line pt-5 text-xs text-fg-muted">
        <p>{content.eligibility_note}</p>
        <p>
          {version ? `Report version ${version}` : "Report"}
          {generatedAt || content.generated_at ? `, generated ${formatDateTime(generatedAt ?? content.generated_at)}` : ""}
          {content.kit_version ? `. Kit ${content.kit_version}` : ""}
          {content.scoring_version ? `, ${content.scoring_version}` : ""}.
        </p>
      </footer>
    </article>
  );
}
