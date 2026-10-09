/**
 * Media bound to short-lived signed URLs: answer audio (signed on the first
 * press of play), integrity evidence frames (signed when scrolled into view)
 * and session recordings (signed per part, re-signed before expiry without
 * losing the playback position). Files are only ever read through signed
 * URLs the API issued after authorizing (and auditing) the request.
 */

import { AudioLines, ImageOff, Pause, Play, RotateCcw, ScanEye } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type RefCallback } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Skeleton } from "@/components/ui/Skeleton";
import { Tooltip } from "@/components/ui/Tooltip";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useMediaUrl } from "../api/hooks";
import { useRecordingSource, type RecordingPart } from "../api/recordings";
import type { IntegrityEventView } from "../api/types";
import { eventTitle, inlineEvidenceUrl } from "../lib/integrity";

export function AnswerAudio({ mediaId, label = "Play answer", className }: { mediaId: string; label?: string; className?: string }) {
  const [requested, setRequested] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const signed = useMediaUrl(mediaId, { enabled: requested });
  const url = signed.data?.url ?? null;

  useEffect(() => {
    const audio = audioRef.current;
    if (!requested || !url || !audio) return;
    if (audio.getAttribute("src") !== url) audio.src = url;
    void audio.play().catch(() => setPlaying(false));
  }, [requested, url]);

  if (signed.isError) {
    return (
      <Tooltip content="The recording isn't available (it may have passed its retention period).">
        <span tabIndex={0} className={cn("inline-flex items-center gap-1.5 rounded-xs text-xs text-fg-subtle", className)}>
          <Play className="size-3.5" aria-hidden="true" />
          Audio unavailable
        </span>
      </Tooltip>
    );
  }

  const toggle = () => {
    const audio = audioRef.current;
    if (!requested) {
      setRequested(true);
      return;
    }
    if (!audio || !url) return;
    if (audio.paused) void audio.play().catch(() => setPlaying(false));
    else audio.pause();
  };

  const progress = duration ? Math.min(1, position / duration) : 0;
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Button
        size="xs"
        variant="outline"
        onClick={toggle}
        loading={requested && signed.isFetching && !url}
        leftIcon={playing ? <Pause /> : <Play />}
        aria-label={playing ? "Pause answer audio" : label}
      >
        {playing ? "Pause" : label}
      </Button>
      {requested && url ? (
        <span className="flex items-center gap-2 text-xs text-fg-muted tabular-nums">
          <span className="relative h-1 w-20 overflow-hidden rounded-full bg-surface-sunken" aria-hidden="true">
            <span className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${progress * 100}%` }} />
          </span>
          {formatClock(position)}
          {duration ? ` / ${formatClock(duration)}` : ""}
        </span>
      ) : null}
      <audio
        ref={audioRef}
        preload="none"
        className="hidden"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(Number.isFinite(e.currentTarget.duration) ? e.currentTarget.duration : null)}
      />
    </span>
  );
}

function useInView<T extends HTMLElement>(): [RefCallback<T>, boolean] {
  const [inView, setInView] = useState(false);
  const observer = useRef<IntersectionObserver | null>(null);
  const ref = (element: T | null) => {
    observer.current?.disconnect();
    if (!element || inView) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    observer.current = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setInView(true);
          observer.current?.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.current.observe(element);
  };
  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, inView];
}

/** Evidence frame thumbnail; opens a larger view. */
export function EvidenceFrame({ event, className }: { event: IntegrityEventView; className?: string }) {
  const inline = inlineEvidenceUrl(event);
  const [ref, inView] = useInView<HTMLButtonElement>();
  const [open, setOpen] = useState(false);
  const signed = useMediaUrl(inline ? null : event.evidence_media_id, { enabled: !inline && inView });
  const src = inline ?? signed.data?.url ?? null;
  const when = typeof event.elapsed_seconds === "number" ? formatClock(event.elapsed_seconds) : null;
  const alt = `Evidence frame${when ? ` captured at ${when}` : ""} for: ${eventTitle(event)}`;

  if (!event.evidence_media_id && !inline) return null;
  if (signed.isError) {
    return (
      <span className={cn("flex h-16 w-24 shrink-0 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-line-strong text-[10px] text-fg-subtle", className)}>
        <ImageOff className="size-4" aria-hidden="true" />
        Frame expired
      </span>
    );
  }
  return (
    <>
      <button
        ref={ref}
        type="button"
        onClick={() => src && setOpen(true)}
        className={cn(
          "group relative h-16 w-24 shrink-0 overflow-hidden rounded-md border border-line bg-surface-sunken focus-visible:outline-2 focus-visible:outline-offset-2",
          className,
        )}
        aria-label={`Open ${alt.toLowerCase()}`}
      >
        {src ? <img src={src} alt={alt} loading="lazy" className="size-full object-cover" /> : <Skeleton className="size-full rounded-none" />}
        <span className="absolute inset-x-0 bottom-0 flex items-center gap-1 bg-gradient-to-t from-black/55 to-transparent px-1.5 pt-3 pb-1 text-[10px] font-medium text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <ScanEye className="size-3" aria-hidden="true" />
          View
        </span>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="xl">
          <DialogHeader>
            <DialogTitle>{eventTitle(event)}</DialogTitle>
            <DialogDescription>
              {when ? `Captured at ${when} into the interview. ` : ""}An observation for review, not a determination.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>{src ? <img src={src} alt={alt} className="mx-auto max-h-[70vh] w-auto rounded-md border border-line" /> : null}</DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}


// ------------------------------------------------------------------ recordings

export interface RecordingVideoHandle {
  /** Jump to a media position (ms) and optionally start playing. */
  seek(mediaMs: number, play?: boolean): void;
  play(): void;
  pause(): void;
  readonly element: HTMLVideoElement | null;
}

export interface RecordingVideoProps {
  sessionId: string;
  part: Pick<RecordingPart, "partId" | "audioOnly">;
  /** Accessible name of the player. */
  label: string;
  rate?: number;
  muted?: boolean;
  /** Native controls (off for a second stream that follows the main player). */
  controls?: boolean;
  onTime?: (mediaMs: number) => void;
  onPlayingChange?: (playing: boolean) => void;
  className?: string;
  videoTestId?: string;
}

const MAX_SOURCE_RETRIES = 3;

/**
 * One recording part in a native player. The signed URL is short-lived: a
 * fresh one is swapped in before it expires (or after a failed request) at the
 * same position and play state. Downloading from the player is switched off
 * (the audited download is an admin action); playback itself is audited when
 * the URL is issued.
 */
export const RecordingVideo = forwardRef<RecordingVideoHandle, RecordingVideoProps>(function RecordingVideo(
  { sessionId, part, label, rate = 1, muted = false, controls = true, onTime, onPlayingChange, className, videoTestId = "recording-video" },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const source = useRecordingSource(sessionId, part.partId);
  const resumeAt = useRef<{ time: number; playing: boolean } | null>(null);
  const pendingSeek = useRef<{ ms: number; play: boolean } | null>(null);
  /** A fresh URL kept until the next pause or failed request (no hiccup mid-playback). */
  const pendingUrl = useRef<string | null>(null);
  const playingRef = useRef(false);
  const failures = useRef(0);
  const [failed, setFailed] = useState(false);
  const url = source.data?.url ?? null;

  const swapTo = (next: string) => {
    const video = videoRef.current;
    if (!video) return;
    if (video.getAttribute("src")) resumeAt.current ??= { time: video.currentTime, playing: playingRef.current };
    pendingUrl.current = null;
    video.src = next;
  };

  useEffect(() => {
    failures.current = 0;
    setFailed(false);
    resumeAt.current = null;
  }, [part.partId]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !url || video.getAttribute("src") === url) return;
    if (video.getAttribute("src") && playingRef.current && !video.error) {
      pendingUrl.current = url;
      return;
    }
    swapTo(url); // touches only refs and the element, so it is not a dependency
  }, [url]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }, [rate]);

  useImperativeHandle(
    ref,
    () => ({
      seek(mediaMs: number, play = false) {
        const video = videoRef.current;
        if (!video) return;
        if (video.readyState >= 1) {
          video.currentTime = Math.max(0, mediaMs / 1000);
          if (play) void video.play().catch(() => undefined);
        } else {
          pendingSeek.current = { ms: mediaMs, play };
        }
      },
      play() {
        void videoRef.current?.play().catch(() => undefined);
      },
      pause() {
        videoRef.current?.pause();
      },
      get element() {
        return videoRef.current;
      },
    }),
    [],
  );

  const onLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = rate;
    failures.current = 0;
    const resume = resumeAt.current;
    resumeAt.current = null;
    const seek = pendingSeek.current;
    pendingSeek.current = null;
    if (seek) {
      video.currentTime = Math.max(0, seek.ms / 1000);
      if (seek.play) void video.play().catch(() => undefined);
    } else if (resume) {
      video.currentTime = resume.time;
      if (resume.playing) void video.play().catch(() => undefined);
    }
  };

  const onError = () => {
    const video = videoRef.current;
    if (failures.current >= MAX_SOURCE_RETRIES) {
      setFailed(true);
      return;
    }
    failures.current += 1;
    // Most often an expired link (seeking re-requests the file): re-sign and carry on.
    if (video && video.getAttribute("src")) resumeAt.current = { time: video.currentTime, playing: playingRef.current };
    const next = pendingUrl.current;
    if (next) swapTo(next);
    else void source.refetch();
  };

  const retry = () => {
    failures.current = 0;
    setFailed(false);
    void source.refetch();
  };

  const unavailable = failed || (source.isError && !url);
  return (
    <div className={cn("relative overflow-hidden rounded-md bg-ink-950", part.audioOnly ? "h-28 sm:h-32" : "aspect-video", className)}>
      <video
        ref={videoRef}
        data-testid={videoTestId}
        aria-label={label}
        className="size-full object-contain"
        controls={controls}
        controlsList="nodownload noremoteplayback"
        disableRemotePlayback
        playsInline
        preload="metadata"
        muted={muted}
        onContextMenu={(event) => event.preventDefault()}
        onLoadedMetadata={onLoadedMetadata}
        onError={onError}
        onTimeUpdate={(event) => onTime?.(Math.round(event.currentTarget.currentTime * 1000))}
        onSeeked={(event) => onTime?.(Math.round(event.currentTarget.currentTime * 1000))}
        onPlay={() => {
          playingRef.current = true;
          onPlayingChange?.(true);
        }}
        onPause={() => {
          playingRef.current = false;
          onPlayingChange?.(false);
          if (pendingUrl.current) swapTo(pendingUrl.current);
        }}
        onEnded={() => {
          playingRef.current = false;
          onPlayingChange?.(false);
        }}
      />
      {part.audioOnly ? (
        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 px-3 pt-3 text-xs font-medium text-ink-200">
          <AudioLines className="size-4" aria-hidden="true" />
          Audio only: there was no camera picture for this part
        </div>
      ) : null}
      {!url && !unavailable ? <Skeleton className="absolute inset-0 rounded-none opacity-40" /> : null}
      {unavailable ? (
        <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-ink-950/90 p-4 text-center text-sm text-ink-100">
          <ImageOff className="size-5" aria-hidden="true" />
          <p>This part can't be played right now. It may have passed its retention period.</p>
          <Button size="xs" variant="outline" leftIcon={<RotateCcw />} onClick={retry}>
            Try again
          </Button>
        </div>
      ) : null}
    </div>
  );
});

