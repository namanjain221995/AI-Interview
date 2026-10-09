/**
 * IntegrityMonitor: turns browser signals into `ClientIntegrityEvent`
 * observations, driven by the session's frozen policy. These are observations
 * for a human reviewer, never verdicts, and the candidate-facing copy must stay
 * neutral.
 *
 * What a browser can and cannot see (also stated to the candidate):
 * - Fullscreen can be requested and its exit observed, not enforced.
 * - Switching tabs (visibilitychange) and windows (blur/focus) is visible;
 *   OS-level shortcuts (Alt+Tab, Cmd+Tab, the Windows key) are not.
 * - A second monitor is only reported where `screen.isExtended` exists
 *   (Chromium); other browsers say nothing.
 *
 * Interval signals (hidden tab, lost focus, left fullscreen) are reported ONCE,
 * when the interval closes, with `duration_ms` (the server's duration rules
 * need it); intervals still open when monitoring stops or the page unloads are
 * closed by `finalize()`. Rapid switching is derived server-side from the
 * individual events, so it is not reported here. Clipboard details carry sizes
 * only (`length`, `lines`, `context`), never the content.
 *
 * Page reloads are detected by the server (a resumed session whose new socket
 * says hello with last_seq 0, app/realtime/runtime.py attach) and recorded as
 * `page_reload` there; reporting them from the browser too would double count.
 */
import type { ClientIntegrityEvent, IntegrityEventType } from "@/lib/realtime/protocol";

import { classifyShortcut } from "./shortcuts";

export interface MonitorPolicy {
  enabled: boolean;
  require_fullscreen: boolean;
  monitor_focus: boolean;
  monitor_clipboard: boolean;
  monitor_shortcuts: boolean;
  camera_required: boolean;
  allow_page_reload_resume?: boolean;
}

export interface MonitorEnv {
  win: Window;
  doc: Document;
  /** Epoch milliseconds. */
  now: () => number;
  isFullscreen: () => boolean;
  isHidden: () => boolean;
  navigationType: () => string | null;
  screenExtended: () => boolean | null;
  storage: Pick<Storage, "getItem" | "setItem"> | null;
}

export function browserEnv(): MonitorEnv {
  return {
    win: window,
    doc: document,
    now: () => Date.now(),
    isFullscreen: () => Boolean(document.fullscreenElement),
    isHidden: () => document.visibilityState === "hidden",
    navigationType: () => {
      try {
        const entry = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
        return entry?.type ?? null;
      } catch {
        return null;
      }
    },
    screenExtended: () => {
      const s = window.screen as Screen & { isExtended?: boolean };
      return typeof s.isExtended === "boolean" ? s.isExtended : null;
    },
    storage: (() => {
      try {
        return window.sessionStorage;
      } catch {
        return null;
      }
    })(),
  };
}

export interface IntegrityMonitorOptions {
  policy: MonitorPolicy;
  sessionId: string;
  emit: (event: ClientIntegrityEvent) => void;
  /** Fullscreen state changed (the room shows a gentle "return to full screen" prompt). */
  onFullscreenChange?: (inFullscreen: boolean) => void;
  /** Is the event target inside the code editor (paste there is handled by the workspace)? */
  isCodingTarget?: (target: EventTarget | null) => boolean;
  env?: MonitorEnv;
}

export type ClipboardContext = "coding" | "page";

/** Size-only description of clipboard text. */
export function clipboardDetails(text: string, context: ClipboardContext): { length: number; lines: number; context: ClipboardContext } {
  return { length: text.length, lines: text ? text.split(/\r\n|\r|\n/).length : 0, context };
}

const THROTTLE_MS: Partial<Record<IntegrityEventType, number>> = {
  context_menu: 10_000,
  prohibited_shortcut: 3_000,
  copy_event: 5_000,
  paste_event: 3_000,
  multiple_displays_detected: 60_000,
};
const MIN_FOCUS_LOSS_MS = 250;

export class IntegrityMonitor {
  private readonly opts: IntegrityMonitorOptions;
  private readonly env: MonitorEnv;
  private readonly cleanups: Array<() => void> = [];
  private readonly lastEmitted = new Map<string, number>();
  private hiddenAt: number | null = null;
  private blurAt: number | null = null;
  private fullscreenExitAt: number | null = null;
  private suspendedUntil = 0;
  private running = false;
  private fullscreenWanted = false;
  private cameraMuteTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: IntegrityMonitorOptions) {
    this.opts = options;
    this.env = options.env ?? browserEnv();
  }

  get active(): boolean {
    return this.running;
  }

  private get policy(): MonitorPolicy {
    return this.opts.policy;
  }

  private emit(type: IntegrityEventType, details: Record<string, unknown> = {}, extra: { durationMs?: number; occurredAt?: number; throttleKey?: string } = {}): void {
    if (!this.running || !this.policy.enabled) return;
    const now = this.env.now();
    const throttle = THROTTLE_MS[type];
    const key = extra.throttleKey ?? type;
    if (throttle) {
      const last = this.lastEmitted.get(key) ?? 0;
      if (now - last < throttle) return;
    }
    this.lastEmitted.set(key, now);
    const event: ClientIntegrityEvent = { event_type: type, occurred_at: new Date(extra.occurredAt ?? now).toISOString(), details };
    if (extra.durationMs !== undefined) event.duration_ms = Math.max(0, Math.round(extra.durationMs));
    this.opts.emit(event);
  }

  private listen<K extends keyof WindowEventMap>(target: Window, type: K, handler: (event: WindowEventMap[K]) => void, capture?: boolean): void;
  private listen<K extends keyof DocumentEventMap>(target: Document, type: K, handler: (event: DocumentEventMap[K]) => void, capture?: boolean): void;
  private listen(target: Window | Document, type: string, handler: (event: Event) => void, capture = false): void {
    target.addEventListener(type, handler, capture);
    this.cleanups.push(() => target.removeEventListener(type, handler, capture));
  }

  /** Ignore focus/visibility changes caused by our own prompts (permission dialogs, screen picker). */
  suspend(ms: number): void {
    this.suspendedUntil = Math.max(this.suspendedUntil, this.env.now() + ms);
  }

  private suspended(): boolean {
    return this.env.now() < this.suspendedUntil;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    const { win, doc } = this.env;
    if (!this.policy.enabled) return;

    this.checkDisplays();

    this.listen(doc, "visibilitychange", () => this.onVisibility());
    this.listen(doc, "fullscreenchange", () => this.onFullscreen());
    if (this.policy.monitor_focus) {
      this.listen(win, "blur", () => this.onBlur());
      this.listen(win, "focus", () => this.onFocus());
    }
    if (this.policy.monitor_clipboard) {
      this.listen(doc, "copy", (e) => this.onClipboard("copy", e), true);
      this.listen(doc, "cut", (e) => this.onClipboard("cut", e), true);
      this.listen(doc, "paste", (e) => this.onPaste(e), true);
      this.listen(doc, "contextmenu", () => this.emit("context_menu"), true);
    }
    if (this.policy.monitor_shortcuts) {
      this.listen(win, "keydown", (e) => this.onKey(e), true);
      this.listen(
        win,
        "keyup",
        (e) => {
          // PrintScreen is often only delivered on key-up.
          if (e.key === "PrintScreen") this.emit("prohibited_shortcut", { shortcut: "print_screen", combo: "PrintScreen" }, { throttleKey: "shortcut:print_screen" });
        },
        true,
      );
    }
    const screen = win.screen as Screen & EventTarget;
    if (typeof screen?.addEventListener === "function") {
      const onChange = () => this.checkDisplays();
      screen.addEventListener("change", onChange);
      this.cleanups.push(() => screen.removeEventListener("change", onChange));
    }
  }

  /**
   * Close intervals that are still open (tab hidden, focus away, out of
   * fullscreen) with their duration so far. Called on stop and on page unload.
   */
  finalize(): void {
    const now = this.env.now();
    if (this.hiddenAt !== null) {
      this.emit("tab_hidden", { open: true }, { occurredAt: this.hiddenAt, durationMs: now - this.hiddenAt });
      this.hiddenAt = null;
    }
    if (this.blurAt !== null && now - this.blurAt >= MIN_FOCUS_LOSS_MS) {
      this.emit("focus_lost", { open: true }, { occurredAt: this.blurAt, durationMs: now - this.blurAt });
    }
    this.blurAt = null;
    if (this.fullscreenExitAt !== null) {
      this.emit("fullscreen_exited", { open: true }, { occurredAt: this.fullscreenExitAt, durationMs: now - this.fullscreenExitAt });
      this.fullscreenExitAt = null;
    }
  }

  stop(): void {
    if (!this.running) return;
    this.finalize();
    this.running = false;
    for (const cleanup of this.cleanups.splice(0)) cleanup();
    if (this.cameraMuteTimer) clearTimeout(this.cameraMuteTimer);
    this.cameraMuteTimer = null;
  }

  /** The interview asked for fullscreen (Begin); exits are now observations. */
  setFullscreenWanted(wanted: boolean): void {
    this.fullscreenWanted = wanted;
    if (!wanted) this.fullscreenExitAt = null;
  }

  recordFullscreenDeclined(reason: string): void {
    if (this.policy.require_fullscreen) this.emit("fullscreen_declined", { reason });
  }

  private checkDisplays(): void {
    const extended = this.env.screenExtended();
    if (extended) this.emit("multiple_displays_detected", { source: "screen.isExtended" });
  }

  private onVisibility(): void {
    const now = this.env.now();
    if (this.env.isHidden()) {
      if (this.suspended()) return;
      this.hiddenAt = now;
      this.blurAt = null; // the blur that accompanies a tab switch belongs to this interval
      return;
    }
    if (this.hiddenAt !== null) this.emit("tab_hidden", {}, { occurredAt: this.hiddenAt, durationMs: now - this.hiddenAt });
    this.hiddenAt = null;
  }

  private onBlur(): void {
    if (this.suspended() || this.env.isHidden()) return;
    this.blurAt = this.env.now();
  }

  private onFocus(): void {
    const at = this.blurAt;
    this.blurAt = null;
    if (at === null || this.hiddenAt !== null) return;
    const duration = this.env.now() - at;
    if (duration < MIN_FOCUS_LOSS_MS) return;
    this.emit("focus_lost", {}, { occurredAt: at, durationMs: duration });
  }

  private onFullscreen(): void {
    const inFullscreen = this.env.isFullscreen();
    this.opts.onFullscreenChange?.(inFullscreen);
    if (!this.fullscreenWanted || !this.policy.require_fullscreen) return;
    const now = this.env.now();
    if (!inFullscreen) {
      if (this.fullscreenExitAt === null) this.fullscreenExitAt = now;
      return;
    }
    if (this.fullscreenExitAt !== null) {
      this.emit("fullscreen_exited", {}, { occurredAt: this.fullscreenExitAt, durationMs: now - this.fullscreenExitAt });
      this.fullscreenExitAt = null;
    }
  }

  private onClipboard(kind: "copy" | "cut", event: ClipboardEvent): void {
    let text = "";
    try {
      text = this.env.win.getSelection?.()?.toString() ?? "";
    } catch {
      text = "";
    }
    const context: ClipboardContext = this.opts.isCodingTarget?.(event.target) ? "coding" : "page";
    this.emit("copy_event", { kind, ...clipboardDetails(text, context) });
  }

  private onPaste(event: ClipboardEvent): void {
    if (this.opts.isCodingTarget?.(event.target)) return; // the coding workspace reports these
    const text = event.clipboardData?.getData("text") ?? "";
    this.emit("paste_event", clipboardDetails(text, "page"));
  }

  /** Reported by the coding workspace (it decides about blocking). Sizes only, never content. */
  recordCodingPaste(length: number, lines: number, options: { threshold: number; blocked: boolean }): void {
    const details = { length, lines, context: "coding" as const, blocked: options.blocked };
    if (length >= options.threshold) {
      this.emit("large_paste_in_coding", { ...details, threshold: options.threshold }, { throttleKey: `large_paste:${this.env.now()}` });
    } else {
      this.emit("paste_event", details);
    }
  }

  private onKey(event: KeyboardEvent): void {
    const match = classifyShortcut(event);
    if (!match) return;
    const inEditor = Boolean(this.opts.isCodingTarget?.(event.target));
    if (match.preventable) event.preventDefault();
    // Ctrl/Cmd+S inside the editor is a saving habit, not an observation worth a reviewer's time.
    if (match.kind === "save" && inEditor) return;
    this.emit("prohibited_shortcut", { shortcut: match.kind, combo: match.combo }, { throttleKey: `shortcut:${match.kind}` });
  }

  /** Camera / microphone track health. */
  watchTrack(track: MediaStreamTrack, kind: "camera" | "microphone"): () => void {
    const type: IntegrityEventType = kind === "camera" ? "camera_disabled" : "microphone_disabled";
    const onEnded = () => this.emit(type, { reason: "ended" }, { throttleKey: `${type}:ended` });
    const onMute = () => {
      if (this.cameraMuteTimer) clearTimeout(this.cameraMuteTimer);
      // Brief mutes happen when devices renegotiate; only sustained ones are observations.
      this.cameraMuteTimer = setTimeout(() => {
        if (track.muted && track.readyState === "live") this.emit(type, { reason: "muted" }, { throttleKey: `${type}:muted` });
      }, 3000);
    };
    const onUnmute = () => {
      if (this.cameraMuteTimer) clearTimeout(this.cameraMuteTimer);
      this.cameraMuteTimer = null;
    };
    track.addEventListener("ended", onEnded);
    track.addEventListener("mute", onMute);
    track.addEventListener("unmute", onUnmute);
    const cleanup = () => {
      track.removeEventListener("ended", onEnded);
      track.removeEventListener("mute", onMute);
      track.removeEventListener("unmute", onUnmute);
    };
    this.cleanups.push(cleanup);
    return cleanup;
  }

  /** From the local luminance check (lens covered or camera pointed at a dark surface). */
  recordCameraBlocked(stats: { mean: number; stddev: number; seconds: number }): void {
    this.emit(
      "camera_blocked",
      { mean_luminance: Math.round(stats.mean), stddev: Math.round(stats.stddev), sustained_seconds: stats.seconds, client_check: "luminance" },
      { throttleKey: "camera_blocked" },
    );
  }

  /** Screen-share lifecycle (from ScreenShareManager). */
  recordScreenShare(type: "screen_share_started" | "screen_share_stopped" | "screen_share_not_full_screen", details: Record<string, unknown> = {}): void {
    this.emit(type, details, { throttleKey: `${type}:${this.env.now()}` });
  }
}
