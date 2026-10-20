import { ChevronDown, ChevronRight, Gavel, History, Info, TriangleAlert, UserRound } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { RadioCard, RadioGroup, RadioGroupItem } from "@/components/ui/RadioGroup";
import { Textarea } from "@/components/ui/Textarea";
import { isApiError } from "@/lib/api";
import { usePermission } from "@/lib/auth";
import { formatDateTime, pluralize } from "@/lib/format";

import { FieldShell, ListEditor } from "@/features/recruiter/admin/components/Field";

import { useRecordIntakeDecision } from "../../api";
import { BAND_LABELS, competencyLabel, DECISION_ADVISORY_NOTE, OUTCOME_DESCRIPTIONS, OUTCOME_LABELS, RESOLUTION_LABELS } from "../../labels";
import { formatTotal } from "../../lib/decimals";
import {
  decisionPayload,
  decisionServerError,
  differsFromBand,
  emptyDecisionDraft,
  hasDecisionErrors,
  NOTE_MAX,
  RATIONALE_MAX,
  REQUESTED_EVIDENCE_MAX,
  unresolvedItems,
  validateDecision,
  type DecisionDraft,
  type DecisionErrors,
} from "../../lib/decision";
import { COMPETENCY_KEYS, DECISION_OUTCOMES, type DecisionOutcome, type IntakeDecision, type IntakeDetail, type IntakeEvaluation, type ReviewResolution } from "../../types";
import { BandBadge, OutcomeBadge } from "../IntakeBadges";
import { GroupDetails, GroupTitle, groupReviewItems, sortReviewItems } from "./ReviewItems";

const FINISHED = new Set(["completing", "completed", "aborted", "failed"]);
const STALE_MS = 15 * 60_000;

function DecisionHistory({ decisions }: { decisions: readonly IntakeDecision[] }) {
  return (
    <Card data-testid="intake-decision-history">
      <CardHeader title="Decision history" description="Every decision is kept, with its rationale. The newest one is current." />
      <CardContent className="pt-3">
        {decisions.length === 0 ? (
          <p className="text-sm text-fg-muted">No decision recorded yet.</p>
        ) : (
          <ol className="grid grid-cols-1 divide-y divide-line-subtle">
            {decisions.map((decision, index) => (
              <li key={decision.id} className="grid grid-cols-1 gap-2 py-3 first:pt-0 last:pb-0" data-testid="intake-decision-entry" data-outcome={decision.outcome}>
                <div className="flex flex-wrap items-center gap-2">
                  <OutcomeBadge outcome={decision.outcome} label={decision.outcome_label} size="md" />
                  {index === 0 ? (
                    <Badge size="sm" tone="outline">
                      Current
                    </Badge>
                  ) : null}
                  {decision.overrides_ai ? (
                    <Badge size="sm" tone="warning" icon={<TriangleAlert aria-hidden="true" />}>
                      Differs from the AI recommendation
                    </Badge>
                  ) : null}
                </div>
                <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-fg-muted">
                  <UserRound className="size-3.5" aria-hidden="true" />
                  {decision.reviewer?.full_name ?? "A reviewer"}, {formatDateTime(decision.created_at)}
                  {decision.ai_band_at_decision ? (
                    <span>
                      . AI recommendation then: {BAND_LABELS[decision.ai_band_at_decision]}
                      {decision.ai_total_at_decision ? ` (${formatTotal(decision.ai_total_at_decision)} / 5)` : ""}
                    </span>
                  ) : null}
                </p>
                <p className="text-sm leading-6 whitespace-pre-wrap text-fg">{decision.rationale}</p>
                {decision.requested_evidence.length ? (
                  <div className="grid grid-cols-1 gap-1">
                    <span className="text-xs font-medium text-fg">Requested from the candidate</span>
                    <ul className="grid grid-cols-1 list-disc gap-0.5 pl-5 text-[13px] text-fg-muted">
                      {decision.requested_evidence.map((item, i) => (
                        <li key={i}>{item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {decision.review_items_resolution.length ? (
                  <p className="text-xs text-fg-muted">{pluralize(decision.review_items_resolution.length, "review item")} resolved with this decision.</p>
                ) : null}
                {Object.keys(decision.competency_notes).length ? (
                  <dl className="grid grid-cols-1 gap-1 text-[13px]">
                    {Object.entries(decision.competency_notes).map(([key, note]) => (
                      <div key={key} className="grid grid-cols-1 gap-0.5 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-2">
                        <dt className="text-fg-muted">{competencyLabel(key)}</dt>
                        <dd className="text-fg">{note}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

export function DecisionTab({ detail, evaluation }: { detail: IntakeDetail; evaluation: IntakeEvaluation | null }) {
  const canDecide = usePermission("reviews:write");
  const record = useRecordIntakeDecision(detail.id, detail.session?.id ?? null);
  const [draft, setDraft] = useState<DecisionDraft>(emptyDecisionDraft);
  const [errors, setErrors] = useState<DecisionErrors>({});
  const [attempted, setAttempted] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);

  const band = evaluation?.status === "ready" ? evaluation.band : null;
  const sessionFinished = Boolean(detail.session && FINISHED.has(detail.session.status));
  // A re-evaluation shows in `evaluation_ref` while `evaluation` is still the last ready one; stale runs don't block (design 8.9).
  const latest = detail.evaluation_ref;
  const running = Boolean(
    (latest && (latest.status === "pending" || latest.status === "running") && latest.id !== evaluation?.id) ||
      (evaluation && (evaluation.status === "pending" || evaluation.status === "running") && Date.now() - Date.parse(evaluation.created_at || "") < STALE_MS),
  );
  const openItems = useMemo(() => sortReviewItems(detail.review_items.filter((i) => !i.resolution)), [detail.review_items]);
  const openGroups = useMemo(() => groupReviewItems(openItems), [openItems]);
  const differs = differsFromBand(draft.outcome, band);
  const unresolved = unresolvedItems(draft, openItems);
  const unresolvedGroups = openGroups.filter((g) => g.items.some((i) => !draft.resolutions[i.id]?.resolution)).length;

  const update = (patch: Partial<DecisionDraft>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    if (attempted) setErrors(validateDecision(next));
  };
  // One choice (and note) resolves every item of a group.
  const setResolution = (ids: readonly string[], patch: Partial<{ resolution: ReviewResolution | null; note: string }>) => {
    const resolutions = { ...draft.resolutions };
    for (const id of ids) resolutions[id] = { ...(resolutions[id] ?? { resolution: null, note: "" }), ...patch };
    update({ resolutions });
  };

  const submit = async () => {
    setAttempted(true);
    const found = validateDecision(draft);
    setErrors(found);
    if (hasDecisionErrors(found)) return;
    try {
      await record.mutateAsync(decisionPayload(draft, openItems));
      setDraft(emptyDecisionDraft());
      setAttempted(false);
      setErrors({});
    } catch (error) {
      setErrors(decisionServerError(isApiError(error) ? error.code : undefined, error instanceof Error ? error.message : ""));
    }
  };

  const blocked = !sessionFinished ? "Decisions open when the interview has ended." : running ? "A new evaluation is running. Decide once it finishes, so the decision refers to the latest results." : null;

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
      <div className="grid grid-cols-1 content-start gap-6">
        {canDecide ? (
          <Card data-testid="intake-decision-form">
            <CardHeader
              title="Record a decision"
              description={DECISION_ADVISORY_NOTE}
              action={
                band ? (
                  <span className="flex items-center gap-2 text-xs text-fg-muted">
                    AI recommendation <BandBadge band={band} label={evaluation?.band_label} />
                  </span>
                ) : null
              }
            />
            <CardContent className="pt-4">
              <fieldset disabled={Boolean(blocked) || record.isPending} className="grid min-w-0 grid-cols-1 gap-6">
                {blocked ? (
                  <Callout tone="info" icon={<Info />}>
                    {blocked}
                  </Callout>
                ) : null}
                <FieldShell label="Outcome" required error={errors.outcome}>
                  {() => (
                    <RadioGroup value={draft.outcome ?? ""} onValueChange={(v) => update({ outcome: v as DecisionOutcome })} aria-label="Outcome" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {DECISION_OUTCOMES.map((outcome) => (
                        <RadioCard key={outcome} value={outcome} title={OUTCOME_LABELS[outcome]} description={OUTCOME_DESCRIPTIONS[outcome]} data-testid={`intake-decision-outcome-${outcome}`} />
                      ))}
                    </RadioGroup>
                  )}
                </FieldShell>
                {differs ? (
                  <Callout tone="warning" icon={<TriangleAlert />} title="This differs from the AI recommendation" data-testid="intake-override-warning">
                    The AI suggested {band ? BAND_LABELS[band].toLowerCase() : "a different outcome"}. Deciding otherwise is your call to make; say why in the rationale. The difference is recorded with the decision.
                  </Callout>
                ) : null}
                <FieldShell label="Rationale" required error={errors.rationale} description="What in the interview supports this decision. Stored with your name and visible to other reviewers.">
                  {(control) => (
                    <Textarea
                      {...control}
                      data-testid="intake-decision-rationale"
                      rows={4}
                      autoResize
                      maxRows={14}
                      maxLength={RATIONALE_MAX}
                      showCount
                      value={draft.rationale}
                      onChange={(e) => update({ rationale: e.target.value })}
                    />
                  )}
                </FieldShell>
                {draft.outcome === "request_evidence" ? (
                  <FieldShell label="What you need from the candidate" required error={errors.requested_evidence}>
                    {(control) => (
                      <ListEditor
                        id={control.id}
                        aria-describedby={control["aria-describedby"]}
                        value={draft.requestedEvidence}
                        onChange={(requestedEvidence) => update({ requestedEvidence })}
                        placeholder="e.g. A link to the project repository"
                        addLabel="Add another request"
                        maxItems={REQUESTED_EVIDENCE_MAX}
                      />
                    )}
                  </FieldShell>
                ) : null}

                {openItems.length ? (
                  <div className="grid grid-cols-1 gap-3">
                    <div className="grid grid-cols-1 gap-0.5">
                      <span className="text-[13px] font-medium text-fg">Review items</span>
                      <span className="text-xs text-fg-muted">
                        Resolve each one with this decision. {unresolved.length ? `${pluralize(unresolvedGroups, "item")} still open; you can decide anyway.` : "All resolved."}
                      </span>
                    </div>
                    <ul className="grid grid-cols-1 divide-y divide-line-subtle rounded-md border border-line">
                      {openGroups.map((group) => {
                        const ids = group.items.map((i) => i.id);
                        const value = draft.resolutions[ids[0]] ?? { resolution: null, note: "" };
                        return (
                          <li key={group.key} className="grid grid-cols-1 gap-2.5 px-3.5 py-3">
                            <GroupTitle group={group} />
                            {group.items.length > 1 ? <GroupDetails group={group} /> : null}
                            <RadioGroup
                              value={value.resolution ?? ""}
                              onValueChange={(v) => setResolution(ids, { resolution: v as ReviewResolution })}
                              aria-label={`Resolution for ${group.title}`}
                              data-testid={`intake-review-item-resolution-${group.key}`}
                              className="flex flex-wrap gap-x-5 gap-y-2"
                            >
                              {(["confirmed", "dismissed", "follow_up"] as const).map((r) => (
                                <RadioGroupItem key={r} value={r} label={RESOLUTION_LABELS[r]} />
                              ))}
                            </RadioGroup>
                            {value.resolution ? (
                              <Input
                                size="sm"
                                maxLength={NOTE_MAX}
                                placeholder="Note (optional)"
                                aria-label={`Note on ${group.title}`}
                                aria-invalid={errors.notes?.[`item:${ids[0]}`] ? true : undefined}
                                value={value.note}
                                onChange={(e) => setResolution(ids, { note: e.target.value })}
                              />
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}

                <div className="grid grid-cols-1 gap-3">
                  <button type="button" aria-expanded={notesOpen} onClick={() => setNotesOpen((v) => !v)} className="inline-flex w-fit items-center gap-1 rounded-xs text-[13px] font-medium text-fg-muted hover:text-fg">
                    {notesOpen ? <ChevronDown className="size-4" aria-hidden="true" /> : <ChevronRight className="size-4" aria-hidden="true" />}
                    Notes per competency (optional)
                  </button>
                  {notesOpen ? (
                    <div className="grid grid-cols-1 gap-3">
                      <p className="text-xs text-fg-muted">Your notes sit next to the AI scores; they never change them.</p>
                      {COMPETENCY_KEYS.map((key) => (
                        <FieldShell key={key} label={competencyLabel(key)} error={errors.notes?.[`competency:${key}`]}>
                          {(control) => (
                            <Textarea
                              {...control}
                              rows={1}
                              autoResize
                              maxRows={6}
                              maxLength={NOTE_MAX}
                              value={draft.competencyNotes[key] ?? ""}
                              onChange={(e) => update({ competencyNotes: { ...draft.competencyNotes, [key]: e.target.value } })}
                            />
                          )}
                        </FieldShell>
                      ))}
                    </div>
                  ) : null}
                </div>

                {errors.form ? (
                  <Callout tone="danger" role="alert">
                    {errors.form}
                  </Callout>
                ) : null}
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-subtle pt-4">
                  <p className="flex items-center gap-1.5 text-xs text-fg-muted">
                    <UserRound className="size-3.5" aria-hidden="true" />
                    Recorded with your name and the time.
                  </p>
                  <Button leftIcon={<Gavel />} onClick={() => void submit()} loading={record.isPending} loadingText="Recording…" data-testid="intake-decision-submit">
                    Record decision
                  </Button>
                </div>
              </fieldset>
            </CardContent>
          </Card>
        ) : (
          <Callout tone="neutral" icon={<History />} title="You can read decisions but not record them">
            Recording a decision needs reviewer access.
          </Callout>
        )}
      </div>
      <DecisionHistory decisions={detail.decisions} />
    </div>
  );
}
