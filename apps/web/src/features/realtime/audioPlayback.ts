/**
 * AudioPlayback: plays interviewer TTS chunks gaplessly on one AudioContext.
 *
 *   tts.chunk (metadata) + binary 0x02 (PCM16, same chunk_id)
 *     -> PlaybackScheduler (ordering + start times on the AudioContext clock)
 *     -> AudioBufferSourceNode.start(T) -> duck GainNode -> AnalyserNode -> speakers
 *
 * The analyser feeds avatar lip-sync amplitude; each scheduled chunk is also
 * handed to `onCue` (SpeechCue for AvatarController.enqueueSpeech). Playback
 * events (started / progress / finished / interrupted) are reported through
 * `onEvent` so the runtime can send playback.* messages. Barge-in stops every
 * scheduled source immediately.
 *
 * Session recording taps the interviewer's voice after the duck gain
 * (`createRecordingMix`): duck GainNode -> GainNode -> MediaStreamAudioDestinationNode,
 * mixed there with the raw microphone. That destination only feeds the
 * MediaRecorder; it is never connected to the speakers, so the mix adds no echo
 * and leaves playback, the analyser and barge-in untouched.
 */
import { int16ToFloat } from "@/lib/realtime/pcm";
import type { TtsChunkMsg } from "@/lib/realtime/protocol";

import { metaFromMessage, PlaybackScheduler, type PlaybackEvent, type ScheduledChunk } from "./playbackScheduler";

export type PlaybackReport =
  | PlaybackEvent
  | { type: "interrupted"; utteranceId: string; playedMs: number; chunkId: number | null; hadAudio: boolean };

export interface SpeechCueOut {
  utteranceId: string;
  chunkId: number;
  startTime: number;
  durationMs: number;
  visemes: TtsChunkMsg["visemes"];
  text: string;
}

/** The conversation as one audio track for the session recording (see `createRecordingMix`). */
export interface RecordingMix {
  /** Mono track: interviewer voice (post-duck) + candidate microphone. Not audible locally. */
  readonly stream: MediaStream;
  /** Which sides of the conversation the track carries. */
  readonly sources: { interviewer: boolean; candidate: boolean };
  /** Disconnects the tap and stops the track (idempotent). */
  release(): void;
}

export interface RecordingMixOptions {
  /** Level of the interviewer voice in the mix (default 0.9). */
  interviewerGain?: number;
  /** Level of the microphone in the mix (default 1). */
  microphoneGain?: number;
}

export interface AudioPlaybackOptions {
  context?: AudioContext;
  onEvent?: (event: PlaybackReport) => void;
  onCue?: (cue: SpeechCueOut) => void;
  /** Gain while the candidate talks over the interviewer (0-1). */
  duckGain?: number;
  tickMs?: number;
}

export class AudioPlayback {
  readonly context: AudioContext;
  readonly analyser: AnalyserNode;
  private readonly gain: GainNode;
  /** playback.progress every ~500 ms (the server's delivery / spoken-portion tracking). */
  private readonly scheduler = new PlaybackScheduler({ progressIntervalS: 0.5 });
  private readonly sources = new Map<ScheduledChunk, AudioBufferSourceNode>();
  private readonly opts: AudioPlaybackOptions;
  private timer: ReturnType<typeof setInterval> | null = null;
  private ducked = false;
  private disposed = false;
  private readonly ownsContext: boolean;
  private readonly mixes = new Set<RecordingMix>();

  constructor(options: AudioPlaybackOptions = {}) {
    this.opts = options;
    this.ownsContext = !options.context;
    this.context = options.context ?? new AudioContext({ latencyHint: "interactive" });
    this.gain = this.context.createGain();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.6;
    this.gain.connect(this.analyser);
    this.analyser.connect(this.context.destination);
    this.timer = setInterval(() => this.tick(), options.tickMs ?? 40);
  }

  /** Must be called from a user gesture at least once (autoplay policy). */
  async resume(): Promise<void> {
    if (this.context.state === "suspended") {
      try {
        await this.context.resume();
      } catch {
        /* reported by the caller through context.state */
      }
    }
  }

  get state(): AudioContextState {
    return this.context.state;
  }

  begin(utteranceId: string): void {
    this.scheduler.begin(utteranceId);
  }

  handleChunkMeta(msg: TtsChunkMsg): void {
    this.scheduler.addMeta(metaFromMessage(msg));
  }

  /** Binary 0x02 frame payload. */
  handleAudio(chunkId: number, samples: Int16Array, final: boolean): void {
    if (this.disposed) return;
    const scheduled = this.scheduler.addAudio(chunkId, samples, final, this.context.currentTime);
    for (const chunk of scheduled) this.play(chunk);
  }

  end(utteranceId: string): void {
    this.scheduler.end(utteranceId);
  }

  hasAudio(utteranceId: string): boolean {
    return this.scheduler.hasAudio(utteranceId);
  }

  isCancelled(utteranceId: string): boolean {
    return this.scheduler.isCancelled(utteranceId);
  }

  isPlaying(): boolean {
    return this.scheduler.isPlaying(this.context.currentTime);
  }

  /** Stop one utterance (barge-in) or everything; reports `interrupted` for each. */
  interrupt(utteranceId?: string, report = true): void {
    const now = this.context.currentTime;
    const results = this.scheduler.interrupt(now, utteranceId);
    for (const result of results) {
      for (const chunk of result.chunks) this.stopSource(chunk, now);
      if (report) {
        this.opts.onEvent?.({
          type: "interrupted",
          utteranceId: result.utteranceId,
          playedMs: result.playedMs,
          chunkId: result.chunkId,
          hadAudio: result.hadAudio,
        });
      }
    }
    this.setDuck(false);
  }

  /** Instant volume duck while the candidate talks over the interviewer. */
  setDuck(on: boolean): void {
    if (this.ducked === on || this.disposed) return;
    this.ducked = on;
    const now = this.context.currentTime;
    const target = on ? (this.opts.duckGain ?? 0.35) : 1;
    try {
      this.gain.gain.cancelScheduledValues(now);
      this.gain.gain.setTargetAtTime(target, now, on ? 0.02 : 0.12);
    } catch {
      this.gain.gain.value = target;
    }
  }

  private play(chunk: ScheduledChunk): void {
    const ctx = this.context;
    try {
      const buffer = ctx.createBuffer(1, Math.max(1, chunk.samples.length), chunk.sampleRate);
      buffer.copyToChannel(int16ToFloat(chunk.samples) as Float32Array<ArrayBuffer>, 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(this.gain);
      source.onended = () => {
        source.disconnect();
        this.sources.delete(chunk);
      };
      source.start(Math.max(chunk.startTime, ctx.currentTime));
      this.sources.set(chunk, source);
    } catch (error) {
      console.error("[playback] failed to schedule chunk", error);
    }
    this.opts.onCue?.({
      utteranceId: chunk.utteranceId,
      chunkId: chunk.chunkId,
      startTime: chunk.startTime,
      durationMs: Math.round(chunk.playMs),
      visemes: chunk.visemes,
      text: chunk.text,
    });
  }

  private stopSource(chunk: ScheduledChunk, now: number): void {
    const source = this.sources.get(chunk);
    if (!source) return;
    this.sources.delete(chunk);
    try {
      source.onended = null;
      source.stop(now);
      source.disconnect();
    } catch {
      /* already stopped */
    }
  }

  /**
   * One audio track with both sides of the conversation for the session
   * recording: the interviewer's voice tapped after the duck gain plus the raw
   * microphone, mixed in a MediaStreamAudioDestinationNode that is never routed
   * to the speakers. Null when the browser cannot build it (then record the
   * microphone alone). Call `release()` when the recording part ends.
   */
  createRecordingMix(microphone: MediaStream | null, options: RecordingMixOptions = {}): RecordingMix | null {
    const ctx = this.context;
    if (this.disposed || typeof ctx.createMediaStreamDestination !== "function") return null;
    let destination: MediaStreamAudioDestinationNode;
    let voice: GainNode;
    try {
      destination = ctx.createMediaStreamDestination();
      voice = ctx.createGain();
    } catch {
      return null;
    }
    try {
      // Speech only: one channel halves the Opus bitrate and keeps both voices centred.
      destination.channelCount = 1;
      destination.channelCountMode = "explicit";
      destination.channelInterpretation = "speakers";
    } catch {
      /* keep the default stereo destination */
    }
    voice.gain.value = options.interviewerGain ?? 0.9;
    this.gain.connect(voice);
    voice.connect(destination);
    let micSource: MediaStreamAudioSourceNode | null = null;
    let micLevel: GainNode | null = null;
    const micTrack = microphone?.getAudioTracks().find((track) => track.readyState !== "ended") ?? null;
    if (micTrack && typeof ctx.createMediaStreamSource === "function") {
      try {
        micSource = ctx.createMediaStreamSource(new MediaStream([micTrack]));
        micLevel = ctx.createGain();
        micLevel.gain.value = options.microphoneGain ?? 1;
        micSource.connect(micLevel);
        micLevel.connect(destination);
      } catch {
        micSource = null;
        micLevel = null;
      }
    }
    let released = false;
    const mix: RecordingMix = {
      stream: destination.stream,
      sources: { interviewer: true, candidate: micSource !== null },
      release: () => {
        if (released) return;
        released = true;
        this.mixes.delete(mix);
        for (const disconnect of [() => this.gain.disconnect(voice), () => voice.disconnect(), () => micSource?.disconnect(), () => micLevel?.disconnect()]) {
          try {
            disconnect();
          } catch {
            /* already disconnected */
          }
        }
        for (const track of destination.stream.getTracks()) track.stop();
      },
    };
    this.mixes.add(mix);
    return mix;
  }

  private tick(): void {
    if (this.disposed) return;
    const events = this.scheduler.tick(this.context.currentTime);
    for (const event of events) this.opts.onEvent?.(event);
  }

  dispose(): void {
    if (this.disposed) return;
    this.interrupt(undefined, false);
    for (const mix of [...this.mixes]) mix.release();
    this.disposed = true;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    try {
      this.gain.disconnect();
      this.analyser.disconnect();
    } catch {
      /* ignore */
    }
    if (this.ownsContext) void this.context.close().catch(() => undefined);
  }
}
