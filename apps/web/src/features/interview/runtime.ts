/**
 * InterviewRuntime: wires the realtime client, microphone capture, TTS
 * playback (or browser speech / text fallbacks), the avatar controller and the
 * integrity monitor for one live session. React components only read the
 * store and call the control methods; everything with side effects is here.
 */
import type { AvatarController, AvatarStatus } from "@/features/avatar";
import { estimateWordDurationMs } from "@/features/avatar";
import { completeRecording, createOrResumeSession, endPortalSession, getDisclosure, getWsTicket, postIntegrityEvents, uploadFrame, uploadRecording } from "@/features/candidate/api";
import { readLocal, writeLocal, type ClientPrefs } from "@/features/candidate/journey";
import { IntegrityBatcher } from "@/features/integrity/batcher";
import { RoomFaceCheck } from "@/features/integrity/face/roomFaceCheck";
import { BackoffUploadsError, FrameCapturer, StopUploadsError } from "@/features/integrity/frames";
import { IntegrityMonitor } from "@/features/integrity/monitor";
import type { EffectivePolicy } from "@/features/integrity/policy";
import { SessionRecorder, type RecordingFatal } from "@/features/integrity/recorder";
import { ScreenShareManager, type ScreenShareEvent } from "@/features/integrity/screenShare";
import { AudioCapture, MIC_CONSTRAINTS } from "@/features/realtime/audioCapture";
import { AudioPlayback, type PlaybackReport, type RecordingMix, type SpeechCueOut } from "@/features/realtime/audioPlayback";
import { RealtimeClient } from "@/features/realtime/client";
import { BrowserSpeech, estimateReadingMs } from "@/features/realtime/speechFallback";
import { activityDockMode } from "@/features/intake/candidate/room/activityModel";
import { clockRunning, type InterviewStore } from "@/features/realtime/store";
import { isApiError } from "@/lib/api";
import {
  FRAME_TTS_AUDIO,
  unpackFrame,
  type ActivityPhase,
  type ActivityPhaseMsg,
  type ClientInfo,
  type ClientMessage,
  type CodeLanguage,
  type IntegrityEventType,
  type RunKind,
  type ServerMessage,
} from "@/lib/realtime/protocol";

export interface RuntimeApi {
  fetchTicket: (sessionId: string) => Promise<{ ticket: string; ws_url: string }>;
  postIntegrityEvents: typeof postIntegrityEvents;
  uploadFrame: typeof uploadFrame;
  uploadRecording: typeof uploadRecording;
  completeRecording: typeof completeRecording;
  endSession: typeof endPortalSession;
}

const DEFAULT_API: RuntimeApi = { fetchTicket: getWsTicket, postIntegrityEvents, uploadFrame, uploadRecording, completeRecording, endSession: endPortalSession };
/** A start the server could not complete is retried this many times before the candidate has to act (S5). */
const START_RETRIES = 3;
/** A recording part that failed for a transient reason is retried as a new part this many times. */
const RECORDING_RESTARTS = 3;
const RECORDING_RETRY_MS = 15_000;

export interface InterviewRuntimeOptions {
  interviewId: string;
  sessionId: string;
  store: InterviewStore;
  policy: EffectivePolicy;
  prefs: ClientPrefs;
  avatar?: AvatarController | null;
  screenShare?: ScreenShareManager | null;
  api?: Partial<RuntimeApi>;
  onEnded?: () => void;
  onScreenShareStopped?: () => void;
}

export class MediaAccessError extends Error {
  constructor(
    message: string,
    readonly device: "microphone" | "camera",
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "MediaAccessError";
  }
}

const NO_AUDIO_GRACE_MS = 2500;

export class InterviewRuntime {
  readonly store: InterviewStore;
  readonly sessionId: string;
  private readonly opts: InterviewRuntimeOptions;
  private readonly api: RuntimeApi;
  private client: RealtimeClient | null = null;
  private capture: AudioCapture | null = null;
  private playback: AudioPlayback | null = null;
  private readonly speech = new BrowserSpeech();
  private monitor: IntegrityMonitor | null = null;
  private batcher: IntegrityBatcher | null = null;
  private frames: FrameCapturer | null = null;
  private screenFrames: FrameCapturer | null = null;
  /** On-device face check (policy + consent); its observations go through the batcher. */
  private faceCheck: RoomFaceCheck | null = null;
  /**
   * Conversation recording: camera picture + both voices (or audio only), one part per
   * recorder run. Policy and consent are checked by the server on every upload.
   */
  private recorder: SessionRecorder | null = null;
  private recordingMix: RecordingMix | null = null;
  private recordingStarting = false;
  /** Recording refused for good (policy, consent, session over) or impossible in this browser. */
  private recordingDisabled = false;
  private recordingRestarts = 0;
  private recordingRetryTimer: ReturnType<typeof setTimeout> | null = null;
  private reportedRecordingProblem = false;
  private recordedVideo: MediaStreamTrack | null = null;
  /** Why the current part started (begin, server, camera_ended, retry): for tests and diagnostics. */
  recordingReason: string | null = null;
  /** The server's `record_video` (policy AND consent) from session.state; null until known. */
  private serverRecordVideo: boolean | null = null;
  private lastStateSyncAt = Number.NEGATIVE_INFINITY;
  private lastConnectedAt = Number.NEGATIVE_INFINITY;
  /** Shared-screen recording (policy record_screen + consent); a new part for every share. */
  private screenRecorder: SessionRecorder | null = null;
  /** Screen-share observations made before the monitor existed (sharing happens in the lobby). */
  private readonly pendingScreenEvents: Array<{ type: ScreenShareEvent; details: Record<string, unknown> }> = [];
  private reportedScreenRecordingUnsupported = false;
  private media: MediaStream | null = null;
  private avatar: AvatarController | null;
  private avatarUnsub: (() => void) | null = null;
  private reportedAvatarErrors = new Set<string>();
  private startSent = false;
  private ended = false;
  private disposed = false;
  private micLevel = 0;
  private micSpeaking = false;
  private lastAvatarLevelAt = 0;
  private readonly levelListeners = new Set<(level: number, speaking: boolean) => void>();
  /** Utterances that switched to browser speech/text after (some) audio. */
  private readonly pendingFallback = new Map<string, string>();
  private readonly audioPlayedMs = new Map<string, number>();
  private readonly noAudioTimers = new Map<string, ReturnType<typeof setTimeout>>();
  /**
   * Text-only delivery per utterance. The server sends one tts.fallback per chunk, so
   * reading time accumulates; the utterance counts as delivered once its text is
   * complete (ai.utterance.end) and the reading time has passed.
   */
  private readonly textTracks = new Map<string, { startedAt: number; deadline: number; offset: number; closed: boolean; timer: ReturnType<typeof setTimeout> | null }>();
  /** Utterances whose ai.utterance.end arrived (their text is complete). */
  private readonly endedUtterances = new Set<string>();
  private endFallbackTimer: ReturnType<typeof setTimeout> | null = null;
  private startRetries = 0;
  private ttsMode: ClientPrefs["ttsMode"];
  private micAvailable = true;
  /** Intake read-and-relay: removes the passage at the local countdown zero even if the server is late. */
  private relayHideTimer: ReturnType<typeof setTimeout> | null = null;
  private lastActivityFinishAt = Number.NEGATIVE_INFINITY;
  private cameraOnly: MediaStream | null = null;
  private readonly mediaListeners = new Set<() => void>();
  private pendingFullscreenDecline: string | null = null;

  constructor(options: InterviewRuntimeOptions) {
    this.opts = options;
    this.store = options.store;
    this.sessionId = options.sessionId;
    this.api = { ...DEFAULT_API, ...options.api };
    this.avatar = options.avatar ?? null;
    this.ttsMode = options.prefs.ttsMode;
    if (this.ttsMode === "browser" && !BrowserSpeech.isSupported()) this.ttsMode = "text";
    this.attachAvatarStatus();
  }

  // ------------------------------------------------------------------ setup

  setAvatar(avatar: AvatarController | null): void {
    this.avatar = avatar;
    this.attachAvatarStatus();
    if (avatar && this.playback) avatar.attachAudio(this.playback.context, this.playback.analyser);
    const s = this.store.getState().session;
    if (avatar && s) avatar.setState(s.avatarState);
  }

  private attachAvatarStatus(): void {
    this.avatarUnsub?.();
    this.avatarUnsub = null;
    if (!this.avatar) return;
    this.avatarUnsub = this.avatar.subscribe((status: AvatarStatus) => {
      if (status.error && !this.reportedAvatarErrors.has(status.error)) {
        this.reportedAvatarErrors.add(status.error);
        this.send({ type: "client.error", component: "avatar", message: `${status.mode}: ${status.error}`.slice(0, 500), fatal: false });
      }
    });
  }

  /**
   * The synchronous part of "Begin interview": runs inside the click so the
   * browser grants audio playback and fullscreen.
   */
  prepareGesture(): void {
    if (!this.playback) {
      this.playback = new AudioPlayback({ onEvent: (e) => this.onPlayback(e), onCue: (cue) => this.onCue(cue) });
      this.avatar?.attachAudio(this.playback.context, this.playback.analyser);
    }
    void this.playback.resume();
    if (this.opts.policy.integrity.enabled && this.opts.policy.integrity.require_fullscreen) this.requestFullscreen();
  }

  /** Request (or re-enter) fullscreen; failures are reported, never fatal. */
  requestFullscreen(): void {
    const el = document.documentElement;
    if (document.fullscreenElement || typeof el.requestFullscreen !== "function") return;
    this.monitor?.setFullscreenWanted(true);
    el.requestFullscreen({ navigationUI: "hide" }).then(
      () => this.monitor?.setFullscreenWanted(true),
      (error: unknown) => {
        this.pendingFullscreenDecline = error instanceof Error ? error.message : "declined";
        this.monitor?.recordFullscreenDeclined(this.pendingFullscreenDecline);
      },
    );
  }

  /** Acquire devices, start monitoring and connect. Call right after prepareGesture(). */
  async begin(): Promise<void> {
    if (this.client) return;
    const { policy } = this.opts;
    this.store.dispatch({ type: "begun" });

    // Integrity first, so the permission prompts below are not misread as focus loss.
    this.batcher = new IntegrityBatcher({
      sendWs: (events) => this.client?.send({ type: "integrity.events", events }) ?? false,
      sendRest: async (events, options) => {
        const result = await this.api.postIntegrityEvents(this.sessionId, events, options);
        // Over REST the gentle warnings come back in the response instead of as integrity.warning.
        for (const warning of result?.warnings ?? []) {
          this.store.dispatch({ type: "warning.local", eventType: (warning.eventType as IntegrityEventType | null) ?? null, message: warning.message, at: performance.now() });
        }
      },
    });
    this.monitor = new IntegrityMonitor({
      policy: policy.monitor,
      sessionId: this.sessionId,
      emit: (event) => this.batcher?.push(event),
      isCodingTarget: (target) => target instanceof Element && Boolean(target.closest("[data-coding-editor]")),
    });
    this.monitor.suspend(8000);
    this.monitor.start();
    for (const { type, details } of this.pendingScreenEvents.splice(0)) this.monitor.recordScreenShare(type, details);
    if (policy.integrity.require_fullscreen) {
      this.monitor.setFullscreenWanted(Boolean(document.fullscreenElement) || this.pendingFullscreenDecline === null);
      if (this.pendingFullscreenDecline) this.monitor.recordFullscreenDeclined(this.pendingFullscreenDecline);
    }

    await this.acquireMedia();

    this.client = new RealtimeClient({
      sessionId: this.sessionId,
      // The system check already opened (and closed) this session's socket once; continuing
      // from its seq keeps the server from mistaking the room's first connection for a reload.
      initialLastSeq: consumeProbeSeq(this.opts.interviewId, this.sessionId),
      fetchTicket: () => this.api.fetchTicket(this.sessionId),
      clientInfo: () => this.clientInfo(),
      maxReconnectAttempts: 60,
      // 409 session_ended / consent_required, 403/404/410: a new ticket will never be issued.
      isFatalTicketError: (error) => isApiError(error) && [401, 403, 404, 409, 410].includes(error.status),
    });
    this.client.on("message", (msg) => this.onMessage(msg));
    this.client.on("binary", (data) => this.onBinary(data));
    this.client.on("status", (status, info) => {
      if (status === "connected") this.lastConnectedAt = performance.now();
      this.store.dispatch({ type: "connection", status, attempt: info.attempt, reason: info.reason, at: performance.now() });
      if (status !== "connected" && status !== "idle") {
        this.stopSpeaking(false);
        // The read-and-relay passage is never on screen without a live connection.
        this.clearRelayHideTimer();
        this.store.dispatch({ type: "activity.hide_local", reason: "disconnect", at: performance.now() });
      }
      // Closed by the server because the session is over (4011): leave the room.
      if (status === "offline" && info.reason === "session_ended") this.finish();
    });
    this.client.on("rtt", (ms) => this.store.dispatch({ type: "rtt", ms }));
    for (const message of this.early.splice(0)) this.client.send(message);
    this.client.connect();
    this.startScreenShareWatch();
    window.addEventListener("pagehide", this.onPageHide);
  }

  private readonly onPageHide = () => {
    // Close open intervals (e.g. a hidden tab) so their duration is not lost, then flush.
    this.monitor?.finalize();
    this.faceCheck?.finalize();
    void this.batcher?.flush({ keepalive: true });
  };

  private async acquireMedia(): Promise<void> {
    const { policy } = this.opts;
    const wantVideo = policy.integrity.camera_required || policy.uploadCameraFrames || policy.recordCamera;
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS, video: wantVideo ? { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" } : false });
    } catch (error) {
      // Retry audio-only: a missing camera must not block a voice interview unless required.
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_CONSTRAINTS });
      } catch (audioError) {
        if (!policy.recording.allow_typed_answers) throw new MediaAccessError("We couldn't use your microphone.", "microphone", audioError ?? error);
        this.micAvailable = false;
        this.store.dispatch({
          type: "notice.local",
          code: "mic_unavailable",
          level: "warning",
          message: "We can't use your microphone, so you'll type your answers. You can allow microphone access in the address bar and reload to speak instead.",
          at: performance.now(),
        });
      }
    }
    this.media = stream;
    const audioTrack = stream?.getAudioTracks()[0];
    const videoTrack = stream?.getVideoTracks()[0];
    this.cameraOnly = videoTrack ? new MediaStream([videoTrack]) : null;
    if (audioTrack && this.monitor) this.monitor.watchTrack(audioTrack, "microphone");
    if (videoTrack && this.monitor) this.monitor.watchTrack(videoTrack, "camera");
    if (!videoTrack && policy.integrity.camera_required) {
      this.batcher?.push({ event_type: "camera_disabled", occurred_at: new Date().toISOString(), details: { reason: "unavailable_at_start" } });
    }

    if (stream && audioTrack) {
      this.capture = new AudioCapture({
        onFrame: (frame) => {
          this.client?.sendBinary(frame);
        },
        onLevel: (vad) => this.onMicLevel(vad.level, vad.speaking),
        onVadChange: (speaking, level) => this.onVadChange(speaking, level),
        onError: (error) => this.send({ type: "client.error", component: "audio_capture", message: String(error).slice(0, 500) }),
      });
      try {
        await this.capture.start(stream);
      } catch (error) {
        this.send({ type: "client.error", component: "audio_capture", message: `worklet: ${String(error)}`.slice(0, 500), fatal: false });
        this.capture = null;
      }
    }

    this.notifyMedia();

    if (videoTrack && stream) {
      const cameraStream = new MediaStream([videoTrack]);
      this.frames = new FrameCapturer({
        stream: cameraStream,
        kind: "camera",
        uploadIntervalMs: policy.uploadCameraFrames ? policy.integrity.camera_frame_interval_seconds * 1000 : null,
        upload: (image, capturedAt) => this.uploadFrameSafe(image, "camera", capturedAt),
        onBlocked: (stats) => {
          this.monitor?.recordCameraBlocked(stats);
          this.store.dispatch({
            type: "warning.local",
            eventType: "camera_blocked",
            message: "We can't see your camera picture clearly. Check that nothing is covering the lens and the room has some light.",
            at: performance.now(),
          });
        },
      });
      void this.frames.start();
      this.startFaceCheck(cameraStream);
    }
    void this.startRecording("begin");
  }

  /** On-device face check (lazy: MediaPipe loads only here and in the system check). */
  private startFaceCheck(stream: MediaStream): void {
    if (this.faceCheck || this.opts.policy.faceCheck === false) return;
    const interviewId = this.opts.interviewId;
    this.faceCheck = new RoomFaceCheck({
      stream,
      decision: this.opts.policy.faceCheck,
      emit: (event) => this.batcher?.push(event),
      captureSnapshot: () => this.frames?.captureBlob() ?? Promise.resolve(null),
      uploadSnapshot: async (image, capturedAt, faceCheck) => {
        await this.api.uploadFrame(this.sessionId, image, "camera", capturedAt, { trigger: "face_check", faceCheck });
      },
      notify: (kind, message) => this.store.dispatch({ type: "notice.local", code: `face_${kind}`, level: "info", message, at: performance.now() }),
      reportError: (message) => this.send({ type: "client.error", component: "camera", message: message.slice(0, 500), fatal: false }),
      checkConsent: async () => {
        const disclosure = await getDisclosure(interviewId);
        return disclosure.consented && disclosure.items.some((item) => item.key === "face_check" && item.enabled);
      },
    });
    void this.faceCheck.start();
  }

  // ------------------------------------------------------------------ recording

  /** Interview clock in ms (the clock of transcript times), or null while it isn't known. */
  sessionClockMs(): number | null {
    const state = this.store.getState();
    const session = state.session;
    // After a reconnect the clock is only trusted again once the server's state arrived.
    if (!session || !clockRunning(state) || this.lastStateSyncAt < this.lastConnectedAt) return null;
    return Math.max(0, Math.round(session.elapsedS * 1000 + (performance.now() - session.syncedAtMs)));
  }

  private wantsRecording(): boolean {
    if (this.recordingDisabled || this.ended || this.disposed || this.serverRecordVideo === false) return false;
    return this.opts.policy.recordVideo || this.serverRecordVideo === true;
  }

  /**
   * Starts a recording part: the camera picture (when allowed and present) plus one
   * audio track mixing the interviewer's voice with the microphone. Without a
   * camera the part is audio only; the interview never waits for any of it.
   */
  private async startRecording(reason: string): Promise<void> {
    if (this.recorder || this.recordingStarting || !this.wantsRecording()) return;
    this.recordingStarting = true;
    try {
      if (this.playback && this.playback.state !== "running") await this.playback.resume();
      if (this.recorder || !this.wantsRecording()) return;
      // Without this tab's consent record (another tab or device) the server's confirmation decides:
      // the camera is only on here because the interview itself needs it, so it may be recorded.
      const allowCamera = this.opts.policy.recordCamera || (!this.opts.policy.recordVideo && this.serverRecordVideo === true);
      const video = allowCamera ? (this.media?.getVideoTracks().find((track) => track.readyState === "live") ?? null) : null;
      const microphone = this.media?.getAudioTracks().find((track) => track.readyState === "live") ?? null;
      this.recordingMix?.release();
      this.recordingMix = this.playback?.state === "running" ? this.playback.createRecordingMix(microphone ? new MediaStream([microphone]) : null) : null;
      const audio = this.recordingMix?.stream.getAudioTracks()[0] ?? microphone;
      const tracks = [video, audio].filter((track): track is MediaStreamTrack => Boolean(track));
      if (tracks.length === 0) return;
      const audioOnly = !video;
      if (!SessionRecorder.isSupported("camera", audioOnly)) {
        this.recordingDisabled = true;
        this.reportRecordingProblem(`recording unsupported (${audioOnly ? "audio" : "video"}): MediaRecorder cannot record this stream`);
        this.recordingMix?.release();
        this.recordingMix = null;
        return;
      }
      const recorder: SessionRecorder = new SessionRecorder({
        stream: new MediaStream(tracks),
        kind: "camera",
        sessionClock: () => this.sessionClockMs(),
        upload: (segment, meta) => this.api.uploadRecording(this.sessionId, segment, meta.index, meta.partStartedAt, "camera", meta).then(() => undefined),
        complete: (info) => this.api.completeRecording(this.sessionId, info).then(() => undefined),
        onFatal: (fatal) => this.onRecordingFatal(recorder, fatal),
      });
      if (!recorder.start()) {
        this.recordingDisabled = true;
        this.reportRecordingProblem("recording could not start: MediaRecorder rejected the stream");
        this.recordingMix?.release();
        this.recordingMix = null;
        return;
      }
      this.recorder = recorder;
      this.recordedVideo?.removeEventListener("ended", this.onRecordedVideoEnded);
      this.recordedVideo = video;
      video?.addEventListener("ended", this.onRecordedVideoEnded);
      this.recordingReason = reason;
    } finally {
      this.recordingStarting = false;
    }
  }

  /** Ends the current part (last timeslice uploaded, part reported complete). */
  private async stopRecording(): Promise<void> {
    const recorder = this.recorder;
    this.recorder = null;
    this.recordedVideo?.removeEventListener("ended", this.onRecordedVideoEnded);
    this.recordedVideo = null;
    const mix = this.recordingMix;
    this.recordingMix = null;
    try {
      await recorder?.stop();
    } finally {
      mix?.release();
    }
  }

  /** The camera went away mid-interview: keep recording the conversation as audio only. */
  private readonly onRecordedVideoEnded = () => {
    if (this.ended || this.disposed) return;
    void this.stopRecording().then(() => this.startRecording("camera_ended"));
  };

  private onRecordingFatal(recorder: SessionRecorder, fatal: RecordingFatal): void {
    if (this.recorder !== recorder) return;
    this.recorder = null;
    this.recordingMix?.release();
    this.recordingMix = null;
    if (fatal.permanent) {
      this.recordingDisabled = true;
      this.reportRecordingProblem(`recording stopped: ${fatal.reason}`);
      return;
    }
    if (this.recordingRestarts >= RECORDING_RESTARTS) {
      this.reportRecordingProblem(`recording stopped after ${RECORDING_RESTARTS} restarts: ${fatal.reason}`);
      return;
    }
    this.recordingRestarts += 1;
    this.recordingRetryTimer = setTimeout(() => {
      this.recordingRetryTimer = null;
      void this.startRecording("retry");
    }, RECORDING_RETRY_MS);
  }

  /** session.state carries the server's view of policy + consent (`integrity.record_video`). */
  private onServerRecordingFlag(flags: Record<string, unknown> | null | undefined): void {
    const value = flags?.record_video;
    if (typeof value !== "boolean" || value === this.serverRecordVideo) return;
    this.serverRecordVideo = value;
    if (value) void this.startRecording("server");
    else if (this.recorder) void this.stopRecording();
  }

  private reportRecordingProblem(message: string): void {
    if (this.reportedRecordingProblem) return;
    this.reportedRecordingProblem = true;
    this.send({ type: "client.error", component: "camera", message: message.slice(0, 500), fatal: false });
  }

  private async uploadFrameSafe(image: Blob, kind: "camera" | "screen", capturedAt: string): Promise<void> {
    try {
      const faceHint = kind === "camera" ? this.faceCheck?.periodicFrameHint() : undefined;
      if (faceHint) await this.api.uploadFrame(this.sessionId, image, kind, capturedAt, { faceCheck: faceHint });
      else await this.api.uploadFrame(this.sessionId, image, kind, capturedAt);
    } catch (error) {
      throw frameUploadDecision(error);
    }
  }

  private startScreenShareWatch(): void {
    const manager = this.opts.screenShare;
    if (!manager?.mediaStream) return;
    if (this.opts.policy.uploadScreenFrames) {
      this.screenFrames?.stop();
      this.screenFrames = new FrameCapturer({
        stream: manager.mediaStream,
        kind: "screen",
        uploadIntervalMs: this.opts.policy.integrity.screen_frame_interval_seconds * 1000,
        upload: (image, capturedAt) => this.uploadFrameSafe(image, "screen", capturedAt),
        maxWidth: 1280,
      });
      void this.screenFrames.start();
    }
    this.startScreenRecording(manager.mediaStream);
  }

  /** Records the shared screen when the policy asks for it and the candidate consented. */
  private startScreenRecording(stream: MediaStream): void {
    if (!this.opts.policy.recordScreen) return;
    if (!SessionRecorder.isSupported()) {
      if (this.reportedScreenRecordingUnsupported) return;
      this.reportedScreenRecordingUnsupported = true;
      this.send({ type: "client.error", component: "screen_share", message: "screen recording unsupported: MediaRecorder unavailable in this browser", fatal: false });
      this.store.dispatch({
        type: "notice.local",
        code: "screen_recording_unsupported",
        level: "warning",
        message: "This browser can't record your shared screen, so the hiring team won't receive a screen recording. The interview continues normally.",
        at: performance.now(),
      });
      return;
    }
    void this.screenRecorder?.stop();
    const recorder: SessionRecorder = new SessionRecorder({
      stream,
      kind: "screen",
      sessionClock: () => this.sessionClockMs(),
      upload: (segment, meta) => this.api.uploadRecording(this.sessionId, segment, meta.index, meta.partStartedAt, "screen", meta).then(() => undefined),
      complete: (info) => this.api.completeRecording(this.sessionId, info).then(() => undefined),
      onFatal: () => {
        if (this.screenRecorder === recorder) this.screenRecorder = null;
      },
    });
    this.screenRecorder = recorder.start() ? recorder : null;
  }

  /**
   * A screen-share observation from the lobby or the room banner (started, or a window
   * or tab shared instead of the entire screen). Kept until the monitor starts.
   */
  recordScreenShareEvent(type: Exclude<ScreenShareEvent, "screen_share_stopped">, details: Record<string, unknown> = {}): void {
    if (this.monitor) this.monitor.recordScreenShare(type, details);
    else if (this.pendingScreenEvents.length < 8) this.pendingScreenEvents.push({ type, details });
  }

  /** Called by the room when the shared screen stops (track ended). */
  screenShareStopped(): void {
    this.monitor?.recordScreenShare("screen_share_stopped");
    this.screenFrames?.stop();
    this.screenFrames = null;
    void this.screenRecorder?.stop();
    this.screenRecorder = null;
    this.store.dispatch({
      type: "warning.local",
      eventType: "screen_share_stopped",
      message: "Screen sharing has stopped. This interview needs your entire screen shared. Share it again to continue.",
      at: performance.now(),
    });
    this.opts.onScreenShareStopped?.();
  }

  /** Screen shared again from the room banner (the room reports the new share's observations). */
  screenShareRestarted(manager: ScreenShareManager): void {
    this.opts.screenShare = manager;
    this.startScreenShareWatch();
  }

  private clientInfo(): ClientInfo {
    const status = this.avatar?.getStatus();
    return {
      user_agent: navigator.userAgent.slice(0, 300),
      webgl: status ? status.mode === "3d" : false,
      avatar_mode: status?.mode ?? this.opts.prefs.avatarMode,
      tts_mode: this.ttsMode,
      sample_rate: 16000,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
  }

  // ------------------------------------------------------------------ incoming

  private onMessage(msg: ServerMessage): void {
    this.store.dispatch({ type: "server", msg, at: performance.now() });
    const avatar = this.avatar;
    switch (msg.type) {
      case "session.state": {
        this.lastStateSyncAt = performance.now();
        this.onServerRecordingFlag(msg.integrity);
        this.faceCheck?.onServerFlags(msg.integrity as Record<string, unknown> | null | undefined);
        avatar?.setState(msg.avatar_state);
        if (msg.status === "live" || msg.status === "paused") this.startSent = true;
        if (!this.startSent && ["created", "preparing", "ready"].includes(msg.status)) {
          this.startSent = true;
          this.send({ type: "control.start" });
        }
        if (["completed", "aborted", "failed"].includes(msg.status)) this.finish();
        break;
      }
      case "turn.state": {
        if (msg.state === "processing") avatar?.setState("THINKING");
        else if (msg.state === "listening" && !this.playback?.isPlaying() && !this.speech.speakingId) avatar?.setState("LISTENING");
        else if (msg.state === "candidate_speaking") avatar?.setCandidateSpeaking(true, Math.max(0.2, this.micLevel));
        if (msg.state !== "candidate_speaking" && !this.micSpeaking) avatar?.setCandidateSpeaking(false, 0);
        break;
      }
      case "avatar.behavior":
        avatar?.applyBehavior(msg);
        break;
      case "ai.utterance.start": {
        this.playback?.begin(msg.utterance_id);
        if (msg.behavior) avatar?.applyBehavior(msg.behavior);
        if (this.ttsMode === "text") this.store.dispatch({ type: "utterance.mode", utteranceId: msg.utterance_id, mode: "text" });
        break;
      }
      case "tts.chunk":
        if (this.ttsMode === "server") this.playback?.handleChunkMeta(msg);
        this.clearNoAudioTimer(msg.utterance_id);
        break;
      case "tts.fallback":
        this.onFallback(msg.utterance_id, msg.text);
        break;
      case "ai.utterance.end": {
        this.playback?.end(msg.utterance_id);
        this.markEnded(msg.utterance_id);
        this.scheduleNoAudioCheck(msg.utterance_id, msg.text);
        break;
      }
      case "barge_in": {
        this.playback?.interrupt(msg.utterance_id);
        if (this.speech.has(msg.utterance_id)) this.speech.cancel(true);
        this.clearTextTrack(msg.utterance_id);
        this.pendingFallback.delete(msg.utterance_id);
        avatar?.interrupt();
        break;
      }
      case "error": {
        // A start the server could not complete for a transient reason (model hiccup) is retried a few times
        // with a short backoff before the candidate has to act; the store already shows the notice (S5).
        if (msg.code === "start_failed" && this.startRetries < START_RETRIES) {
          const attempt = ++this.startRetries;
          this.startSent = false;
          setTimeout(() => {
            if (this.ended || this.startSent) return;
            this.startSent = true;
            this.send({ type: "control.start" });
          }, 1500 * attempt);
        }
        break;
      }
      case "session.ended":
        this.clearRelayHideTimer();
        this.finish();
        break;
      case "activity.phase":
        this.onActivityPhase(msg);
        break;
      case "activity.start":
      case "activity.end":
        this.clearRelayHideTimer();
        break;
      default:
        break;
    }
  }

  /** Arms the local hide for the read-and-relay passage: at its countdown zero, whatever the network does. */
  private onActivityPhase(msg: ActivityPhaseMsg): void {
    this.clearRelayHideTimer();
    if (msg.kind !== "read_relay" || msg.phase !== "read" || msg.status !== "running" || msg.hide_content) return;
    const activityId = msg.activity_id;
    const remaining = Math.max(0, Number.isFinite(msg.remaining_ms) ? msg.remaining_ms : 0);
    this.relayHideTimer = setTimeout(() => {
      this.relayHideTimer = null;
      this.store.dispatch({ type: "activity.hide_local", reason: "countdown", activityId, at: performance.now() });
    }, remaining);
  }

  private clearRelayHideTimer(): void {
    if (this.relayHideTimer) clearTimeout(this.relayHideTimer);
    this.relayHideTimer = null;
  }

  private onBinary(data: ArrayBuffer): void {
    if (data.byteLength < 8 || this.ttsMode !== "server") return;
    const frame = unpackFrame(data);
    if (frame.frameType !== FRAME_TTS_AUDIO) return;
    this.playback?.handleAudio(frame.id, frame.payload, (frame.flags & 1) === 1);
  }

  private onCue(cue: SpeechCueOut): void {
    this.avatar?.enqueueSpeech({ utteranceId: cue.utteranceId, chunkId: cue.chunkId, startTime: cue.startTime, durationMs: cue.durationMs, visemes: cue.visemes, text: cue.text });
  }

  private onPlayback(event: PlaybackReport): void {
    switch (event.type) {
      case "started":
        this.send({ type: "playback.started", utterance_id: event.utteranceId, played_ms: 0, chunk_id: event.chunkId });
        break;
      case "chunk_start":
        this.store.dispatch({ type: "playback.chunk_started", utteranceId: event.utteranceId, text: event.chunk.text });
        break;
      case "progress":
        this.send({ type: "playback.progress", utterance_id: event.utteranceId, played_ms: event.playedMs, chunk_id: event.chunkId });
        break;
      case "finished": {
        const fallback = this.pendingFallback.get(event.utteranceId);
        if (fallback !== undefined) {
          this.pendingFallback.delete(event.utteranceId);
          this.audioPlayedMs.set(event.utteranceId, event.playedMs);
          this.speakFallback(event.utteranceId, fallback);
          return;
        }
        this.send({ type: "playback.finished", utterance_id: event.utteranceId, played_ms: event.playedMs, chunk_id: event.chunkId });
        this.store.dispatch({ type: "playback.done", utteranceId: event.utteranceId, interrupted: false });
        break;
      }
      case "interrupted":
        if (event.hadAudio) this.send({ type: "playback.interrupted", utterance_id: event.utteranceId, played_ms: event.playedMs, ...(event.chunkId !== null ? { chunk_id: event.chunkId } : {}) });
        this.store.dispatch({ type: "playback.done", utteranceId: event.utteranceId, interrupted: true });
        break;
    }
  }

  private onFallback(utteranceId: string, text: string): void {
    this.clearNoAudioTimer(utteranceId);
    if (this.playback?.hasAudio(utteranceId) && this.playback.isPlaying()) {
      // Speak the rest after the audio already scheduled for this utterance (one fallback per chunk).
      const earlier = this.pendingFallback.get(utteranceId);
      this.pendingFallback.set(utteranceId, earlier ? `${earlier} ${text}` : text);
      return;
    }
    this.speakFallback(utteranceId, text);
  }

  /** The utterance's text is complete: queued fallback speech/text may now report delivery. */
  private markEnded(utteranceId: string): void {
    this.endedUtterances.add(utteranceId);
    if (this.endedUtterances.size > 64) this.endedUtterances.delete(this.endedUtterances.values().next().value!);
    this.speech.end(utteranceId);
    this.closeTextTrack(utteranceId);
  }

  private speakFallback(utteranceId: string, text: string): void {
    const offset = this.audioPlayedMs.get(utteranceId) ?? 0;
    const useBrowser = this.ttsMode !== "text" && BrowserSpeech.isSupported() && !this.textTracks.has(utteranceId);
    if (useBrowser) {
      const continuing = this.speech.has(utteranceId);
      if (!continuing) this.store.dispatch({ type: "utterance.mode", utteranceId, mode: "browser" });
      const ok = this.speech.speak(utteranceId, text, {
        onStart: () => {
          if (!offset) this.send({ type: "playback.started", utterance_id: utteranceId, played_ms: 0 });
        },
        onWord: (word) => {
          this.avatar?.speakWordBoundary(word, estimateWordDurationMs(word));
          this.store.dispatch({ type: "playback.word", utteranceId });
        },
        onEnd: ({ interrupted, playedMs }) => {
          this.send({ type: interrupted ? "playback.interrupted" : "playback.finished", utterance_id: utteranceId, played_ms: offset + playedMs });
          this.store.dispatch({ type: "playback.done", utteranceId, interrupted });
          this.audioPlayedMs.delete(utteranceId);
        },
      });
      if (ok) {
        if (this.endedUtterances.has(utteranceId)) this.speech.end(utteranceId);
        return;
      }
    }
    // Text only: give the candidate time to read every chunk, then tell the server it was delivered.
    let track = this.textTracks.get(utteranceId);
    const now = performance.now();
    if (!track) {
      this.store.dispatch({ type: "utterance.mode", utteranceId, mode: "text" });
      if (!offset) this.send({ type: "playback.started", utterance_id: utteranceId, played_ms: 0 });
      track = { startedAt: now, deadline: now, offset, closed: this.endedUtterances.has(utteranceId), timer: null };
      this.textTracks.set(utteranceId, track);
    }
    track.deadline = Math.max(track.deadline, now) + estimateReadingMs(text);
    this.armTextTrack(utteranceId);
  }

  private armTextTrack(utteranceId: string): void {
    const track = this.textTracks.get(utteranceId);
    if (!track) return;
    if (track.timer) clearTimeout(track.timer);
    // Until the text is complete, a long safety net stands in for a lost ai.utterance.end.
    const wait = Math.max(0, track.deadline - performance.now()) + (track.closed ? 0 : 15_000);
    track.timer = setTimeout(() => this.finishTextTrack(utteranceId), wait);
  }

  private closeTextTrack(utteranceId: string): void {
    const track = this.textTracks.get(utteranceId);
    if (!track || track.closed) return;
    track.closed = true;
    this.armTextTrack(utteranceId);
  }

  private finishTextTrack(utteranceId: string): void {
    const track = this.textTracks.get(utteranceId);
    if (!track) return;
    this.textTracks.delete(utteranceId);
    this.send({ type: "playback.finished", utterance_id: utteranceId, played_ms: track.offset + Math.round(track.deadline - track.startedAt) });
    this.store.dispatch({ type: "playback.done", utteranceId, interrupted: false });
  }

  /** An utterance ended without any audio (text-only client, TTS skipped): fall back after a grace period. */
  private scheduleNoAudioCheck(utteranceId: string, text: string): void {
    if (this.playback?.hasAudio(utteranceId) || this.speech.has(utteranceId) || this.textTracks.has(utteranceId)) return;
    if (this.playback?.isCancelled(utteranceId)) return;
    this.clearNoAudioTimer(utteranceId);
    const grace = this.ttsMode === "server" ? NO_AUDIO_GRACE_MS : 0;
    this.noAudioTimers.set(
      utteranceId,
      setTimeout(() => {
        this.noAudioTimers.delete(utteranceId);
        if (this.playback?.hasAudio(utteranceId) || this.speech.has(utteranceId) || this.textTracks.has(utteranceId)) return;
        const known = this.store.getState().utterances.find((u) => u.id === utteranceId);
        const body = text?.trim() ? text : (known?.text ?? "");
        if (!body.trim()) return;
        if (this.ttsMode === "server") this.speakFallbackText(utteranceId, body);
        else this.speakFallback(utteranceId, body);
      }, grace),
    );
  }

  private speakFallbackText(utteranceId: string, text: string): void {
    // Server audio never arrived: show the words and report delivery after a reading pause.
    const saved = this.ttsMode;
    this.ttsMode = "text";
    this.speakFallback(utteranceId, text);
    this.ttsMode = saved;
  }

  private clearNoAudioTimer(id: string): void {
    const t = this.noAudioTimers.get(id);
    if (t) clearTimeout(t);
    this.noAudioTimers.delete(id);
  }

  private clearTextTrack(id: string): void {
    const track = this.textTracks.get(id);
    if (track?.timer) clearTimeout(track.timer);
    this.textTracks.delete(id);
  }

  /** Stop all interviewer speech locally (disconnects): the server re-speaks after resuming. */
  private stopSpeaking(report: boolean): void {
    this.playback?.interrupt(undefined, report);
    this.speech.cancel(report);
    for (const id of [...this.textTracks.keys()]) this.clearTextTrack(id);
    this.pendingFallback.clear();
    this.avatar?.interrupt();
  }

  // ------------------------------------------------------------------ microphone

  private onMicLevel(level: number, speaking: boolean): void {
    this.micLevel = level;
    for (const listener of this.levelListeners) listener(level, speaking);
    const now = performance.now();
    if (speaking && now - this.lastAvatarLevelAt > 66) {
      this.lastAvatarLevelAt = now;
      this.avatar?.setCandidateSpeaking(true, level);
    }
  }

  private onVadChange(speaking: boolean, level: number): void {
    this.micSpeaking = speaking;
    this.send({ type: "vad.local", speaking });
    this.playback?.setDuck(speaking && this.playback.isPlaying());
    this.avatar?.setCandidateSpeaking(speaking, speaking ? level : 0);
  }

  /** 0-1 mic level stream for meters (outside React state). */
  onLevel(listener: (level: number, speaking: boolean) => void): () => void {
    this.levelListeners.add(listener);
    return () => this.levelListeners.delete(listener);
  }

  /** Current interviewer output level (0-1) from the playback analyser. */
  outputLevel(): number {
    const analyser = this.playback?.analyser;
    if (!analyser) return 0;
    const data = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) sum += data[i]! * data[i]!;
    const rms = Math.sqrt(sum / data.length);
    return Math.max(0, Math.min(1, rms * 4.5));
  }

  get micLevelNow(): number {
    return this.micLevel;
  }

  get hasMicrophone(): boolean {
    return this.micAvailable && Boolean(this.capture?.isRunning);
  }

  /** Stable camera-only stream for the picture-in-picture (null without a camera). */
  get cameraStream(): MediaStream | null {
    return this.cameraOnly;
  }

  /** Devices were acquired or lost. */
  onMedia(listener: () => void): () => void {
    this.mediaListeners.add(listener);
    return () => this.mediaListeners.delete(listener);
  }

  private notifyMedia(): void {
    for (const listener of this.mediaListeners) listener();
  }

  // ------------------------------------------------------------------ controls

  private send(message: ClientMessage): boolean {
    if (!this.client) {
      // Before the socket exists (device setup), keep diagnostics for the first connection.
      if (message.type === "client.error" && this.early.length < 10) this.early.push(message);
      return false;
    }
    return this.client.send(message);
  }

  private early: ClientMessage[] = [];

  endTurn(): void {
    this.send({ type: "control.end_turn" });
  }

  repeat(): void {
    this.send({ type: "control.repeat" });
  }

  /**
   * Ends the current activity phase early ("Finish reading", "I'm ready", "Start now",
   * "I'm done"). Double presses inside 600 ms are dropped (the server allows 2/s).
   */
  activityFinish(activityId: string, phase: ActivityPhase): boolean {
    const now = performance.now();
    if (now - this.lastActivityFinishAt < 600) return false;
    this.lastActivityFinishAt = now;
    // Never queued while offline (a press must not end a restarted attempt after a reconnect).
    if (!this.send({ type: "activity.finish", activity_id: activityId, phase })) return false;
    // The local hide stays armed: the server ends the phase (and hides the passage) right away.
    this.store.dispatch({ type: "activity.finish_requested", activityId, phase, at: now });
    return true;
  }

  /** Typing is for answers: never while reading aloud, never in a silent reading or preparation phase. */
  typingAllowed(): boolean {
    const mode = activityDockMode(this.store.getState().activity);
    return mode === "none" || mode === "capture";
  }

  sendTypedAnswer(text: string): boolean {
    const trimmed = text.trim().slice(0, 8000);
    if (!trimmed) return false;
    if (!this.typingAllowed()) return false;
    this.send({ type: "answer.text", text: trimmed });
    this.store.dispatch({ type: "candidate.typed", text: trimmed, at: performance.now() });
    return true;
  }

  endInterview(reason = "candidate_ended"): void {
    if (this.ended) return;
    // Over the socket while it is up; over REST (keepalive) while it is not. A press while offline used to be
    // dropped, leaving the server session paused until the sweeper found it (S6, audit B6).
    const overSocket = this.client?.status === "connected" && this.send({ type: "control.end_interview", reason });
    if (overSocket) {
      // If the server never confirms (connection lost right after), still leave after a while.
      this.endFallbackTimer = setTimeout(() => this.finish(), 12_000);
      return;
    }
    this.api.endSession(this.sessionId, reason).catch(() => undefined);
    this.finish();
  }

  codeUpdate(codingSessionId: string, update: { code: string; language: CodeLanguage; version: number; cursorLine?: number }): void {
    this.send({ type: "code.update", coding_session_id: codingSessionId, code: update.code, language: update.language, version: update.version, ...(update.cursorLine ? { cursor_line: update.cursorLine } : {}) });
  }

  codeRun(codingSessionId: string, kind: RunKind, code: string, language: CodeLanguage): void {
    this.store.dispatch({ type: "coding.run_requested", kind });
    this.send({ type: "code.run", coding_session_id: codingSessionId, kind, code, language });
  }

  codeSubmit(codingSessionId: string, code: string, language: CodeLanguage): void {
    this.store.dispatch({ type: "coding.run_requested", kind: "submit" });
    this.send({ type: "code.submit", coding_session_id: codingSessionId, code, language });
  }

  hintRequest(codingSessionId: string): void {
    this.store.dispatch({ type: "coding.hint_requested" });
    this.send({ type: "code.hint_request", coding_session_id: codingSessionId });
  }

  codeLanguage(codingSessionId: string, language: CodeLanguage): void {
    this.store.dispatch({ type: "coding.language", language });
    this.send({ type: "code.language", coding_session_id: codingSessionId, language });
  }

  codingPaste(length: number, lines: number, blocked: boolean, threshold: number): void {
    this.monitor?.recordCodingPaste(length, lines, { threshold, blocked });
  }

  retryConnection(): void {
    this.client?.retryNow();
  }

  reportEditorError(message: string): void {
    this.send({ type: "client.error", component: "editor", message: message.slice(0, 500) });
  }

  // ------------------------------------------------------------------ end / dispose

  private finish(): void {
    if (this.ended) return;
    this.ended = true;
    if (this.endFallbackTimer) clearTimeout(this.endFallbackTimer);
    this.client?.markEnded();
    this.capture?.stop();
    this.faceCheck?.stop();
    this.monitor?.stop();
    void this.batcher?.flush({ keepalive: true });
    this.frames?.stop();
    this.screenFrames?.stop();
    // The last timeslices keep uploading in the background (also after leaving the room).
    if (this.recordingRetryTimer) clearTimeout(this.recordingRetryTimer);
    void this.stopRecording();
    void this.screenRecorder?.stop();
    this.opts.screenShare?.stop();
    for (const track of this.media?.getTracks() ?? []) track.stop();
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
    // Let the closing words finish before leaving the room.
    const started = performance.now();
    const wait = () => {
      const busy = this.playback?.isPlaying() || this.speech.speakingId || this.textTracks.size > 0;
      if (busy && performance.now() - started < 12_000) {
        setTimeout(wait, 250);
        return;
      }
      this.opts.onEnded?.();
    };
    setTimeout(wait, 600);
  }

  get isEnded(): boolean {
    return this.ended;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    window.removeEventListener("pagehide", this.onPageHide);
    if (this.endFallbackTimer) clearTimeout(this.endFallbackTimer);
    this.clearRelayHideTimer();
    for (const t of this.noAudioTimers.values()) clearTimeout(t);
    for (const track of this.textTracks.values()) if (track.timer) clearTimeout(track.timer);
    this.client?.close(1000, "page closed");
    if (this.recordingRetryTimer) clearTimeout(this.recordingRetryTimer);
    void this.stopRecording();
    this.capture?.stop();
    this.playback?.dispose();
    this.speech.cancel(false);
    this.faceCheck?.stop();
    this.monitor?.stop();
    void this.batcher?.flush({ keepalive: true });
    this.batcher?.stop();
    this.frames?.stop();
    this.screenFrames?.stop();
    void this.screenRecorder?.stop();
    this.opts.screenShare?.stop();
    for (const track of this.media?.getTracks() ?? []) track.stop();
    this.avatarUnsub?.();
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
  }
}

/** Create or resume the session for this interview (idempotent). */
export async function ensureSession(interviewId: string): Promise<string> {
  const ref = await createOrResumeSession(interviewId);
  return ref.sessionId;
}

/**
 * Frame upload failures: 403 (frames not enabled / consent required), 404 and
 * 409/410 (session ended) stop capturing for good; 429 waits for Retry-After;
 * a rejected frame (413/415/422) or a transient failure pauses briefly.
 */
export function frameUploadDecision(error: unknown): Error {
  if (isApiError(error)) {
    if ([401, 403, 404, 409, 410].includes(error.status)) return new StopUploadsError(error.code || error.message);
    if (error.status === 429) return new BackoffUploadsError(error.message, Math.max(5, error.retryAfterSeconds ?? 30) * 1000);
    if ([413, 415, 422].includes(error.status)) return new BackoffUploadsError(error.message, 30_000);
  }
  return error instanceof Error ? error : new Error(String(error));
}

/** Seq seen by the system-check probe for this session (read once, then forgotten). */
export function consumeProbeSeq(interviewId: string, sessionId: string): number {
  const probe = readLocal<{ sessionId: string; seq: number } | null>(interviewId, "probe", null);
  writeLocal(interviewId, "probe", null);
  return probe && probe.sessionId === sessionId && Number.isFinite(probe.seq) ? probe.seq : 0;
}
