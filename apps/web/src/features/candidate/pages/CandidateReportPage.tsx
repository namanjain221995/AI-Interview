import { ArrowLeft, BookOpen, Download, Printer } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { useCandidateChrome } from "@/components/layout/CandidateShell";
import { BarList } from "@/components/charts/BarList";
import { ScoreRing } from "@/components/domain/ScoreRing";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton, SkeletonText } from "@/components/ui/Skeleton";
import { formatDate } from "@/lib/format";
import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import { useDocumentTitle } from "@/lib/hooks";
import type { CodeLanguage } from "@/lib/types";

import type { CandidateReportView } from "../api";
import { JourneyBoundary, JourneyError, usePortalInterview } from "../hooks";
import { isReportReady, reportMode, useCandidateReport, useSessionId } from "../reportAccess";

function Section({ title, children, id }: { title: string; children: ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="border-t border-line pt-8 break-inside-avoid">
      <h2 id={id} className="text-heading text-fg">
        {title}
      </h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

export default function CandidateReportPage() {
  useDocumentTitle("Your interview feedback");
  const { interviewId = "" } = useParams();
  const interview = usePortalInterview(interviewId);
  const data = interview.data;
  useCandidateChrome({ orgName: data?.organizationName || null, subtitle: data?.job.title || null, hideStepper: true, width: "wide" });
  const sessionId = useSessionId(interviewId, data);
  const report = useCandidateReport(sessionId, { poll: false });

  return (
    <JourneyBoundary query={interview}>
      {report.isPending && sessionId ? (
        <div className="grid gap-6" aria-busy="true" aria-label="Loading your feedback">
          <Skeleton className="h-10 w-2/3" />
          <SkeletonText lines={4} />
          <Skeleton className="h-56 w-full" />
        </div>
      ) : report.isError ? (
        <JourneyError error={report.error} onRetry={() => void report.refetch()} />
      ) : !report.data || !isReportReady(report.data) ? (
        <EmptyState
          bordered
          icon={<BookOpen />}
          title={reportMode(data) === "never" ? "Feedback isn't shared for this interview" : "Your feedback isn't available yet"}
          description={
            reportMode(data) === "immediate"
              ? "It's still being prepared. This usually takes a few minutes after the interview ends."
              : reportMode(data) === "never"
                ? `${data?.organizationName || "The hiring team"} doesn't share individual interview feedback.`
                : `It's shared after ${data?.organizationName || "the hiring team"} reviews your interview.`
          }
          actions={
            <Button variant="outline" asChild leftIcon={<ArrowLeft />}>
              <Link to={`/candidate/${interviewId}/complete`}>Back</Link>
            </Button>
          }
        />
      ) : (
        <Report report={report.data} jobTitle={data?.job.title ?? null} interviewId={interviewId} />
      )}
    </JourneyBoundary>
  );
}

function Report({ report, jobTitle, interviewId }: { report: CandidateReportView; jobTitle: string | null; interviewId: string }) {
  const n = report.narrative;
  const strengths = n.technical_strengths ?? [];
  const improve = n.areas_to_improve ?? [];
  const study = n.recommended_study_areas ?? [];
  const strong = n.strong_answers ?? [];
  const incomplete = n.incomplete_answers ?? [];
  const nextSteps = n.next_steps ?? [];
  const topics = [...report.topics].sort((a, b) => b.score - a.score);
  const questionLabel = (id: string, index: number) => report.questionText[id] ?? `Question ${index + 1}`;

  return (
    <article data-testid="candidate-report" className="grid gap-10">
      <header className="grid gap-8 md:grid-cols-[minmax(0,1fr)_auto] md:items-start">
        <div className="max-w-2xl">
          <p className="text-[15px] text-fg-muted">
            {report.jobTitle ?? jobTitle ?? "Interview"}
            {report.generatedAt ? `, ${formatDate(report.generatedAt)}` : ""}
          </p>
          <h1 className="mt-1 text-[2rem] leading-[2.5rem] font-semibold tracking-[-0.022em] text-fg">Your interview feedback</h1>
          {n.overall_summary ? <p className="mt-4 text-[15px] leading-7 text-fg">{n.overall_summary}</p> : null}
          <p className="mt-4 text-[13px] leading-5 text-fg-muted">
            {report.note ??
              "This feedback is based only on what you said and wrote during the interview, and is meant to help you grow. A person at the hiring organization makes the hiring decision."}
          </p>
        </div>
        <div className="flex items-center gap-4 rounded-xl border border-line bg-surface px-5 py-4 md:flex-col md:items-center md:px-6 md:py-5">
          <ScoreRing value={report.overallScore} max={100} size="lg" ariaLabel={`Overall score ${report.overallScore !== null ? Math.round(report.overallScore) : "not available"} out of 100`} />
          <div className="md:text-center">
            <p className="text-sm font-medium text-fg">Overall</p>
            <p data-testid="report-overall-score" className="text-[13px] text-fg-muted tabular-nums">
              {report.overallScore !== null ? `${Math.round(report.overallScore)} out of 100` : "Not scored"}
            </p>
          </div>
        </div>
      </header>

      <div className="flex flex-wrap gap-2 print:hidden">
        <Button variant="outline" size="sm" leftIcon={<Printer />} onClick={() => window.print()}>
          Print or save as PDF
        </Button>
        {report.artifactUrl && report.artifactUrl.startsWith("/") ? (
          <Button variant="outline" size="sm" asChild leftIcon={<Download />}>
            <a href={report.artifactUrl} target="_blank" rel="noopener noreferrer">
              Download a copy
            </a>
          </Button>
        ) : null}
        <Button variant="ghost" size="sm" asChild leftIcon={<ArrowLeft />}>
          <Link to={`/candidate/${interviewId}/complete`}>Back</Link>
        </Button>
      </div>

      {strengths.length || improve.length ? (
        <div className="grid gap-10 lg:grid-cols-2">
          {strengths.length ? (
            <Section title="What went well" id="strengths">
              <ul className="grid gap-4">
                {strengths.map((s) => (
                  <li key={s.title}>
                    <p className="text-[15px] leading-6 font-medium text-fg">{s.title}</p>
                    <p className="mt-0.5 text-sm leading-6 text-fg-muted">{s.detail}</p>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
          {improve.length ? (
            <Section title="Areas to develop" id="improve">
              <ul className="grid gap-5">
                {improve.map((a) => (
                  <li key={a.title}>
                    <p className="text-[15px] leading-6 font-medium text-fg">{a.title}</p>
                    <p className="mt-0.5 text-sm leading-6 text-fg-muted">{a.detail}</p>
                    {a.learning_guidance ? (
                      <p className="mt-2 border-l-2 border-accent-line pl-3 text-sm leading-6 text-fg">
                        <span className="font-medium">How to build it: </span>
                        {a.learning_guidance}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
        </div>
      ) : null}

      {topics.length ? (
        <Section title="Scores by topic" id="topics">
          <p className="-mt-2 mb-5 max-w-2xl text-sm text-fg-muted">Each topic is scored from 0 to 10, from the evidence in your answers. Topics with little evidence carry less weight.</p>
          <BarList
            aria-label="Scores by topic"
            className="max-w-2xl"
            items={topics.map((t) => ({ key: t.key, label: t.label, value: t.score, hint: t.unanswered.length ? `Not covered yet: ${t.unanswered.join(", ")}` : undefined }))}
            max={10}
            valueFormatter={(v) => `${v.toFixed(1)} / 10`}
          />
        </Section>
      ) : null}

      {report.coding.length || n.coding_feedback ? (
        <Section title="Coding exercise" id="coding">
          {n.coding_feedback ? <p className="max-w-3xl text-sm leading-6 text-fg">{n.coding_feedback}</p> : null}
          <ul className="mt-4 grid gap-4 md:grid-cols-2">
            {report.coding.map((c, i) => (
              <li key={`${c.title}-${i}`} className="rounded-lg border border-line bg-surface p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-[15px] font-medium text-fg">{c.title}</p>
                  {c.language ? <Badge tone="outline">{CODE_LANGUAGE_LABELS[c.language as CodeLanguage] ?? c.language}</Badge> : null}
                </div>
                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
                  {c.testsTotal !== null ? (
                    <div>
                      <dt className="text-[13px] text-fg-muted">Tests passed</dt>
                      <dd className="font-medium text-fg tabular-nums">
                        {c.testsPassed ?? 0} of {c.testsTotal}
                      </dd>
                    </div>
                  ) : null}
                  {c.hintsUsed !== null ? (
                    <div>
                      <dt className="text-[13px] text-fg-muted">Hints used</dt>
                      <dd className="font-medium text-fg tabular-nums">{c.hintsUsed}</dd>
                    </div>
                  ) : null}
                  {c.timeComplexity ? (
                    <div>
                      <dt className="text-[13px] text-fg-muted">Time complexity</dt>
                      <dd className="font-mono text-[13px] text-fg">{c.timeComplexity}</dd>
                    </div>
                  ) : null}
                </dl>
                {c.summary ? <p className="mt-3 text-sm leading-6 text-fg-muted">{c.summary}</p> : null}
                {c.improvements.length ? (
                  <ul className="mt-2 list-disc pl-5 text-sm leading-6 text-fg-muted">
                    {c.improvements.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {n.problem_solving_feedback || n.communication_feedback ? (
        <div className="grid gap-10 lg:grid-cols-2">
          {n.problem_solving_feedback ? (
            <Section title="Problem solving" id="problem-solving">
              <p className="text-sm leading-6 text-fg">{n.problem_solving_feedback}</p>
            </Section>
          ) : null}
          {n.communication_feedback ? (
            <Section title="Communication" id="communication">
              <p className="text-sm leading-6 text-fg">{n.communication_feedback}</p>
            </Section>
          ) : null}
        </div>
      ) : null}

      {strong.length || incomplete.length ? (
        <div className="grid gap-10 lg:grid-cols-2">
          {strong.length ? (
            <Section title="Your strongest answers" id="strong">
              <ul className="grid gap-4">
                {strong.map((h, i) => (
                  <li key={h.question_id + i}>
                    <p className="text-sm font-medium text-fg">{questionLabel(h.question_id, i)}</p>
                    <p className="mt-0.5 text-sm leading-6 text-fg-muted">{h.note}</p>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
          {incomplete.length ? (
            <Section title="Answers that could go further" id="incomplete">
              <ul className="grid gap-4">
                {incomplete.map((h, i) => (
                  <li key={h.question_id + i}>
                    <p className="text-sm font-medium text-fg">{questionLabel(h.question_id, i)}</p>
                    <p className="mt-0.5 text-sm leading-6 text-fg-muted">{h.note}</p>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
        </div>
      ) : null}

      {report.questions.some((q) => q.summary) ? (
        <Section title="Question by question" id="questions">
          <ol className="grid max-w-3xl gap-2">
            {report.questions
              .filter((q) => q.summary)
              .map((q, i) => (
                <li key={q.id}>
                  <details className="group rounded-lg border border-line bg-surface px-4 py-3 open:pb-4">
                    <summary className="flex cursor-pointer list-none items-start gap-3 text-sm font-medium text-fg">
                      <span className="w-5 shrink-0 text-fg-subtle tabular-nums">{q.sequence ?? i + 1}</span>
                      <span className="flex-1">{q.followUp ? <span className="mr-1.5 text-fg-muted">Follow-up:</span> : null}{q.text}</span>
                    </summary>
                    <div className="mt-2 pl-8 text-sm leading-6 text-fg-muted">
                      <p>{q.summary}</p>
                      {q.missing.length ? <p className="mt-1.5 text-fg">Worth adding next time: {q.missing.join("; ")}.</p> : null}
                    </div>
                  </details>
                </li>
              ))}
          </ol>
        </Section>
      ) : null}

      {study.length ? (
        <Section title="What to study next" id="study">
          <ul className="grid gap-5 md:grid-cols-2">
            {study.map((s) => (
              <li key={s.topic} className="rounded-lg border border-line bg-surface p-4">
                <p className="text-[15px] font-medium text-fg">{s.topic}</p>
                <p className="mt-1 text-sm leading-6 text-fg-muted">{s.why}</p>
                {s.next_steps?.length ? (
                  <ul className="mt-2 list-disc pl-5 text-sm leading-6 text-fg">
                    {s.next_steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {nextSteps.length ? (
        <Section title="Next steps" id="next">
          <ol className="grid max-w-2xl list-decimal gap-2 pl-5 text-sm leading-6 text-fg">
            {nextSteps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </Section>
      ) : null}
    </article>
  );
}
