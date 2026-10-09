import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

import { IntegrityMonitor, type MonitorEnv, type MonitorPolicy } from "../monitor";

const POLICY: MonitorPolicy = {
  enabled: true,
  require_fullscreen: true,
  monitor_focus: true,
  monitor_clipboard: true,
  monitor_shortcuts: true,
  camera_required: true,
};

interface Harness {
  monitor: IntegrityMonitor;
  events: ClientIntegrityEvent[];
  env: MonitorEnv;
  state: { now: number; hidden: boolean; fullscreen: boolean; navType: string | null; extended: boolean | null; storage: Map<string, string> };
  types: () => string[];
}

function setup(policy: Partial<MonitorPolicy> = {}, preset: Partial<Harness["state"]> = {}): Harness {
  const state = { now: 1_700_000_000_000, hidden: false, fullscreen: true, navType: "navigate", extended: false, storage: new Map<string, string>(), ...preset };
  const env: MonitorEnv = {
    win: window,
    doc: document,
    now: () => state.now,
    isFullscreen: () => state.fullscreen,
    isHidden: () => state.hidden,
    navigationType: () => state.navType,
    screenExtended: () => state.extended,
    storage: { getItem: (k) => state.storage.get(k) ?? null, setItem: (k, v) => void state.storage.set(k, v) },
  };
  const events: ClientIntegrityEvent[] = [];
  const monitor = new IntegrityMonitor({
    policy: { ...POLICY, ...policy },
    sessionId: "s1",
    emit: (e) => events.push(e),
    env,
    isCodingTarget: (t) => t instanceof Element && Boolean(t.closest("[data-coding-editor]")),
  });
  return { monitor, events, env, state, types: () => events.map((e) => e.event_type) };
}

let h: Harness | null = null;
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  h?.monitor.stop();
  h = null;
  vi.useRealTimers();
  document.body.innerHTML = "";
});

function advance(ms: number) {
  h!.state.now += ms;
}

describe("IntegrityMonitor", () => {
  it("reports a hidden tab once, on return, with its duration", () => {
    h = setup();
    h.monitor.start();
    h.state.hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("blur"));
    advance(4200);
    h.state.hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    expect(h.types()).toEqual(["tab_hidden"]);
    expect(h.events[0]!.duration_ms).toBe(4200);
    expect(Date.parse(h.events[0]!.occurred_at)).toBe(1_700_000_000_000);
  });

  it("closes a still-open hidden interval with its duration when monitoring stops", () => {
    h = setup();
    h.monitor.start();
    h.state.hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
    advance(15_000);
    vi.advanceTimersByTime(15_000);
    expect(h.events).toHaveLength(0);
    h.monitor.finalize();
    expect(h.events[0]).toMatchObject({ event_type: "tab_hidden", duration_ms: 15_000, details: { open: true } });
    h.state.hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
    expect(h.events).toHaveLength(1);
  });

  it("reports window focus loss but ignores very short blurs", () => {
    h = setup();
    h.monitor.start();
    window.dispatchEvent(new Event("blur"));
    advance(100);
    window.dispatchEvent(new Event("focus"));
    expect(h.events).toHaveLength(0);
    window.dispatchEvent(new Event("blur"));
    advance(2500);
    window.dispatchEvent(new Event("focus"));
    expect(h.events).toMatchObject([{ event_type: "focus_lost", duration_ms: 2500 }]);
  });

  it("leaves rapid-switching derivation to the server", () => {
    h = setup();
    h.monitor.start();
    for (let i = 0; i < 6; i++) {
      window.dispatchEvent(new Event("blur"));
      advance(1000);
      window.dispatchEvent(new Event("focus"));
      advance(2000);
    }
    expect(h.types()).toEqual(Array(6).fill("focus_lost"));
    expect(h.events.every((e) => e.duration_ms === 1000)).toBe(true);
  });

  it("ignores focus changes caused by our own prompts", () => {
    h = setup();
    h.monitor.start();
    h.monitor.suspend(5000);
    window.dispatchEvent(new Event("blur"));
    advance(2000);
    window.dispatchEvent(new Event("focus"));
    expect(h.events).toHaveLength(0);
  });

  it("reports a fullscreen exit once, on return, with its duration", () => {
    h = setup();
    const changes: boolean[] = [];
    h.monitor = new IntegrityMonitor({ policy: POLICY, sessionId: "s1", emit: (e) => h!.events.push(e), env: h.env, onFullscreenChange: (v) => changes.push(v) });
    h.monitor.start();
    h.state.fullscreen = false;
    document.dispatchEvent(new Event("fullscreenchange"));
    h.state.fullscreen = true;
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(h.events).toHaveLength(0); // fullscreen was never requested yet
    h.monitor.setFullscreenWanted(true);
    h.state.fullscreen = false;
    document.dispatchEvent(new Event("fullscreenchange"));
    advance(7000);
    expect(h.events).toHaveLength(0);
    h.state.fullscreen = true;
    document.dispatchEvent(new Event("fullscreenchange"));
    expect(h.events).toMatchObject([{ event_type: "fullscreen_exited", duration_ms: 7000 }]);
    expect(changes).toEqual([false, true, false, true]);
    h.monitor.recordFullscreenDeclined("denied");
    expect(h.types()).toEqual(["fullscreen_exited", "fullscreen_declined"]);
  });

  it("closes an open fullscreen exit when monitoring stops", () => {
    h = setup();
    h.monitor.start();
    h.monitor.setFullscreenWanted(true);
    h.state.fullscreen = false;
    document.dispatchEvent(new Event("fullscreenchange"));
    advance(3000);
    h.monitor.stop();
    expect(h.events).toMatchObject([{ event_type: "fullscreen_exited", duration_ms: 3000, details: { open: true } }]);
  });

  it("records clipboard use and the context menu, throttled", () => {
    h = setup();
    h.monitor.start();
    document.dispatchEvent(new Event("copy"));
    document.dispatchEvent(new Event("copy"));
    const paste = new Event("paste") as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "hello world" } });
    document.dispatchEvent(paste);
    document.dispatchEvent(new Event("contextmenu"));
    expect(h.types()).toEqual(["copy_event", "paste_event", "context_menu"]);
    expect(h.events[1]!.details).toEqual({ length: 11, lines: 1, context: "page" });
    expect(JSON.stringify(h.events)).not.toContain("hello world");
    advance(6000);
    document.dispatchEvent(new Event("copy"));
    expect(h.types().filter((t) => t === "copy_event")).toHaveLength(2);
  });

  it("leaves editor pastes to the coding workspace", () => {
    h = setup();
    h.monitor.start();
    document.body.innerHTML = '<div data-coding-editor><textarea id="ed"></textarea></div>';
    const paste = new Event("paste", { bubbles: true }) as ClipboardEvent;
    Object.defineProperty(paste, "clipboardData", { value: { getData: () => "x".repeat(500) } });
    document.getElementById("ed")!.dispatchEvent(paste);
    expect(h.events).toHaveLength(0);
    h.monitor.recordCodingPaste(500, 12, { threshold: 200, blocked: true });
    h.monitor.recordCodingPaste(20, 1, { threshold: 200, blocked: false });
    expect(h.events).toMatchObject([
      { event_type: "large_paste_in_coding", details: { length: 500, lines: 12, context: "coding", threshold: 200, blocked: true } },
      { event_type: "paste_event", details: { length: 20, lines: 1, context: "coding", blocked: false } },
    ]);
  });

  it("records prohibited shortcuts and prevents print/save", () => {
    h = setup();
    h.monitor.start();
    const print = new KeyboardEvent("keydown", { key: "p", code: "KeyP", ctrlKey: true, cancelable: true });
    window.dispatchEvent(print);
    expect(print.defaultPrevented).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "I", code: "KeyI", ctrlKey: true, shiftKey: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "c", code: "KeyC", ctrlKey: true })); // plain copy: not a shortcut event
    window.dispatchEvent(new KeyboardEvent("keyup", { key: "PrintScreen" }));
    expect(h.events.map((e) => e.details?.shortcut)).toEqual(["print", "devtools", "print_screen"]);
  });

  it("does not report Ctrl+S inside the code editor", () => {
    h = setup();
    h.monitor.start();
    document.body.innerHTML = '<div data-coding-editor><textarea id="ed"></textarea></div>';
    const save = new KeyboardEvent("keydown", { key: "s", code: "KeyS", ctrlKey: true, bubbles: true, cancelable: true });
    document.getElementById("ed")!.dispatchEvent(save);
    expect(save.defaultPrevented).toBe(true);
    expect(h.events).toHaveLength(0);
  });

  it("reports multiple displays but leaves page reloads to the server", () => {
    h = setup({}, { navType: "reload", extended: true });
    h.state.storage.set("interview:active:s1", "1");
    h.monitor.start();
    expect(h.types()).toEqual(["multiple_displays_detected"]);
  });

  it("emits nothing when monitoring is disabled, and honours individual switches", () => {
    h = setup({ enabled: false });
    h.monitor.start();
    window.dispatchEvent(new Event("blur"));
    advance(3000);
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("copy"));
    expect(h.events).toHaveLength(0);
    h.monitor.stop();

    h = setup({ monitor_clipboard: false, monitor_focus: false });
    h.monitor.start();
    document.dispatchEvent(new Event("copy"));
    window.dispatchEvent(new Event("blur"));
    advance(3000);
    window.dispatchEvent(new Event("focus"));
    expect(h.events).toHaveLength(0);
  });

  it("watches camera and microphone tracks", () => {
    h = setup();
    h.monitor.start();
    const track = new EventTarget() as MediaStreamTrack;
    Object.defineProperty(track, "muted", { value: true });
    Object.defineProperty(track, "readyState", { value: "live" });
    h.monitor.watchTrack(track, "camera");
    track.dispatchEvent(new Event("mute"));
    vi.advanceTimersByTime(3000);
    track.dispatchEvent(new Event("ended"));
    expect(h.events.map((e) => [e.event_type, e.details?.reason])).toEqual([
      ["camera_disabled", "muted"],
      ["camera_disabled", "ended"],
    ]);
  });

  it("stops listening when stopped", () => {
    h = setup();
    h.monitor.start();
    h.monitor.stop();
    document.dispatchEvent(new Event("copy"));
    expect(h.events).toHaveLength(0);
  });
});
