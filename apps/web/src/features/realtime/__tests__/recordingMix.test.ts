/**
 * AudioPlayback.createRecordingMix: the interviewer's voice is tapped after the
 * duck gain and mixed with the microphone into a destination that is never
 * routed to the speakers; playback itself is untouched.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AudioPlayback } from "../audioPlayback";

type Edge = [string, string];

class FakeTrack {
  readyState: "live" | "ended" = "live";
  stopped = false;
  constructor(readonly kind: "audio" | "video") {}
  stop() {
    this.stopped = true;
    this.readyState = "ended";
  }
}

class FakeStream {
  constructor(readonly tracks: FakeTrack[] = []) {}
  getTracks() {
    return this.tracks;
  }
  getAudioTracks() {
    return this.tracks.filter((t) => t.kind === "audio");
  }
}

let edges: Edge[] = [];

class FakeNode {
  constructor(readonly name: string) {}
  connect<T extends FakeNode>(node: T): T {
    edges.push([this.name, node.name]);
    return node;
  }
  disconnect(node?: FakeNode) {
    edges = edges.filter(([from, to]) => from !== this.name || (node !== undefined && to !== node.name));
  }
}

class FakeContext {
  state = "running";
  currentTime = 0;
  destination = new FakeNode("speakers");
  private n = 0;
  createGain() {
    return Object.assign(new FakeNode(`gain${this.n++}`), { gain: { value: 1, setTargetAtTime() {}, cancelScheduledValues() {} } });
  }
  createAnalyser() {
    return Object.assign(new FakeNode("analyser"), { fftSize: 0, smoothingTimeConstant: 0 });
  }
  createMediaStreamDestination() {
    return Object.assign(new FakeNode("recording"), { stream: new FakeStream([new FakeTrack("audio")]), channelCount: 2, channelCountMode: "max", channelInterpretation: "speakers" });
  }
  createMediaStreamSource() {
    return new FakeNode("microphone");
  }
  close() {
    return Promise.resolve();
  }
}

beforeEach(() => {
  edges = [];
  vi.useFakeTimers();
  vi.stubGlobal("MediaStream", FakeStream);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function reachable(from: string): Set<string> {
  const seen = new Set<string>([from]);
  const stack = [from];
  while (stack.length) {
    const node = stack.pop()!;
    for (const [a, b] of edges) if (a === node && !seen.has(b)) (seen.add(b), stack.push(b));
  }
  return seen;
}

describe("recording mix", () => {
  it("mixes the post-duck interviewer voice with the microphone, never into the speakers", () => {
    const ctx = new FakeContext();
    const playback = new AudioPlayback({ context: ctx as unknown as AudioContext });
    const duck = "gain0"; // the playback's duck gain (created first)
    expect(reachable(duck).has("speakers")).toBe(true); // playback path unchanged
    const mic = new FakeStream([new FakeTrack("audio")]) as unknown as MediaStream;
    const mix = playback.createRecordingMix(mic)!;
    expect(mix).not.toBeNull();
    expect(mix.sources).toEqual({ interviewer: true, candidate: true });
    expect(reachable(duck).has("recording")).toBe(true);
    expect(reachable("microphone").has("recording")).toBe(true);
    // Echo guard: the microphone never reaches the speakers; the recording destination is a sink.
    expect(reachable("microphone").has("speakers")).toBe(false);
    expect(edges.some(([from]) => from === "recording")).toBe(false);
    expect(reachable(duck).has("analyser")).toBe(true);

    mix.release();
    expect(reachable(duck).has("recording")).toBe(false);
    expect(reachable("microphone").has("recording")).toBe(false);
    expect((mix.stream.getTracks()[0] as unknown as FakeTrack).stopped).toBe(true);
    expect(reachable(duck).has("speakers")).toBe(true);
    playback.dispose();
  });

  it("records the interviewer alone without a microphone and releases on dispose", () => {
    const ctx = new FakeContext();
    const playback = new AudioPlayback({ context: ctx as unknown as AudioContext });
    const mix = playback.createRecordingMix(null)!;
    expect(mix.sources).toEqual({ interviewer: true, candidate: false });
    playback.dispose();
    expect((mix.stream.getTracks()[0] as unknown as FakeTrack).stopped).toBe(true);
    expect(playback.createRecordingMix(null)).toBeNull();
  });

  it("is null when the browser cannot build a stream destination", () => {
    const ctx = new FakeContext() as unknown as Record<string, unknown>;
    delete ctx.createMediaStreamDestination;
    (ctx as { createMediaStreamDestination?: unknown }).createMediaStreamDestination = undefined;
    const playback = new AudioPlayback({ context: ctx as unknown as AudioContext });
    expect(playback.createRecordingMix(null)).toBeNull();
    playback.dispose();
  });
});
