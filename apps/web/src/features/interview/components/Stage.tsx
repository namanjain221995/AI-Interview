import { Camera, CameraOff, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { AvatarStage, type AvatarController } from "@/features/avatar";
import { LevelMeter } from "@/features/candidate/components/LevelMeter";
import { useInterviewState, wordCount, type InterviewStore } from "@/features/realtime/store";
import { cn } from "@/lib/utils";

import { useAvatarStatus, useRuntimeMedia } from "../hooks";
import type { InterviewRuntime } from "../runtime";

/** The interviewer. Carries the avatar test id and the active render mode. */
export function AvatarPanel({ controller, className, compact = false }: { controller: AvatarController | null; className?: string; compact?: boolean }) {
  const status = useAvatarStatus(controller);
  const mode = status?.mode ?? "none";
  return (
    <div data-testid="avatar-stage" data-avatar-mode={controller ? mode : "none"} className={cn("relative overflow-hidden", className)}>
      {controller ? <AvatarStage controller={controller} framing="bust" occludedCorner={compact ? "bottom-right" : undefined} className="size-full" /> : null}
      {!controller || mode === "none" ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <span className={cn("max-h-full max-w-full rounded-full bg-surface-raised ring-1 ring-line", compact ? "size-14" : "size-24")} aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}

/**
 * A quiet divider between the interviewer and the captions that names who
 * holds the floor: brand blue while the interviewer speaks, signal cyan while
 * the candidate does. The presence orb is the room's one voice-reactive
 * element, so this line never pulses with audio levels; it only cross-fades
 * between speakers (and stays put under reduced motion).
 */
export function PresenceLine({ store, className }: { runtime?: InterviewRuntime | null; store: InterviewStore; className?: string }) {
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const voice = turn === "candidate_speaking" || turn === "listening" ? "candidate" : turn === "ai_speaking" ? "interviewer" : "idle";
  return (
    <div className={cn("pointer-events-none relative h-6", className)} aria-hidden="true">
      <div className="absolute inset-x-[8%] top-1/2 h-px bg-line" />
      <div
        data-voice={voice}
        className={cn(
          "absolute inset-x-[30%] top-1/2 h-px -translate-y-1/2 rounded-full opacity-70 transition-[background-color,opacity] duration-500 motion-reduce:transition-none",
          "data-[voice=interviewer]:bg-[linear-gradient(90deg,transparent,var(--accent),transparent)]",
          "data-[voice=candidate]:bg-[linear-gradient(90deg,transparent,var(--signal),transparent)]",
          "data-[voice=idle]:opacity-0",
        )}
      />
    </div>
  );
}

/** Candidate camera picture-in-picture with a live mic meter. */
export function CameraPip({ runtime, begun = true, className }: { runtime: InterviewRuntime | null; begun?: boolean; className?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { camera: stream } = useRuntimeMedia(runtime);
  const hasVideo = Boolean(stream);

  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = stream;
  }, [stream]);

  return (
    <div className={cn("relative overflow-hidden rounded-lg bg-surface-raised ring-1 ring-line", className)}>
      {hasVideo ? (
        <video ref={videoRef} autoPlay muted playsInline className="size-full -scale-x-100 object-cover" aria-label="Your camera" />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-1 px-2 text-center text-fg-subtle">
          {begun ? <CameraOff className="size-4 sm:size-5" aria-hidden="true" /> : <Camera className="size-4 sm:size-5" aria-hidden="true" />}
          <span className="text-[11px] leading-4 sm:text-xs">{begun ? "Camera off" : "Starts when you begin"}</span>
        </div>
      )}
      {hasVideo ? (
        <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-black/60 to-transparent px-2 pt-4 pb-1.5 sm:px-2.5 sm:pt-5 sm:pb-2">
          <span className="text-[11px] font-medium text-white/85">You</span>
          <MicMeter runtime={runtime} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * The server deferred the start until the interview plan is ready (it begins by
 * itself). Calm, specific, and honest about the clock.
 */
export function PreparingPanel({ interviewerName, activity, className }: { interviewerName: string; activity?: string | null; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("mx-auto w-full max-w-xl rounded-xl border border-line bg-surface-raised/70 px-4 py-3.5 sm:px-5 sm:py-4", className)}>
      <p className="flex items-center gap-2 text-[15px] font-medium text-fg">
        <Loader2 className="size-4 shrink-0 animate-spin text-accent-fg" aria-hidden="true" />
        Finishing your interview plan
      </p>
      <p className="mt-1.5 text-sm leading-6 text-fg-muted">
        {interviewerName} is reading your résumé so the questions fit your experience. The interview starts by itself, usually within a minute or two, and the clock only starts when {interviewerName} begins.
      </p>
      {activity ? <p className="mt-2 text-[13px] leading-5 text-fg-subtle">Now: {activity}</p> : null}
    </div>
  );
}

/** Mic meter fed by the runtime's level stream (no React re-render per frame). */
export function MicMeter({ runtime, className, surface = "video" }: { runtime: InterviewRuntime | null; className?: string; surface?: "video" | "panel" }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!runtime) return;
    const el = ref.current;
    if (!el) return;
    let peak = 0;
    return runtime.onLevel((level) => {
      peak = Math.max(level, peak * 0.85);
      const bars = el.querySelectorAll<HTMLSpanElement>("[data-bar]");
      const lit = Math.round(peak * bars.length);
      bars.forEach((bar, i) => {
        bar.dataset.lit = i < lit ? "1" : "0";
      });
      el.setAttribute("aria-valuenow", String(Math.round(peak * 100)));
    });
  }, [runtime]);
  return (
    <div ref={ref} role="meter" aria-label="Microphone level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={0} className={cn("flex h-3.5 items-end gap-[3px]", className)}>
      {Array.from({ length: 8 }, (_, i) => (
        <span
          key={i}
          data-bar=""
          data-lit="0"
          className={cn("w-[3px] rounded-full data-[lit=1]:bg-signal", surface === "video" ? "bg-white/25" : "bg-line-strong")}
          style={{ height: `${35 + i * 9}%` }}
        />
      ))}
    </div>
  );
}

export { LevelMeter };

/**
 * Live subtitles. The interviewer line shows words already spoken in full
 * colour and the rest of the sentence dimmed (still readable), so captions track
 * the voice. Acknowledgements ("Right.") are shown quietly so they never read like
 * a question. Screen readers get each finished utterance once, not every token.
 */
export function Subtitles({ store, compact = false, className }: { store: InterviewStore; compact?: boolean; className?: string }) {
  const utterance = useInterviewState(store, (s) => s.utterance);
  const fallback = useInterviewState(store, (s) => s.lastInterviewerText);
  const show = useInterviewState(store, (s) => s.session?.showSubtitles ?? true);
  const segments = useInterviewState(store, (s) => s.candidate.segments);
  const fresh = useInterviewState(store, (s) => s.candidate.segmentEpoch === s.candidate.epoch);
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const interviewer = useInterviewState(store, (s) => s.session?.interviewerName ?? "Interviewer");
  // A primitive (id + text) so the selector result is stable between store updates.
  const announcement = useInterviewState(store, (s) => {
    for (let i = s.utterances.length - 1; i >= 0; i -= 1) {
      const u = s.utterances[i]!;
      if (u.ended && u.kind !== "acknowledgment" && u.text.trim()) return `${u.id}\u0000${u.text.trim()}`;
    }
    return "";
  });
  const announced = announcement.slice(announcement.indexOf("\u0000") + 1);

  const text = utterance?.text.trim() ? utterance.text : fallback;
  const words = text.trim() ? text.trim().split(/\s+/) : [];
  const spokenWords = utterance && utterance.text.trim() ? Math.min(words.length, utterance.mode === "pending" ? 0 : utterance.spokenWords) : words.length;
  const allSpoken = !utterance || utterance.delivered || utterance.mode === "text" || spokenWords >= words.length;
  const spoken = allSpoken ? words.join(" ") : words.slice(0, spokenWords).join(" ");
  const upcoming = allSpoken ? "" : words.slice(spokenWords).join(" ");
  const quiet = utterance?.kind === "acknowledgment" && Boolean(utterance.text.trim());
  const candidateText = fresh ? segments.map((s) => s.text.trim()).filter(Boolean).join(" ") : "";
  const candidateActive = turn === "candidate_speaking" || turn === "listening" || turn === "processing";

  return (
    <div className={cn("grid gap-3 text-center", !show && "sr-only", className)}>
      <p
        data-testid="subtitle-interviewer"
        aria-hidden={show ? undefined : true}
        className={cn(
          "mx-auto max-w-3xl text-balance",
          compact ? "text-left text-sm leading-6" : quiet ? "text-base leading-7" : "text-lg leading-8 font-medium sm:text-[1.375rem] sm:leading-9",
          quiet ? "text-fg-muted" : "text-fg",
        )}
      >
        {spoken}
        {upcoming ? <span className="text-fg-muted/80">{spoken ? " " : ""}{upcoming}</span> : null}
      </p>
      <p
        data-testid="subtitle-candidate"
        className={cn(
          "mx-auto max-w-2xl transition-opacity duration-300",
          compact ? "text-left text-[13px] leading-5" : "text-[15px] leading-6",
          candidateText ? "opacity-100" : "opacity-0",
          candidateActive ? "text-signal-fg" : "text-fg-muted",
        )}
      >
        {candidateText ? (
          <>
            <span className="sr-only">You said: </span>
            {wordCount(candidateText) > 60 ? `…${candidateText.split(/\s+/).slice(-60).join(" ")}` : candidateText}
          </>
        ) : null}
      </p>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement ? `${interviewer}: ${announced}` : ""}
      </p>
    </div>
  );
}
