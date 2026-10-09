import { AArrowDown, AArrowUp, Check, Contrast, EyeOff, MicOff } from "lucide-react";
import { useEffect, useId, useRef, useState, type ClipboardEvent, type MouseEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { readLocal, writeLocal } from "@/features/candidate/journey";
import { useInterviewState, type InterviewStore } from "@/features/realtime/store";
import { formatClock } from "@/lib/format";
import type { ActivityPhase } from "@/lib/realtime/protocol";
import { clamp, cn } from "@/lib/utils";

import {
  activityRemainingMs,
  contentVisible,
  finishEnabled,
  finishLocksForS,
  KIND_LABELS,
  phaseCopy,
  spokenDuration,
  type ActivityContentView,
  type ActivityView,
  type PhaseCopy,
} from "./activityModel";

/** Passage and card text sizes (px). Default 20, or 18 on phones. */
export const FONT_STEPS = [18, 20, 22, 25, 28] as const;
/** End reasons that get the brief "Done" (anything else simply closes). */
export const DONE_REASONS: ReadonlySet<string> = new Set(["completed", "finished_early", "deadline"]);

export interface ActivityPanelProps {
  store: InterviewStore;
  onFinish: (activityId: string, phase: ActivityPhase) => void;
  /** Live microphone level for capture phases (the room passes its MicMeter). */
  micMeter?: ReactNode;
  /** Answers are recorded (policy and consent): the capture indicator says "Recording", otherwise "Mic on". */
  recording: boolean;
  /** Keeps the candidate's text size and page choice for this interview (never the passage). */
  interviewId?: string;
  className?: string;
}

interface ReadingPrefs {
  size: number;
  light: boolean;
}

function useReadingPrefs(interviewId: string | undefined): [ReadingPrefs, (patch: Partial<ReadingPrefs>) => void] {
  const [prefs, setPrefs] = useState<ReadingPrefs>(() => {
    const saved = interviewId ? readLocal<Partial<ReadingPrefs> | null>(interviewId, "reading", null) : null;
    const wide = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(min-width: 640px)").matches;
    const size = typeof saved?.size === "number" && (FONT_STEPS as readonly number[]).includes(saved.size) ? saved.size : wide ? 20 : 18;
    return { size, light: saved?.light === true };
  });
  const update = (patch: Partial<ReadingPrefs>) =>
    setPrefs((current) => {
      const next = { ...current, ...patch };
      if (interviewId) writeLocal(interviewId, "reading", next);
      return next;
    });
  return [prefs, update];
}

/** Re-render every `ms` while `active` (the countdown and its bar). */
function useTicker(ms: number, active: boolean): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!active) return;
    setNow(performance.now());
    const id = setInterval(() => setNow(performance.now()), ms);
    return () => clearInterval(id);
  }, [ms, active]);
  return active ? performance.now() : now;
}

/**
 * The reading and speaking exercises (INTAKE_INTERVIEW.md §11.2): one surface
 * with a hairline countdown along its top edge, the title and what to do now,
 * the passage or topic card set for reading, and one finish action. The
 * read-and-relay passage is rendered only while the store holds it and its own
 * countdown is above zero; once hidden it is gone from the page.
 */
export function ActivityPanel({ store, onFinish, micMeter, recording, interviewId, className }: ActivityPanelProps) {
  const activity = useInterviewState(store, (s) => s.activity);
  const clockOn = useInterviewState(store, (s) => s.connection.status === "connected" && !s.ended);
  const interviewer = useInterviewState(store, (s) => s.session?.interviewerName || "The interviewer");
  const caption = useInterviewState(store, (s) => (s.utterance?.text.trim() ? s.utterance.text.trim() : ""));
  const [prefs, setPrefs] = useReadingPrefs(interviewId);
  const now = useTicker(250, Boolean(activity && activity.status === "running" && clockOn));
  if (!activity) return null;
  return (
    <PanelBody
      activity={activity}
      remainingMs={activityRemainingMs(activity, now, clockOn)}
      interviewer={interviewer}
      caption={caption}
      prefs={prefs}
      setPrefs={setPrefs}
      onFinish={onFinish}
      micMeter={micMeter}
      recording={recording}
      className={className}
    />
  );
}

function PanelBody({
  activity,
  remainingMs,
  interviewer,
  caption,
  prefs,
  setPrefs,
  onFinish,
  micMeter,
  recording,
  className,
}: {
  activity: ActivityView;
  remainingMs: number | null;
  interviewer: string;
  caption: string;
  prefs: ReadingPrefs;
  setPrefs: (patch: Partial<ReadingPrefs>) => void;
  onFinish: (activityId: string, phase: ActivityPhase) => void;
  micMeter?: ReactNode;
  recording: boolean;
  className?: string;
}) {
  const titleId = useId();
  const lockId = useId();
  const copy = phaseCopy(activity, interviewer);
  const running = activity.status === "running";
  const showContent = contentVisible(activity, remainingMs);
  const seconds = remainingMs === null ? 0 : Math.ceil(remainingMs / 1000);
  const timed = running && remainingMs !== null && activity.durationMs > 0;
  // A re-shown window (after a reconnect) shows its share of the whole phase, e.g. 12 of 30 seconds.
  const planMs = Math.max(activity.durationMs, activity.phases.find((p) => p.phase === activity.phase)?.durationMs ?? 0);
  const fraction = timed ? clamp(remainingMs! / planMs, 0, 1) : activity.status === "armed" ? 1 : 0;
  const low = timed && remainingMs! <= 10_000;
  const canFinish = finishEnabled(activity, remainingMs);
  const lockS = finishLocksForS(activity, remainingMs);
  const done = activity.status === "ended" && DONE_REASONS.has(activity.endReason ?? "");
  const textOnScreen = showContent && Boolean(activity.content);
  const title = activity.title || activity.content?.title || KIND_LABELS[activity.kind];
  const announcement = useAnnouncement(activity, copy, remainingMs);
  // The footer carries the finish action, the reading options and what to do now; nothing to show, no footer.
  const showFooter = running || (activity.status === "between" && (Boolean(copy.instruction) || textOnScreen));

  return (
    <section
      data-testid="activity-panel"
      data-kind={activity.kind}
      data-phase={activity.phase ?? "none"}
      data-status={activity.status}
      aria-labelledby={titleId}
      className={cn("relative flex max-h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-overlay", className)}
    >
      {/* The countdown, as a hairline that empties from the right. */}
      <div className="absolute inset-x-0 top-0 h-[3px] bg-track" aria-hidden="true">
        <div
          className={cn("h-full origin-left transition-[width,background-color] duration-300 ease-linear motion-reduce:transition-none", low ? "bg-warning" : "bg-accent")}
          style={{ width: `${fraction * 100}%` }}
        />
      </div>

      {/* Phones: the countdown stays beside the title and the microphone status sits under the lede. */}
      <header className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-5 gap-y-2.5 px-4 pt-5 pb-4 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:px-7 sm:pt-6">
        <div className="col-start-1 row-start-1 min-w-0">
          <h2 id={titleId} data-testid="activity-title" className="text-lg leading-7 font-semibold tracking-[-0.011em] text-balance text-fg sm:text-xl">
            {title}
          </h2>
          {copy.lede ? <p className="mt-1 text-sm leading-6 text-fg-muted">{copy.lede}</p> : null}
        </div>
        {running ? (
          <div className="col-start-1 row-start-2 sm:col-start-2 sm:row-start-1 sm:self-center">
            <MicStatus capture={activity.capture} recording={recording} meter={micMeter} />
          </div>
        ) : null}
        {running ? (
          <div className="col-start-2 row-span-2 row-start-1 sm:col-start-3 sm:row-span-1">
            <Countdown seconds={seconds} word={copy.timerWord} low={low} />
          </div>
        ) : null}
      </header>

      <div
        data-testid="activity-body"
        data-page={prefs.light && textOnScreen ? "light" : "dark"}
        className={cn(
          "min-h-[13rem] flex-1 overflow-y-auto overscroll-contain border-t border-line-subtle px-4 py-5 sm:px-10 sm:py-8",
          prefs.light && textOnScreen ? "bg-white text-ink-900" : "text-fg",
        )}
      >
        {done ? (
          <Done kind={activity.kind} />
        ) : activity.status === "ended" ? null : activity.status === "armed" ? (
          <Instructions caption={caption} plan={copy.instruction} />
        ) : showContent && activity.content ? (
          activity.kind === "topic_card" ? (
            <TopicCard content={activity.content} size={prefs.size} light={prefs.light} caption={activity.status === "between" ? caption : ""} />
          ) : (
            <Passage content={activity.content} size={prefs.size} light={prefs.light} guarded={activity.kind === "read_relay"} />
          )
        ) : activity.kind === "read_relay" && !(activity.status === "between" && activity.phase === "relay") ? (
          <HiddenNotice activity={activity} interviewer={interviewer} caption={activity.status === "between" ? caption : ""} />
        ) : activity.status === "between" ? (
          <Continuing caption={caption} />
        ) : (
          <Waiting kind={activity.kind} />
        )}
      </div>

      {showFooter ? (
        <footer className="grid grid-cols-1 gap-3 border-t border-line-subtle px-4 py-3.5 sm:flex sm:items-center sm:gap-5 sm:px-7 sm:py-4">
          {copy.instruction ? <p className="text-sm leading-5 text-fg-muted sm:order-2 sm:min-w-0 sm:flex-1">{copy.instruction}</p> : <span className="max-sm:hidden sm:order-2 sm:flex-1" />}
          <div className="flex items-center justify-between gap-3 sm:contents">
            {textOnScreen ? <ReadingControls prefs={prefs} setPrefs={setPrefs} className="sm:order-1" /> : <span />}
            {copy.finishLabel && running ? (
              <Button
                size="lg"
                className="sm:order-3"
                disabled={!canFinish}
                loading={activity.finishRequested}
                loadingText="One moment"
                aria-describedby={lockS > 0 ? lockId : undefined}
                onClick={() => activity.phase && onFinish(activity.id, activity.phase)}
                data-testid="activity-finish-button"
              >
                {copy.finishLabel}
              </Button>
            ) : null}
          </div>
          {lockS > 0 ? (
            <span id={lockId} className="sr-only">
              Available in {lockS} second{lockS === 1 ? "" : "s"}
            </span>
          ) : null}
        </footer>
      ) : null}

      <p className="sr-only" aria-live="polite" aria-atomic="true" data-testid="activity-announcer">
        {announcement}
      </p>
    </section>
  );
}

// ------------------------------------------------------------------ header parts

function Countdown({ seconds, word, low }: { seconds: number; word: string; low: boolean }) {
  return (
    <div data-testid="activity-countdown" data-remaining-s={seconds} role="timer" aria-label={`${spokenDuration(seconds)} ${word}`} className="grid justify-items-end">
      <span className={cn("text-[1.625rem] leading-8 font-semibold tracking-[-0.02em] tabular-nums", low ? "text-warning-fg" : "text-fg")}>{formatClock(seconds)}</span>
      <span className="text-xs leading-4 text-fg-muted">{word}</span>
    </div>
  );
}

function MicStatus({ capture, recording, meter }: { capture: boolean; recording: boolean; meter?: ReactNode }) {
  if (!capture) {
    return (
      <span data-testid="activity-mic-status" data-capture="false" className="flex items-center gap-1.5 text-xs leading-4 text-fg-muted">
        <MicOff className="size-3.5" aria-hidden="true" />
        Not recording
      </span>
    );
  }
  return (
    <span data-testid="activity-mic-status" data-capture="true" className="flex items-center gap-2 text-xs leading-4 font-medium text-fg">
      <span className="relative flex size-2" aria-hidden="true">
        <span className="absolute inset-0 animate-pulse-soft rounded-full bg-danger motion-reduce:animate-none" />
        <span className="relative size-2 rounded-full bg-danger" />
      </span>
      {recording ? "Recording" : "Mic on"}
      {meter}
    </span>
  );
}

function ReadingControls({ prefs, setPrefs, className }: { prefs: ReadingPrefs; setPrefs: (patch: Partial<ReadingPrefs>) => void; className?: string }) {
  const index = Math.max(0, (FONT_STEPS as readonly number[]).indexOf(prefs.size));
  return (
    <div role="group" aria-label="Reading options" className={cn("flex items-center gap-0.5", className)}>
      <IconButton
        tooltip={false}
        label="Smaller text"
        icon={<AArrowDown />}
        disabled={index === 0}
        onClick={() => setPrefs({ size: FONT_STEPS[Math.max(0, index - 1)]! })}
        data-testid="activity-font-smaller"
      />
      <IconButton
        tooltip={false}
        label="Larger text"
        icon={<AArrowUp />}
        disabled={index === FONT_STEPS.length - 1}
        onClick={() => setPrefs({ size: FONT_STEPS[Math.min(FONT_STEPS.length - 1, index + 1)]! })}
        data-testid="activity-font-larger"
      />
      <Button
        variant="ghost"
        leftIcon={<Contrast />}
        aria-pressed={prefs.light}
        onClick={() => setPrefs({ light: !prefs.light })}
        data-testid="activity-contrast-toggle"
        className="px-2.5 aria-pressed:bg-surface-active aria-pressed:text-fg"
      >
        <span className="max-sm:sr-only">Light page</span>
      </Button>
    </div>
  );
}

// ------------------------------------------------------------------ body variants

const prevent = (event: ClipboardEvent | MouseEvent) => event.preventDefault();

function Passage({ content, size, light, guarded }: { content: ActivityContentView; size: number; light: boolean; guarded: boolean }) {
  return (
    <div
      lang="en"
      data-testid="activity-passage"
      className={cn("w-full max-w-[62ch] text-pretty", guarded && "select-none", light ? "text-ink-900" : "text-fg")}
      style={{ fontSize: `${size}px`, lineHeight: 1.7 }}
      onCopy={guarded ? prevent : undefined}
      onCut={guarded ? prevent : undefined}
      onContextMenu={guarded ? prevent : undefined}
      draggable={false}
    >
      {content.paragraphs.map((paragraph, i) => (
        <p key={i} className={i ? "mt-[0.9em]" : undefined}>
          {paragraph}
        </p>
      ))}
    </div>
  );
}

function TopicCard({ content, size, light, caption }: { content: ActivityContentView; size: number; light: boolean; caption: string }) {
  return (
    <div lang="en" className="w-full max-w-[62ch]">
      {caption ? <Caption text={caption} className="mb-5" /> : null}
      {content.instruction ? <p className={cn("text-[15px] leading-6", light ? "text-ink-500" : "text-fg-muted")}>{content.instruction}</p> : null}
      <ol data-testid="activity-card-bullets" className="mt-5 grid grid-cols-1 gap-[0.8em]" style={{ fontSize: `${size}px` }}>
        {content.bullets.map((bullet, i) => (
          <li key={i} className="grid grid-cols-[1.6em_minmax(0,1fr)] items-baseline leading-[1.45]">
            <span className={cn("text-[0.8em] font-semibold tabular-nums", light ? "text-ink-400" : "text-fg-subtle")}>{i + 1}</span>
            <span className={cn("font-medium", light ? "text-ink-900" : "text-fg")}>{bullet}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Caption({ text, className }: { text: string; className?: string }) {
  return <p className={cn("max-w-[62ch] text-[15px] leading-6 text-fg-muted", className)}>{text}</p>;
}

function Instructions({ caption, plan }: { caption: string; plan: string }) {
  return (
    <div className="flex h-full max-w-[56ch] flex-col justify-center gap-4 py-2">
      <p className="flex items-center gap-3 text-[17px] leading-7 font-medium text-fg">
        <span className="flex h-4 items-center gap-[3px]" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <span key={i} className="trace-bar h-full w-[3px] rounded-full bg-accent" style={{ animationDelay: `${i * 140}ms` }} />
          ))}
        </span>
        Listen to the instructions
      </p>
      {caption ? <p className="text-[17px] leading-7 text-fg">{caption}</p> : null}
      <p className="text-sm leading-6 text-fg-muted">{plan}</p>
    </div>
  );
}

function HiddenNotice({ activity, interviewer, caption }: { activity: ActivityView; interviewer: string; caption: string }) {
  const reconnecting = activity.hiddenBy === "disconnect" && activity.phase === "read";
  const relay = activity.phase === "relay" && activity.status === "running";
  return (
    <div data-testid="activity-hidden-notice" className="flex h-full max-w-[56ch] flex-col justify-center gap-3 py-2">
      <p className="flex items-center gap-2.5 text-[17px] leading-7 font-semibold text-fg">
        <EyeOff className="size-5 shrink-0 text-fg-muted" aria-hidden="true" />
        {reconnecting ? "The passage is hidden while we reconnect" : "The passage is hidden"}
      </p>
      <p className="text-[17px] leading-7 text-fg-muted">
        {relay
          ? "Explain it in your own words: the main idea first, then any details you remember. It's fine to say what you're unsure about."
          : reconnecting
            ? "If there's reading time left when you're back, it appears again for the time that's left."
            : `It won't be shown again. ${interviewer} will ask you to explain it in your own words.`}
      </p>
      {caption ? <Caption text={caption} className="text-fg" /> : null}
    </div>
  );
}

/** After a capture phase, until the interview moves on (also while the server recovers from a stall). */
function Continuing({ caption }: { caption: string }) {
  return (
    <div className="flex h-full max-w-[56ch] flex-col justify-center gap-3 py-2">
      <p className="text-[17px] leading-7 text-fg-muted">The interview continues in a moment.</p>
      {caption ? <Caption text={caption} className="text-fg" /> : null}
    </div>
  );
}

function Waiting({ kind }: { kind: ActivityView["kind"] }) {
  return (
    <div className="flex h-full max-w-[56ch] items-center py-2">
      <p className="text-[15px] leading-6 text-fg-muted">{kind === "topic_card" ? "The topic card appears in a moment." : "The passage appears in a moment."}</p>
    </div>
  );
}

function Done({ kind }: { kind: ActivityView["kind"] }) {
  return (
    <div role="status" className="mx-auto flex h-full max-w-[56ch] flex-col items-center justify-center gap-2 py-6 text-center">
      <Check className="size-7 text-success" strokeWidth={2.5} aria-hidden="true" />
      <p className="text-xl leading-7 font-semibold text-fg">Done</p>
      <p className="text-sm leading-6 text-fg-muted">{kind === "topic_card" ? "That's the speaking exercise. The interview continues." : "Thank you. The interview continues."}</p>
    </div>
  );
}

// ------------------------------------------------------------------ announcements

/** Polite announcements: the phase changing, the passage hiding, ten seconds left. */
function useAnnouncement(activity: ActivityView, copy: PhaseCopy, remainingMs: number | null): string {
  const [message, setMessage] = useState(copy.announce);
  const phaseKey = `${activity.id}:${activity.attempt}:${activity.status}:${activity.phase ?? ""}`;
  const lastPhaseKey = useRef(phaseKey);
  const hidden = activity.kind === "read_relay" && !activity.content && activity.hiddenBy !== null && activity.hiddenBy !== "disconnect";
  const lastHidden = useRef(hidden);
  const tenLeft = activity.status === "running" && remainingMs !== null && remainingMs > 0 && remainingMs <= 10_000 && activity.durationMs > 15_000;
  const lastTen = useRef(tenLeft);

  useEffect(() => {
    if (lastPhaseKey.current === phaseKey) return;
    lastPhaseKey.current = phaseKey;
    if (copy.announce) setMessage(copy.announce);
  }, [phaseKey, copy.announce]);
  useEffect(() => {
    if (hidden && !lastHidden.current) setMessage("The passage is now hidden.");
    lastHidden.current = hidden;
  }, [hidden]);
  useEffect(() => {
    if (tenLeft && !lastTen.current) setMessage("10 seconds left.");
    lastTen.current = tenLeft;
  }, [tenLeft]);
  return message;
}
