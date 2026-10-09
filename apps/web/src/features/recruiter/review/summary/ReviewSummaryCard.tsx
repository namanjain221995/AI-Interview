import { Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { type ReviewPoint, type ReviewSummary, useReviewSummary } from "./reviewSummary";

function short(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 s";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = seconds / 60;
  return minutes < 10 ? `${minutes.toFixed(1)} min` : `${Math.round(minutes)} min`;
}

function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="grid min-w-0 grid-cols-1 gap-0.5 rounded-md bg-surface-sunken px-3 py-2">
      <span className="text-2xs font-medium tracking-wide text-fg-subtle uppercase">{label}</span>
      <span className="truncate text-sm font-semibold text-fg tabular-nums">{value}</span>
      {hint ? <span className="truncate text-2xs text-fg-subtle">{hint}</span> : null}
    </div>
  );
}

function Points({ title, items, empty, tone, onOpen }: { title: string; items: ReviewPoint[]; empty: string; tone: "positive" | "negative"; onOpen?: (point: ReviewPoint) => void }) {
  return (
    <div className="grid min-w-0 grid-cols-1 content-start gap-2">
      <h4 className="text-[13px] font-semibold text-fg">{title}</h4>
      {items.length === 0 ? (
        <p className="text-[13px] text-fg-muted">{empty}</p>
      ) : (
        <ul className="grid grid-cols-1 gap-2">
          {items.map((p) => (
            <li key={`${p.competencyKey}-${p.evidenceId ?? p.text}`} className={cn("grid grid-cols-1 gap-1 border-l-2 pl-3", tone === "positive" ? "border-success-line" : "border-warning-line")}>
              <span className="text-2xs font-medium text-fg-subtle">{p.competency}</span>
              <span className="text-[13px] leading-5 text-fg">{p.text}</span>
              {p.quote ? (
                onOpen && (p.answerId || p.startMs !== null) ? (
                  <button type="button" className="w-fit rounded-xs text-left text-xs text-fg-muted italic hover:underline" onClick={() => onOpen(p)}>
                    “{p.quote}”
                  </button>
                ) : (
                  <span className="text-xs text-fg-muted italic">“{p.quote}”</span>
                )
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ReviewSummaryView({ summary, onOpenEvidence }: { summary: ReviewSummary; onOpenEvidence?: (point: ReviewPoint) => void }) {
  const f = summary.facts;
  const levelTone = summary.insufficient ? "warning" : summary.evidenceLevel === "partial" ? "signal" : "success";
  return (
    <Card data-testid="review-summary" data-evidence-level={summary.evidenceLevel}>
      <CardHeader
        title="AI review summary"
        description="What the candidate actually did, then what the evidence shows."
        action={
          <Badge tone={levelTone} data-testid="review-summary-headline">
            {summary.headline}
          </Badge>
        }
      />
      <CardContent className="grid grid-cols-1 gap-4 pt-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Fact label="Interview" value={short(f.durationSeconds)} hint={f.plannedSeconds ? `of ${short(f.plannedSeconds)} planned` : undefined} />
          <Fact label="Candidate spoke" value={`${f.speakingTimeEstimated ? "≈ " : ""}${short(f.candidateSpeakingSeconds)}`} hint={`${f.candidateWords} words`} />
          <Fact label="Answered" value={`${f.questionsAnswered} of ${f.questionsAsked}`} hint={f.questionsSkipped ? `${f.questionsSkipped} skipped` : "none skipped"} />
          <Fact label="Silence" value={f.silenceShare === null ? "—" : formatPercent(f.silenceShare)} hint="of interview time" />
        </div>

        {summary.statements.length ? (
          <ul className="grid list-disc grid-cols-1 gap-1 pl-5 text-[13px] leading-5 text-fg-muted" data-testid="review-summary-facts">
            {summary.statements.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        ) : null}

        {summary.evidenceStatement ? (
          <Callout tone={summary.insufficient ? "warning" : "info"} title={summary.insufficient ? "Too little evidence to judge" : "Partial evidence"} data-testid="review-summary-evidence">
            {summary.evidenceStatement}
          </Callout>
        ) : null}

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Points title="Strengths with evidence" items={summary.strengths} tone="positive" empty={summary.insufficient ? "None can be stated from this little evidence." : "No supporting evidence recorded."} onOpen={onOpenEvidence} />
          <Points title="Weaknesses with evidence" items={summary.weaknesses} tone="negative" empty="No gaps recorded." onOpen={onOpenEvidence} />
        </div>

        <div className="grid grid-cols-1 gap-1.5 rounded-md border border-line-subtle p-3" data-testid="review-summary-ai">
          <span className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
            <Sparkles className="size-3.5 text-accent" aria-hidden="true" />
            AI assessment
          </span>
          {summary.aiAssessment ? (
            <p className="text-[13px] leading-5 whitespace-pre-line text-fg">{summary.aiAssessment.text}</p>
          ) : (
            <p className="text-[13px] text-fg-muted">{summary.aiWithheldReason ?? "Not available."}</p>
          )}
          <p className="text-2xs text-fg-subtle">{summary.note}</p>
        </div>
      </CardContent>
    </Card>
  );
}

/** The review summary card for one session (hidden while the session is still running). */
export function ReviewSummaryCard({ sessionId, enabled = true, onOpenEvidence }: { sessionId: string | null | undefined; enabled?: boolean; onOpenEvidence?: (point: ReviewPoint) => void }) {
  const query = useReviewSummary(sessionId, { enabled });
  if (!enabled || !sessionId) return null;
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError || !query.data) return null;
  if (!query.data.completed) return null;
  return <ReviewSummaryView summary={query.data} onOpenEvidence={onOpenEvidence} />;
}
