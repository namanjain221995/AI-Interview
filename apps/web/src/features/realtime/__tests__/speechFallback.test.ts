import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserSpeech } from "../speechFallback";

class FakeUtterance {
  voice: unknown = null;
  lang = "";
  rate = 1;
  pitch = 1;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((e: { error: string }) => void) | null = null;
  onboundary: ((e: { name: string; charIndex: number }) => void) | null = null;
  constructor(readonly text: string) {}
}

/** speechSynthesis plays queued utterances in order; tests advance it explicitly. */
class FakeSynth {
  queue: FakeUtterance[] = [];
  cancelled = 0;
  speak(u: FakeUtterance) {
    this.queue.push(u);
  }
  cancel() {
    this.cancelled += 1;
    const pending = this.queue.splice(0);
    pending.forEach((u) => u.onerror?.({ error: "canceled" }));
  }
  getVoices() {
    return [];
  }
  /** Speak the next queued sentence to the end. */
  playNext() {
    const u = this.queue.shift();
    u?.onstart?.();
    u?.onend?.();
    return u?.text;
  }
}

let synth: FakeSynth;

beforeEach(() => {
  vi.useFakeTimers();
  synth = new FakeSynth();
  vi.stubGlobal("speechSynthesis", synth);
  vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("BrowserSpeech", () => {
  it("queues every fallback chunk of an utterance instead of cutting the previous one", () => {
    const speech = new BrowserSpeech();
    const onEnd = vi.fn();
    const onStart = vi.fn();
    expect(speech.speak("u1", "Tell me about your last incident.", { onStart, onEnd })).toBe(true);
    // The server sends the next chunk of the same utterance while the first is speaking.
    expect(speech.speak("u1", "What did you change afterwards?", { onEnd: vi.fn() })).toBe(true);
    expect(synth.cancelled).toBe(0);
    expect(synth.queue.map((u) => u.text)).toEqual(["Tell me about your last incident.", "What did you change afterwards?"]);
    expect(synth.playNext()).toBe("Tell me about your last incident.");
    expect(synth.playNext()).toBe("What did you change afterwards?");
    // All queued text was spoken, but more may still come until the utterance ends.
    expect(onEnd).not.toHaveBeenCalled();
    expect(speech.has("u1")).toBe(true);
    speech.end("u1");
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledWith(expect.objectContaining({ interrupted: false }));
    expect(speech.has("u1")).toBe(false);
  });

  it("plays the next utterance after the current one and cancels both on barge-in", () => {
    const speech = new BrowserSpeech();
    const first = vi.fn();
    const second = vi.fn();
    speech.speak("intro", "Hi, I'm Maya.", { onEnd: first });
    speech.speak("q1", "To start, walk me through your platform.", { onEnd: second });
    speech.end("intro");
    expect(speech.speakingId).toBe("intro");
    synth.playNext();
    expect(first).toHaveBeenCalledWith(expect.objectContaining({ interrupted: false }));
    expect(speech.speakingId).toBe("q1");
    speech.cancel(true);
    expect(second).toHaveBeenCalledWith(expect.objectContaining({ interrupted: true }));
    expect(speech.speakingId).toBeNull();
  });

  it("finishes an utterance whose engine never reports the end", () => {
    const speech = new BrowserSpeech();
    const onEnd = vi.fn();
    speech.speak("u2", "Short question?", { onEnd });
    speech.end("u2");
    vi.advanceTimersByTime(10_000);
    expect(onEnd).toHaveBeenCalledWith(expect.objectContaining({ interrupted: false }));
  });
});
