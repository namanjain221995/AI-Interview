import { useQuery } from "@tanstack/react-query";
import { Camera, Expand, FileText, Headphones, Mic, Timer, TimerReset } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { getDisclosure } from "@/features/candidate/api";
import { StepFooter } from "@/features/candidate/components/StepLayout";
import type { JourneyContext } from "@/features/candidate/hooks";
import { DEFAULT_INTEGRITY } from "@/features/integrity/policy";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";

import { getPortalIntake, intakeKeys, normalizePortalIntake } from "../api";
import type { IntakePartView } from "../types";

/** What happens in each part, in the candidate's words (never how it is assessed). */
export const PART_DESCRIPTIONS: Record<string, string> = {
  greeting: "Introduce yourself in about 90 seconds, then talk about a project or role you're proud of.",
  resume: "Questions about the work on your résumé: what you did yourself, and how.",
  mandatory: "A few standard questions about the role you want, the language you know best and the training.",
  reading: "Read a short passage aloud. Then read another one silently for 30 seconds and explain it in your own words.",
  topic: "A topic card with four points: 60 seconds to prepare, then about 2 minutes to speak.",
  learning: "How you go about learning something new.",
  close: "Your questions about the program.",
};

/** The two parts with on-screen timers. */
export const TIMED_PARTS: ReadonlySet<string> = new Set(["reading", "topic"]);

export function minutesLabel(minutes: IntakePartView["minutes"]): string | null {
  if (!minutes) return null;
  return minutes.min === minutes.max ? `about ${minutes.min} min` : `${minutes.min} to ${minutes.max} min`;
}

/**
 * The shape of the interview: seven segments sized by their usual length, the two
 * timed exercises in the accent colour. The room's top bar uses the same seven
 * segments, so the candidate recognises where they are.
 */
export function PartsStrip({ parts, className }: { parts: IntakePartView[]; className?: string }) {
  const weight = (p: IntakePartView) => (p.minutes ? (p.minutes.min + p.minutes.max) / 2 : 5);
  return (
    <div aria-hidden="true" data-testid="intake-parts-strip" className={cn("flex h-2 gap-0.5 overflow-hidden rounded-[3px]", className)}>
      {parts.map((part) => (
        <span key={part.key} className={cn("h-full", TIMED_PARTS.has(part.key) ? "bg-accent" : "bg-fg-subtle/35")} style={{ flexGrow: weight(part), flexBasis: 0 }} />
      ))}
    </div>
  );
}

export function PartsList({ parts }: { parts: IntakePartView[] }) {
  return (
    <ol className="mt-5 grid grid-cols-1 divide-y divide-line">
      {parts.map((part) => (
        <li key={part.key} data-part={part.key} className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-3 py-3.5 sm:grid-cols-[2rem_minmax(0,1fr)_auto] sm:gap-x-4">
          <span className="pt-px text-[13px] leading-6 font-semibold text-fg-muted tabular-nums">{part.number}</span>
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[15px] leading-6 font-medium text-fg">
              {part.title}
              {TIMED_PARTS.has(part.key) ? (
                <span className="inline-flex items-center gap-1 text-xs font-medium text-accent-fg">
                  <Timer className="size-3.5" aria-hidden="true" />
                  Timed
                </span>
              ) : null}
            </p>
            {PART_DESCRIPTIONS[part.key] ? <p className="mt-0.5 max-w-xl text-sm leading-6 text-fg-muted">{PART_DESCRIPTIONS[part.key]}</p> : null}
            {minutesLabel(part.minutes) ? <p className="mt-0.5 text-[13px] leading-5 text-fg-subtle tabular-nums sm:hidden">{minutesLabel(part.minutes)}</p> : null}
          </div>
          <span className="hidden pt-px text-[13px] leading-6 whitespace-nowrap text-fg-muted tabular-nums sm:block">{minutesLabel(part.minutes)}</span>
        </li>
      ))}
    </ol>
  );
}

function Need({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3.5">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center text-fg-muted [&_svg]:size-[18px]" aria-hidden="true">
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-[15px] leading-6 font-medium text-fg">{title}</p>
        <p className="text-sm leading-6 text-fg-muted">{children}</p>
      </div>
    </li>
  );
}

const RECORDED_KEYS = ["microphone", "transcript", "audio_recording", "video_recording", "camera", "camera_frames", "screen_share"];

/**
 * Welcome page for intake interviews (INTAKE_INTERVIEW.md §11.2): what the
 * interview is, how long it takes, its seven parts, what to prepare, what is
 * recorded and who decides. Never rubrics, scores or how answers are weighed.
 */
export function IntakeOverview({ ctx, firstName }: { ctx: JourneyContext; firstName: string | null }) {
  const { interview } = ctx;
  const intakeQuery = useQuery({ queryKey: intakeKeys.portal(ctx.interviewId), queryFn: () => getPortalIntake(ctx.interviewId), meta: { errorToast: false }, refetchOnWindowFocus: false, retry: false });
  const disclosure = useQuery({ queryKey: queryKeys.portal.disclosure(ctx.interviewId), queryFn: () => getDisclosure(ctx.interviewId), meta: { errorToast: false }, retry: false });
  const intake = intakeQuery.data ?? normalizePortalIntake({}, ctx.interviewId);
  const org = intake.organizationName || interview.organizationName || "The hiring team";
  const program = intake.programName;
  const minutes = interview.intake?.estimatedMinutes ?? intake.estimatedMinutes;
  const interviewer = interview.interviewerName;
  const integrity = { ...DEFAULT_INTEGRITY, ...(interview.policy.integrity ?? {}) };
  const recorded = (disclosure.data?.items ?? []).filter((item) => RECORDED_KEYS.includes(item.key));

  return (
    <div data-testid="intake-overview">
      <p className="text-[15px] text-fg-muted">{firstName ? `Hi ${firstName},` : "Hello,"}</p>
      <h1 className="mt-1 max-w-2xl text-[2rem] leading-[2.5rem] font-semibold tracking-[-0.022em] text-balance text-fg sm:text-display">
        {program ? `Your intake interview for the ${program}` : "Your intake interview"}
      </h1>
      <p className="mt-4 max-w-2xl text-[15px] leading-7 text-fg-muted">
        You'll talk with {interviewer}, an AI interviewer, for about {minutes} minutes ({intake.durationRange.min} to {intake.durationRange.max}). It's a
        conversation in seven short parts, and it's fine to say when you don't know something. A person at {org} makes every decision. You won't see scores
        or a report.
      </p>

      <section className="mt-10" aria-labelledby="parts-heading">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 id="parts-heading" className="text-heading text-fg">
            Seven short parts
          </h2>
          <p className="text-[13px] text-fg-muted">About {minutes} minutes in all</p>
        </div>
        <PartsStrip parts={intake.parts} className="mt-4" />
        <PartsList parts={intake.parts} />
      </section>

      <section className="mt-10" aria-labelledby="needs-heading">
        <h2 id="needs-heading" className="text-heading text-fg">
          Before you start
        </h2>
        <ul className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2">
          <Need icon={<TimerReset />} title={`${intake.durationRange.max + 10} uninterrupted minutes`}>
            If your connection drops, open your invitation link again and you'll continue where you left off.
          </Need>
          <Need icon={<Mic />} title="A working microphone">
            You answer out loud, and you'll read a passage aloud. Headphones help but aren't required.
          </Need>
          <Need icon={<Headphones />} title="A quiet, private room">
            Background noise makes your answers harder to transcribe, especially while you read.
          </Need>
          {integrity.enabled && integrity.camera_required ? (
            <Need icon={<Camera />} title="Your camera on">
              Your camera stays on during the interview. We check it before you begin.
            </Need>
          ) : (
            <Need icon={<FileText />} title="Your résumé">
              You'll upload it next, then check what we read from it.
            </Need>
          )}
          {integrity.enabled && integrity.require_fullscreen ? (
            <Need icon={<Expand />} title="Full screen">
              The interview runs in full screen. If you leave it, that's noted for the hiring team to review.
            </Need>
          ) : null}
        </ul>
      </section>

      <section className="mt-10" aria-labelledby="privacy-heading">
        <h2 id="privacy-heading" className="text-heading text-fg">
          Privacy and recording
        </h2>
        <dl className="mt-5 grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-3">
          <div className="grid content-start gap-1">
            <dt className="text-[13px] leading-5 font-medium text-fg">What's recorded</dt>
            <dd className="text-sm leading-6 text-fg-muted">
              {recorded.length ? recorded.map((item) => item.title).join(", ") : "Your answers, as audio and a written transcript"}. The consent step lists every detail before anything starts.
            </dd>
          </div>
          <div className="grid content-start gap-1">
            <dt className="text-[13px] leading-5 font-medium text-fg">Who can see it</dt>
            <dd className="text-sm leading-6 text-fg-muted">Only the hiring team at {org}. The AI interviewer never decides anything.</dd>
          </div>
          <div className="grid content-start gap-1">
            <dt className="text-[13px] leading-5 font-medium text-fg">How long it's kept</dt>
            <dd className="text-sm leading-6 text-fg-muted">{disclosure.data?.retentionSummary ?? "For the period stated on the consent step, then deleted."}</dd>
          </div>
        </dl>
      </section>

      <StepFooter>
        <Button size="lg" onClick={ctx.next} data-testid="welcome-continue">
          Continue
        </Button>
      </StepFooter>
    </div>
  );
}
