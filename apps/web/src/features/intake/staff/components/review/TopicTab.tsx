import { CircleCheck, CircleDashed, CircleMinus, MessagesSquare } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { cn } from "@/lib/utils";

import { AnswerAudio } from "@/features/recruiter/review/components/media";

import { COVERAGE_LABELS, COVERAGE_TONES, FLOW_LABELS, GAP_HANDLING_LABELS, lookup, TRANSCRIPT_SOURCE_LABELS } from "../../labels";
import { formatScore5 } from "../../lib/decimals";
import { activityOf, activityTranscript } from "../../lib/review";
import type { CoverageStatus, IntakeDetail } from "../../types";
import { EvidenceChip } from "./EvidenceChip";
import { MATERIAL_TEXT } from "./PassageAlignment";

const ICON: Record<CoverageStatus, typeof CircleCheck> = { covered: CircleCheck, partial: CircleDashed, missing: CircleMinus };

export function TopicTab({ detail }: { detail: IntakeDetail }) {
  const activity = activityOf(detail, "topic_card");
  const summary = detail.evaluation?.summary ?? null;
  const structured = detail.evaluation?.competencies.find((c) => c.key === "structured_answer") ?? null;
  const transcript = activityTranscript(activity);
  const bullets = activity?.material.bullets ?? [];

  if (!activity && !summary?.bullets.length) {
    return (
      <Card>
        <EmptyState icon={<MessagesSquare />} title="The speaking exercise hasn't happened yet" description="Part 5: a topic card with four points, one minute to prepare and two minutes to speak." />
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]" data-testid="intake-topic-tab">
      <Card data-testid="intake-topic-card">
        <CardHeader
          title={activity?.material.title || "Topic card"}
          description={[activity?.material.key ? `Card ${activity.material.key}` : null, activity?.material.type_label].filter(Boolean).join(", ") || undefined}
          action={
            <span className="inline-flex items-baseline gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5">
              <span className="text-xs text-fg-muted">Structured answer</span>
              <span className="text-lg leading-6 font-semibold text-fg">{structured?.status === "scored" ? formatScore5(structured.score) : "—"}</span>
              <span className="text-xs text-fg-subtle">/ 5</span>
            </span>
          }
        />
        <CardContent className="grid grid-cols-1 gap-5 pt-4">
          {activity?.material.instruction ? <p className={cn(MATERIAL_TEXT, "text-fg-muted")}>{activity.material.instruction}</p> : null}
          <ol className="grid grid-cols-1 gap-3">
            {(bullets.length ? bullets : summary?.bullets.map((b) => b.text ?? "") ?? []).map((text, index) => {
              const judged = summary?.bullets.find((b) => b.index === index + 1) ?? null;
              const status = judged?.status ?? null;
              const Icon = status ? ICON[status] : null;
              return (
                <li key={index} className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-3" data-testid={`intake-bullet-coverage-${index + 1}`} data-status={status ?? "unknown"}>
                  <span className="mt-1 flex size-6 items-center justify-center rounded-full bg-neutral-soft text-xs font-semibold text-neutral-fg ring-1 ring-neutral-line ring-inset" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div className="grid grid-cols-1 min-w-0 gap-1.5">
                    <p className={MATERIAL_TEXT} lang="en">
                      {text || judged?.text}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      {status && Icon ? (
                        <Badge size="sm" tone={COVERAGE_TONES[status]} icon={<Icon aria-hidden="true" />}>
                          {COVERAGE_LABELS[status]}
                        </Badge>
                      ) : (
                        <span className="text-xs text-fg-subtle">Not judged yet</span>
                      )}
                      {judged?.evidence ? <EvidenceChip intakeId={detail.id} evidence={{ ...judged.evidence, quote: judged.quote ?? judged.evidence.quote }} /> : judged?.quote ? <span className="text-[13px] text-fg-muted italic">“{judged.quote}”</span> : null}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="grid grid-cols-1 gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="text-sm font-semibold text-fg">What the candidate said</h4>
              {transcript.source ? (
                <Badge size="sm" tone={transcript.live ? "warning" : "outline"}>
                  {lookup(TRANSCRIPT_SOURCE_LABELS, transcript.source)}
                </Badge>
              ) : null}
              {activity?.audio_media_id ? <AnswerAudio mediaId={activity.audio_media_id} label="Play the answer" className="ml-auto" /> : null}
            </div>
            <p className="rounded-md bg-surface-subtle px-3.5 py-3 text-sm leading-6 whitespace-pre-wrap text-fg ring-1 ring-line-subtle ring-inset">{transcript.text || <span className="text-fg-subtle">No speech captured.</span>}</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader title="How the answer was built" />
        <CardContent className="grid grid-cols-1 gap-4 pt-3">
          <dl className="grid grid-cols-1 gap-3">
            <div className="grid grid-cols-1 gap-0.5">
              <dt className="text-xs text-fg-muted">Flow</dt>
              <dd className="text-sm font-medium text-fg">{summary?.flow ? lookup(FLOW_LABELS, summary.flow) : "—"}</dd>
            </div>
            <div className="grid grid-cols-1 gap-0.5">
              <dt className="text-xs text-fg-muted">Examples</dt>
              <dd className="text-sm font-medium text-fg">{summary?.examples_used === null || summary?.examples_used === undefined ? "—" : summary.examples_used ? "Used a concrete example" : "No concrete example"}</dd>
            </div>
            <div className="grid grid-cols-1 gap-0.5">
              <dt className="text-xs text-fg-muted">Gaps</dt>
              <dd className="text-sm font-medium text-fg">{summary?.gap_handling ? lookup(GAP_HANDLING_LABELS, summary.gap_handling) : "—"}</dd>
            </div>
          </dl>
          {summary?.followup_analysis ? (
            <div className="grid grid-cols-1 gap-1 border-t border-line-subtle pt-3">
              <span className="text-xs text-fg-muted">The follow-up on the weakest point</span>
              <p className="text-[13px] leading-5 text-fg">{summary.followup_analysis}</p>
            </div>
          ) : null}
          {structured?.reasoning ? (
            <div className="grid grid-cols-1 gap-1 border-t border-line-subtle pt-3">
              <span className="text-xs text-fg-muted">Evaluator's reasoning</span>
              <p className="text-[13px] leading-5 text-fg">{structured.reasoning}</p>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
