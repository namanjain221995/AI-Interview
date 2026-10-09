import { CircleAlert, Expand, Eye, Keyboard, MonitorUp, RefreshCw, WifiOff, X } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import type { ResumeView } from "@/features/candidate/api";
import { PreparationProgress } from "@/features/candidate/components/PreparationProgress";
import { monitoringSummary } from "@/features/integrity/policy";
import { useInterviewState, type InterviewStore } from "@/features/realtime/store";
import type { IntegrityPolicy } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface PreparationState {
  ready: boolean;
  phase: "ready" | "analysing" | "preparing" | "resume_failed" | "resume_missing";
  /** What the résumé analysis is doing right now. */
  activity?: string | null;
  /** Résumé pipeline status, for the progress shown while waiting. */
  resume?: ResumeView | null;
  canStartAnyway: boolean;
  onStartAnyway: () => void;
  resumeHref: string;
}

export interface BeginGateProps {
  preparation: PreparationState;
  interviewerName: string;
  roundName: string | null;
  minutes: number | null;
  resuming: boolean;
  integrity: IntegrityPolicy;
  screenRequired: boolean;
  screenShared: boolean;
  onShareScreen: () => void;
  onBegin: () => void;
  starting: boolean;
  error: string | null;
  systemCheckHref: string;
}

const FULLSCREEN_ITEM = "Whether the interview stays in full screen";

/**
 * The lobby. While the interview is being tailored to the résumé it says so, shows
 * real progress and unlocks Begin by itself; it then states plainly what happens
 * when you press Begin and what is observed.
 */
export function BeginGate({ preparation, interviewerName, roundName, minutes, resuming, integrity, screenRequired, screenShared, onShareScreen, onBegin, starting, error, systemCheckHref }: BeginGateProps) {
  const monitored = monitoringSummary(integrity);
  const fullscreen = integrity.enabled && integrity.require_fullscreen;
  const blocked = preparation.phase === "resume_failed" || preparation.phase === "resume_missing";
  const waiting = !preparation.ready;
  const tailoring = waiting && !blocked;
  const statusId = useId();
  const monitorId = useId();

  const title = resuming ? "Welcome back" : tailoring ? "Your interview is almost ready" : "Ready when you are";
  const lede = resuming
    ? `Your interview is saved. ${interviewerName} will pick up where you left off.`
    : tailoring
      ? `${interviewerName} is tailoring the questions to your experience. Begin unlocks by itself as soon as it's ready.`
      : `${interviewerName} will introduce ${roundName ? `the ${roundName}` : "the interview"} and ask the first question${minutes ? `. It takes about ${minutes} minutes` : ""}. Speak naturally and pause when you've finished an answer.`;
  const observed = monitored.filter((item) => !(fullscreen && item === FULLSCREEN_ITEM));

  return (
    <div className="absolute inset-0 z-20 flex items-start justify-center overflow-y-auto bg-[radial-gradient(70%_60%_at_50%_45%,transparent,var(--surface-sunken)_85%)] p-3 sm:items-center sm:p-6">
      <section
        aria-labelledby="begin-gate-title"
        className="w-full max-w-lg animate-pop-in rounded-2xl border border-line bg-surface/95 p-5 shadow-overlay backdrop-blur-md sm:p-8"
      >
        <h1 id="begin-gate-title" className="text-[1.375rem] leading-8 font-semibold tracking-[-0.016em] text-fg sm:text-title">
          {title}
        </h1>
        <p className="mt-2 text-[15px] leading-6 text-fg-muted">{lede}</p>

        {tailoring ? (
          <div id={statusId} role="status" aria-live="polite" className="mt-6 rounded-lg border border-line bg-surface-subtle p-4">
            <PreparationProgress view={preparation.resume} fallbackLabel={`${interviewerName} is getting ready`} />
            <p className="mt-3 border-t border-line-subtle pt-3 text-[13px] leading-5 text-fg-muted">
              Keep this tab open. While you wait, you can{" "}
              <Link to={systemCheckHref} className="font-medium text-accent-fg underline-offset-2 hover:underline">
                check your camera and microphone again
              </Link>
              .
            </p>
            {preparation.canStartAnyway ? (
              <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                <Button size="sm" variant="outline" onClick={preparation.onStartAnyway}>
                  Start without waiting
                </Button>
                <p className="text-[13px] leading-5 text-fg-muted">The first questions may be more general.</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {blocked ? (
          <div id={statusId} role="status" className="mt-6 flex items-start gap-3 rounded-lg border border-warning-line bg-warning-soft p-4">
            <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
            <p className="text-sm leading-6 text-fg">
              {preparation.phase === "resume_failed" ? "We couldn't read your résumé, so the interview can't be tailored yet." : "Your résumé is needed before the interview."}{" "}
              <Link to={preparation.resumeHref} className="font-medium text-accent-fg underline underline-offset-2">
                {preparation.phase === "resume_failed" ? "Upload it again" : "Upload your résumé"}
              </Link>
            </p>
          </div>
        ) : null}

        {fullscreen || observed.length ? (
          <section aria-labelledby={monitorId} className="mt-4 rounded-lg border border-line p-4">
            <h2 id={monitorId} className="flex items-center gap-2 text-sm font-medium text-fg">
              <Eye className="size-4 text-fg-muted" aria-hidden="true" />
              What's observed during the interview
            </h2>
            <ul className="mt-2.5 grid gap-1.5 text-[13px] leading-5 text-fg-muted">
              {fullscreen ? (
                <li className="flex items-start gap-2">
                  <Expand className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                  <span>Full screen turns on when you begin</span>
                </li>
              ) : null}
              {observed.map((item) => (
                <li key={item} className="flex items-start gap-2">
                  <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[13px] leading-5 text-fg-muted">
              A browser can't lock your computer, so these are observations for a person to review, never automatic decisions.
            </p>
          </section>
        ) : null}

        {screenRequired ? (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-4">
            <div>
              <p className="text-sm font-medium text-fg">Share your entire screen</p>
              <p className="text-[13px] text-fg-muted">{screenShared ? "Your screen is shared." : "Required for this interview. Choose Entire screen."}</p>
            </div>
            <Button size="sm" variant={screenShared ? "ghost" : "outline"} leftIcon={<MonitorUp />} onClick={onShareScreen} disabled={screenShared}>
              {screenShared ? "Shared" : "Share screen"}
            </Button>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="mt-4 rounded-md border border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger-fg">
            {error}
          </p>
        ) : null}

        <Button
          size="xl"
          fullWidth
          className="mt-6"
          onClick={onBegin}
          loading={starting}
          loadingText="Starting"
          disabled={(screenRequired && !screenShared) || waiting}
          aria-describedby={waiting ? statusId : undefined}
          data-testid="interview-begin-button"
        >
          {resuming ? "Rejoin interview" : "Begin interview"}
        </Button>
        {tailoring ? (
          <p className="mt-3 text-center text-[13px] text-fg-subtle">Begin unlocks automatically when your interview is ready.</p>
        ) : (
          <p className="mt-3 text-center text-[13px] text-fg-subtle">
            Something not working?{" "}
            <Link to={systemCheckHref} className="font-medium text-accent-fg underline-offset-2 hover:underline">
              Run the system check again
            </Link>
          </p>
        )}
      </section>
    </div>
  );
}

interface OfflineCopy {
  title: string;
  body: string;
  action: "retry" | "reload" | null;
  actionLabel?: string;
}

/** What to tell the candidate when the connection is gone for good (by close reason). */
export function offlineCopy(reason: string | null, fatalMessage: string | null): OfflineCopy {
  switch (reason) {
    case "replaced_by_new_connection":
      return {
        title: "This interview is open somewhere else",
        body: "It continued in another tab or window. Close this one, or continue here instead.",
        action: "reload",
        actionLabel: "Continue here",
      };
    case "too_many_reconnects":
      return {
        title: "The interview can't continue",
        body: fatalMessage ?? "The connection dropped too many times. The hiring team has been told and will be in touch.",
        action: null,
      };
    case "ticket_rejected":
      return { title: "We couldn't reconnect", body: "This interview can't be resumed from this page. Open your invitation link again.", action: null };
    case "session_ended":
      return { title: "The interview has ended", body: fatalMessage ?? "Thank you. Your answers are saved.", action: null };
    case "bad_request":
    case "forbidden_origin":
    case "session_not_found":
      return {
        title: "The interview couldn't connect",
        body: fatalMessage ?? "Reload the page. If it keeps happening, contact the hiring team.",
        action: "reload",
        actionLabel: "Reload",
      };
    default:
      return { title: "We lost the connection", body: "Check your internet connection, then try again. Your answers so far are saved.", action: "retry", actionLabel: "Try again" };
  }
}

/** Covers the room while the connection recovers. Nothing underneath unmounts. */
export function ReconnectOverlay({ store, onRetry }: { store: InterviewStore; onRetry: () => void }) {
  const connection = useInterviewState(store, (s) => s.connection);
  const begun = useInterviewState(store, (s) => s.begun);
  const ended = useInterviewState(store, (s) => Boolean(s.ended));
  const fatal = useInterviewState(store, (s) => s.fatalError);
  if (!begun || ended) return null;
  const firstConnect = !connection.everConnected && (connection.status === "connecting" || connection.status === "idle");
  if (connection.status === "connected" || connection.status === "closed") return null;
  const offline = connection.status === "offline";
  const copy = offline ? offlineCopy(connection.reason, fatal?.message ?? null) : null;
  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-overlay/70 p-4 backdrop-blur-[3px]" role="alertdialog" aria-live="assertive" aria-label={copy?.title ?? "Reconnecting"}>
      <div className="w-full max-w-sm rounded-xl border border-line bg-surface-raised p-6 text-center shadow-overlay">
        <span className="mx-auto flex size-10 items-center justify-center rounded-full bg-neutral-soft text-fg-muted">
          {offline ? <WifiOff className="size-5" aria-hidden="true" /> : <Spinner size="lg" label={null} />}
        </span>
        <h2 className="mt-4 text-heading text-fg">{copy?.title ?? (firstConnect ? "Connecting to your interview" : "Reconnecting")}</h2>
        <p className="mt-1.5 text-sm leading-6 text-fg-muted">
          {copy?.body ?? (firstConnect ? "This usually takes a second." : "Your answers so far are saved. The interviewer will pick up right where you left off.")}
        </p>
        {copy?.action === "retry" || (!offline && !firstConnect) ? (
          <Button className="mt-5" variant={offline ? "primary" : "outline"} leftIcon={<RefreshCw />} onClick={onRetry}>
            {copy?.actionLabel ?? "Try now"}
          </Button>
        ) : null}
        {copy?.action === "reload" ? (
          <Button className="mt-5" leftIcon={<RefreshCw />} onClick={() => window.location.reload()}>
            {copy.actionLabel ?? "Reload"}
          </Button>
        ) : null}
        {!offline && connection.attempt > 2 ? <p className="mt-3 text-xs text-fg-subtle tabular-nums">Attempt {connection.attempt}</p> : null}
      </div>
    </div>
  );
}

interface NoticeItem {
  id: string;
  tone: "info" | "warning" | "error";
  message: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
}

function Notice({ item }: { item: NoticeItem }) {
  return (
    <div
      role={item.tone === "info" ? "status" : "alert"}
      className={cn(
        "pointer-events-auto flex w-full max-w-xl animate-float-in items-start gap-3 rounded-lg border px-3.5 py-2.5 text-sm shadow-raised backdrop-blur-md",
        item.tone === "info" && "border-line bg-surface-raised/95 text-fg",
        item.tone === "warning" && "border-warning-line bg-surface-raised/95 text-fg",
        item.tone === "error" && "border-danger-line bg-surface-raised/95 text-fg",
      )}
    >
      <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", item.tone === "info" ? "bg-accent" : item.tone === "warning" ? "bg-warning" : "bg-danger")} aria-hidden="true" />
      <div className="min-w-0 flex-1 leading-6">{item.message}</div>
      {item.action ? <div className="shrink-0">{item.action}</div> : null}
      {item.onDismiss ? (
        <button type="button" onClick={item.onDismiss} aria-label="Dismiss" className="-mr-1 flex size-6 shrink-0 items-center justify-center rounded-sm text-fg-subtle hover:bg-surface-hover hover:text-fg">
          <X className="size-3.5" />
        </button>
      ) : null}
    </div>
  );
}

/** Notices: degradations, gentle integrity reminders, the full-screen prompt. Never accusatory. */
export function NoticeArea({
  store,
  fullscreenPrompt,
  onFullscreen,
  onTypeAnswer,
  screenSharePrompt,
  onShareScreen,
  placement = "overlay",
}: {
  store: InterviewStore;
  fullscreenPrompt: boolean;
  onFullscreen: () => void;
  onTypeAnswer: (() => void) | null;
  screenSharePrompt: boolean;
  onShareScreen: () => void;
  /** "overlay" floats over the stage; "inline" flows in a panel (coding mode, so controls stay clickable). */
  placement?: "overlay" | "inline";
}) {
  const notices = useInterviewState(store, (s) => s.notices);
  const warnings = useInterviewState(store, (s) => s.warnings);
  const sttDegraded = useInterviewState(store, (s) => s.sttDegraded);
  const candidateSpoke = useInterviewState(store, (s) => s.candidateSpoke);
  const fatal = useInterviewState(store, (s) => s.fatalError);
  // The transcription notice is dismissible; it comes back only if trouble starts again.
  const [sttDismissed, setSttDismissed] = useState(false);
  useEffect(() => {
    if (!sttDegraded) setSttDismissed(false);
  }, [sttDegraded]);

  // Informational notices and gentle integrity reminders fade on their own; degradation
  // warnings stay until dismissed or resolved.
  useEffect(() => {
    const timers = notices
      .filter((n) => n.level === "info")
      .map((n) => setTimeout(() => store.dispatch({ type: "notice.dismiss", code: n.code }), Math.max(1000, 8000 - (performance.now() - n.at))));
    return () => timers.forEach(clearTimeout);
  }, [notices, store]);
  useEffect(() => {
    const timers = warnings.map((w) => setTimeout(() => store.dispatch({ type: "warning.dismiss", id: w.id }), Math.max(1000, 12_000 - (performance.now() - w.at))));
    return () => timers.forEach(clearTimeout);
  }, [warnings, store]);

  const items: NoticeItem[] = [];
  if (fatal) items.push({ id: "fatal", tone: "error", message: fatal.message });
  if (screenSharePrompt) {
    items.push({
      id: "screen",
      tone: "warning",
      message: "Screen sharing has stopped. This interview needs your entire screen shared.",
      action: (
        <Button size="xs" variant="outline" leftIcon={<MonitorUp />} onClick={onShareScreen}>
          Share again
        </Button>
      ),
    });
  }
  if (fullscreenPrompt) {
    items.push({
      id: "fullscreen",
      tone: "info",
      message: "This interview is set up to run in full screen.",
      action: (
        <Button size="xs" variant="outline" leftIcon={<Expand />} onClick={onFullscreen}>
          Return to full screen
        </Button>
      ),
    });
  }
  // Only after the candidate has actually spoken (the server also requires failed
  // transcription of real speech): never during the introduction or on silence.
  if (sttDegraded && candidateSpoke && onTypeAnswer && !sttDismissed) {
    items.push({
      id: "stt",
      tone: "info",
      message: "Transcription is slow right now. Keep talking, or type your answer.",
      action: (
        <Button size="xs" variant="ghost" leftIcon={<Keyboard />} onClick={onTypeAnswer}>
          Type answer
        </Button>
      ),
      onDismiss: () => setSttDismissed(true),
    });
  }
  for (const w of warnings) {
    items.push({ id: w.id, tone: "info", message: w.message, onDismiss: () => store.dispatch({ type: "warning.dismiss", id: w.id }) });
  }
  for (const n of notices) {
    if (sttDegraded && n.code.startsWith("stt_")) continue;
    // The deferred start has its own panel on the stage.
    if (n.code === "preparing") continue;
    items.push({ id: n.id, tone: n.level, message: n.message, onDismiss: () => store.dispatch({ type: "notice.dismiss", code: n.code }) });
  }

  return (
    <div
      data-testid="interview-notice"
      aria-live="polite"
      className={cn(
        "flex flex-col gap-2",
        placement === "overlay" ? "pointer-events-none absolute inset-x-0 top-3 z-20 items-center px-4" : "items-stretch empty:hidden [&>*]:max-w-none [&>*]:shadow-none",
      )}
    >
      {items.slice(0, placement === "overlay" ? 4 : 3).map((item) => (
        <Notice key={item.id} item={item} />
      ))}
    </div>
  );
}
