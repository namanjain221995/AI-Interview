import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router";

import { StageShell } from "@/components/layout/StageShell";
import type { PortalInterviewView } from "@/features/candidate/api";
import { resumeActivity } from "@/features/candidate/components/ResumeProgressChip";
import { DEFAULT_PREFS, readLocal, writeLocal, type ClientPrefs } from "@/features/candidate/journey";
import { useInterviewReadiness } from "@/features/candidate/readiness";
import { selectedRound } from "@/features/candidate/round";
import { CodingWorkspace } from "@/features/coding/CodingWorkspace";
import { ActivityPanel, DONE_REASONS } from "@/features/intake/candidate/room/ActivityPanel";
import { ActivityCue, ActivityStage } from "@/features/intake/candidate/room/ActivityStage";
import { effectivePolicy, monitoringSummary, type ConsentFlags } from "@/features/integrity/policy";
import { ScreenShareManager, type ScreenShareEvent } from "@/features/integrity/screenShare";
import { codingRemainingSeconds, clockRunning, createInterviewStore, useInterviewState } from "@/features/realtime/store";
import { useDocumentTitle, usePrefersReducedMotion } from "@/lib/hooks";
import type { CodeLanguage, RunKind } from "@/lib/realtime/protocol";
import { cn } from "@/lib/utils";

import { ControlDock, EndInterviewDialog, TypedAnswerPanel } from "./components/Controls";
import { PersistentSlot, usePersistentNode } from "./components/PersistentSlot";
import { BeginGate, NoticeArea, ReconnectOverlay } from "./components/Overlays";
import { RoomTopBar, TurnCue } from "./components/RoomChrome";
import { AvatarPanel, CameraPip, MicMeter, PreparingPanel, PresenceLine, Subtitles } from "./components/Stage";
import { useFullscreen, useNow } from "./hooks";
import { InterviewRuntime, MediaAccessError } from "./runtime";
import { useAvatarController } from "./useAvatarController";

/** How long the brief "Done" stays after an intake activity ends. */
const ACTIVITY_DONE_MS = 1400;

function describeBeginError(error: unknown): string {
  if (error instanceof MediaAccessError) {
    return "We couldn't use your microphone. Allow microphone access in the address bar (the camera or lock icon), then press Begin again.";
  }
  return error instanceof Error && error.message ? error.message : "Something went wrong while starting. Try again.";
}

export interface InterviewRoomProps {
  interview: PortalInterviewView;
  sessionId: string;
  sessionStatus: string;
}

export function InterviewRoom({ interview, sessionId, sessionStatus }: InterviewRoomProps) {
  const navigate = useNavigate();
  const reducedMotion = usePrefersReducedMotion();
  const round = selectedRound(interview);
  const store = useMemo(() => createInterviewStore(), []);
  const prefs = useMemo(() => readLocal<ClientPrefs>(interview.id, "prefs", DEFAULT_PREFS), [interview.id]);
  const consent = useMemo(() => readLocal<ConsentFlags | null>(interview.id, "consent", null), [interview.id]);
  const liveIntegrity = useInterviewState(store, (s) => s.session?.integrity ?? null);
  const policy = useMemo(() => effectivePolicy(interview.policy, liveIntegrity, consent), [interview.policy, liveIntegrity, consent]);
  const [runtime, setRuntime] = useState<InterviewRuntime | null>(null);
  const runtimeRef = useRef<InterviewRuntime | null>(null);
  const screenRef = useRef<ScreenShareManager | null>(null);
  /** Screen-share observations made before the runtime exists (sharing happens in the lobby). */
  const pendingScreenEvents = useRef<Array<{ type: Exclude<ScreenShareEvent, "screen_share_stopped">; details: Record<string, unknown> }>>([]);
  const [screenShared, setScreenShared] = useState(false);
  const [screenStopped, setScreenStopped] = useState(false);
  const screenStoppedRef = useRef(false);
  const [starting, setStarting] = useState(false);
  const [beginError, setBeginError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const fullscreen = useFullscreen();
  const readiness = useInterviewReadiness(interview, sessionId);
  const [startAnyway, setStartAnyway] = useState(false);
  // The interviewer is rendered once into this node and moved between layouts (never remounted).
  const avatarNode = usePersistentNode();

  const begun = useInterviewState(store, (s) => s.begun);
  const turn = useInterviewState(store, (s) => s.session?.turnState ?? "idle");
  const status = useInterviewState(store, (s) => s.session?.status ?? sessionStatus);
  const preparing = useInterviewState(store, (s) => s.preparing);
  const hasUtterance = useInterviewState(store, (s) => s.utterances.length > 0);
  const coding = useInterviewState(store, (s) => s.coding);
  const codingActive = Boolean(coding && !coding.ended);
  // Intake: a reading or speaking exercise takes over the stage (never during coding).
  const activity = useInterviewState(store, (s) => s.activity);
  const activityActive = Boolean(activity) && !codingActive;

  useEffect(() => {
    // Until the server's first state sync, show the round's full length (paused).
    const seconds = (round?.durationMinutes ?? 0) * 60;
    store.dispatch({
      type: "seed",
      session: { id: sessionId, status: sessionStatus as never, interviewerName: interview.interviewerName, roundName: round?.name ?? "", timeLimitS: seconds, remainingS: seconds },
    });
  }, [store, sessionId, sessionStatus, interview.interviewerName, round?.name, round?.durationMinutes]);

  const avatar = useAvatarController(interview, prefs, reducedMotion);

  useEffect(() => {
    runtime?.setAvatar(avatar);
  }, [runtime, avatar]);

  // After activity.end: a brief "Done" (finished exercises only), then the conversation layout returns.
  const endedActivityId = activity?.status === "ended" ? activity.id : null;
  const endedWithDone = activity?.status === "ended" && DONE_REASONS.has(activity.endReason ?? "");
  useEffect(() => {
    if (!endedActivityId) return;
    const timer = setTimeout(() => store.dispatch({ type: "activity.dismiss", activityId: endedActivityId }), endedWithDone ? ACTIVITY_DONE_MS : 0);
    return () => clearTimeout(timer);
  }, [endedActivityId, endedWithDone, store]);

  // Typing is for answers: close the panel while reading aloud, reading silently or preparing.
  const activityKey = activity ? `${activity.id}:${activity.status}:${activity.phase ?? ""}` : "";
  useEffect(() => {
    if (activityKey && runtimeRef.current && !runtimeRef.current.typingAllowed()) setTyping(false);
  }, [activityKey]);

  useEffect(
    () => () => {
      runtimeRef.current?.dispose();
      runtimeRef.current = null;
      screenRef.current?.stop();
    },
    [],
  );

  const goComplete = useCallback(() => {
    writeLocal(interview.id, "session", sessionId);
    navigate(`/candidate/${interview.id}/complete?session=${encodeURIComponent(sessionId)}`, { replace: true });
  }, [interview.id, sessionId, navigate]);

  // Screen recording needs a shared screen too, even when sharing itself isn't required.
  const screenRequired = (policy.integrity.enabled && policy.integrity.screen_share_required) || policy.recordScreen;

  const forwardScreenEvent = (type: ScreenShareEvent, details: Record<string, unknown>) => {
    // A stop is reported by the runtime itself (screenShareStopped), with the room banner.
    if (type === "screen_share_stopped") return;
    const event = { type, details: { ...details, restarted: screenStoppedRef.current } };
    if (runtimeRef.current) runtimeRef.current.recordScreenShareEvent(event.type, event.details);
    else if (pendingScreenEvents.current.length < 8) pendingScreenEvents.current.push(event);
  };

  const shareScreen = async () => {
    const manager = new ScreenShareManager({
      onEvent: forwardScreenEvent,
      onStopped: () => {
        setScreenShared(false);
        if (runtimeRef.current) {
          runtimeRef.current.screenShareStopped();
          screenStoppedRef.current = true;
          setScreenStopped(true);
        }
      },
    });
    try {
      await manager.start();
      screenRef.current?.stop();
      screenRef.current = manager;
      setScreenShared(true);
      if (runtimeRef.current && screenStoppedRef.current) {
        runtimeRef.current.screenShareRestarted(manager);
        screenStoppedRef.current = false;
        setScreenStopped(false);
      }
      if (manager.displaySurface && manager.displaySurface !== "monitor") {
        setBeginError("You shared a window or tab. For this interview, please share your entire screen.");
      } else {
        setBeginError(null);
      }
    } catch {
      setBeginError("Screen sharing was cancelled. This interview needs your entire screen shared.");
    }
  };

  const begin = () => {
    setBeginError(null);
    let rt = runtimeRef.current;
    if (!rt) {
      rt = new InterviewRuntime({
        interviewId: interview.id,
        sessionId,
        store,
        policy,
        prefs,
        avatar,
        screenShare: screenRef.current,
        onEnded: goComplete,
      });
      for (const event of pendingScreenEvents.current.splice(0)) rt.recordScreenShareEvent(event.type, event.details);
      runtimeRef.current = rt;
      setRuntime(rt);
    }
    // Synchronous part first: audio unlock + fullscreen need the click's user activation.
    rt.prepareGesture();
    setStarting(true);
    rt.begin()
      .catch((error: unknown) => {
        runtimeRef.current?.dispose();
        runtimeRef.current = null;
        setRuntime(null);
        setBeginError(describeBeginError(error));
      })
      .finally(() => setStarting(false));
  };

  // Server ended the session while the gate was still up (e.g. reopened after completion).
  useEffect(() => {
    if (["completed", "completing", "aborted"].includes(status) && !begun) goComplete();
  }, [status, begun, goComplete]);

  const now = useNow(1000);
  const codingRemaining = useInterviewState(store, (s) => codingRemainingSeconds(s.coding, now, clockRunning(s)));
  const resuming = ["live", "paused"].includes(sessionStatus);
  const fullscreenPrompt = begun && policy.integrity.enabled && policy.integrity.require_fullscreen && !fullscreen && !!runtime && !runtime.isEnded;
  const allowTyped = useInterviewState(store, (s) => s.session?.allowTypedAnswers ?? true);
  const gateUp = !begun || (!runtime && !starting);
  const ready = readiness.ready || startAnyway || resuming;

  useDocumentTitle(
    gateUp
      ? ready
        ? "Ready to begin"
        : "Preparing your interview"
      : preparing
        ? "Starting soon"
        : codingActive
          ? "Coding exercise"
          : activityActive
            ? activity?.kind === "topic_card"
              ? "Speaking exercise"
              : "Reading exercise"
            : "Interview in progress",
  );

  const notices = (placement: "overlay" | "inline") => (
    <NoticeArea
      store={store}
      placement={placement}
      fullscreenPrompt={fullscreenPrompt}
      onFullscreen={() => runtimeRef.current?.requestFullscreen()}
      onTypeAnswer={allowTyped ? () => setTyping(true) : null}
      screenSharePrompt={screenRequired && screenStopped}
      onShareScreen={() => void shareScreen()}
    />
  );

  const onCodeUpdate = useCallback(
    (u: { code: string; language: CodeLanguage; version: number; cursorLine?: number }) => coding && runtimeRef.current?.codeUpdate(coding.sessionId, u),
    [coding],
  );
  const onPaste = useCallback(
    (length: number, lines: number, blocked: boolean) => runtimeRef.current?.codingPaste(length, lines, blocked, policy.largePasteThreshold),
    [policy.largePasteThreshold],
  );

  const preparingNow = preparing && !hasUtterance;
  const observed = useMemo(() => monitoringSummary(policy.integrity), [policy.integrity]);
  const resumeNote = readiness.resume ? resumeActivity(readiness.resume) : null;

  return (
    <StageShell
      header={<RoomTopBar store={store} fallbackRound={round?.name ?? ""} fallbackInterviewer={interview.interviewerName} observed={observed} />}
      footer={
        gateUp ? null : (
          <div className="relative">
            {typing ? (
              <div className="absolute inset-x-0 bottom-full z-20 flex justify-center px-3 pb-3 sm:px-4">
                <TypedAnswerPanel runtime={runtime} store={store} onClose={() => setTyping(false)} className="w-full max-w-2xl" />
              </div>
            ) : null}
            <ControlDock store={store} runtime={runtime} typingOpen={typing} onOpenTyping={() => setTyping((v) => !v)} onEnd={() => setConfirmEnd(true)} />
          </div>
        )
      }
    >
      <div
        data-testid="interview-room"
        data-turn-state={turn}
        data-session-status={status}
        data-coding={codingActive ? "true" : "false"}
        data-preparing={preparingNow ? "true" : "false"}
        data-activity-kind={activityActive ? activity?.kind : undefined}
        data-activity-phase={activityActive ? (activity?.phase ?? "armed") : undefined}
        className="relative h-full min-h-0"
      >
        {createPortal(<AvatarPanel controller={avatar} compact={codingActive} className="size-full" />, avatarNode)}

        <div className="relative h-full min-h-0" inert={gateUp || undefined}>
          {!codingActive && !activityActive ? notices("overlay") : null}

          {codingActive && coding ? (
            <CodingWorkspace
              coding={coding}
              remainingS={codingRemaining}
              blockPaste={policy.integrity.block_paste_in_coding}
              pasteThreshold={policy.largePasteThreshold}
              onCodeUpdate={onCodeUpdate}
              onRun={(kind: RunKind, code, language) => runtimeRef.current?.codeRun(coding.sessionId, kind, code, language)}
              onSubmit={(code, language) => runtimeRef.current?.codeSubmit(coding.sessionId, code, language)}
              onHint={() => runtimeRef.current?.hintRequest(coding.sessionId)}
              onLanguage={(language) => runtimeRef.current?.codeLanguage(coding.sessionId, language)}
              onPaste={onPaste}
              aside={
                <div className="grid gap-3">
                  {notices("inline")}
                  <div className="relative aspect-[4/3]">
                    <PersistentSlot node={avatarNode} className="absolute inset-0" />
                    <CameraPip runtime={runtime} begun={begun} className="absolute right-0 bottom-0 aspect-[4/3] w-[36%] rounded-xl shadow-overlay" />
                  </div>
                  <PresenceLine runtime={runtime} store={store} className="-my-2" />
                  <TurnCue store={store} className="justify-start text-[13px]" />
                  <Subtitles store={store} compact className="max-h-44 overflow-y-auto scrollbar-thin" />
                </div>
              }
            />
          ) : activityActive ? (
            <ActivityStage
              presence={<PersistentSlot node={avatarNode} className="absolute inset-0" />}
              cue={<ActivityCue store={store} />}
              camera={<CameraPip runtime={runtime} begun={begun} className="aspect-[4/3] w-full rounded-xl shadow-overlay" />}
              notices={notices("inline")}
            >
              <ActivityPanel
                store={store}
                onFinish={(activityId, phase) => runtimeRef.current?.activityFinish(activityId, phase)}
                micMeter={<MicMeter runtime={runtime} surface="panel" />}
                recording={(policy.recording.record_answer_audio && consent?.audio_recording !== false) || policy.recordVideo}
                interviewId={interview.id}
              />
            </ActivityStage>
          ) : (
            <div className="mx-auto flex h-full max-w-6xl flex-col px-3 pt-3 pb-2 sm:px-6 sm:pt-4 sm:pb-3">
              <div className="relative mx-auto min-h-0 w-full max-w-4xl flex-1">
                {/* No frame: the orb floats on the room background with its own glow. */}
                <PersistentSlot node={avatarNode} className="absolute inset-0" />
                <CameraPip runtime={runtime} begun={begun} className="absolute right-1 bottom-1 aspect-[4/3] w-24 rounded-xl shadow-overlay sm:right-2 sm:bottom-2 sm:w-40 lg:w-44" />
              </div>
              <div className={cn("contents", gateUp && "invisible")}>
                <PresenceLine runtime={runtime} store={store} className="mx-auto mt-2 w-full max-w-3xl" />
                <TurnCue store={store} preparing={preparingNow} className="mt-1" />
                {preparingNow ? (
                  <PreparingPanel interviewerName={interview.interviewerName} activity={resumeNote} className="mt-4" />
                ) : (
                  <Subtitles store={store} className="mt-3 min-h-[6.5rem] px-1 sm:mt-4 sm:min-h-[7.5rem] sm:px-2" />
                )}
              </div>
            </div>
          )}
        </div>

        {gateUp ? (
          <BeginGate
            preparation={{
              ready,
              phase: readiness.phase,
              activity: resumeNote,
              resume: readiness.resume,
              canStartAnyway: readiness.canStartAnyway,
              onStartAnyway: () => setStartAnyway(true),
              resumeHref: `/candidate/${interview.id}/resume`,
            }}
            interviewerName={interview.interviewerName}
            roundName={round?.name ?? null}
            minutes={round?.durationMinutes ?? null}
            resuming={resuming}
            integrity={policy.integrity}
            screenRequired={screenRequired}
            screenShared={screenShared}
            onShareScreen={() => void shareScreen()}
            onBegin={begin}
            starting={starting}
            error={beginError}
            systemCheckHref={`/candidate/${interview.id}/system-check`}
          />
        ) : null}

        <ReconnectOverlay store={store} onRetry={() => runtimeRef.current?.retryConnection()} />
      </div>

      <EndInterviewDialog open={confirmEnd} onOpenChange={setConfirmEnd} onConfirm={() => runtimeRef.current?.endInterview()} />
    </StageShell>
  );
}
