/**
 * System-check probes. Each returns a CheckResult; none throws. Pure parts
 * (capability evaluation, browser parsing, verdicts) are unit-tested.
 */
import { resolveSocketUrl } from "@/features/realtime/client";
import { parseServerMessage } from "@/lib/realtime/parse";
import { PROTOCOL_VERSION } from "@/lib/realtime/protocol";

export type CheckKey = "browser" | "camera" | "face" | "microphone" | "speakers" | "network" | "avatar" | "screen";
export type CheckState = "pending" | "running" | "pass" | "warn" | "fail";

export interface CheckResult {
  state: CheckState;
  summary: string;
  detail?: string;
}

export interface BrowserCapabilities {
  secureContext: boolean;
  getUserMedia: boolean;
  audioWorklet: boolean;
  webSocket: boolean;
  webgl: boolean;
  webgl2: boolean;
  fullscreen: boolean;
  speechSynthesis: boolean;
  displayMedia: boolean;
}

export function detectCapabilities(): BrowserCapabilities {
  let webgl = false;
  let webgl2 = false;
  try {
    const canvas = document.createElement("canvas");
    webgl2 = Boolean(canvas.getContext("webgl2"));
    webgl = webgl2 || Boolean(canvas.getContext("webgl"));
  } catch {
    webgl = false;
  }
  return {
    secureContext: typeof window !== "undefined" && window.isSecureContext !== false,
    getUserMedia: typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function",
    audioWorklet: typeof AudioWorkletNode !== "undefined",
    webSocket: typeof WebSocket !== "undefined",
    webgl,
    webgl2,
    fullscreen: typeof document !== "undefined" && Boolean(document.fullscreenEnabled),
    speechSynthesis: typeof window !== "undefined" && "speechSynthesis" in window,
    displayMedia: typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getDisplayMedia === "function",
  };
}

export function evaluateBrowser(caps: BrowserCapabilities, options: { fullscreenRequired: boolean }): CheckResult {
  if (!caps.secureContext) {
    return { state: "fail", summary: "This page needs a secure (https) connection", detail: "Open the invitation link exactly as it was sent, starting with https://." };
  }
  const missing: string[] = [];
  if (!caps.getUserMedia) missing.push("camera and microphone access");
  if (!caps.audioWorklet) missing.push("real-time audio");
  if (!caps.webSocket) missing.push("live connections");
  if (missing.length) {
    return { state: "fail", summary: "This browser can't run the interview", detail: `It's missing ${missing.join(", ")}. Use the latest Chrome, Edge, Firefox or Safari.` };
  }
  const notes: string[] = [];
  if (!caps.webgl) notes.push("3D graphics are off, so you'll see a 2D interviewer");
  if (options.fullscreenRequired && !caps.fullscreen) notes.push("full screen isn't available in this browser");
  if (notes.length) return { state: "warn", summary: "Your browser works, with a small limitation", detail: `${notes.join("; ")}.` };
  return { state: "pass", summary: "Your browser supports everything the interview needs" };
}

export function parseBrowser(ua: string): { name: string; version: string } {
  const rules: Array<[string, RegExp]> = [
    ["Edge", /Edg\/([\d.]+)/],
    ["Opera", /OPR\/([\d.]+)/],
    ["Chrome", /Chrome\/([\d.]+)/],
    ["Firefox", /Firefox\/([\d.]+)/],
    ["Safari", /Version\/([\d.]+).*Safari/],
  ];
  for (const [name, pattern] of rules) {
    const match = pattern.exec(ua);
    if (match) return { name, version: match[1]!.split(".")[0]! };
  }
  return { name: "Unknown", version: "" };
}

/** Friendly reason for a getUserMedia failure. */
export function describeMediaError(error: unknown, device: "camera" | "microphone"): string {
  const name = error instanceof DOMException || error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return `Access to your ${device} is blocked. Click the camera icon in the address bar, allow access, then run the check again.`;
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return `We couldn't find a ${device}. Connect one and run the check again.`;
  if (name === "NotReadableError" || name === "AbortError") return `Your ${device} is in use by another app. Close apps like Zoom or Teams, then run the check again.`;
  return `We couldn't start your ${device}. Run the check again, or try another browser.`;
}

/** Verdict from the avatar probe's measured frame rate. */
export function avatarVerdict(mode: "3d" | "2d" | "none", fps: number): { result: CheckResult; chosen: "3d" | "2d" } {
  if (mode === "3d" && fps >= 24) return { result: { state: "pass", summary: `The 3D interviewer runs smoothly (${Math.round(fps)} fps)` }, chosen: "3d" };
  if (mode === "3d") {
    return {
      result: { state: "warn", summary: "We'll use the 2D interviewer", detail: `3D ran at ${Math.round(fps)} fps on this device, so a lighter view keeps things smooth. Audio and subtitles are the same.` },
      chosen: "2d",
    };
  }
  return { result: { state: "warn", summary: "We'll use the 2D interviewer", detail: "3D graphics aren't available here. Audio and subtitles are the same." }, chosen: "2d" };
}

export function networkVerdict(rttMs: number | null, ok: boolean): CheckResult {
  if (!ok || rttMs === null) {
    return { state: "fail", summary: "We couldn't open a live connection", detail: "A firewall, VPN or proxy may be blocking it. Try another network, then run the check again." };
  }
  if (rttMs > 600) return { state: "warn", summary: `Your connection is slow (${Math.round(rttMs)} ms)`, detail: "The interview will work, but responses may feel delayed. A wired or closer Wi-Fi connection helps." };
  return { state: "pass", summary: `Live connection works (${Math.round(rttMs)} ms round trip)` };
}

/**
 * Open the interview WebSocket with a fresh ticket, send hello + a heartbeat
 * and measure the round trip, then close normally (code 1000) before the
 * interview starts.
 */
export async function probeRealtime(
  sessionId: string,
  getTicket: (sessionId: string) => Promise<{ ticket: string; ws_url: string }>,
  timeoutMs = 8000,
): Promise<{ ok: boolean; rttMs: number | null; lastSeq: number; error?: string }> {
  let ticket: { ticket: string; ws_url: string };
  try {
    ticket = await getTicket(sessionId);
  } catch (error) {
    return { ok: false, rttMs: null, lastSeq: 0, error: error instanceof Error ? error.message : "ticket failed" };
  }
  return new Promise((resolve) => {
    let settled = false;
    let socket: WebSocket | null = null;
    let sentAt = 0;
    let lastSeq = 0;
    const finish = (result: { ok: boolean; rttMs: number | null; error?: string }) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        socket?.close(1000, "system check");
      } catch {
        /* ignore */
      }
      resolve({ ...result, lastSeq });
    };
    const timer = setTimeout(() => finish({ ok: false, rttMs: null, error: "timeout" }), timeoutMs);
    try {
      socket = new WebSocket(resolveSocketUrl(ticket.ws_url, ticket.ticket, sessionId));
    } catch (error) {
      finish({ ok: false, rttMs: null, error: error instanceof Error ? error.message : "socket failed" });
      return;
    }
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      socket?.send(
        JSON.stringify({
          type: "hello",
          protocol: PROTOCOL_VERSION,
          last_seq: 0,
          client: { user_agent: navigator.userAgent, webgl: true, avatar_mode: "none", tts_mode: "text", sample_rate: 16000, system_check: true },
        }),
      );
      sentAt = performance.now();
      socket?.send(JSON.stringify({ type: "heartbeat", t: sentAt }));
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const message = parseServerMessage(event.data);
      if (message && message.seq > lastSeq) lastSeq = message.seq;
      if (message?.type === "heartbeat.ack") finish({ ok: true, rttMs: performance.now() - sentAt });
    };
    socket.onerror = () => finish({ ok: false, rttMs: null, error: "socket error" });
    socket.onclose = (event) => finish({ ok: false, rttMs: null, error: `closed ${event.code}` });
  });
}

/** Gentle two-note chime through the given context (speaker test). */
export function playChime(ctx: AudioContext): number {
  const now = ctx.currentTime + 0.05;
  const master = ctx.createGain();
  master.gain.value = 0.18;
  master.connect(ctx.destination);
  [
    [659.25, 0],
    [987.77, 0.18],
  ].forEach(([freq, offset]) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq!;
    gain.gain.setValueAtTime(0, now + offset!);
    gain.gain.linearRampToValueAtTime(1, now + offset! + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + offset! + 0.9);
    osc.connect(gain).connect(master);
    osc.start(now + offset!);
    osc.stop(now + offset! + 1);
  });
  return 1.2;
}
