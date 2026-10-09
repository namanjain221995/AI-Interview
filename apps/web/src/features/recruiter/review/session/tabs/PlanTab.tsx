import { CircleCheck, CircleDashed, CircleSlash, Hourglass, Scale, TriangleAlert } from "lucide-react";
import type { ComponentType, SVGProps } from "react";

import { DifficultyBadge } from "@/components/domain/DifficultyBadge";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatMinutes } from "@/lib/format";
import { CLAIM_STATUS_LABELS, labelFor, SEGMENT_KIND_LABELS } from "@/lib/labels";
import type { ClaimStatus, ContradictionFinding } from "@/lib/types";

import { CompetencyTag } from "../../components/primitives";
import { questionLabel } from "../../lib/questions";
import { useSessionContext } from "../context";

const CLAIM_STYLE: Record<ClaimStatus, { tone: BadgeTone; Icon: ComponentType<SVGProps<SVGSVGElement>> }> = {
  verified: { tone: "success", Icon: CircleCheck },
  partially_verified: { tone: "accent", Icon: Scale },
  contradicted: { tone: "warning", Icon: TriangleAlert },
  in_progress: { tone: "neutral", Icon: Hourglass },
  unverified: { tone: "outline", Icon: CircleDashed },
  not_applicable: { tone: "outline", Icon: CircleSlash },
};

const SEVERITY_TONE: Record<ContradictionFinding["severity"], BadgeTone> = { high: "warning", medium: "neutral", low: "outline" };

function PlanCard() {
  const { session, index } = useSessionContext();
  const plan = session.plan;
  if (!plan) {
    return (
      <Card>
        <CardHeader title="Interview plan" />
        <EmptyState size="sm" title="No plan recorded" description="The plan is prepared before the interview starts. It may still be generating." />
      </Card>
    );
  }
  const totalMinutes = plan.segments.reduce((sum, s) => sum + (s.time_budget_minutes || 0), 0);
  return (
    <Card>
      <CardHeader title="Interview plan" description="Prepared from the job, the round and the resume before the interview. The interviewer adapts it as answers come in." />
      <CardContent className="grid grid-cols-1 gap-6">
        {plan.opening_focus ? (
          <p className="text-sm leading-6 text-fg">
            <span className="font-medium">Opening focus: </span>
            {plan.opening_focus}
          </p>
        ) : null}

        {plan.segments.length ? (
          <section className="grid grid-cols-1 gap-3" aria-labelledby="plan-segments">
            <h3 id="plan-segments" className="text-sm font-semibold text-fg">
              Segments <span className="font-normal text-fg-muted">({formatMinutes(totalMinutes * 60)} planned)</span>
            </h3>
            <div className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
              {plan.segments.map((s, i) => (
                <span key={s.key} className={i % 2 ? "h-full bg-fg-subtle/60" : "h-full bg-fg-muted/70"} style={{ flexGrow: Math.max(1, s.time_budget_minutes), flexBasis: 0 }} />
              ))}
            </div>
            <ol className="grid grid-cols-1 gap-2">
              {plan.segments.map((s, i) => (
                <li key={s.key} className="grid grid-cols-1 gap-1 rounded-md border border-line-subtle px-3.5 py-2.5 sm:grid-cols-[1.5rem_minmax(0,12rem)_minmax(0,1fr)_auto] sm:items-start sm:gap-3">
                  <span className="text-xs font-semibold text-fg-muted tabular-nums">{i + 1}</span>
                  <span className="text-[13px] font-medium text-fg">{labelFor(SEGMENT_KIND_LABELS, s.kind)}</span>
                  <span className="grid grid-cols-1 gap-1">
                    {s.goals.length ? <span className="text-[13px] text-fg-muted">{s.goals.join("; ")}</span> : null}
                    {s.competency_keys.length ? (
                      <span className="flex flex-wrap gap-x-3 gap-y-1">
                        {s.competency_keys.map((k) => (
                          <CompetencyTag key={k} name={index.nameOf(k)} color={index.colorOf(k)} size="sm" />
                        ))}
                      </span>
                    ) : null}
                  </span>
                  <span className="text-xs text-fg-muted tabular-nums">{s.time_budget_minutes} min</span>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {plan.competency_targets.length ? (
          <section className="grid grid-cols-1 gap-3" aria-labelledby="plan-targets">
            <h3 id="plan-targets" className="text-sm font-semibold text-fg">
              Competency targets
            </h3>
            <ul className="grid grid-cols-1 gap-2">
              {[...plan.competency_targets]
                .sort((a, b) => b.priority - a.priority)
                .map((t) => (
                  <li key={t.key} className="grid grid-cols-1 gap-1.5 rounded-md border border-line-subtle px-3.5 py-2.5">
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <CompetencyTag name={index.nameOf(t.key)} color={index.colorOf(t.key)} className="font-medium text-fg" />
                      <span className="text-xs text-fg-muted">Priority {t.priority}</span>
                      <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
                        Starts at <DifficultyBadge value={t.starting_difficulty} size="sm" />
                      </span>
                    </span>
                    {t.focus_areas.length ? (
                      <span className="flex flex-wrap gap-1.5">
                        {t.focus_areas.map((f) => (
                          <Badge key={f} tone="outline" size="sm">
                            {f}
                          </Badge>
                        ))}
                      </span>
                    ) : null}
                    {t.rationale ? <span className="text-[13px] text-fg-muted">{t.rationale}</span> : null}
                  </li>
                ))}
            </ul>
          </section>
        ) : null}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {plan.risks_to_probe.length ? (
            <section className="grid grid-cols-1 content-start gap-1.5" aria-labelledby="plan-risks">
              <h3 id="plan-risks" className="text-sm font-semibold text-fg">
                Areas to probe
              </h3>
              <ul className="grid grid-cols-1 list-disc gap-0.5 pl-4 text-[13px] text-fg-muted">
                {plan.risks_to_probe.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <section className="grid grid-cols-1 content-start gap-1.5" aria-labelledby="plan-difficulty">
            <h3 id="plan-difficulty" className="text-sm font-semibold text-fg">
              Difficulty strategy
            </h3>
            <p className="flex flex-wrap items-center gap-2 text-[13px] text-fg-muted">
              Starts at <DifficultyBadge value={plan.initial_difficulty} size="sm" />
            </p>
            {plan.difficulty_strategy ? <p className="text-[13px] text-fg-muted">{plan.difficulty_strategy}</p> : null}
            {plan.coding_plan ? (
              <p className="text-[13px] text-fg-muted">
                <span className="font-medium text-fg">Coding: </span>
                {plan.coding_plan}
              </p>
            ) : null}
          </section>
        </div>
      </CardContent>
    </Card>
  );
}

function ClaimsCard() {
  const { claims, questions, nav } = useSessionContext();
  const verification = new Map<string, { note: string; questionId: string }>();
  for (const q of questions) {
    for (const a of q.answers) {
      for (const v of a.evaluation?.evaluation?.claim_verifications ?? []) verification.set(v.claim_id, { note: v.note, questionId: q.id });
    }
  }
  const discussedIn = new Map<string, string>();
  for (const q of questions) if (q.claim_id && !discussedIn.has(q.claim_id)) discussedIn.set(q.claim_id, q.id);
  const order: ClaimStatus[] = ["contradicted", "partially_verified", "verified", "in_progress", "unverified", "not_applicable"];
  const sorted = [...claims].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const supported = claims.filter((c) => c.status === "verified" || c.status === "partially_verified").length;

  return (
    <Card>
      <CardHeader
        title="Resume claims"
        description={claims.length ? `${supported} of ${claims.length} supported by the interview so far.` : "Claims picked from the resume for the interviewer to explore."}
      />
      <CardContent>
        {sorted.length === 0 ? (
          <p className="py-4 text-sm text-fg-muted">No resume claims were selected for this interview.</p>
        ) : (
          <ul className="grid grid-cols-1 gap-2">
            {sorted.map((claim) => {
              const style = CLAIM_STYLE[claim.status] ?? CLAIM_STYLE.unverified;
              const check = verification.get(claim.id);
              const questionId = check?.questionId ?? discussedIn.get(claim.id);
              const question = questionId ? questions.find((q) => q.id === questionId) : undefined;
              return (
                <li key={claim.id} className="grid grid-cols-1 gap-1.5 rounded-md border border-line-subtle px-3.5 py-3">
                  <span className="flex flex-wrap items-start justify-between gap-2">
                    <span className="text-sm text-fg">{claim.claim}</span>
                    <Badge tone={style.tone} size="sm" icon={<style.Icon aria-hidden="true" />}>
                      {labelFor(CLAIM_STATUS_LABELS, claim.status)}
                    </Badge>
                  </span>
                  {check?.note ? <span className="text-[13px] text-fg-muted">{check.note}</span> : null}
                  {question ? (
                    <button type="button" className="w-fit rounded-xs text-xs font-medium text-accent hover:underline" onClick={() => nav.go({ tab: "questions", question: question.id })}>
                      Discussed in {questionLabel(question)}
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ContradictionsCard() {
  const { questions, nav, session } = useSessionContext();
  const items: Array<ContradictionFinding & { questionId: string | null }> = [];
  for (const q of questions) for (const a of q.answers) for (const c of a.evaluation?.evaluation?.contradictions ?? []) items.push({ ...c, questionId: q.id });
  // The live state may track contradictions the evaluations above don't carry (e.g. resolved while verifying).
  for (const c of session.contradictions ?? []) {
    const text = c.description?.trim();
    if (!text || items.some((i) => text.startsWith(i.statement))) continue;
    const severity = c.severity === "high" || c.severity === "medium" ? c.severity : "low";
    items.push({ statement: text, conflicts_with: "", severity, questionId: null });
  }
  return (
    <Card>
      <CardHeader title="Statements to reconcile" description="Places where two statements don't line up. Often a misunderstanding; worth a respectful follow-up." />
      <CardContent>
        {items.length === 0 ? (
          <p className="py-4 text-sm text-fg-muted">No contradictions were found.</p>
        ) : (
          <ul className="grid grid-cols-1 gap-2">
            {items.map((c, i) => {
              const q = questions.find((x) => x.id === c.questionId);
              return (
                <li key={`${c.questionId}-${i}`} className="grid grid-cols-1 gap-2 rounded-md border border-line-subtle px-3.5 py-3">
                  <span className="flex items-center justify-between gap-2">
                    <Badge tone={SEVERITY_TONE[c.severity]} size="sm">
                      {c.severity === "high" ? "Significant" : c.severity === "medium" ? "Moderate" : "Minor"}
                    </Badge>
                    {q ? (
                      <button type="button" className="rounded-xs text-xs font-medium text-accent hover:underline" onClick={() => nav.go({ tab: "questions", question: q.id })}>
                        From {questionLabel(q)}
                      </button>
                    ) : null}
                  </span>
                  {c.conflicts_with ? (
                    <span className="grid grid-cols-1 gap-1 text-[13px] sm:grid-cols-2 sm:gap-4">
                      <span>
                        <span className="block text-xs text-fg-muted">Said</span>
                        <span className="text-fg">{c.statement}</span>
                      </span>
                      <span>
                        <span className="block text-xs text-fg-muted">Doesn't match</span>
                        <span className="text-fg">{c.conflicts_with}</span>
                      </span>
                    </span>
                  ) : (
                    <span className="text-[13px] text-fg">{c.statement}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function PlanTab() {
  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
      <PlanCard />
      <div className="grid grid-cols-1 gap-6">
        <ClaimsCard />
        <ContradictionsCard />
      </div>
    </div>
  );
}
