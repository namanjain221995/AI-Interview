import { Eye } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { StepFooter } from "@/features/candidate/components/StepLayout";
import type { JourneyContext } from "@/features/candidate/hooks";
import { DEFAULT_INTEGRITY, DEFAULT_RECORDING, monitoringSummary } from "@/features/integrity/policy";

export const INTAKE_INSTRUCTIONS_TITLE = "How the intake interview works";
export const INTAKE_INSTRUCTIONS_LEDE = "It's a conversation, with two short exercises in the middle. Answer the way you would with a person, and take a moment whenever you need one.";

/**
 * Instructions for intake interviews (INTAKE_INTERVIEW.md §11.2): the flow, the
 * reading exercise (read aloud; later read silently for 30 seconds, then explain),
 * the speaking exercise (60 seconds to prepare, 2 minutes to speak), that "I don't
 * know" is fine, and that there's no feedback at the end.
 */
export function IntakeInstructions({ ctx }: { ctx: JourneyContext }) {
  const { interview } = ctx;
  const interviewer = interview.interviewerName;
  const integrity = { ...DEFAULT_INTEGRITY, ...(interview.policy.integrity ?? {}) };
  const recording = { ...DEFAULT_RECORDING, ...(interview.policy.recording ?? {}) };
  const monitored = monitoringSummary(integrity);

  const flow: Array<{ title: string; body: string }> = [
    { title: `${interviewer} introduces the interview`, body: "Then asks you to introduce yourself in about 90 seconds. Long answers aren't cut off." },
    { title: "Questions about your experience", body: "Built from your résumé and your details. Expect to be asked what you did yourself, and how." },
    {
      title: "A few standard questions",
      body: `${interviewer} confirms some answers from your details form instead of asking them again, and asks one short question in the language you know best.`,
    },
    {
      title: "The reading exercise",
      body: "First, read a short passage aloud from the screen, at your own pace (up to 3 minutes). Later a second passage appears for 30 seconds: read it silently, then explain it in your own words. It disappears after 30 seconds and doesn't come back.",
    },
    {
      title: "The speaking exercise",
      body: "A topic card with four points. You get 60 seconds to prepare, and nothing is recorded while you do. Then you speak for about 2 minutes, connecting all four points.",
    },
    { title: "How you learn, then your questions", body: "The interview ends with your questions about the program. The hiring team reviews everything afterwards." },
  ];

  return (
    <div data-testid="intake-instructions">
      <section aria-labelledby="flow-heading">
        <h2 id="flow-heading" className="text-heading text-fg">
          What to expect
        </h2>
        <ol className="mt-5 grid gap-0">
          {flow.map((item, index) => (
            <li key={item.title} className="relative flex gap-4 pb-6 last:pb-0">
              {index < flow.length - 1 ? <span aria-hidden="true" className="absolute top-8 bottom-1 left-[13px] w-px bg-line" /> : null}
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-surface text-[13px] font-semibold text-fg tabular-nums ring-1 ring-line-strong" aria-hidden="true">
                {index + 1}
              </span>
              <div className="min-w-0 pt-0.5">
                <p className="text-[15px] leading-6 font-medium text-fg">{item.title}</p>
                <p className="mt-0.5 max-w-xl text-sm leading-6 text-fg-muted">{item.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="mt-10 grid grid-cols-1 gap-8 sm:grid-cols-2" aria-label="Good to know">
        <div>
          <h2 className="text-heading text-fg">Good to know</h2>
          <ul className="mt-4 grid gap-3 text-sm leading-6 text-fg-muted">
            <li>Saying "I don't know" is fine.</li>
            <li>Take a moment to think whenever you need one; you can say "let me think".</li>
            <li>Ask {interviewer} to repeat a question, or press Repeat question.</li>
            <li>While you read, you can make the text larger or switch to a light page.</li>
            {recording.allow_typed_answers ? <li>You can type an answer instead of speaking, except in the reading exercise.</li> : null}
            <li>There's no feedback at the end: the hiring team reviews the interview and contacts you about next steps.</li>
          </ul>
        </div>
        {monitored.length ? (
          <div>
            <h2 className="flex items-center gap-2 text-heading text-fg">
              <Eye className="size-4 text-fg-muted" aria-hidden="true" />
              What's observed
            </h2>
            <ul className="mt-4 grid gap-2 text-sm leading-6 text-fg-muted">
              {monitored.map((item) => (
                <li key={item} className="flex gap-2.5">
                  <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-fg-subtle" aria-hidden="true" />
                  {item}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[13px] leading-5 text-fg-subtle">
              A browser can't lock your computer. These are noted as observations for a person to review, never decisions on their own.
            </p>
          </div>
        ) : null}
      </section>

      <StepFooter onBack={ctx.back}>
        <Button size="lg" onClick={ctx.next} data-testid="instructions-continue">
          Continue
        </Button>
      </StepFooter>
    </div>
  );
}
