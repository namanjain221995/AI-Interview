import { Check, CircleCheck, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";

import { TERMINAL_RESUME_FAILURES, type ResumeSectionState, type ResumeView } from "../api";

/** Until the first read of the file is on screen, check often (it takes about a second for a text file). */
export const FIRST_READ_POLL_MS = 600;
export const REFINE_POLL_MS = 1500;

function processing(view: ResumeView | undefined): boolean {
  if (!view || view.status === "none" || TERMINAL_RESUME_FAILURES.has(view.status)) return false;
  return !(view.status === "ready" && view.profileReady);
}

/** Poll quickly until the first read is visible, then calmly until the profile is checked. */
export function resumePollInterval(view: ResumeView | undefined): number | false {
  if (!processing(view)) return false;
  return view?.summary ? REFINE_POLL_MS : FIRST_READ_POLL_MS;
}

/**
 * What we know about one part of the summary: still being checked, checked,
 * or kept as read from the file. Honest by construction: it mirrors the
 * server's per-section state, never a timer.
 */
function SectionMark({ sectionKey, state, final }: { sectionKey: string; state: ResumeSectionState | undefined; final: boolean }) {
  if (!state || state === "pending") return null;
  if (state === "done") {
    if (final) return null;
    return (
      <span data-testid={`resume-section-${sectionKey}`} data-state="done" className="inline-flex items-center gap-1 text-xs font-medium text-success-fg">
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
        Checked
      </span>
    );
  }
  if (state === "fallback") {
    return (
      <span data-testid={`resume-section-${sectionKey}`} data-state="fallback" className="text-xs text-fg-subtle">
        As read from your file
      </span>
    );
  }
  return (
    <span data-testid={`resume-section-${sectionKey}`} data-state={state} className="inline-flex items-center gap-1 text-xs text-fg-subtle">
      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
      Checking
    </span>
  );
}

function SummarySection({ title, sectionKey, state, final, children }: { title: string; sectionKey: string; state: ResumeSectionState | undefined; final: boolean; children: ReactNode }) {
  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-[13px] font-medium text-fg-muted">{title}</h3>
        <SectionMark sectionKey={sectionKey} state={state} final={final} />
      </div>
      {children}
    </div>
  );
}

export function SummaryPanel({ view }: { view: ResumeView }) {
  const s = view.summary;
  const stage = view.profileStage ?? (s ? "full" : "none");
  const final = stage === "full" && view.profileReady;
  const states = new Map((view.sections ?? []).map((section) => [section.key, section.state]));
  const interview = states.get("interview");
  const education = s?.education ?? [];
  return (
    <section data-testid="resume-summary" data-stage={stage} aria-labelledby="summary-heading" className="rounded-lg border border-line bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="summary-heading" className="break-words text-heading text-fg">
            {s?.name ?? "Your résumé"}
          </h2>
          {s?.headline ? <p className="mt-1 max-w-xl text-sm leading-6 text-fg-muted">{s.headline}</p> : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!final ? <SectionMark sectionKey="basics" state={states.get("basics")} final={final} /> : null}
          {stage === "quick" ? (
            <Badge tone="neutral" data-testid="resume-profile-stage">
              First read
            </Badge>
          ) : null}
          {s?.experienceYears !== null && s?.experienceYears !== undefined ? (
            <Badge tone="neutral">{Math.max(1, Math.round(s.experienceYears))}+ years of experience</Badge>
          ) : null}
        </div>
      </div>
      {s?.partial ? (
        <p data-testid="resume-partial-note" className="mt-4 rounded-md bg-surface-sunken px-3 py-2 text-[13px] leading-5 text-fg-muted">
          {s.partial.pagesRead && s.partial.pagesTotal
            ? `From page ${s.partial.pagesRead} of ${s.partial.pagesTotal}. We're still scanning the rest; it fills in here when it's done.`
            : "From the first pages. We're still scanning the rest; it fills in here when it's done."}
        </p>
      ) : null}
      {s?.roles.length ? (
        <SummarySection title="Recent roles" sectionKey="experience" state={states.get("experience")} final={final}>
          <ul className="mt-2 grid gap-2">
            {s.roles.map((role, i) => (
              <li key={`${role.title}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-4 text-sm">
                <span className="min-w-0 text-fg">
                  <span className="font-medium">{role.title}</span>
                  {role.company ? <span className="text-fg-muted"> at {role.company}</span> : null}
                </span>
                {role.period ? <span className="text-[13px] text-fg-subtle tabular-nums">{role.period}</span> : null}
              </li>
            ))}
          </ul>
        </SummarySection>
      ) : null}
      {s?.skills.length ? (
        <SummarySection title="Skills we noticed" sectionKey="skills" state={states.get("skills")} final={final}>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {s.skills.map((skill) => (
              <li key={skill} className="max-w-full">
                <Badge tone="outline" className="max-w-full truncate">
                  {skill}
                </Badge>
              </li>
            ))}
          </ul>
        </SummarySection>
      ) : null}
      {education.length || s?.certifications.length ? (
        <SummarySection title="Education and certifications" sectionKey="education" state={states.get("education")} final={final}>
          <ul className="mt-2 grid gap-1 text-sm text-fg">
            {education.map((item, i) => (
              <li key={`${item.institution}-${i}`} className="break-words">
                {[item.degree, item.field].filter(Boolean).join(" ")}
                {item.degree || item.field ? <span className="text-fg-muted"> · </span> : null}
                <span className={item.degree || item.field ? "text-fg-muted" : undefined}>{item.institution}</span>
              </li>
            ))}
            {s?.certifications.length ? <li className="break-words text-fg-muted">{s.certifications.join(", ")}</li> : null}
          </ul>
        </SummarySection>
      ) : null}
      {!s ? <p className="mt-2 text-sm text-fg-muted">We've read your résumé. The interviewer will use it to ask about your experience.</p> : null}
      {interview && interview !== "pending" && !final ? (
        <p data-testid="resume-section-interview" data-state={interview} className="mt-5 flex items-center gap-2 text-[13px] text-fg-muted">
          {interview === "done" ? (
            <CircleCheck className="size-3.5 shrink-0 text-success" aria-hidden="true" />
          ) : interview === "fallback" ? null : (
            <Loader2 className="size-3.5 shrink-0 animate-spin text-accent-fg" aria-hidden="true" />
          )}
          {interview === "done" ? "Interview topics ready" : interview === "fallback" ? "Interview topics prepared from your file" : "Preparing your interview topics"}
        </p>
      ) : null}
      <p className="mt-5 border-t border-line pt-4 text-[13px] leading-5 text-fg-muted">
        {stage === "quick"
          ? "This is our first read of your file, shown straight away while we check the details. Nothing here is scored. If something looks wrong, upload a different file."
          : "This is only a quick check that we read the file correctly. Nothing here is scored. If something looks wrong, upload a different file."}
      </p>
    </section>
  );
}

