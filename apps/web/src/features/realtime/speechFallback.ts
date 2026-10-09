/**
 * Fallbacks when server TTS is unavailable (`tts.fallback`): the browser's
 * speechSynthesis (word boundaries drive the avatar's mouth and the subtitle
 * progress), or plain text with a reading-time estimate so the server still
 * learns when the question was delivered.
 */

export function estimateReadingMs(text: string): number {
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  // ~170 words per minute reading/listening pace, never less than 1.5 s.
  return Math.max(1500, Math.round((words * 60_000) / 170));
}

/** Split into sentences so long utterances do not hit Chrome's ~15 s speech cut-off. */
export function splitSentences(text: string): string[] {
  const parts = text.match(/[^.!?]+[.!?]+["')\]]*\s*|[^.!?]+$/g) ?? [text];
  return parts.map((p) => p.trim()).filter(Boolean);
}

export interface BrowserSpeechCallbacks {
  onStart?: () => void;
  onWord?: (word: string) => void;
  onEnd?: (result: { interrupted: boolean; playedMs: number }) => void;
}

function pickVoice(): SpeechSynthesisVoice | null {
  try {
    const voices = window.speechSynthesis.getVoices();
    const english = voices.filter((v) => /^en(-|_|$)/i.test(v.lang));
    return (
      english.find((v) => /natural|neural|google us english|samantha|aria|jenny/i.test(v.name)) ??
      english.find((v) => /en-us/i.test(v.lang)) ??
      english[0] ??
      voices[0] ??
      null
    );
  } catch {
    return null;
  }
}

interface SpeechTrack {
  id: string;
  callbacks: BrowserSpeechCallbacks;
  /** performance.now() when its first sentence started speaking. */
  startedAt: number | null;
  /** Sentences handed to speechSynthesis and not yet finished. */
  queued: number;
  /** All text for this utterance has arrived (`ai.utterance.end`). */
  closed: boolean;
  done: boolean;
  chars: number;
  watchdog: ReturnType<typeof setTimeout> | null;
}

/**
 * Browser speechSynthesis fallback. The server sends one `tts.fallback` per sentence
 * chunk and may start the next utterance before the current one has been heard, so
 * speech is queued per utterance (speechSynthesis itself plays strictly in order) and
 * an utterance only finishes once its text is complete (`end(id)`) and spoken.
 */
export class BrowserSpeech {
  private tracks: SpeechTrack[] = [];

  static isSupported(): boolean {
    return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  }

  /** The utterance being spoken (or next in line). */
  get speakingId(): string | null {
    return this.tracks.find((t) => !t.done)?.id ?? null;
  }

  /** Is speech queued or playing for this utterance? */
  has(id: string): boolean {
    return this.tracks.some((t) => t.id === id && !t.done);
  }

  /**
   * Queue `text` for utterance `id`. The first call for an utterance registers its
   * callbacks; later calls (more chunks of the same utterance) append to it.
   */
  speak(id: string, text: string, callbacks: BrowserSpeechCallbacks = {}): boolean {
    if (!BrowserSpeech.isSupported() || !text.trim()) return false;
    const synth = window.speechSynthesis;
    const voice = pickVoice();
    let track = this.tracks.find((t) => t.id === id && !t.done);
    if (!track) {
      track = { id, callbacks, startedAt: null, queued: 0, closed: false, done: false, chars: 0, watchdog: null };
      this.tracks.push(track);
    }
    const state = track;
    const sentences = splitSentences(text);
    state.chars += text.length;
    this.armWatchdog(state);
    for (const sentence of sentences) {
      const utterance = new SpeechSynthesisUtterance(sentence);
      if (voice) utterance.voice = voice;
      utterance.lang = voice?.lang ?? "en-US";
      utterance.rate = 1;
      utterance.pitch = 1;
      state.queued += 1;
      let settled = false;
      const settle = () => {
        if (settled) return;
        settled = true;
        state.queued = Math.max(0, state.queued - 1);
        this.maybeFinish(state);
      };
      utterance.onstart = () => {
        if (state.done || state.startedAt !== null) return;
        state.startedAt = performance.now();
        state.callbacks.onStart?.();
      };
      utterance.onboundary = (event: SpeechSynthesisEvent) => {
        if (state.done || event.name !== "word") return;
        const word = sentence.slice(event.charIndex).match(/^[^\s]+/)?.[0] ?? "";
        if (word) state.callbacks.onWord?.(word);
      };
      utterance.onerror = (event: SpeechSynthesisErrorEvent) => {
        if (event.error === "interrupted" || event.error === "canceled") this.finish(state, true);
        else settle();
      };
      utterance.onend = settle;
      synth.speak(utterance);
    }
    return true;
  }

  /** No more text will come for this utterance; it finishes once everything queued was spoken. */
  end(id: string): void {
    const track = this.tracks.find((t) => t.id === id && !t.done);
    if (!track) return;
    track.closed = true;
    this.maybeFinish(track);
  }

  private maybeFinish(track: SpeechTrack): void {
    if (track.closed && track.queued === 0) this.finish(track, false);
  }

  private finish(track: SpeechTrack, interrupted: boolean): void {
    if (track.done) return;
    track.done = true;
    if (track.watchdog) clearTimeout(track.watchdog);
    this.tracks = this.tracks.filter((t) => t !== track);
    const playedMs = track.startedAt === null ? 0 : Math.round(performance.now() - track.startedAt);
    track.callbacks.onEnd?.({ interrupted, playedMs });
  }

  /** Some engines never fire `end`: cap each utterance at a generous estimate of its length. */
  private armWatchdog(track: SpeechTrack): void {
    if (track.watchdog) clearTimeout(track.watchdog);
    const words = Math.max(1, Math.round(track.chars / 5.5));
    const estimate = Math.max(1500, (words * 60_000) / 170) * 1.8 + 3000;
    track.watchdog = setTimeout(() => {
      const last = this.tracks[this.tracks.length - 1] === track;
      // Finish first: the cancel below fires "canceled" errors that must not read as a barge-in.
      this.finish(track, false);
      if (last) {
        try {
          window.speechSynthesis.cancel();
        } catch {
          /* ignore */
        }
      }
    }, estimate);
  }

  /** Stop all speech (barge-in, disconnect). Reports `interrupted` to unfinished utterances when asked. */
  cancel(report = true): void {
    const tracks = this.tracks;
    this.tracks = [];
    if (BrowserSpeech.isSupported()) {
      try {
        window.speechSynthesis.cancel();
      } catch {
        /* ignore */
      }
    }
    for (const track of tracks) {
      if (track.done) continue;
      track.done = true;
      if (track.watchdog) clearTimeout(track.watchdog);
      if (report) track.callbacks.onEnd?.({ interrupted: true, playedMs: track.startedAt === null ? 0 : Math.round(performance.now() - track.startedAt) });
    }
  }
}
