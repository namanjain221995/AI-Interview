/**
 * AudioCapture: microphone -> AudioWorklet (public/worklets/mic-capture.worklet.js,
 * 16 kHz PCM16, 20 ms frames) -> 100 ms batches packed as binary 0x01 frames
 * with an increasing sequence number, plus a local energy VAD for instant UX.
 *
 * getUserMedia constraints request echo cancellation, noise suppression and
 * automatic gain control so the interviewer's own voice does not trigger the
 * server's barge-in detection.
 */
import { FrameBatcher } from "@/lib/realtime/pcm";
import { packMicFrame } from "@/lib/realtime/protocol";

import { EnergyVad, type VadResult } from "./vad";

export const MIC_WORKLET_URL = "/worklets/mic-capture.worklet.js";

export const MIC_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

export interface AudioCaptureOptions {
  /** A packed 0x01 frame (header + 100 ms PCM16) ready for the socket. */
  onFrame: (frame: ArrayBuffer, seq: number) => void;
  /** Every 20 ms: VAD decision + 0-1 level. */
  onLevel?: (result: VadResult) => void;
  /** Speaking state transitions only. */
  onVadChange?: (speaking: boolean, level: number) => void;
  onError?: (error: unknown) => void;
  workletUrl?: string;
  context?: AudioContext;
}

/** Sequence + batching state, separated from Web Audio for tests. */
export class MicFramer {
  private readonly batcher = new FrameBatcher();
  private seq = 0;

  /** Push one 20 ms PCM16 frame; returns packed binary frames to send. */
  push(frame: Int16Array): Array<{ seq: number; data: ArrayBuffer }> {
    return this.batcher.push(frame).map((batch) => {
      this.seq = (this.seq + 1) >>> 0;
      return { seq: this.seq, data: packMicFrame(this.seq, batch) };
    });
  }

  get lastSeq(): number {
    return this.seq;
  }
}

export class AudioCapture {
  private context: AudioContext | null = null;
  private ownsContext = false;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private readonly framer = new MicFramer();
  private readonly vad = new EnergyVad();
  private readonly opts: AudioCaptureOptions;
  private track: MediaStreamTrack | null = null;
  private running = false;

  constructor(options: AudioCaptureOptions) {
    this.opts = options;
  }

  static isSupported(): boolean {
    return typeof window !== "undefined" && typeof AudioWorkletNode !== "undefined" && typeof AudioContext !== "undefined";
  }

  get isRunning(): boolean {
    return this.running;
  }

  get speaking(): boolean {
    return this.vad.speaking;
  }

  get audioTrack(): MediaStreamTrack | null {
    return this.track;
  }

  /** Start capturing from a stream that has an audio track. */
  async start(stream: MediaStream): Promise<void> {
    if (this.running) return;
    const track = stream.getAudioTracks()[0];
    if (!track) throw new Error("no microphone track");
    this.track = track;
    this.ownsContext = !this.opts.context;
    const context = this.opts.context ?? new AudioContext({ latencyHint: "interactive" });
    this.context = context;
    if (context.state === "suspended") await context.resume().catch(() => undefined);
    await context.audioWorklet.addModule(this.opts.workletUrl ?? MIC_WORKLET_URL);
    const node = new AudioWorkletNode(context, "mic-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      channelCount: 1,
      channelCountMode: "explicit",
      processorOptions: { outputRate: 16000 },
    });
    node.port.onmessage = (event: MessageEvent<{ type: string; pcm?: ArrayBuffer; rms?: number }>) => this.onWorkletMessage(event.data);
    node.onprocessorerror = (event) => this.opts.onError?.(event);
    const source = context.createMediaStreamSource(new MediaStream([track]));
    // Keep the graph pulled without making the microphone audible.
    const sink = context.createGain();
    sink.gain.value = 0;
    source.connect(node);
    node.connect(sink);
    sink.connect(context.destination);
    this.source = source;
    this.node = node;
    this.sink = sink;
    this.running = true;
  }

  private onWorkletMessage(data: { type: string; pcm?: ArrayBuffer; rms?: number }): void {
    if (!this.running || data.type !== "frame" || !data.pcm) return;
    const frame = new Int16Array(data.pcm);
    const vad = this.vad.push(typeof data.rms === "number" ? data.rms : 0);
    this.opts.onLevel?.(vad);
    if (vad.changed) this.opts.onVadChange?.(vad.speaking, vad.level);
    for (const packed of this.framer.push(frame)) this.opts.onFrame(packed.data, packed.seq);
  }

  setMuted(muted: boolean): void {
    this.node?.port.postMessage({ type: "mute", muted });
  }

  stop(): void {
    if (!this.running && !this.context) return;
    this.running = false;
    try {
      this.node?.port.postMessage({ type: "stop" });
      this.source?.disconnect();
      this.node?.disconnect();
      this.sink?.disconnect();
    } catch {
      /* ignore */
    }
    if (this.node) this.node.port.onmessage = null;
    this.node = null;
    this.source = null;
    this.sink = null;
    if (this.ownsContext && this.context) void this.context.close().catch(() => undefined);
    this.context = null;
  }
}
