/**
 * Real TTS lip-sync playback for the avatar playground (dev).
 *
 * RealTtsPlayer plays a captured Kokoro utterance through the interview room's
 * own playback path: AudioPlayback (features/realtime) receives the
 * `tts.chunk` metadata and the PCM exactly as runtime.ts hands them over,
 * PlaybackScheduler places the chunks on the AudioContext clock and every
 * scheduled chunk reaches controller.enqueueSpeech through `onCue`, with the
 * playback analyser attached for loudness. While it plays, the frames the
 * renderer actually produced are recorded (engine.lastFrame on the lip-sync
 * clock) and scored with analyzeLipSync().
 *
 * The strip API (manual clock) replays a fixture deterministically so a
 * script can step the renderer frame by frame and capture mouth images at
 * exact audio times.
 */
import { AudioPlayback, type PlaybackReport, type SpeechCueOut } from "@/features/realtime/audioPlayback";
import { getAvatarInternals } from "../controller";
import type { AvatarController, SpeechCue } from "../types";
import { analyzeLipSync, mouthWeights, timelineFromCues, type LipSyncReport, type MouthSample, type TimedViseme } from "./lipSyncMetrics";
import { deliverChunk, fakeAudioInputs, fixtureScheduler, PcmTimeline } from "./lipSyncSim";
import { chunkMessage, loadTtsFixture } from "./ttsFixtures";

export interface RealTtsOptions {
  /** Deliver the last chunk late so playback underruns mid-utterance (the mouth must close and resume). */
  underrun?: boolean;
}

export interface RealTtsResult {
  id: string;
  report: LipSyncReport;
  samples: MouthSample[];
  cues: SpeechCue[];
  interrupted: boolean;
  /** AudioContext output + base latency the lip-sync clock compensated (ms). */
  outputLatencyMs: number;
  /** Longest gap between recorded frames (ms): a render stall hides visemes. */
  longestFrameMs: number;
}

interface ActivePlayback {
  id: string;
  utteranceId: string;
  cues: SpeechCue[];
  samples: MouthSample[];
  levels: Array<{ t: number; v: number }>;
  timers: Array<ReturnType<typeof setTimeout>>;
  raf: number;
  lastClock: number;
  interrupted: boolean;
  resolve: (result: RealTtsResult) => void;
}

/** Attach the nearest loudness reading to every mouth sample (both sorted by time). */
function attachLevels(samples: MouthSample[], levels: ReadonlyArray<{ t: number; v: number }>): void {
  let j = 0;
  for (const s of samples) {
    while (j + 1 < levels.length && Math.abs(levels[j + 1]!.t - s.t) <= Math.abs(levels[j]!.t - s.t)) j++;
    if (levels.length) s.level = levels[j]!.v;
  }
}

export class RealTtsPlayer {
  private playback: AudioPlayback | null = null;
  private out: GainNode | null = null;
  private buffer: Float32Array<ArrayBuffer> | null = null;
  private muted = true;
  private serial = 0;
  private active: ActivePlayback | null = null;

  constructor(private readonly controller: AvatarController) {}

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.out) this.out.gain.value = muted ? 0 : 0.8;
  }

  private ensurePlayback(): AudioPlayback {
    if (this.playback) return this.playback;
    const playback = new AudioPlayback({ onCue: (cue) => this.onCue(cue), onEvent: (event) => this.onEvent(event) });
    // The room plays straight to the speakers; here the analyser output goes
    // through a mute gain (the analyser still sees the signal).
    const out = playback.context.createGain();
    out.gain.value = this.muted ? 0 : 0.8;
    playback.analyser.disconnect();
    playback.analyser.connect(out).connect(playback.context.destination);
    this.playback = playback;
    this.out = out;
    return playback;
  }

  async play(id: string, options: RealTtsOptions = {}): Promise<RealTtsResult> {
    this.finish(true);
    const { fixture, audio } = await loadTtsFixture(id);
    const playback = this.ensurePlayback();
    this.controller.attachAudio(playback.context, playback.analyser);
    await playback.resume();
    const utteranceId = `tts-${fixture.id}-${++this.serial}`;
    this.controller.applyBehavior({ state: "SPEAKING", tone: "professional" });
    playback.begin(utteranceId);

    return new Promise<RealTtsResult>((resolve) => {
      const active: ActivePlayback = {
        id,
        utteranceId,
        cues: [],
        samples: [],
        levels: [],
        timers: [],
        raf: 0,
        lastClock: -Infinity,
        interrupted: false,
        resolve,
      };
      this.active = active;
      const last = fixture.chunks.length - 1;
      const lateBy = fixture.chunks.slice(0, last).reduce((sum, c) => sum + c.duration_ms / 1000, 0) + 0.45;
      fixture.chunks.forEach((chunk, i) => {
        const delay = options.underrun && i === last && last > 0 ? lateBy : i * 0.25;
        active.timers.push(
          setTimeout(() => {
            if (this.active !== active) return;
            // runtime.ts: tts.chunk -> handleChunkMeta, binary 0x02 -> handleAudio, ai.utterance.end -> end
            playback.handleChunkMeta(chunkMessage(utteranceId, chunk, i + 1));
            playback.handleAudio(chunk.chunk_id, audio[i]!, i === last);
            if (i === last) playback.end(utteranceId);
          }, delay * 1000),
        );
      });
      this.record(active, playback);
    });
  }

  /** Barge-in, as the room handles `barge_in`: stop the audio, interrupt the avatar. */
  interrupt(): void {
    const active = this.active;
    if (!active) return;
    active.interrupted = true;
    this.playback?.interrupt(active.utteranceId);
    this.controller.interrupt();
  }

  dispose(): void {
    this.finish(true);
    this.playback?.dispose();
    this.playback = null;
    this.out = null;
  }

  private record(active: ActivePlayback, playback: AudioPlayback): void {
    const engine = getAvatarInternals(this.controller)?.engine;
    const analyser = playback.analyser;
    const tick = () => {
      if (this.active !== active) return;
      active.raf = requestAnimationFrame(tick);
      if (!this.buffer || this.buffer.length !== analyser.fftSize) this.buffer = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(this.buffer);
      let sum = 0;
      for (let i = 0; i < this.buffer.length; i++) sum += this.buffer[i]! * this.buffer[i]!;
      // The analyser window ends at the graph time; label it with its centre.
      const ctx = playback.context;
      active.levels.push({ t: ctx.currentTime - analyser.fftSize / ctx.sampleRate / 2, v: Math.sqrt(sum / this.buffer.length) });
      const frame = engine?.lastFrame;
      if (frame && frame.speech.clock !== active.lastClock) {
        active.lastClock = frame.speech.clock;
        active.samples.push({ t: frame.speech.clock, face: mouthWeights(frame.face) });
      }
    };
    active.raf = requestAnimationFrame(tick);
  }

  private onCue(cue: SpeechCueOut): void {
    // runtime.ts onCue: the scheduled chunk becomes a SpeechCue for the avatar.
    this.controller.enqueueSpeech({ ...cue });
    this.active?.cues.push({ ...cue });
  }

  private onEvent(event: PlaybackReport): void {
    const active = this.active;
    if (!active || event.utteranceId !== active.utteranceId) return;
    if (event.type === "finished" || event.type === "interrupted") {
      if (event.type === "interrupted") active.interrupted = true;
      // Keep recording briefly so the closing of the mouth is scored too.
      active.timers.push(setTimeout(() => this.finish(false), 600));
    }
  }

  /** End the current playback; `stopAudio` silences it first (pre-empted / disposed). */
  private finish(stopAudio: boolean): void {
    const active = this.active;
    if (!active) return;
    this.active = null;
    cancelAnimationFrame(active.raf);
    for (const timer of active.timers) clearTimeout(timer);
    if (stopAudio) {
      active.interrupted = true;
      this.playback?.interrupt(active.utteranceId, false);
    }
    attachLevels(active.samples, active.levels);
    const ctx = this.playback?.context;
    const latency = ctx ? (Number(ctx.outputLatency) || 0) + (Number(ctx.baseLatency) || 0) : 0;
    let longest = 0;
    for (let i = 1; i < active.samples.length; i++) longest = Math.max(longest, active.samples[i]!.t - active.samples[i - 1]!.t);
    active.resolve({
      id: active.id,
      report: analyzeLipSync(active.samples, active.cues),
      samples: active.samples,
      cues: active.cues,
      interrupted: active.interrupted,
      outputLatencyMs: Math.round(latency * 1000),
      longestFrameMs: Math.round(longest * 1000),
    });
  }
}

// ---------------------------------------------------------------- strips

export interface ManualClock {
  now(): number;
  set(t: number): void;
}

/** Wall clock that only moves when told to (deterministic frame stepping). */
export function createManualClock(start = 1): ManualClock {
  let t = start;
  return {
    now: () => t,
    set: (value: number) => {
      if (Number.isFinite(value)) t = value;
    },
  };
}

export interface StripPlan {
  cues: SpeechCue[];
  visemes: TimedViseme[];
  text: string;
  start: number;
  end: number;
}

export interface StripApi {
  /** Schedule a fixture on the manual clock (all chunks arrive at `at`). */
  prepare(id: string, at: number): Promise<StripPlan>;
  /** Loudness (RMS of the last 20 ms) of the scheduled audio at time t. */
  level(t: number): number;
}

/**
 * Deterministic replay for frame strips: the controller must run on `clock`
 * (createAvatarController(config, { wallClock: clock.now })). Loudness comes
 * from the fixture PCM through a stand-in analyser, exactly like the offline
 * simulation, so a stepped render matches the measured behaviour.
 * `outputLatency` (s) simulates the speaker delay of a real output, so the
 * audible-time clock and the loudness look-ahead work as in a browser.
 */
export function createStripApi(controller: AvatarController, clock: ManualClock, options: { outputLatency?: number } = {}): StripApi {
  let timeline = new PcmTimeline();
  return {
    async prepare(id: string, at: number): Promise<StripPlan> {
      const { fixture, audio } = await loadTtsFixture(id);
      timeline = new PcmTimeline();
      const inputs = fakeAudioInputs(timeline, clock.now, options.outputLatency ?? 0);
      controller.attachAudio(inputs.context, inputs.analyser);
      const scheduler = fixtureScheduler(fixture, `strip-${fixture.id}`);
      const cues: SpeechCue[] = [];
      let end = at;
      fixture.chunks.forEach((_, i) => {
        for (const { endTime, ...cue } of deliverChunk(scheduler, fixture, audio, i, at, timeline)) {
          cues.push(cue);
          controller.enqueueSpeech(cue);
          end = Math.max(end, endTime);
        }
      });
      return { cues, visemes: timelineFromCues(cues), text: fixture.text, start: cues[0]?.startTime ?? at, end };
    },
    level: (t: number) => timeline.level(t),
  };
}
