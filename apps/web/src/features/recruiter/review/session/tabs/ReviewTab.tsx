import { History, Info, Lock } from "lucide-react";
import { useState } from "react";

import { ConfidenceMeter } from "@/components/domain/ConfidenceMeter";
import { RecommendationBadge } from "@/components/domain/RecommendationBadge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Checkbox } from "@/components/ui/Checkbox";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Label } from "@/components/ui/Label";
import { RadioCard, RadioGroup } from "@/components/ui/RadioGroup";
import { Textarea } from "@/components/ui/Textarea";
import { toast } from "@/components/ui/Toaster";
import { errorMessage } from "@/lib/api";
import { usePermission } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";
import { RECOMMENDATION_LABELS, REVIEW_DECISION_LABELS } from "@/lib/labels";
import type { ReviewDecisionValue } from "@/lib/types";

import { useCreateReview } from "../../api/hooks";
import { DecisionBadge, DECISION_DESCRIPTIONS, DECISION_ORDER, differsFromSuggestion } from "../../components/decisions";
import { PanelSkeleton, QueryError } from "../../components/primitives";
import { useSessionContext } from "../context";

function DecisionForm() {
  const { sessionId, session, recommendation, confidence, inProgress, nav } = useSessionContext();
  const canRelease = usePermission("reports:release");
  const [decision, setDecision] = useState<ReviewDecisionValue | null>(null);
  const [notes, setNotes] = useState("");
  const [touched, setTouched] = useState(false);
  const [releaseReport, setReleaseReport] = useState(false);
  const create = useCreateReview(sessionId);
  const candidateReport = session.reports?.candidate;
  // In "after review" mode the decision can release the candidate's feedback report in the same step.
  const offerRelease = canRelease && session.candidate_report_mode === "after_review" && candidateReport?.status === "ready" && !candidateReport.released_to_candidate;
  const differs = decision ? differsFromSuggestion(decision, recommendation) : false;
  // Notes are never forced, but they matter most when overriding the suggestion or closing an application.
  const notesRecommended = differs || decision === "reject";

  const submit = () => {
    setTouched(true);
    if (!decision) return;
    create.mutate(
      { decision, notes: notes.trim() || null, release_candidate_report: offerRelease && releaseReport ? true : undefined },
      {
        onSuccess: (result) => {
          setDecision(null);
          setNotes("");
          setTouched(false);
          setReleaseReport(false);
          if (result?.candidate_report_released === false) toast.warning("Decision recorded, but the candidate report couldn't be released. Try again from the report page.");
          else if (result?.candidate_report_released) toast.success("Candidate report released");
        },
      },
    );
  };

  return (
    <Card>
      <CardHeader title="Record your decision" description="Your decision is stored separately from the AI suggestion, with your name and the suggestion you saw." />
      <CardContent className="grid grid-cols-1 gap-5">
        <div className="grid grid-cols-1 gap-2 rounded-md bg-surface-subtle p-4 ring-1 ring-line-subtle ring-inset">
          <p className="text-xs font-medium text-fg-muted">AI-assisted suggestion right now</p>
          <RecommendationBadge value={recommendation} />
          <ConfidenceMeter value={confidence} />
          <button type="button" className="w-fit rounded-xs text-xs font-medium text-accent hover:underline" onClick={() => nav.go({ tab: "competencies" })}>
            Check the evidence first
          </button>
        </div>

        {inProgress ? (
          <Callout tone="neutral" icon={<Info />}>
            Decisions open once the interview has finished and scores are final.
          </Callout>
        ) : null}

        <fieldset className="grid grid-cols-1 gap-2" disabled={inProgress || create.isPending}>
          <legend className="mb-2 text-sm font-medium text-fg">Decision</legend>
          <RadioGroup value={decision ?? ""} onValueChange={(v) => setDecision(v as ReviewDecisionValue)} className="grid grid-cols-1 gap-2 sm:grid-cols-2" aria-label="Decision">
            {DECISION_ORDER.map((value) => (
              <RadioCard key={value} value={value} title={REVIEW_DECISION_LABELS[value]} description={DECISION_DESCRIPTIONS[value]} />
            ))}
          </RadioGroup>
          {touched && !decision ? <p className="text-xs text-danger-fg">Choose a decision.</p> : null}
        </fieldset>

        {differs && recommendation ? (
          <Callout tone="warning" title="This differs from the AI suggestion">
            The suggestion was “{RECOMMENDATION_LABELS[recommendation]}”. That's fine; you own the decision. A short note on why helps other reviewers understand it.
          </Callout>
        ) : null}

        <div className="grid grid-cols-1 gap-1.5">
          <Label htmlFor="review-notes">
            Notes{notesRecommended ? " (recommended)" : " (optional)"}
          </Label>
          <Textarea
            id="review-notes"
            rows={4}
            autoResize
            maxLength={4000}
            showCount
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={inProgress || create.isPending}
            aria-describedby="review-notes-help"
            placeholder="What the decision rests on: evidence you weighed, follow-ups you want, anything the AI missed."
          />
          <p id="review-notes-help" className="text-xs text-fg-muted">
            {notesRecommended ? "A sentence or two on what the decision rests on helps the next reviewer." : "Visible to other reviewers in your organization."}
          </p>
        </div>

        {offerRelease ? (
          <Checkbox
            checked={releaseReport}
            onCheckedChange={(v) => setReleaseReport(v === true)}
            disabled={inProgress || create.isPending}
            label="Also release the candidate's feedback report"
            description="This job shares reports after a reviewer decides. The candidate report never includes the AI suggestion, integrity details or your notes."
          />
        ) : null}

        {create.isError ? (
          <Callout tone="danger" title="Couldn't record the decision">
            {errorMessage(create.error)}
          </Callout>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button data-testid="review-submit" onClick={submit} loading={create.isPending} disabled={inProgress}>
            Record decision
          </Button>
          <p className="text-xs text-fg-muted">A person makes the hiring decision. The AI suggestion is advice only.</p>
        </div>
      </CardContent>
    </Card>
  );
}

function HistoryCard() {
  const { reviews, reviewsQuery } = useSessionContext();
  return (
    <Card>
      <CardHeader title="Decision history" description="Every decision recorded for this interview, newest first." />
      <CardContent>
        {reviewsQuery.isPending ? (
          <PanelSkeleton rows={2} className="p-0" />
        ) : reviewsQuery.isError ? (
          <QueryError error={reviewsQuery.error} title="Couldn't load decisions" onRetry={() => void reviewsQuery.refetch()} />
        ) : reviews.length === 0 ? (
          <EmptyState size="sm" icon={<History />} title="No decisions yet" description="The first decision recorded here becomes the current one." />
        ) : (
          <ol className="grid grid-cols-1 gap-3">
            {reviews.map((review, i) => (
              <li key={review.id} className="grid grid-cols-1 gap-2 rounded-md border border-line-subtle px-4 py-3">
                <span className="flex flex-wrap items-center gap-2">
                  <DecisionBadge value={review.decision} />
                  {i === 0 ? (
                    <Badge tone="outline" size="sm">
                      Current
                    </Badge>
                  ) : null}
                  {review.overrides_ai ? (
                    <Badge tone="warning" size="sm">
                      Overrode the AI suggestion
                    </Badge>
                  ) : null}
                </span>
                <span className="text-xs text-fg-muted">
                  {review.reviewer?.full_name ?? "A reviewer"}, {formatDateTime(review.created_at)}
                </span>
                {review.notes ? <p className="text-[13px] leading-5 whitespace-pre-wrap text-fg">{review.notes}</p> : null}
                <span className="text-xs text-fg-subtle">
                  AI suggestion at the time: {review.ai_recommendation_at_review ? RECOMMENDATION_LABELS[review.ai_recommendation_at_review] : "none"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

export function ReviewTab() {
  const canReview = usePermission("reviews:write");
  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      {canReview ? (
        <DecisionForm />
      ) : (
        <Card>
          <EmptyState icon={<Lock />} title="You can read decisions but not record them" description="Ask an admin for review access if you should be making decisions." />
        </Card>
      )}
      <HistoryCard />
    </div>
  );
}
