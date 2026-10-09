import { Eye, Wifi, WifiOff } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/Popover";
import { Spinner } from "@/components/ui/Spinner";
import { Tooltip } from "@/components/ui/Tooltip";
import { codingRemainingSeconds, clockRunning, remainingSeconds, useInterviewState, type ConnectionStatus, type InterviewStore } from "@/features/realtime/store";
import { formatClock } from "@/lib/format";
import type { TurnState } from "@/lib/realtime/protocol";
import { cn } from "@/lib/utils";

import { useNow } from "../hooks";

export type ConnectionState = "connected" | "reconnecting" | "offline";

export function connectionState(status: ConnectionStatus): ConnectionState {
  if (status === "connected") return "connected";
  if (status === "offline") return "offline";
  return "reconnecting";
}

export function ConnectionIndicator({ store }: { store: InterviewStore }) {
  const connection = useInterviewState(store, (s) => s.connection);
  const begun = useInterviewState(store, (s) => s.begun);
  const state = connectionState(connection.status);
  const idle = !begun && connection.status === "idle";
  const label =
    idle
      ? "Connects when you begin"
      : state === "connected"
        ? connection.rttMs !== null && connection.rttMs > 600
          ? "Connected, slow"
          : "Connected"
        : state === "offline"
          ? "Offline"
          : connection.everConnected
            ? "Reconnecting"
            : "Connecting";
  return (
    <Tooltip content={connection.rttMs !== null ? `Round trip ${connection.rttMs} ms` : label}>
      <span
        data-testid="connection-status"
        data-state={state}
        role="status"
        className={cn(
          "flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
          (state === "connected" || idle) && "text-fg-muted",
          state === "reconnecting" && !idle && "bg-warning-soft text-warning-fg",
          state === "offline" && "bg-danger-soft text-danger-fg",
        )}
      >
        {state === "connected" || idle ? (
          <Wifi className={cn("size-3.5", idle && "opacity-50")} aria-hidden="true" />
        ) : state === "offline" ? (
          <WifiOff className="size-3.5" aria-hidden="true" />
        ) : (
          <Spinner size="xs" label={null} />
        )}
        <span className={cn(state === "connected" || idle ? "sr-only sm:not-sr-only" : undefined)}>{label}</span>
      </span>
    </Tooltip>
  );
}

export function InterviewTimer({ store }: { store: InterviewStore }) {
  useNow(1000);
  const state = useInterviewState(store, (s) => s);
  const remaining = remainingSeconds(state, performance.now());
  const running = clockRunning(state);
  const coding = state.coding && !state.coding.ended ? codingRemainingSeconds(state.coding, performance.now(), running) : null;
  const low = remaining !== null && remaining <= 120;
  return (
    <span
      data-testid="interview-timer"
      className={cn("flex items-baseline gap-1.5 text-sm tabular-nums", low ? "text-warning-fg" : "text-fg")}
      aria-label={remaining !== null ? `${formatClock(remaining)} remaining${running ? "" : ", paused"}` : "Time remaining"}
    >
      <span className="font-semibold">{remaining !== null ? formatClock(remaining) : "--:--"}</span>
      <span className="text-xs text-fg-muted">{running ? "left" : state.session?.status === "live" ? "paused" : "left"}</span>
      {coding !== null ? <span className="sr-only">Coding time {formatClock(coding)}</span> : null}
    </span>
  );
}

/**
 * Honest, unobtrusive notice that monitoring is on, with exactly what is observed.
 * A popover (not a tooltip) so it also works on touch screens and with the keyboard.
 */
export function MonitoringNotice({ observed }: { observed: readonly string[] }) {
  if (!observed.length) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium text-fg-muted hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-focus"
          aria-label="What's observed during this interview"
        >
          <Eye className="size-3.5" aria-hidden="true" />
          <span className="max-sm:sr-only">Observed</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-4">
        <p className="text-sm font-medium text-fg">What's observed during this interview</p>
        <ul className="mt-2 grid gap-1.5 text-[13px] leading-5 text-fg-muted">
          {observed.map((item) => (
            <li key={item} className="flex items-start gap-2">
              <span className="mt-2 size-1 shrink-0 rounded-full bg-fg-subtle" aria-hidden="true" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[13px] leading-5 text-fg-subtle">These are observations for a person to review, never automatic decisions.</p>
      </PopoverContent>
    </Popover>
  );
}

export function RoomTopBar({
  store,
  fallbackRound,
  fallbackInterviewer,
  observed = [],
}: {
  store: InterviewStore;
  fallbackRound: string;
  fallbackInterviewer: string;
  /** What monitoring notes (from the effective policy); empty when monitoring is off. */
  observed?: readonly string[];
}) {
  const roundName = useInterviewState(store, (s) => s.session?.roundName) || fallbackRound;
  const interviewer = useInterviewState(store, (s) => s.session?.interviewerName) || fallbackInterviewer;
  const status = useInterviewState(store, (s) => s.session?.status);
  const preparing = useInterviewState(store, (s) => s.preparing);
  return (
    <div className="flex h-14 items-center justify-between gap-3 border-b border-line-subtle px-3 sm:gap-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        {status === "live" && !preparing ? (
          <span className="relative flex size-2 shrink-0" aria-hidden="true">
            <span className="absolute inset-0 animate-live-ring rounded-full bg-signal" />
            <span className="relative size-2 rounded-full bg-signal" />
          </span>
        ) : null}
        <div className="grid min-w-0">
          <p className="truncate text-sm font-medium text-fg">{roundName || "Interview"}</p>
          <p className="truncate text-xs text-fg-muted">with {interviewer}, AI interviewer</p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1 sm:gap-4">
        <MonitoringNotice observed={observed} />
        <ConnectionIndicator store={store} />
        <InterviewTimer store={store} />
      </div>
    </div>
  );
}

const CUES: Record<TurnState, (name: string) => string> = {
  idle: () => "Getting the interview ready",
  ai_speaking: (name) => `${name} is speaking`,
  listening: () => "Your turn. Answer whenever you're ready",
  candidate_speaking: () => "Listening",
  processing: (name) => `${name} is thinking`,
  coding: () => "Coding exercise. Think aloud if you like",
  paused: () => "Paused while we reconnect",
  ended: () => "The interview has ended",
};

export function turnCue(turn: TurnState, interviewer: string): string {
  return (CUES[turn] ?? CUES.idle)(interviewer);
}

/** Calm cue for whose turn it is, with a small state-specific motion. Announced politely. */
export function TurnCue({ store, preparing = false, className }: { store: InterviewStore; preparing?: boolean; className?: string }) {
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const name = useInterviewState(store, (s) => s.session?.interviewerName ?? "The interviewer");
  const shown: TurnState = preparing ? "idle" : turn;
  return (
    <div className={cn("flex items-start justify-center gap-2.5 text-sm leading-5 text-fg-muted", className)} role="status" aria-live="polite">
      <span className="flex h-5 shrink-0 items-center">
        <CueGlyph turn={shown} />
      </span>
      <span className={cn(shown === "listening" || shown === "candidate_speaking" ? "text-signal-fg" : undefined)}>
        {preparing ? "Starting in a moment" : turnCue(turn, name)}
      </span>
    </div>
  );
}

function CueGlyph({ turn }: { turn: TurnState }) {
  if (turn === "ai_speaking") {
    return (
      <span className="flex h-3.5 items-center gap-[3px]" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className="trace-bar h-full w-[3px] rounded-full bg-accent" style={{ animationDelay: `${i * 140}ms` }} />
        ))}
      </span>
    );
  }
  if (turn === "listening" || turn === "candidate_speaking") {
    return (
      <span className="relative flex size-2.5" aria-hidden="true">
        <span className={cn("absolute inset-0 rounded-full bg-signal", turn === "listening" && "animate-live-ring")} />
        <span className="relative size-2.5 rounded-full bg-signal" />
      </span>
    );
  }
  if (turn === "processing") {
    return (
      <span className="flex items-center gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span key={i} className="size-1.5 animate-pulse-soft rounded-full bg-fg-muted" style={{ animationDelay: `${i * 260}ms` }} />
        ))}
      </span>
    );
  }
  return <span className="size-1.5 rounded-full bg-fg-subtle" aria-hidden="true" />;
}
