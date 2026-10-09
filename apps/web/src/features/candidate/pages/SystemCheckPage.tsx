import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Check, CircleAlert, Globe, Loader2, MonitorUp, Mic, Radio, ScanFace, Sparkles, TriangleAlert, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { preloadAvatar } from "@/features/avatar";
import { DEFAULT_INTEGRITY, type ConsentFlags } from "@/features/integrity/policy";
import { ScreenShareManager } from "@/features/integrity/screenShare";
import { MIC_CONSTRAINTS } from "@/features/realtime/audioCapture";
import { errorMessage } from "@/lib/api";
import { useDocumentTitle } from "@/lib/hooks";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";

import { createOrResumeSession, getDisclosure, getWsTicket, postSystemCheck } from "../api";
import { LevelMeter } from "../components/LevelMeter";
import { ResumeProgressChip } from "../components/ResumeProgressChip";
import { resumeInProgress, useResumeStatus } from "../readiness";
import { StepFooter, StepHeader } from "../components/StepLayout";
import { JourneyBoundary, useJourney, type JourneyContext } from "../hooks";
import { DEFAULT_PREFS, markVisited, readLocal, writeLocal, type ClientPrefs } from "../journey";
import { AvatarProbe, type AvatarProbeResult } from "../systemCheck/AvatarProbe";
import { FaceCheckOverlay } from "../systemCheck/FaceCheckOverlay";
import { faceCheckAllowed, faceCheckResult, type FaceCheckSnapshot } from "../systemCheck/faceCheck";
import {
  avatarVerdict,
  describeMediaError,
  detectCapabilities,
  evaluateBrowser,
  networkVerdict,
  parseBrowser,
  playChime,
  probeRealtime,
  type CheckKey,
  type CheckResult,
  type CheckState,
} from "../systemCheck/checks";
import { useAudioLevel } from "../systemCheck/useAudioLevel";

export default function SystemCheckPage() {
  useDocumentTitle("Check your setup");
  const { query, ctx } = useJourney("system-check", { width: "wide" });
  return (
    <div>
      <StepHeader
        title="Check your setup"
        lede="We'll test your camera, microphone, speakers and connection, and make sure the interviewer runs smoothly on this device. It takes about 15 seconds."
        aside={ctx ? <ResumeProgressChip interviewId={ctx.interviewId} /> : null}
      />
      <div className="mt-8">
        <JourneyBoundary query={query}>{ctx ? <SystemCheck ctx={ctx} /> : null}</JourneyBoundary>
      </div>
    </div>
  );
}

const CHECK_META: Record<CheckKey, { label: string; icon: ReactNode }> = {
  browser: { label: "Browser", icon: <Globe /> },
  camera: { label: "Camera", icon: <Camera /> },
  face: { label: "Face visible", icon: <ScanFace /> },
  microphone: { label: "Microphone", icon: <Mic /> },
  speakers: { label: "Speakers", icon: <Volume2 /> },
  network: { label: "Connection", icon: <Radio /> },
  avatar: { label: "Interviewer view", icon: <Sparkles /> },
  screen: { label: "Screen sharing", icon: <MonitorUp /> },
};

const IDLE: CheckResult = { state: "pending", summary: "Not checked yet" };

function StateIcon({ state }: { state: CheckState }) {
  return (
    <span
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-full",
        state === "pass" && "bg-success-soft text-success-fg",
        state === "warn" && "bg-warning-soft text-warning-fg",
        state === "fail" && "bg-danger-soft text-danger-fg",
        state === "running" && "bg-accent-soft text-accent-fg",
        state === "pending" && "bg-track text-fg-muted ring-1 ring-inset ring-line",
      )}
      aria-hidden="true"
    >
      {state === "pass" ? <Check className="size-3.5" strokeWidth={3} /> : null}
      {state === "warn" ? <TriangleAlert className="size-3.5" /> : null}
      {state === "fail" ? <X className="size-3.5" strokeWidth={3} /> : null}
      {state === "running" ? <Loader2 className="size-3.5 animate-spin" /> : null}
      {state === "pending" ? <span className="size-1.5 rounded-full bg-current" /> : null}
    </span>
  );
}

const STATE_TEXT: Record<CheckState, string> = { pending: "not checked", running: "checking", pass: "passed", warn: "works with a note", fail: "needs attention" };

function CheckRow({ check, result, action }: { check: CheckKey; result: CheckResult; action?: ReactNode }) {
  const meta = CHECK_META[check];
  return (
    <li data-testid={`syscheck-status-${check}`} data-state={result.state} className="flex gap-3.5 py-3.5">
      <span className="mt-0.5 text-fg-subtle [&_svg]:size-[18px]" aria-hidden="true">
        {meta.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm font-medium text-fg">
            {meta.label}
            <span className="sr-only">: {STATE_TEXT[result.state]}</span>
          </p>
          <StateIcon state={result.state} />
        </div>
        <p className={cn("mt-0.5 text-sm", result.state === "fail" ? "text-danger-fg" : "text-fg-muted")}>{result.summary}</p>
        {result.detail ? <p className="mt-1 text-[13px] leading-5 text-fg-subtle">{result.detail}</p> : null}
        {action ? <div className="mt-2.5">{action}</div> : null}
      </div>
    </li>
  );
}

function stopStream(stream: MediaStream | null) {
  for (const track of stream?.getTracks() ?? []) track.stop();
}

function SystemCheck({ ctx }: { ctx: JourneyContext }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const integrity = { ...DEFAULT_INTEGRITY, ...(ctx.interview.policy.integrity ?? {}) };
  const screenRequired = integrity.enabled && integrity.screen_share_required;
  const cameraRequired = integrity.camera_required;
  // The on-device face check runs here only under a consent that described it (nothing is sent from this page).
  const disclosure = useQuery({ queryKey: queryKeys.portal.disclosure(ctx.interviewId), queryFn: () => getDisclosure(ctx.interviewId), meta: { errorToast: false }, staleTime: 60_000 });
  const faceAllowed = faceCheckAllowed(integrity, readLocal<ConsentFlags | null>(ctx.interviewId, "consent", null), disclosure.data);
  const checks: CheckKey[] = ["browser", "camera", ...(faceAllowed ? (["face"] as CheckKey[]) : []), "microphone", "speakers", "network", "avatar", ...(screenRequired ? (["screen"] as CheckKey[]) : [])];

  const [results, setResults] = useState<Record<CheckKey, CheckResult>>(() => Object.fromEntries(checks.map((c) => [c, IDLE])) as Record<CheckKey, CheckResult>);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [probeKey, setProbeKey] = useState<number | null>(null);
  const [prefs, setPrefs] = useState<ClientPrefs>(DEFAULT_PREFS);
  const [rttMs, setRttMs] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [faceSnapshot, setFaceSnapshot] = useState<FaceCheckSnapshot | null>(null);
  const [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const attachVideo = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    setVideoEl(el);
  }, []);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const probeResolver = useRef<((r: AvatarProbeResult) => void) | null>(null);
  const screenRef = useRef<ScreenShareManager | null>(null);
  const { level, silent } = useAudioLevel(stream);
  const resumeStatus = useResumeStatus(ctx.interviewId);
  const stillPreparing = resumeInProgress(resumeStatus.data);

  const set = useCallback((key: CheckKey, result: CheckResult) => setResults((prev) => ({ ...prev, [key]: result })), []);

  useEffect(() => {
    preloadAvatar();
    return () => {
      stopStream(streamRef.current);
      screenRef.current?.stop();
      void audioCtxRef.current?.close().catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = stream;
  }, [stream]);

  useEffect(() => {
    if (silent && results.microphone.state === "pass") {
      set("microphone", { state: "warn", summary: "We can't hear anything from your microphone", detail: "Check that it isn't muted (a key or switch on your headset or laptop), then run the check again." });
    }
  }, [silent, results.microphone.state, set]);

  const runAvatarProbe = () =>
    new Promise<AvatarProbeResult>((resolve) => {
      probeResolver.current = resolve;
      setProbeKey(Date.now());
    });

  const run = async () => {
    // Audio must be unlocked inside the click (autoplay policy), before any await.
    let audioCtx = audioCtxRef.current;
    if (!audioCtx || audioCtx.state === "closed") {
      audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
    }
    const resumed = audioCtx.resume().catch(() => undefined);

    setRunning(true);
    setStarted(true);
    setSaveError(null);
    setResults((prev) => Object.fromEntries(Object.keys(prev).map((k) => [k, k === "screen" && prev.screen?.state === "pass" ? prev.screen : { state: "running", summary: "Checking…" }])) as Record<CheckKey, CheckResult>);

    // Browser
    const caps = detectCapabilities();
    set("browser", evaluateBrowser(caps, { fullscreenRequired: integrity.require_fullscreen }));

    // Camera + microphone (one permission prompt)
    stopStream(streamRef.current);
    let media: MediaStream | null = null;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS, video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" } });
    } catch (both) {
      // Find out which device failed.
      const audio = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS }).catch((e: unknown) => e);
      const video = await navigator.mediaDevices.getUserMedia({ video: true }).catch((e: unknown) => e);
      const tracks: MediaStreamTrack[] = [];
      if (audio instanceof MediaStream) tracks.push(...audio.getTracks());
      else set("microphone", { state: "fail", summary: "Microphone unavailable", detail: describeMediaError(audio ?? both, "microphone") });
      if (video instanceof MediaStream) tracks.push(...video.getTracks());
      else
        set("camera", cameraRequired
          ? { state: "fail", summary: "Camera unavailable", detail: describeMediaError(video ?? both, "camera") }
          : { state: "warn", summary: "Camera unavailable", detail: "This interview doesn't require a camera, so you can continue without one." });
      media = tracks.length ? new MediaStream(tracks) : null;
    }
    streamRef.current = media;
    setStream(media);
    if (media?.getAudioTracks()[0]?.readyState === "live") set("microphone", { state: "pass", summary: "Your microphone works. Say something to see the meter move." });
    if (media?.getVideoTracks()[0]?.readyState === "live") set("camera", { state: "pass", summary: "Your camera works. Make sure your face is well lit and centred." });

    // Speakers
    await resumed;
    if (audioCtx.state === "running") {
      const seconds = playChime(audioCtx);
      await new Promise((r) => setTimeout(r, seconds * 1000));
      set("speakers", { state: "pass", summary: "You should have heard a short chime", detail: "Didn't hear it? Turn up your volume or choose another output device, then play it again." });
    } else {
      set("speakers", { state: "warn", summary: "Sound is blocked in this tab", detail: "Click Play test sound. The interviewer speaks out loud, so you'll need sound on." });
    }

    // Connection (creates or resumes the session, which also starts preparing the interview) + avatar, in parallel.
    const network = (async () => {
      try {
        const session = await createOrResumeSession(ctx.interviewId);
        writeLocal(ctx.interviewId, "session", session.sessionId);
        const probe = await probeRealtime(session.sessionId, getWsTicket);
        // The room's first hello continues from here (see consumeProbeSeq).
        writeLocal(ctx.interviewId, "probe", { sessionId: session.sessionId, seq: probe.lastSeq });
        setRttMs(probe.rttMs);
        set("network", networkVerdict(probe.rttMs, probe.ok));
      } catch (error) {
        set("network", { state: "fail", summary: "We couldn't reach the interview service", detail: errorMessage(error, "Check your connection and run the check again.") });
      }
    })();
    const avatar = (async () => {
      const probe = await runAvatarProbe();
      const verdict = avatarVerdict(probe.mode, probe.fps);
      setPrefs((p) => ({ ...p, avatarMode: verdict.chosen, avatarFps: Math.round(probe.fps) || null }));
      set("avatar", verdict.result);
      setProbeKey(null);
    })();
    await Promise.all([network, avatar]);

    if (screenRequired) {
      setResults((prev) => (prev.screen.state === "pass" ? prev : { ...prev, screen: { state: "pending", summary: "Share your entire screen to test it" } }));
    }
    setRunning(false);
  };

  const testScreen = async () => {
    set("screen", { state: "running", summary: "Choose Entire screen in the browser dialog…" });
    const manager = new ScreenShareManager({ onEvent: () => undefined });
    screenRef.current?.stop();
    screenRef.current = manager;
    try {
      await manager.start();
      const surface = manager.displaySurface;
      manager.stop();
      set(
        "screen",
        surface && surface !== "monitor"
          ? { state: "fail", summary: "Please share your entire screen", detail: "You shared a window or tab. In the dialog, choose Entire screen." }
          : { state: "pass", summary: "Screen sharing works. You'll share again when the interview begins." },
      );
    } catch {
      set("screen", { state: "fail", summary: "Screen sharing was cancelled", detail: "This interview requires sharing your entire screen. Try again and choose Entire screen." });
    }
  };

  const replayChime = () => {
    const audioCtx = audioCtxRef.current ?? new AudioContext();
    audioCtxRef.current = audioCtx;
    void audioCtx.resume().then(() => {
      playChime(audioCtx);
      set("speakers", { state: "pass", summary: "You should have heard a short chime", detail: "Didn't hear it? Turn up your volume or choose another output device." });
    });
  };

  const hasVideo = Boolean(stream?.getVideoTracks().length);
  const cameraWorks = hasVideo && results.camera.state === "pass";
  const resultFor = (check: CheckKey): CheckResult => (check === "face" ? faceCheckResult(faceSnapshot, cameraWorks) : (results[check] ?? IDLE));
  // The face check only ever gives tips: it never holds the candidate back.
  const done = started && !running && checks.every((c) => c === "face" || results[c].state === "pass" || results[c].state === "warn");
  const failures = checks.filter((c) => c !== "face" && results[c].state === "fail");

  const proceed = async () => {
    setSaving(true);
    setSaveError(null);
    const ua = navigator.userAgent;
    const conn = (navigator as Navigator & { connection?: { downlink?: number } }).connection;
    try {
      await postSystemCheck(ctx.interviewId, {
        camera: results.camera.state === "pass",
        microphone: results.microphone.state !== "fail",
        // "warn" means sound was blocked before a click; Begin unlocks audio, so it isn't a failure.
        speakers: results.speakers.state !== "fail",
        webgl: prefs.avatarMode === "3d",
        fullscreen: detectCapabilities().fullscreen,
        ...(screenRequired ? { screen_share: results.screen?.state === "pass" } : {}),
        network: { rtt_ms: rttMs !== null ? Math.round(rttMs) : null, downlink_mbps: conn?.downlink ?? null },
        browser: { ...parseBrowser(ua), user_agent: ua },
        displays: (window.screen as Screen & { isExtended?: boolean }).isExtended ? 2 : 1,
        avatar_mode: prefs.avatarMode,
        ...(prefs.avatarFps !== null ? { avatar_fps: prefs.avatarFps } : {}),
      });
      const session = await createOrResumeSession(ctx.interviewId);
      writeLocal(ctx.interviewId, "session", session.sessionId);
      writeLocal(ctx.interviewId, "prefs", prefs);
      markVisited(ctx.interviewId, "system-check");
      stopStream(streamRef.current);
      streamRef.current = null;
      void queryClient.invalidateQueries({ queryKey: queryKeys.portal.interview(ctx.interviewId) });
      navigate(`/candidate/${ctx.interviewId}/interview?session=${encodeURIComponent(session.sessionId)}`);
    } catch (error) {
      setSaveError(errorMessage(error, "We couldn't save the check. Try again."));
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-3">
          <div data-testid="syscheck-camera-preview" className="relative aspect-[4/3] overflow-hidden rounded-xl bg-surface-sunken ring-1 ring-line">
            {hasVideo ? (
              <video ref={attachVideo} autoPlay muted playsInline className="size-full -scale-x-100 object-cover" aria-label="Your camera preview" />
            ) : (
              <div className="flex size-full flex-col items-center justify-center gap-2 px-6 text-center">
                <Camera className="size-6 text-fg-subtle" aria-hidden="true" />
                <p className="text-sm text-fg-muted">{started ? "No camera picture" : "Your camera preview appears here"}</p>
              </div>
            )}
            {hasVideo && faceAllowed ? <FaceCheckOverlay video={videoEl} enabled={cameraWorks} onSnapshot={setFaceSnapshot} /> : null}
            {probeKey !== null ? (
              <div className="absolute top-3 right-3 h-[42%] w-[34%] overflow-hidden rounded-lg bg-surface-raised ring-1 ring-line">
                <AvatarProbe
                  key={probeKey}
                  interviewerName={ctx.interview.interviewerName}
                  modelUrl={ctx.interview.policy.avatar?.model_url || undefined}
                  onResult={(r) => probeResolver.current?.(r)}
                  className="size-full"
                />
              </div>
            ) : null}
          </div>
          <div className="flex items-center justify-between gap-4 rounded-lg border border-line bg-surface px-4 py-3">
            <div className="flex items-center gap-2 text-sm text-fg-muted">
              <Mic className="size-4" aria-hidden="true" />
              {stream?.getAudioTracks().length ? "Speak to test your microphone" : "Microphone"}
            </div>
            <LevelMeter level={level} tone={stream ? "signal" : "muted"} />
          </div>
        </div>

        <div>
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface px-4 sm:px-5" aria-live="polite">
            {checks.map((check) => (
              <CheckRow
                key={check}
                check={check}
                result={resultFor(check)}
                action={
                  check === "speakers" && started && !running ? (
                    <Button size="xs" variant="outline" leftIcon={<Volume2 />} onClick={replayChime}>
                      Play test sound
                    </Button>
                  ) : check === "screen" && started && !running && results.screen.state !== "pass" ? (
                    <Button size="xs" variant="outline" leftIcon={<MonitorUp />} onClick={() => void testScreen()}>
                      Share screen to test
                    </Button>
                  ) : undefined
                }
              />
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button size="lg" variant={started ? "outline" : "primary"} loading={running} loadingText="Checking" onClick={() => void run()} data-testid="syscheck-run">
              {started ? "Run checks again" : "Start checks"}
            </Button>
            {!started ? <p className="text-[13px] text-fg-muted">Your browser will ask to use your camera and microphone.</p> : null}
          </div>
          {failures.length && !running ? (
            <Callout tone="warning" className="mt-4" title="Fix the items marked above, then run the checks again">
              Most problems are solved by allowing camera and microphone access in the address bar, or closing other video-call apps.
            </Callout>
          ) : null}
          {saveError ? (
            <Callout tone="danger" className="mt-4">
              {saveError}
            </Callout>
          ) : null}
        </div>
      </div>

      <StepFooter
        onBack={ctx.back}
        note={
          done
            ? stillPreparing
              ? "Everything you need is working. We're still preparing your interview from your résumé; Begin unlocks on the next screen as soon as it's ready."
              : "Everything you need is working. The interview starts on the next screen when you press Begin."
            : undefined
        }
      >
        <Button size="lg" disabled={!done} loading={saving} loadingText="Getting ready" onClick={() => void proceed()} data-testid="syscheck-continue">
          Continue to interview
        </Button>
      </StepFooter>
      {failures.includes("network") ? (
        <p className="mt-3 flex items-center gap-1.5 text-[13px] text-fg-muted">
          <CircleAlert className="size-3.5" aria-hidden="true" /> The interview needs a live connection to work.
        </p>
      ) : null}
    </div>
  );
}
