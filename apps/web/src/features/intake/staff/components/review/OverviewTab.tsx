import { FileText, Lock, UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { usePermission } from "@/lib/auth";
import { formatDate, formatDateTime, formatNumber, pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ReviewSummaryCard } from "@/features/recruiter/review/summary/ReviewSummaryCard";

import {
  AVAILABILITY_LABELS,
  COMFORT_LABELS,
  COMMITMENT_LABELS,
  ELIGIBILITY_NOTE,
  LANGUAGE_LABELS,
  OPEN_TO_AI_ML_LABELS,
  SCRIPTING_LABELS,
  lookup,
  TARGET_ROLE_LABELS,
  VERDICT_LABELS,
  VERDICT_TONES,
} from "../../labels";
import type { IntakeDetail, IntakeEligibilityView } from "../../types";
import { EvidenceChip } from "./EvidenceChip";
import { ReviewItemList } from "./ReviewItems";
import { StageProgress } from "./StageProgress";

function Row({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 gap-0.5 py-2 sm:grid-cols-[minmax(9rem,38%)_minmax(0,1fr)] sm:gap-3", className)}>
      <dt className="text-[13px] text-fg-muted">{label}</dt>
      <dd className="min-w-0 text-sm break-words text-fg">{children}</dd>
    </div>
  );
}

const none = <span className="text-fg-subtle">Not given</span>;

/** Session states after which the AI review summary is written. */
const FINISHED_SESSION_STATUSES: readonly string[] = ["completed", "aborted", "failed"];

function yesNo(value: boolean | null): ReactNode {
  if (value === null) return <span className="text-fg-muted">Prefers to discuss with the hiring team</span>;
  return value ? "Yes" : "No";
}

function EligibilityBlock({ eligibility }: { eligibility: IntakeEligibilityView }) {
  return (
    <Card data-testid="intake-eligibility">
      <CardHeader title="Work authorisation" description={ELIGIBILITY_NOTE} />
      <CardContent className="pt-3">
        {eligibility.answered_at ? (
          <dl className="grid grid-cols-1 divide-y divide-line-subtle">
            <Row label={`Authorised to work in ${eligibility.country ?? "the country"}`}>{yesNo(eligibility.authorized)}</Row>
            {eligibility.ask_sponsorship ? <Row label="Needs sponsorship now or later">{yesNo(eligibility.needs_sponsorship)}</Row> : null}
            <Row label="Answered">{formatDateTime(eligibility.answered_at)}</Row>
          </dl>
        ) : (
          <p className="text-sm text-fg-muted">Not answered yet. The candidate answers on their onboarding form.</p>
        )}
      </CardContent>
    </Card>
  );
}

export function OverviewTab({ detail, nowElapsed }: { detail: IntakeDetail; nowElapsed: number | null }) {
  const canSeeCandidates = usePermission("candidates:read");
  const ob = detail.onboarding;
  const review = detail.resume_review;
  const roleText = ob?.target_role ? (ob.target_role === "other" && ob.target_role_other ? `Other: ${ob.target_role_other}` : TARGET_ROLE_LABELS[ob.target_role]) : detail.target_role ? TARGET_ROLE_LABELS[detail.target_role] : null;
  const openItems = detail.review_items.filter((i) => !i.resolution).length;
  const claims = detail.evaluation?.summary.claims ?? [];
  const sessionDone = !!detail.session && FINISHED_SESSION_STATUSES.includes(detail.session.status);

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]" data-testid="intake-overview-tab">
      {sessionDone ? (
        <div className="min-w-0 lg:col-span-2">
          <ReviewSummaryCard sessionId={detail.session?.id} />
        </div>
      ) : null}
      <div className="grid grid-cols-1 content-start gap-6">
        <Card>
          <CardHeader title="Candidate" description={ob ? `From the onboarding form, ${ob.submitted_at ? formatDate(ob.submitted_at) : "submitted"}.` : "The onboarding form isn't submitted yet."} />
          <CardContent className="pt-3">
            <dl className="grid grid-cols-1 divide-y divide-line-subtle">
              <Row label="Name">{ob?.full_name || detail.candidate.full_name}</Row>
              <Row label="Email">{detail.candidate.email}</Row>
              <Row label="Target role">{roleText ?? none}</Row>
              {ob ? (
                <>
                  <Row label="Open to AI/ML work">{ob.open_to_ai_ml ? OPEN_TO_AI_ML_LABELS[ob.open_to_ai_ml] : none}</Row>
                  <Row label="Strongest language">{ob.primary_language ? (ob.primary_language === "other" ? ob.primary_language_other || "Other" : LANGUAGE_LABELS[ob.primary_language]) : none}</Row>
                  <Row label="Self-rating">{ob.self_rating !== null ? `${ob.self_rating} / 10` : none}</Row>
                  <Row label="Scripting">
                    {ob.scripting ? SCRIPTING_LABELS[ob.scripting] : none}
                    {ob.scripting_details ? <span className="block text-[13px] text-fg-muted">{ob.scripting_details}</span> : null}
                  </Row>
                  <Row label="Data and ML libraries">{ob.data_ml_libraries || none}</Row>
                  <Row label="Hands-on experience">{ob.years_hands_on !== null ? pluralize(ob.years_hands_on, "year") : none}</Row>
                  <Row label="Comfortable with the expected level">{ob.experience_level_comfort ? COMFORT_LABELS[ob.experience_level_comfort] : none}</Row>
                  <Row label="Training commitment">
                    {ob.training_commitment ? COMMITMENT_LABELS[ob.training_commitment] : none}
                    {ob.weekly_hours !== null ? <span className="text-fg-muted">, {formatNumber(ob.weekly_hours)} hours a week</span> : null}
                  </Row>
                  <Row label="Availability">
                    {ob.availability ? AVAILABILITY_LABELS[ob.availability] : none}
                    {ob.available_from ? <span className="text-fg-muted">, from {formatDate(ob.available_from)}</span> : null}
                  </Row>
                  <Row label="Technical background">{ob.technical_background ? <span className="whitespace-pre-wrap">{ob.technical_background}</span> : none}</Row>
                </>
              ) : null}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader
            title="Résumé"
            action={
              canSeeCandidates && detail.candidate.id ? (
                <Link to={`/app/candidates/${detail.candidate.id}?tab=resumes`} className="inline-flex items-center gap-1.5 text-[13px] font-medium text-accent hover:underline" data-testid="intake-resume-link">
                  <UserRound className="size-3.5" aria-hidden="true" />
                  Candidate profile
                </Link>
              ) : undefined
            }
          />
          <CardContent className="grid grid-cols-1 gap-3 pt-3">
            {detail.resume ? (
              <p className="flex items-center gap-2 text-sm text-fg">
                <FileText className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
                <span className="min-w-0 truncate">{detail.resume.original_filename}</span>
                <Badge size="sm" tone="outline">
                  {detail.resume.status}
                </Badge>
              </p>
            ) : (
              <p className="text-sm text-fg-muted">No résumé uploaded{detail.settings.resume_required ? " yet. One is required before the interview starts." : "."}</p>
            )}
            {review ? (
              <div className="grid grid-cols-1 gap-2">
                <p className="text-[13px] text-fg-muted">
                  {review.action === "skipped"
                    ? "The candidate continued without reviewing the extracted résumé (allowed after three minutes of processing)."
                    : `The candidate checked the extracted résumé${review.confirmed_at ? ` on ${formatDate(review.confirmed_at)}` : ""}${review.corrections.length ? ` and made ${pluralize(review.corrections.length, "correction")}` : " without changes"}.`}
                </p>
                {review.corrections.length ? (
                  <ul className="grid grid-cols-1 gap-1 rounded-md bg-surface-subtle px-3 py-2.5 text-[13px] ring-1 ring-line-subtle ring-inset">
                    {review.corrections.slice(0, 8).map((c, i) => (
                      <li key={`${c.path}-${i}`} className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-2">
                        <span className="text-fg-subtle">{c.op === "added" ? "Added" : c.op === "removed" ? "Removed" : "Changed"}</span>
                        <span className="min-w-0 break-words text-fg">
                          {describeCorrection(c.before, c.after, c.op)}
                          <span className="block text-xs text-fg-subtle">{c.path}</span>
                        </span>
                      </li>
                    ))}
                    {review.corrections.length > 8 ? <li className="text-xs text-fg-subtle">and {review.corrections.length - 8} more</li> : null}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {claims.length ? (
              <div className="grid grid-cols-1 gap-2 border-t border-line-subtle pt-3" data-testid="intake-claims">
                <span className="text-[13px] font-medium text-fg">Résumé claims probed in the interview</span>
                <ul className="grid grid-cols-1 divide-y divide-line-subtle">
                  {claims.map((claim) => (
                    <li key={claim.claim} className="grid grid-cols-1 gap-1.5 py-2.5 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="text-xs font-semibold text-fg-subtle">{claim.claim}</span>
                        <span className="min-w-0 text-sm text-fg">{claim.text ?? "Claim from the résumé"}</span>
                        <Badge size="sm" tone={VERDICT_TONES[claim.verdict] ?? "neutral"}>
                          {lookup(VERDICT_LABELS, claim.verdict)}
                        </Badge>
                        {claim.source === "candidate_correction" ? <span className="text-xs text-fg-muted">Added by the candidate</span> : null}
                      </div>
                      {claim.note ? <p className="text-[13px] leading-5 text-fg-muted">{claim.note}</p> : null}
                      {claim.unresolved.length ? <p className="text-xs text-fg-muted">Still open: {claim.unresolved.join("; ")}</p> : null}
                      {claim.evidence.length ? (
                        <ul className="flex flex-wrap gap-1.5">
                          {claim.evidence.slice(0, 3).map((ref, i) => (
                            <li key={i} className="max-w-full min-w-0">
                              <EvidenceChip intakeId={detail.id} evidence={ref} />
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader title="Stage progress" description="Time used against each stage's budget. The track ends at the stage's cap of 1.25 times its budget." />
          <CardContent className="pt-4">
            <StageProgress stages={detail.stages} nowElapsed={nowElapsed} />
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 content-start gap-6">
        <Card>
          <CardHeader title="Review items" description={openItems ? `${pluralize(openItems, "item")} to look at. Items flag evidence for a person; they never decide.` : "Items flag evidence for a person; they never decide."} />
          <CardContent className="pt-3">
            <ReviewItemList intakeId={detail.id} items={detail.review_items} />
          </CardContent>
        </Card>
        {detail.eligibility?.enabled ? <EligibilityBlock eligibility={detail.eligibility} /> : null}
        <Card>
          <CardHeader title="Invitation" />
          <CardContent className="pt-3">
            <dl className="grid grid-cols-1 divide-y divide-line-subtle">
              <Row label="Duration">{detail.settings.duration_minutes} minutes</Row>
              <Row label="Link">
                {detail.interview?.invite_expires_at ? `Valid until ${formatDateTime(detail.interview.invite_expires_at)}` : detail.status === "draft" ? "Not sent yet" : "—"}
                {detail.settings.require_access_code ? (
                  <span className="mt-0.5 flex items-center gap-1 text-xs text-fg-muted">
                    <Lock className="size-3" aria-hidden="true" />
                    Access code required
                  </span>
                ) : null}
              </Row>
              {detail.interview?.scheduled_for ? <Row label="Earliest start">{formatDateTime(detail.interview.scheduled_for)}</Row> : null}
              <Row label="Reconnections allowed">{detail.settings.max_reconnects}</Row>
              <Row label="Interviewer presence">{detail.settings.presence === "orb" ? "Voice orb" : "Audio indicator only"}</Row>
              <Row label="Assigned reviewer">{detail.assigned_reviewer?.full_name ?? <span className="text-fg-subtle">No one yet</span>}</Row>
              <Row label="Created">{formatDateTime(detail.created_at)}</Row>
              {detail.notes ? <Row label="Notes">{<span className="whitespace-pre-wrap">{detail.notes}</span>}</Row> : null}
            </dl>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function short(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(short).join(", ");
  if (typeof value === "object") {
    const r = value as Record<string, unknown>;
    return String(r.title ?? r.name ?? r.institution ?? JSON.stringify(value));
  }
  return String(value);
}

function describeCorrection(before: unknown, after: unknown, op: string): string {
  if (op === "added") return short(after);
  if (op === "removed") return short(before);
  return `${short(before) || "(empty)"} → ${short(after) || "(empty)"}`;
}
