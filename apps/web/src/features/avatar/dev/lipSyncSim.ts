/**
 * Offline lip-sync replay (tests + dev). Plays a real TTS fixture through the
 * interview room's scheduling path on a fake clock: every chunk goes through
 * PlaybackScheduler (features/realtime) exactly like AudioPlayback, each
 * scheduled chunk becomes the same SpeechCue AudioPlayback hands to
 * controller.enqueueSpeech, and a stand-in AnalyserNode serves the chunk PCM
 * at the scheduled times so loudness modulation behaves as in the browser.
 * The engine is stepped at a fixed frame rate and every frame's final face
 * weights are recorded for analyzeLipSync().
 */
import { metaFromMessage, PlaybackScheduler } from "@/features/realtime/playbackScheduler";
import { createAvatarController, getAvatarInternals } from "../controller";
import type { SpeechCue } from "../types";
import { analyzeLipSync, mouthWeights, type LipSyncReport, type LipSyncThresholds, type MouthSample } from "./lipSyncMetrics";
import { chunkMessage, type TtsFixture } from "./ttsFixtures";

/** Typical AudioContext rate; the analyser window covers fftSize samples at this rate. */
const CONTEXT_RATE = 48000;
const FFT_SIZE = 1024;

interface PlacedAudio {
  start: number;
  rate: number;
  samples: Int16Array;
}

/** Scheduled PCM on the audio clock (what the speakers would play). */
export class PcmTimeline {
  private readonly placed: PlacedAudio[] = [];

  add(start: number, samples: Int16Array, rate: number): void {
    this.placed.push({ start, rate, samples });
  }

  /** Sample value (-1..1) at time `t` (s), linearly interpolated. */
  valueAt(t: number): number {
    for (const p of this.placed) {
      const x = (t - p.start) * p.rate;
      if (x < 0 || x > p.samples.length - 1) continue;
      const i = Math.floor(x);
      const f = x - i;
      const a = p.samples[i]!;
      const b = p.samples[Math.min(p.samples.length - 1, i + 1)]!;
      return (a + (b - a) * f) / 32768;
    }
    return 0;
  }

  /** AnalyserNode.getFloatTimeDomainData equivalent: the window ending at `t`. */
  fill(buffer: Float32Array, t: number): void {
    const n = buffer.length;
    for (let k = 0; k < n; k++) buffer[k] = this.valueAt(t - (n - 1 - k) / CONTEXT_RATE);
  }

  /** RMS of the last `window` seconds before `t`. */
  level(t: number, window = 0.02): number {
    const steps = 96;
    let sum = 0;
    for (let k = 0; k < steps; k++) {
      const v = this.valueAt(t - (window * k) / steps);
      sum += v * v;
    }
    return Math.sqrt(sum / steps);
  }
}

/** AudioContext + AnalyserNode stand-ins on an arbitrary clock, fed from a PcmTimeline. */
export function fakeAudioInputs(timeline: PcmTimeline, now: () => number, outputLatency = 0): { context: AudioContext; analyser: AnalyserNode } {
  // With an output latency the listener hears graph time `now - outputLatency` (see audioClock.ts).
  const context = {
    get currentTime() {
      return now();
    },
    ...(outputLatency > 0 ? { outputLatency, baseLatency: 0 } : {}),
  } as unknown as AudioContext;
  const analyser = { fftSize: FFT_SIZE, getFloatTimeDomainData: (buffer: Float32Array) => timeline.fill(buffer, now()) } as unknown as AnalyserNode;
  return { context, analyser };
}

/**
 * Hand chunk `index` of a fixture to the scheduler at time `now` (its tts.chunk
 * metadata must already be registered) and return the SpeechCues that
 * AudioPlayback.play() would pass to controller.enqueueSpeech for every chunk
 * that became playable, placing their PCM on `timeline`.
 */
export function deliverChunk(
  scheduler: PlaybackScheduler,
  fixture: TtsFixture,
  audio: readonly Int16Array[],
  index: number,
  now: number,
  timeline: PcmTimeline,
): Array<SpeechCue & { endTime: number }> {
  const chunk = fixture.chunks[index]!;
  const final = index === fixture.chunks.length - 1;
  return scheduler.addAudio(chunk.chunk_id, audio[index]!, final, now).map((scheduled) => {
    timeline.add(scheduled.startTime, scheduled.samples, scheduled.sampleRate);
    return {
      utteranceId: scheduled.utteranceId,
      chunkId: scheduled.chunkId,
      startTime: scheduled.startTime,
      durationMs: Math.round(scheduled.playMs),
      visemes: scheduled.visemes,
      text: scheduled.text,
      endTime: scheduled.endTime,
    };
  });
}

/** A scheduler (AudioPlayback's settings) with every chunk's metadata registered. */
export function fixtureScheduler(fixture: TtsFixture, utteranceId: string): PlaybackScheduler {
  const scheduler = new PlaybackScheduler({ progressIntervalS: 0.5 });
  scheduler.begin(utteranceId);
  fixture.chunks.forEach((chunk, i) => scheduler.addMeta(metaFromMessage(chunkMessage(utteranceId, chunk, i + 1))));
  return scheduler;
}

export interface LipSyncSimOptions {
  fps?: number;
  /** Quiet time before the first chunk's audio arrives (s). */
  startAt?: number;
  /** Arrival of each chunk's audio relative to the first (s); default 0.25 s apart (TTS runs ahead). */
  arrivals?: number[];
  /** Feed loudness from the PCM through a stand-in AnalyserNode (default true, as in the room). */
  analyser?: boolean;
  /** Simulated time after the last chunk ends (s). */
  tail?: number;
  thresholds?: Partial<LipSyncThresholds>;
  seed?: number;
}

export interface LipSyncSimResult {
  samples: MouthSample[];
  cues: SpeechCue[];
  report: LipSyncReport;
}

export function simulateFixture(fixture: TtsFixture, audio: readonly Int16Array[], options: LipSyncSimOptions = {}): LipSyncSimResult {
  const fps = options.fps ?? 60;
  const startAt = options.startAt ?? 0.5;
  const tail = options.tail ?? 0.8;
  let t = 0;
  const controller = createAvatarController(
    { modelUrl: "procedural", modelFormat: "procedural", interviewerName: "Maya", renderMode: "none", seed: options.seed ?? 7, reducedMotion: false },
    { wallClock: () => t },
  );
  const engine = getAvatarInternals(controller)!.engine;
  const timeline = new PcmTimeline();
  const inputs = fakeAudioInputs(timeline, () => t);
  controller.attachAudio(inputs.context, options.analyser === false ? null : inputs.analyser);

  const utteranceId = `fixture-${fixture.id}`;
  const scheduler = fixtureScheduler(fixture, utteranceId);
  const arrivals = fixture.chunks.map((_, i) => startAt + (options.arrivals?.[i] ?? i * 0.25));
  const delivered = new Set<number>();
  const cues: SpeechCue[] = [];
  const samples: MouthSample[] = [];
  let lastEnd = 0;

  for (let frame = 0; frame < fps * 120; frame++) {
    t = frame / fps;
    fixture.chunks.forEach((_, i) => {
      if (delivered.has(i) || t < arrivals[i]!) return;
      delivered.add(i);
      for (const { endTime, ...cue } of deliverChunk(scheduler, fixture, audio, i, t, timeline)) {
        cues.push(cue);
        controller.enqueueSpeech(cue);
        lastEnd = Math.max(lastEnd, endTime);
      }
    });
    const out = engine.update();
    samples.push({ t, face: mouthWeights(out.face), level: timeline.level(t) });
    if (delivered.size === fixture.chunks.length && t > lastEnd + tail) break;
  }
  controller.dispose();
  return { samples, cues, report: analyzeLipSync(samples, cues, options.thresholds) };
}
