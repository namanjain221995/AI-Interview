import { BookOpenText, CircleCheck, CircleDashed, CircleMinus, Ear, Sigma } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatClock, formatNumber, formatPercent, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

import { AnswerAudio } from "@/features/recruiter/review/components/media";
import { useSessionReport } from "@/features/recruiter/review/api/hooks";

import { normalizeIntakeReport } from "../../api";
import { ACTIVITY_END_LABELS, COVERAGE_LABELS, COVERAGE_TONES, lookup, PARAPHRASE_LABELS, PRONUNCIATION_NOTE, TECHNICAL_READING_FORMULA, TRANSCRIPT_SOURCE_LABELS, TRANSCRIPT_STATUS_LABELS } from "../../labels";
import { formatScore5 } from "../../lib/decimals";
import { activityOf, activityTranscript } from "../../lib/review";
import type { CoverageStatus, IntakeActivity, IntakeDetail, ReadingMetricsView } from "../../types";
import { EvidenceChip } from "./EvidenceChip";
import { AlignmentLegend, MATERIAL_TEXT, PassageAlignment } from "./PassageAlignment";

function Metric({ name, label, value, hint }: { name: string; label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="grid grid-cols-1 min-w-0 content-start gap-0.5 border-r border-b border-line-subtle px-4 py-3" data-testid={`intake-reading-metric-${name}`}>
      <dt className="truncate text-xs text-fg-muted">{label}</dt>
      <dd className="text-lg leading-7 font-semibold text-fg">{value}</dd>
      {hint ? <dd className="text-xs text-fg-subtle">{hint}</dd> : null}
    </div>
  );
}

function MetricsGrid({ metrics }: { metrics: ReadingMetricsView }) {
  const pct = (v: number | null) => (v === null ? "—" : formatPercent(v));
  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <dl className="-mr-px -mb-px grid grid-cols-2 sm:grid-cols-4">
        <Metric name="completeness" label="Completeness" value={pct(metrics.completeness)} hint="Content words read" />
        <Metric name="wer" label="Word error rate" value={pct(metrics.wer)} hint={metrics.strict_wer !== null ? `Strict ${pct(metrics.strict_wer)}` : undefined} />
        <Metric name="duration" label="Duration" value={metrics.duration_seconds !== null ? formatClock(metrics.duration_seconds) : "—"} />
        <Metric name="wpm" label="Pace" value={metrics.words_per_minute !== null ? `${formatNumber(Math.round(metrics.words_per_minute))} wpm` : "—"} hint="80-220 is expected" />
        <Metric
          name="pauses"
          label="Pauses"
          value={metrics.pause_count !== null ? formatNumber(metrics.pause_count) : "—"}
          hint={metrics.longest_pause_seconds ? `Longest ${metrics.longest_pause_seconds.toFixed(1)} s` : undefined}
        />
        <Metric name="long_pauses" label="Long pauses (2 s+)" value={metrics.long_pause_count !== null ? formatNumber(metrics.long_pause_count) : "—"} hint="One is allowed" />
        <Metric
          name="sentences"
          label="Sentences"
          value={metrics.sentences_read !== null ? `${metrics.sentences_read} read` : "—"}
          hint={`${metrics.sentences_partial ?? 0} partial, ${metrics.sentences_skipped ?? 0} skipped`}
        />
        <Metric
          name="changes"
          label="Word changes"
          value={formatNumber((metrics.omissions ?? 0) + (metrics.substitutions ?? 0) + (metrics.insertions ?? 0) + (metrics.repetitions ?? 0))}
          hint={`${metrics.omissions ?? 0} missed, ${metrics.substitutions ?? 0} changed, ${metrics.insertions ?? 0} extra, ${metrics.repetitions ?? 0} repeated`}
        />
      </dl>
    </div>
  );
}

function ScoreChip({ label, score }: { label: string; score: number | null }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5">
      <span className="text-xs text-fg-muted">{label}</span>
      <span className="text-lg leading-6 font-semibold text-fg">{score !== null ? score : "—"}</span>
      <span className="text-xs text-fg-subtle">/ 5</span>
    </span>
  );
}

function Basis({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return (
    <ul className="grid grid-cols-1 list-disc gap-1 pl-5 text-[13px] leading-5 text-fg-muted">
      {lines.map((line, i) => (
        <li key={i}>{line}</li>
      ))}
    </ul>
  );
}

function TranscriptBlock({ activity, title }: { activity: IntakeActivity | null; title: string }) {
  const transcript = activityTranscript(activity);
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-fg">{title}</h4>
        {activity ? (
          <Badge size="sm" tone={transcript.live ? "warning" : "outline"}>
            {transcript.source ? lookup(TRANSCRIPT_SOURCE_LABELS, transcript.source) : TRANSCRIPT_STATUS_LABELS[activity.transcript.status]}
          </Badge>
        ) : null}
        {activity?.audio_media_id ? <AnswerAudio mediaId={activity.audio_media_id} label="Play the capture" className="ml-auto" /> : null}
      </div>
      <p className="rounded-md bg-surface-subtle px-3.5 py-3 text-sm leading-6 whitespace-pre-wrap text-fg ring-1 ring-line-subtle ring-inset">
        {transcript.text || <span className="text-fg-subtle">{activity?.transcript.status === "pending" || activity?.transcript.status === "pending_worker" ? "The final transcript is still being prepared." : "No speech captured."}</span>}
      </p>
    </div>
  );
}

const COVERAGE_ICON: Record<CoverageStatus, typeof CircleCheck> = { covered: CircleCheck, partial: CircleDashed, missing: CircleMinus };

const NOT_ASSESSED_REASONS: Record<string, string> = {
  not_captured: "Nothing was captured for this exercise.",
  not_run: "The exercise didn't run.",
  skipped: "The exercise was skipped.",
  disconnected: "Every attempt ended with a lost connection.",
  disconnect: "Every attempt ended with a lost connection.",
  transcript_unavailable: "Speech was heard but no transcript could be made.",
  stt_degraded: "Speech recognition was degraded; listen to the recording.",
};

/** A drill that couldn't be assessed for technical reasons: insufficient evidence, never a low score. */
function NotAssessed({ reason }: { reason: string | null }) {
  return (
    <Callout tone="neutral" title="Not assessed: insufficient evidence">
      {lookup(NOT_ASSESSED_REASONS, reason, "The capture couldn't be assessed.")} The competency is marked insufficient rather than scored low.
    </Callout>
  );
}

function captureLine(activity: IntakeActivity | null): string | null {
  if (!activity?.capture) return null;
  const parts: string[] = [];
  if (activity.capture.started_ms !== null && activity.capture.ended_ms !== null) parts.push(`${formatClock((activity.capture.ended_ms - activity.capture.started_ms) / 1000)} captured`);
  if (activity.capture.end_reason) parts.push(lookup(ACTIVITY_END_LABELS, activity.capture.end_reason).toLowerCase());
  if (activity.attempt > 1) parts.push(`attempt ${activity.attempt}`);
  if (activity.capture.typed) parts.push("typed");
  return parts.length ? parts.join(", ") : null;
}

export function ReadingTab({ detail }: { detail: IntakeDetail }) {
  const evaluation = detail.evaluation;
  const drillA = evaluation?.summary.drill_a ?? null;
  const drillB = evaluation?.summary.drill_b ?? null;
  const readActivity = activityOf(detail, "reading_aloud");
  const relayActivity = activityOf(detail, "read_relay");
  const technicalReading = evaluation?.competencies.find((c) => c.key === "technical_reading") ?? null;
  const sessionId = detail.session?.id ?? "";
  // Word-level events: from the evaluation when present, else from the generated report (design 15.11 F).
  const needReport = Boolean(sessionId && drillA && !drillA.events && detail.report);
  const report = useSessionReport(sessionId, "recruiter", { enabled: needReport });
  const reportEvents = needReport ? (normalizeIntakeReport(report.data?.content)?.F_reading.drill_a?.events ?? null) : null;
  const events = drillA?.events ?? reportEvents;

  if (!readActivity && !relayActivity && !drillA && !drillB) {
    return (
      <Card>
        <EmptyState icon={<BookOpenText />} title="The reading exercise hasn't happened yet" description="Part 4 of the interview: a passage read aloud (Drill A), then a passage read silently for 30 seconds and explained (Drill B)." />
      </Card>
    );
  }

  const drillAScore = drillA?.score ?? null;
  const drillBScore = drillB?.score ?? null;

  return (
    <div className="grid grid-cols-1 gap-6" data-testid="intake-reading-tab">
      <Card className="flex-row flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
        <Sigma className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
        <p className="min-w-0 flex-1 text-sm text-fg">{TECHNICAL_READING_FORMULA}</p>
        <p className="text-sm text-fg-muted tabular-nums" data-testid="intake-technical-reading">
          0.4 × {drillAScore ?? "—"} + 0.6 × {drillBScore ?? "—"} ={" "}
          <span className="font-semibold text-fg">{technicalReading?.status === "scored" ? formatScore5(technicalReading.score) : "insufficient evidence"}</span>
        </p>
      </Card>

      {/* --------------------------------------------------------------- Drill A */}
      <Card data-testid="intake-reading-drill-a">
        <CardHeader
          title="Drill A, read aloud"
          description={
            readActivity?.material.title || drillA?.passage.title
              ? `Passage ${readActivity?.material.key || drillA?.passage.key || ""}: ${readActivity?.material.title || drillA?.passage.title}${readActivity?.material.level ? `, ${readActivity.material.level}` : ""}`
              : "Passage not recorded"
          }
          action={<ScoreChip label="Delivery" score={drillAScore} />}
        />
        <CardContent className="grid grid-cols-1 gap-5 pt-4">
          {readActivity?.material.paragraphs?.length ? (
            <div className="grid grid-cols-1 gap-3">
              <PassageAlignment paragraphs={readActivity.material.paragraphs} events={events} metrics={drillA?.metrics ?? null} />
              {events ? <AlignmentLegend /> : null}
            </div>
          ) : (
            <p className="text-sm text-fg-muted">The passage text isn't available for this interview.</p>
          )}
          {drillA?.status === "insufficient" ? <NotAssessed reason={drillA.reason} /> : null}
          {drillA?.metrics ? <MetricsGrid metrics={drillA.metrics} /> : drillA?.status === "insufficient" ? null : <p className="text-sm text-fg-muted">Reading metrics appear after the evaluation.</p>}
          <div className="grid grid-cols-1 gap-2">
            <Basis lines={drillA?.basis ?? []} />
            <p className="flex items-start gap-1.5 text-[13px] text-fg-muted" data-testid="intake-pronunciation">
              <Ear className="mt-0.5 size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
              <span>
                <span className="font-medium text-fg">{PRONUNCIATION_NOTE}.</span> The score uses which words were read and timing only; accent never affects it.
              </span>
            </p>
          </div>
          <TranscriptBlock activity={readActivity} title="What was heard" />
          {captureLine(readActivity) ? <p className="text-xs text-fg-subtle">{captureLine(readActivity)}</p> : null}
        </CardContent>
      </Card>

      {/* --------------------------------------------------------------- Drill B */}
      <Card data-testid="intake-reading-drill-b">
        <CardHeader
          title="Drill B, read and relay"
          description={
            relayActivity?.material.title || drillB?.passage.title
              ? `Passage ${relayActivity?.material.key || drillB?.passage.key || ""}: ${relayActivity?.material.title || drillB?.passage.title}${relayActivity?.reveal?.shown_ms ? `, shown for ${Math.round(relayActivity.reveal.shown_ms / 1000)} s` : ", shown for 30 s"}`
              : "Passage not recorded"
          }
          action={<ScoreChip label="Relay" score={drillBScore} />}
        />
        <CardContent className="grid grid-cols-1 gap-5 pt-4">
          {relayActivity?.material.paragraphs?.length ? (
            <details className="group rounded-md border border-line-subtle px-4 py-3" open>
              <summary className="cursor-pointer text-[13px] font-medium text-fg-muted select-none hover:text-fg">The passage the candidate read silently</summary>
              <div className={cn("mt-3 grid gap-3", MATERIAL_TEXT)} lang="en">
                {relayActivity.material.paragraphs.map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
              {relayActivity.reveal?.interrupted ? <p className="mt-2 text-xs text-warning-fg">The reading was interrupted by a disconnection; the passage was shown again only for the time left.</p> : null}
            </details>
          ) : null}
          {drillB?.status === "insufficient" ? <NotAssessed reason={drillB.reason} /> : null}
          <TranscriptBlock activity={relayActivity} title="The relay, in the candidate's words" />
          {drillB?.concepts.length ? (
            <div className="grid grid-cols-1 gap-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h4 className="text-sm font-semibold text-fg">Key concepts</h4>
                <span className="text-xs text-fg-muted">
                  {pluralize(drillB.concepts.filter((c) => c.status === "covered").length, "concept")} covered
                  {drillB.coverage !== null ? `, ${formatPercent(drillB.coverage)} coverage` : ""}
                </span>
              </div>
              <ul className="grid grid-cols-1 divide-y divide-line-subtle rounded-md border border-line">
                {drillB.concepts.map((concept) => {
                  const Icon = COVERAGE_ICON[concept.status];
                  return (
                    <li key={concept.key} className="grid grid-cols-1 gap-1.5 px-3.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_8rem] sm:gap-3" data-testid="intake-relay-concept" data-status={concept.status}>
                      <div className="grid grid-cols-1 min-w-0 gap-1">
                        <p className="text-sm text-fg">{concept.text}</p>
                        {concept.quote ? <p className="text-[13px] text-fg-muted italic">“{concept.quote}”</p> : null}
                      </div>
                      <span className="sm:justify-self-end">
                        <Badge size="sm" tone={COVERAGE_TONES[concept.status]} icon={<Icon aria-hidden="true" />}>
                          {COVERAGE_LABELS[concept.status]}
                        </Badge>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="grid grid-cols-1 content-start gap-1">
              <span className="text-xs text-fg-muted">Paraphrase</span>
              <span className="text-sm font-medium text-fg">{drillB?.paraphrase ? lookup(PARAPHRASE_LABELS, drillB.paraphrase) : "—"}</span>
            </div>
            <div className="grid grid-cols-1 content-start gap-1">
              <span className="text-xs text-fg-muted">Factual errors about the passage</span>
              {drillB?.misstatements.length ? (
                <ul className="flex flex-wrap gap-1.5">
                  {drillB.misstatements.map((m, i) => (
                    <li key={i} className="max-w-full min-w-0">
                      <EvidenceChip intakeId={detail.id} evidence={m} />
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-sm font-medium text-fg">{drillB ? "None found" : "—"}</span>
              )}
            </div>
          </div>
          {drillB?.followups.length ? (
            <div className="grid grid-cols-1 gap-2">
              <h4 className="text-sm font-semibold text-fg">Follow-up questions</h4>
              <ol className="grid grid-cols-1 gap-3">
                {drillB.followups.map((f, i) => (
                  <li key={i} className="grid grid-cols-1 gap-1">
                    <p className="text-sm font-medium text-fg">{f.question}</p>
                    {f.answer ? <p className="text-[13px] leading-5 text-fg-muted">“{f.answer}”</p> : <p className="text-xs text-fg-subtle">Not answered</p>}
                    {f.note ? <p className="text-xs text-fg-muted">{f.note}</p> : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          <Basis lines={drillB?.basis ?? []} />
          {captureLine(relayActivity) ? <p className="text-xs text-fg-subtle">{captureLine(relayActivity)}</p> : null}
        </CardContent>
      </Card>
    </div>
  );
}
