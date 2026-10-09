import { FileText, Headphones, Laptop, TimerReset } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { IntakeOverview } from "@/features/intake/candidate/components/IntakeOverview";
import { isIntakeInterview } from "@/features/intake/candidate/journey";
import { useDocumentTitle } from "@/lib/hooks";

import { Fact, StepFooter } from "../components/StepLayout";
import { JourneyBoundary, useJourney, usePortalMe } from "../hooks";
import { selectedRound } from "../round";

function Need({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3.5">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-surface text-fg-muted ring-1 ring-line [&_svg]:size-4" aria-hidden="true">
        {icon}
      </span>
      <div>
        <p className="text-[15px] leading-6 font-medium text-fg">{title}</p>
        <p className="text-sm leading-6 text-fg-muted">{children}</p>
      </div>
    </li>
  );
}

export default function WelcomePage() {
  useDocumentTitle("Welcome");
  const { query, ctx } = useJourney("welcome", { width: "default" });

  return (
    <JourneyBoundary query={query}>
      {ctx ? <Welcome ctx={ctx} /> : null}
    </JourneyBoundary>
  );
}

function Welcome({ ctx }: { ctx: NonNullable<ReturnType<typeof useJourney>["ctx"]> }) {
  const { interview } = ctx;
  const me = usePortalMe();
  const round = selectedRound(interview);
  const firstName = me.data?.firstName ?? interview.candidateName?.split(/\s+/)[0] ?? null;
  const minutes = round?.durationMinutes ?? null;
  const interviewer = interview.interviewerName;

  // Intake interviews: seven parts, timed exercises, privacy and who decides.
  if (isIntakeInterview(interview)) return <IntakeOverview ctx={ctx} firstName={firstName} />;

  return (
    <div>
      <p className="text-[15px] text-fg-muted">{firstName ? `Hi ${firstName},` : "Hello,"}</p>
      <h1 className="mt-1 max-w-2xl text-[2rem] leading-[2.5rem] font-semibold tracking-[-0.022em] text-fg sm:text-display">
        Your interview for {interview.job.title || "this role"} is ready when you are.
      </h1>
      <p className="mt-4 max-w-2xl text-[15px] leading-7 text-fg-muted">
        You'll talk with {interviewer}, an AI interviewer{minutes ? `, for about ${minutes} minutes` : ""}. It's a conversation: answer the way you would with a
        person, and take a moment to think whenever you need one. {interview.organizationName || "The hiring team"} reviews every interview, and a person makes
        every hiring decision.
      </p>

      <dl className="mt-9 grid grid-cols-2 gap-x-8 gap-y-5 border-y border-line py-6 sm:grid-cols-4">
        <Fact label="Role">{interview.job.title || "—"}</Fact>
        <Fact label="Interview">{round?.name ?? (interview.allowRoundSelection ? "You choose" : "—")}</Fact>
        <Fact label="Length">{minutes ? `About ${minutes} min` : "—"}</Fact>
        <Fact label="Interviewer">{interviewer}</Fact>
      </dl>

      <section className="mt-10" aria-labelledby="needs-heading">
        <h2 id="needs-heading" className="text-heading text-fg">
          Before you start
        </h2>
        <ul className="mt-5 grid gap-5 sm:grid-cols-2">
          <Need icon={<TimerReset />} title={minutes ? `${minutes + 10} uninterrupted minutes` : "Uninterrupted time"}>
            Pick a time when you won't be disturbed. If your connection drops, you can rejoin and continue.
          </Need>
          <Need icon={<Laptop />} title="A computer with a camera and microphone">
            An up-to-date Chrome, Edge, Firefox or Safari. We'll check everything before you begin.
          </Need>
          <Need icon={<Headphones />} title="A quiet, private room">
            Headphones help, but aren't required. Background noise can make your answers harder to transcribe.
          </Need>
          <Need icon={<FileText />} title="Your résumé">
            You'll upload it in a few steps. The interviewer uses it to ask about your real experience.
          </Need>
        </ul>
      </section>

      <StepFooter>
        <Button size="lg" onClick={ctx.next} data-testid="welcome-continue">
          Continue
        </Button>
      </StepFooter>
    </div>
  );
}
