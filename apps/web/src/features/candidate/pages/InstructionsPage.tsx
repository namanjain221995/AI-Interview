import { Eye } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { CODE_LANGUAGE_LABELS } from "@/lib/labels";
import { useDocumentTitle } from "@/lib/hooks";
import { formatList } from "@/lib/format";

import { DEFAULT_INTEGRITY, DEFAULT_RECORDING, monitoringSummary } from "@/features/integrity/policy";
import { INTAKE_INSTRUCTIONS_LEDE, INTAKE_INSTRUCTIONS_TITLE, IntakeInstructions } from "@/features/intake/candidate/components/IntakeInstructions";
import { isIntakeInterview } from "@/features/intake/candidate/journey";

import { StepFooter, StepHeader } from "../components/StepLayout";
import { JourneyBoundary, useJourney, type JourneyContext } from "../hooks";
import { selectedRound } from "../round";

export default function InstructionsPage() {
  useDocumentTitle("How the interview works");
  const { query, ctx } = useJourney("instructions");
  const intake = ctx ? isIntakeInterview(ctx.interview) : false;
  return (
    <div>
      {intake ? (
        <StepHeader title={INTAKE_INSTRUCTIONS_TITLE} lede={INTAKE_INSTRUCTIONS_LEDE} />
      ) : (
        <StepHeader title="How the interview works" lede={ctx ? (selectedRound(ctx.interview)?.instructions ?? DEFAULT_LEDE) : DEFAULT_LEDE} />
      )}
      <div className="mt-10">
        <JourneyBoundary query={query}>{ctx ? intake ? <IntakeInstructions ctx={ctx} /> : <Instructions ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

const DEFAULT_LEDE = "Answer naturally, as you would with a human interviewer. Take a moment to think when you need it.";

function Instructions({ ctx }: { ctx: JourneyContext }) {
  const { interview } = ctx;
  const round = selectedRound(interview);
  const interviewer = interview.interviewerName;
  const integrity = { ...DEFAULT_INTEGRITY, ...(interview.policy.integrity ?? {}) };
  const recording = { ...DEFAULT_RECORDING, ...(interview.policy.recording ?? {}) };
  const monitored = monitoringSummary(integrity);
  const languages = round?.codingLanguages.map((l) => CODE_LANGUAGE_LABELS[l] ?? l) ?? [];

  const flow: Array<{ title: string; body: string }> = [
    { title: `${interviewer} introduces the interview`, body: "Then asks the first question. Questions are spoken aloud and shown as text." },
    {
      title: "Answer out loud, in your own words",
      body: "When you've finished, simply pause. You can also press Done answering. There's no need to rush: say \"let me think\" and the interviewer will wait.",
    },
    {
      title: "Questions adapt to your answers",
      body: "Follow-ups build on what you said and on your résumé. Expect to be asked for specifics such as numbers, trade-offs and what you'd do differently.",
    },
  ];
  if (round?.codingEnabled) {
    flow.push({
      title: "A coding exercise opens in the browser",
      body: `${round.codingMinutes ? `About ${round.codingMinutes} minutes, in` : "In"} ${languages.length ? formatList(languages) : "the language of your choice"}. You can run the visible tests, ask for up to three hints and think aloud while you work.`,
    });
  }
  flow.push({ title: "The interview wraps up", body: "You'll see what happens next. The results go to a person at the hiring organization for review." });

  return (
    <div>
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
              <div className="pt-0.5">
                <p className="text-[15px] leading-6 font-medium text-fg">{item.title}</p>
                <p className="mt-0.5 max-w-xl text-sm leading-6 text-fg-muted">{item.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="mt-10 grid gap-8 sm:grid-cols-2" aria-label="Good to know">
        <div>
          <h2 className="text-heading text-fg">Good to know</h2>
          <ul className="mt-4 grid gap-3 text-sm leading-6 text-fg-muted">
            <li>Ask {interviewer} to repeat or rephrase any question, or press Repeat question.</li>
            {recording.allow_typed_answers ? <li>You can type an answer instead of speaking whenever you prefer.</li> : null}
            {recording.show_live_subtitles ? <li>Live subtitles show what {interviewer} says and what we heard from you.</li> : null}
            <li>If your connection drops, reopen your invitation link. You'll continue where you left off.</li>
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
