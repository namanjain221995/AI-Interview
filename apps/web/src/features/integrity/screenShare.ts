/**
 * Screen sharing when the policy requires it. The browser lets the candidate
 * choose what to share; we ask for the entire screen and report (not block)
 * when a window or tab was chosen instead. Stopping the share is observed and
 * handed to the room so it can show a banner and apply the recruiter-configured
 * behaviour.
 */

export type ScreenShareEvent = "screen_share_started" | "screen_share_stopped" | "screen_share_not_full_screen";

export interface ScreenShareOptions {
  onEvent: (type: ScreenShareEvent, details: Record<string, unknown>) => void;
  onStopped?: () => void;
}

export class ScreenShareManager {
  private stream: MediaStream | null = null;
  private stopping = false;

  constructor(private readonly opts: ScreenShareOptions) {}

  static isSupported(): boolean {
    return typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getDisplayMedia === "function";
  }

  get active(): boolean {
    return Boolean(this.stream?.getVideoTracks().some((t) => t.readyState === "live"));
  }

  get mediaStream(): MediaStream | null {
    return this.stream;
  }

  get displaySurface(): string | null {
    const track = this.stream?.getVideoTracks()[0];
    const settings = track?.getSettings() as (MediaTrackSettings & { displaySurface?: string }) | undefined;
    return settings?.displaySurface ?? null;
  }

  /** Must run inside a user gesture. Resolves with the stream or rejects (declined / unsupported). */
  async start(): Promise<MediaStream> {
    if (!ScreenShareManager.isSupported()) throw new Error("Screen sharing isn't supported in this browser.");
    if (this.active && this.stream) return this.stream;
    const constraints = {
      video: { displaySurface: "monitor", frameRate: { ideal: 5, max: 10 } },
      audio: false,
      selfBrowserSurface: "exclude",
      surfaceSwitching: "exclude",
      monitorTypeSurfaces: "include",
    } as DisplayMediaStreamOptions;
    const stream = await navigator.mediaDevices.getDisplayMedia(constraints);
    this.stream = stream;
    this.stopping = false;
    const track = stream.getVideoTracks()[0];
    const surface = this.displaySurface;
    this.opts.onEvent("screen_share_started", { display_surface: surface });
    if (surface && surface !== "monitor") this.opts.onEvent("screen_share_not_full_screen", { display_surface: surface });
    track?.addEventListener("ended", () => {
      if (this.stopping) return;
      this.stream = null;
      this.opts.onEvent("screen_share_stopped", { display_surface: surface });
      this.opts.onStopped?.();
    });
    return stream;
  }

  /** Stop sharing on our side (end of interview); not reported as a candidate action. */
  stop(): void {
    this.stopping = true;
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    this.stream = null;
  }
}
