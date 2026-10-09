import { FileText, Loader2 } from "lucide-react";
import { Link, useParams } from "react-router";

import { useCandidateChrome } from "@/components/layout/CandidateShell";
import { Button } from "@/components/ui/Button";
import { useDocumentTitle } from "@/lib/hooks";

import { NextRoundCard } from "@/features/links/components/NextRoundCard";

import { JourneyBoundary, usePortalInterview, usePortalMe } from "../hooks";
import { isReportReady, useCandidateReport, useReportMode, useSessionId } from "../reportAccess";

export default function CompletePage() {
  useDocumentTitle("Interview complete");
  const { interviewId = "" } = useParams();
  const interview = usePortalInterview(interviewId);
  const me = usePortalMe();
  const data = interview.data;
  useCandidateChrome({ orgName: data?.organizationName || null, orgLogoUrl: data?.organizationLogoUrl ?? null, subtitle: data?.job.title || null, hideStepper: true });
  const sessionId = useSessionId(interviewId, data);
  const { mode, pending: modePending } = useReportMode(data, sessionId);
  // No feedback is shared (the default): no report card, no polling, no promise.
  const sharesFeedback = Boolean(data) && !modePending && mode !== "never";
  const report = useCandidateReport(sharesFeedback ? sessionId : null, { poll: true, intervalMs: mode === "immediate" ? 5000 : 30_000 });
  const ready = isReportReady(report.data ?? undefined);
  const firstName = me.data?.firstName ?? (data?.candidateName ?? "").split(/\s+/)[0] ?? null;
  const org = data?.organizationName || "The hiring team";

  return (
    <div data-testid="complete-page">
      <JourneyBoundary query={interview}>
        <div>
          <h1 className="max-w-2xl text-[2rem] leading-[2.5rem] font-semibold tracking-[-0.022em] text-fg sm:text-display">
            {firstName ? `Thank you, ${firstName}.` : "Thank you."}
          </h1>
          <p className="mt-4 max-w-2xl text-[15px] leading-7 text-fg-muted">
            Your interview is complete and saved. {org} will review it and be in touch about next steps. You can close this window whenever you like.
          </p>

          <NextRoundCard poll className="mt-10" />

          {sharesFeedback ? (
            <section data-testid="feedback-card" className="mt-10 rounded-xl border border-line bg-surface p-5 sm:p-6" aria-labelledby="feedback-heading">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="max-w-lg">
                  <h2 id="feedback-heading" className="text-heading text-fg">
                    Your feedback report
                  </h2>
                  <p className="mt-1 text-sm leading-6 text-fg-muted">
                    {ready
                      ? "Your feedback is ready: your strengths, areas to develop and what to study next, based on what you said."
                      : mode === "immediate"
                        ? "We're preparing feedback on your strengths and areas to develop. This usually takes a few minutes; this page updates by itself."
                        : `Feedback is shared after ${org} has reviewed your interview. Come back to this page, or to your invitation link, to check.`}
                  </p>
                </div>
                {ready ? (
                  <Button asChild size="lg" leftIcon={<FileText />}>
                    <Link to={`/candidate/${interviewId}/report${sessionId ? `?session=${encodeURIComponent(sessionId)}` : ""}`} data-testid="report-link">
                      View your feedback
                    </Link>
                  </Button>
                ) : (
                  <span className="flex items-center gap-2 text-sm text-fg-muted" role="status">
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                    {mode === "immediate" ? "Preparing" : "Waiting for review"}
                  </span>
                )}
              </div>
            </section>
          ) : null}

          <section className="mt-10" aria-labelledby="next-heading">
            <h2 id="next-heading" className="text-heading text-fg">
              What happens next
            </h2>
            <ol className="mt-5 grid gap-5 sm:grid-cols-3">
              {[
                { title: "A person reviews your interview", body: `Someone at ${org} reads the transcript and the evidence behind every assessment.` },
                { title: "They make the decision", body: "The AI's assessment only assists. People decide who moves forward." },
                { title: "You hear back", body: `${org} contacts you directly about next steps, usually by email.` },
              ].map((step, i) => (
                <li key={step.title} className="grid content-start gap-2 border-t border-line pt-4">
                  <span className="text-[13px] font-semibold text-fg-muted tabular-nums">{i + 1}</span>
                  <p className="text-[15px] leading-6 font-medium text-fg">{step.title}</p>
                  <p className="text-sm leading-6 text-fg-muted">{step.body}</p>
                </li>
              ))}
            </ol>
          </section>

          <p className="mt-10 border-t border-line pt-6 text-[13px] leading-5 text-fg-muted">
            Your recordings, transcript and any images are kept only for the periods described when you gave consent, then deleted. Questions about your data? Contact {org}.
          </p>
        </div>
      </JourneyBoundary>
    </div>
  );
}
