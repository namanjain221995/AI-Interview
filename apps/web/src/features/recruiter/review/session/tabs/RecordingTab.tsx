/**
 * Recording tab: watch the interview (camera picture with both voices, or
 * audio only), part by part, with the transcript in sync (click a line to jump
 * there; the line being heard is highlighted), neutral integrity markers on
 * the timeline, the shared screen as a second stream when it was recorded,
 * playback speed, and an audited download for admins.
 */

import { Download, Film, MonitorUp, RefreshCw, TriangleAlert, Video, VideoOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { toast } from "sonner";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Tooltip } from "@/components/ui/Tooltip";
import { errorMessage } from "@/lib/api";
import { usePermission } from "@/lib/auth";
import { formatBytes, formatClock, formatDuration, formatTime, pluralize } from "@/lib/format";
import { usePersistentState, usePrefersReducedMotion } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { useSessionTranscript } from "../../api/hooks";
import { requestRecordingDownload, useFinalizeRecordings, useSessionRecordings, type RecordingPart, type SessionRecordings } from "../../api/recordings";
import { RecordingVideo, type RecordingVideoHandle } from "../../components/media";
import { PanelSkeleton, QueryError, SegmentedControl } from "../../components/primitives";
import { eventTitle } from "../../lib/integrity";
import {
  activeCueIndex,
  integrityMarkers,
  partDurationMs,
  PLAYBACK_RATES,
  syncedMediaMs,
  transcriptCues,
  type RecordingCue,
  type TimelineMarker,
} from "../../lib/recording";
import { buildTranscriptTurns, SPEAKER_LABELS } from "../../lib/transcript";
import { useSessionContext } from "../context";

const STATUS: Record<RecordingPart["status"], { label: string; tone: BadgeTone }> = {
  ready: { label: "Ready", tone: "success" },
  recording: { label: "Recording", tone: "signal" },
  processing: { label: "Processing", tone: "neutral" },
  failed: { label: "Couldn't process", tone: "warning" },
};

function partRange(part: RecordingPart): string {
  if (!part.startedAt) return part.durationMs ? formatDuration(part.durationMs / 1000) : "";
  const start = formatTime(part.startedAt);
  return part.endedAt ? `${start} – ${formatTime(part.endedAt)}` : start;
}

function partTitle(part: RecordingPart): string {
  return `${part.kind === "screen" ? "Screen" : "Part"} ${part.ordinal}`;
}

// --------------------------------------------------------------- empty

function NoRecording({ data, inProgress }: { data: SessionRecordings; inProgress: boolean }) {
  let title = "No recording was received";
  let description = "The candidate's browser didn't send a recording for this session (for example, it could not record).";
  if (!data.policy.recordVideo) {
    title = "Recording was off for this interview";
    description = "Interviews are recorded only when the job or organization policy turns recording on and the candidate agrees.";
  } else if (!data.policy.consented) {
    title = inProgress ? "Waiting for the candidate's consent" : "The candidate did not agree to be recorded";
    description = "Recordings are made only with the candidate's consent, given before the interview starts.";
  } else if (inProgress) {
    title = "The recording starts with the interview";
    description = "Parts appear here as the candidate's browser uploads them, a few seconds behind live.";
  }
  return (
    <Card data-testid="recording-empty">
      <EmptyState icon={<VideoOff />} title={title} description={description} />
    </Card>
  );
}

// ------------------------------------------------------------ timeline

function RecordingTimeline({
  durationMs,
  mediaMs,
  markers,
  onSeek,
}: {
  durationMs: number;
  mediaMs: number;
  markers: TimelineMarker[];
  onSeek: (mediaMs: number) => void;
}) {
  const pct = durationMs > 0 ? Math.min(100, Math.max(0, (mediaMs / durationMs) * 100)) : 0;
  const seekFromPointer = (event: MouseEvent<HTMLDivElement>) => {
    if (durationMs <= 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
    onSeek(Math.round(Math.min(1, Math.max(0, ratio)) * durationMs));
  };
  return (
    <div className="grid min-w-0 grid-cols-1 gap-1.5">
      {/* Pointer shortcut only: keyboard users seek with the player's own controls or the marker buttons. */}
      <div
        className="relative h-7 cursor-pointer overflow-hidden rounded-md bg-surface-sunken ring-1 ring-line-subtle ring-inset"
        onClick={seekFromPointer}
        data-testid="recording-timeline"
        role="presentation"
      >
        <div className="absolute inset-y-0 left-0 bg-accent-soft" style={{ width: `${pct}%` }} aria-hidden="true" />
        {markers.map((marker) => (
          <Tooltip key={marker.id} content={`${marker.label} at ${formatClock(marker.mediaMs / 1000)}`}>
            <button
              type="button"
              data-testid="recording-marker"
              className="absolute top-1.5 bottom-1.5 w-1.5 -translate-x-1/2 rounded-full bg-fg-subtle transition-colors hover:bg-fg-muted focus-visible:outline-2 focus-visible:outline-offset-1"
              style={{ left: `${marker.leftPct}%` }}
              aria-label={`Observation at ${formatClock(marker.mediaMs / 1000)}: ${marker.label}. Jump to this moment.`}
              onClick={(event) => {
                event.stopPropagation();
                onSeek(Math.max(0, marker.mediaMs - 3_000));
              }}
            />
          </Tooltip>
        ))}
        <div className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-accent" style={{ left: `${pct}%` }} aria-hidden="true" />
      </div>
      <div className="flex items-center justify-between gap-3 text-xs text-fg-subtle tabular-nums">
        <span>{formatClock(mediaMs / 1000)}</span>
        <span className="truncate text-center">
          {markers.length
            ? `${pluralize(markers.length, "observation")} marked. Observations, not determinations.`
            : "No integrity observations in this part"}
        </span>
        <span>{formatClock(durationMs / 1000)}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------- transcript

function SyncedTranscript({
  cues,
  activeIndex,
  playing,
  selectedPartId,
  partNames,
  loading,
  onPick,
}: {
  cues: RecordingCue[];
  activeIndex: number;
  playing: boolean;
  selectedPartId: string | null;
  /** "Part 2" for lines recorded in another part than the one playing. */
  partNames: ReadonlyMap<string, string>;
  loading: boolean;
  onPick: (cue: RecordingCue) => void;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const reducedMotion = usePrefersReducedMotion();

  // Follow the line being heard, inside the panel only (never scroll the page).
  useEffect(() => {
    const list = listRef.current;
    if (!list || activeIndex < 0 || !playing) return;
    const item = list.querySelector<HTMLElement>(`[data-cue-index="${activeIndex}"]`);
    if (!item) return;
    const top = item.offsetTop;
    const bottom = top + item.offsetHeight;
    if (top < list.scrollTop || bottom > list.scrollTop + list.clientHeight) {
      list.scrollTo({ top: Math.max(0, top - list.clientHeight / 3), behavior: reducedMotion ? "auto" : "smooth" });
    }
  }, [activeIndex, playing, reducedMotion]);

  return (
    <Card className="min-w-0" data-testid="recording-transcript">
      <CardHeader title="Transcript" description="Times are positions in the recording. Select a line to play that moment." />
      <CardContent className="pt-3">
        {loading ? (
          <PanelSkeleton rows={6} />
        ) : cues.length === 0 ? (
          <p className="text-sm text-fg-muted">No transcript for this session yet.</p>
        ) : (
          <ol ref={listRef} className="relative grid max-h-[24rem] gap-0.5 overflow-y-auto pr-1 scrollbar-thin xl:max-h-[min(36rem,calc(100dvh-16rem))]">
            {cues.map((cue, index) => {
              const active = index === activeIndex;
              const recorded = cue.partId !== null;
              const elsewhere = recorded && cue.partId !== selectedPartId;
              return (
                <li key={cue.id} data-cue-index={index}>
                  <button
                    type="button"
                    data-testid="recording-line"
                    data-active={active ? "true" : undefined}
                    aria-current={active ? "true" : undefined}
                    disabled={!recorded}
                    onClick={() => onPick(cue)}
                    className={cn(
                      "grid w-full grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2 rounded-md px-2 py-1.5 text-left text-[13px] leading-5 transition-colors",
                      "hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-[-2px] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent",
                      active && "bg-accent-soft ring-1 ring-accent-line ring-inset hover:bg-accent-soft",
                    )}
                    title={recorded ? undefined : "This moment isn't in the recording (for example during a page reload)."}
                  >
                    {/* Same clock as the player (position in its part), so the two always agree. */}
                    <span className="pt-px text-xs text-fg-subtle tabular-nums">
                      {!recorded ? "—" : elsewhere ? (partNames.get(cue.partId!) ?? "Other part") : formatClock((cue.mediaMs ?? 0) / 1000)}
                    </span>
                    <span className="min-w-0">
                      <span className={cn("mr-1.5 text-xs font-semibold", cue.speaker === "candidate" ? "text-fg" : "text-accent-fg")}>
                        {SPEAKER_LABELS[cue.speaker]}
                      </span>
                      <span className={cn("text-fg-muted", active && "text-fg")}>{cue.text}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------- parts

function PartList({ parts, selectedId, onSelect, testIdPrefix }: { parts: RecordingPart[]; selectedId: string | null; onSelect: (part: RecordingPart) => void; testIdPrefix: string }) {
  return (
    <ol className="grid gap-1.5 sm:grid-cols-2" aria-label="Recording parts">
      {parts.map((part) => {
        const selected = part.partId === selectedId;
        const status = STATUS[part.status];
        return (
          <li key={part.partId}>
            <button
              type="button"
              data-testid={`${testIdPrefix}-${part.ordinal}`}
              aria-pressed={selected}
              disabled={part.status !== "ready"}
              onClick={() => onSelect(part)}
              className={cn(
                "flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-left text-[13px] transition-colors",
                "focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed",
                selected ? "border-accent-line bg-accent-soft" : "border-line bg-surface hover:bg-surface-hover",
              )}
            >
              <span className="font-medium text-fg">{partTitle(part)}</span>
              <span className="text-fg-muted tabular-nums">{partRange(part)}</span>
              {part.durationMs ? <span className="text-fg-subtle tabular-nums">{formatDuration(part.durationMs / 1000)}</span> : null}
              <span className="ml-auto flex items-center gap-1">
                {part.audioOnly ? (
                  <Badge size="sm" tone="outline">
                    Audio only
                  </Badge>
                ) : null}
                {part.status !== "ready" ? (
                  <Badge size="sm" tone={status.tone} dot={part.status === "recording"} pulse={part.status === "recording"}>
                    {status.label}
                  </Badge>
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

// ------------------------------------------------------------- the tab

export function RecordingTab() {
  const { sessionId, inProgress } = useSessionContext();
  const recordings = useSessionRecordings(sessionId, { live: inProgress });

  if (recordings.isPending) {
    return (
      <Card data-testid="recording-tab">
        <PanelSkeleton rows={5} />
      </Card>
    );
  }
  if (recordings.isError && !recordings.data) {
    return (
      <Card className="p-5" data-testid="recording-tab">
        <QueryError error={recordings.error} title="Couldn't load the recording" onRetry={() => void recordings.refetch()} />
      </Card>
    );
  }
  const data = recordings.data;
  return (
    <div className="grid grid-cols-1 gap-6" data-testid="recording-tab">
      {data.parts.length === 0 ? <NoRecording data={data} inProgress={inProgress} /> : <RecordingWorkspace data={data} />}
    </div>
  );
}

function RecordingWorkspace({ data }: { data: SessionRecordings }) {
  const { sessionId, inProgress, integrity } = useSessionContext();
  const transcript = useSessionTranscript(sessionId, { live: inProgress });
  const finalize = useFinalizeRecordings(sessionId);
  const canProcess = usePermission("reviews:write");
  const [rate, setRate] = usePersistentState<number>("review.recording.rate", 1);
  const [showScreen, setShowScreen] = usePersistentState<boolean>("review.recording.screen", true);
  const [downloading, setDownloading] = useState(false);

  const cameraParts = useMemo(() => data.parts.filter((p) => p.kind === "camera"), [data.parts]);
  const screenParts = useMemo(() => data.parts.filter((p) => p.kind === "screen"), [data.parts]);
  const readyCamera = cameraParts.filter((p) => p.status === "ready");
  const [selectedId, setSelectedId] = useState<string | null>(() => readyCamera[0]?.partId ?? null);
  const selected = cameraParts.find((p) => p.partId === selectedId && p.status === "ready") ?? readyCamera[0] ?? null;
  const [mediaMs, setMediaMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const player = useRef<RecordingVideoHandle>(null);
  const screenPlayer = useRef<RecordingVideoHandle>(null);
  const pendingSeek = useRef<{ partId: string; ms: number } | null>(null);

  const turns = useMemo(() => buildTranscriptTurns(transcript.data ?? []), [transcript.data]);
  const cues = useMemo(() => transcriptCues(turns, data.parts, data.sessionStartedAt), [turns, data.parts, data.sessionStartedAt]);
  const activeIndex = selected ? activeCueIndex(cues, selected.partId, mediaMs) : -1;
  const markers = useMemo(() => (selected ? integrityMarkers(selected, integrity?.events ?? [], eventTitle) : []), [selected, integrity?.events]);
  const durationMs = selected ? partDurationMs(selected) : 0;
  const partNames = useMemo(() => new Map(cameraParts.map((p) => [p.partId, partTitle(p)])), [cameraParts]);

  // The screen part that overlaps the selected camera part (matched on wall time).
  const screenPart = useMemo(() => {
    if (!selected) return null;
    const ready = screenParts.filter((p) => p.status === "ready");
    return ready.find((p) => syncedMediaMs(selected, Math.min(mediaMs, partDurationMs(selected)), p) !== null) ?? ready.find((p) => syncedMediaMs(selected, 0, p) !== null) ?? null;
  }, [selected, screenParts, mediaMs]);
  const screenAt = selected && screenPart ? syncedMediaMs(selected, mediaMs, screenPart) : null;

  // Keep the second stream in step with the main player.
  useEffect(() => {
    const screen = screenPlayer.current?.element;
    if (!screen || screenAt === null) {
      screen?.pause();
      return;
    }
    if (Math.abs(screen.currentTime * 1000 - screenAt) > 700) screenPlayer.current?.seek(screenAt);
    if (playing && screen.paused) screenPlayer.current?.play();
    if (!playing && !screen.paused) screen.pause();
  }, [screenAt, playing]);

  const selectPart = useCallback((part: RecordingPart, seekMs = 0) => {
    pendingSeek.current = { partId: part.partId, ms: seekMs };
    setSelectedId(part.partId);
    setMediaMs(seekMs);
  }, []);

  // A newly selected part seeks once its player exists.
  useEffect(() => {
    const pending = pendingSeek.current;
    if (!pending || !selected || pending.partId !== selected.partId) return;
    pendingSeek.current = null;
    player.current?.seek(pending.ms, pending.ms > 0);
  }, [selected]);

  const seek = (ms: number, play = false) => {
    setMediaMs(ms);
    player.current?.seek(ms, play);
  };

  const pickCue = (cue: RecordingCue) => {
    if (cue.partId === null || cue.mediaMs === null) return;
    const target = Math.max(0, cue.mediaMs - 400);
    const part = cameraParts.find((p) => p.partId === cue.partId);
    if (!part) return;
    if (part.partId !== selected?.partId) selectPart(part, target);
    else seek(target, true);
  };

  const download = async () => {
    if (!selected) return;
    setDownloading(true);
    try {
      const { url, filename } = await requestRecordingDownload(sessionId, selected.partId);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "";
      link.rel = "noopener";
      document.body.append(link);
      link.click();
      link.remove();
    } catch (error) {
      toast.error("Couldn't download the recording", { description: errorMessage(error) });
    } finally {
      setDownloading(false);
    }
  };

  const unfinished = data.parts.filter((p) => p.status !== "ready");
  const failed = unfinished.filter((p) => p.status === "failed");
  const processNow = () =>
    finalize.mutate(undefined, {
      onSuccess: ({ queued }) =>
        toast.success(queued.length ? `Processing ${pluralize(queued.length, "part")} of the recording` : "Nothing left to process"),
      onError: (error) => toast.error("Couldn't start processing", { description: errorMessage(error) }),
    });

  return (
    <>
      <Callout tone="neutral" icon={<Film />} data-testid="recording-notice" title="Recorded with the candidate's consent">
        Camera picture with both voices, the interviewer's and the candidate's.
        {data.policy.videoDays ? ` Kept for ${pluralize(data.policy.videoDays, "day")}, then deleted automatically.` : ""} Every playback is logged
        {data.canDownload ? "; downloads too." : "."}
      </Callout>

      {unfinished.length > 0 ? (
        <Callout
          tone={failed.length ? "warning" : "info"}
          icon={failed.length ? <TriangleAlert /> : <RefreshCw />}
          data-testid="recording-processing"
          title={
            failed.length
              ? `${pluralize(failed.length, "part")} couldn't be processed`
              : unfinished.some((p) => p.status === "recording")
                ? "Recording in progress"
                : "Preparing the recording for playback"
          }
          actions={
            canProcess && !unfinished.every((p) => p.status === "recording") ? (
              <Button size="xs" variant="outline" leftIcon={<RefreshCw />} loading={finalize.isPending} onClick={processNow} data-testid="recording-finalize">
                {failed.length ? "Try again" : "Process now"}
              </Button>
            ) : undefined
          }
        >
          {failed.length
            ? "The uploaded parts are kept. Trying again often helps; parts whose beginning never arrived can't be played."
            : "Parts become playable shortly after they finish, usually within a minute."}
        </Callout>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(18rem,26rem)]">
        <Card className="min-w-0" data-testid="recording-player">
          <CardHeader
            title={selected ? partTitle(selected) : "Recording"}
            description={
              selected
                ? [partRange(selected), selected.durationMs ? formatDuration(selected.durationMs / 1000) : null, selected.sizeBytes ? formatBytes(selected.sizeBytes) : null]
                    .filter(Boolean)
                    .join(" · ")
                : "No part is ready to play yet."
            }
            action={
              data.canDownload && selected ? (
                <Button size="xs" variant="outline" leftIcon={<Download />} loading={downloading} onClick={() => void download()} data-testid="recording-download">
                  Download
                </Button>
              ) : undefined
            }
          />
          <CardContent className="grid min-w-0 grid-cols-1 gap-4">
            {selected ? (
              <>
                <div className={cn("grid grid-cols-1 gap-3", screenPart && showScreen && "lg:grid-cols-2")}>
                  <RecordingVideo
                    ref={player}
                    key={selected.partId}
                    sessionId={sessionId}
                    part={selected}
                    label={`Interview recording, ${partTitle(selected).toLowerCase()}`}
                    rate={rate}
                    onTime={setMediaMs}
                    onPlayingChange={setPlaying}
                  />
                  {screenPart && showScreen ? (
                    <div className="relative">
                      <RecordingVideo
                        ref={screenPlayer}
                        key={screenPart.partId}
                        sessionId={sessionId}
                        part={screenPart}
                        label="Shared screen recording, follows the main player"
                        rate={rate}
                        muted
                        controls={false}
                        videoTestId="recording-screen-video"
                      />
                      {screenAt === null ? (
                        <div className="absolute inset-0 flex items-center justify-center rounded-md bg-ink-950/80 p-4 text-center text-xs text-ink-100">
                          The screen wasn't shared at this moment.
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                {!selected.seekable ? (
                  <p className="flex items-center gap-1.5 text-xs text-fg-muted">
                    <TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden="true" />
                    This part couldn't be indexed, so jumping around may be slow or limited.
                  </p>
                ) : null}
                <RecordingTimeline durationMs={durationMs} mediaMs={mediaMs} markers={markers} onSeek={(ms) => seek(ms)} />
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SegmentedControl
                    label="Playback speed"
                    value={String(rate)}
                    onChange={(value) => setRate(Number(value))}
                    options={PLAYBACK_RATES.map((value) => ({ value: String(value), label: `${value}×` }))}
                    className="[&_[role=radio]]:tabular-nums"
                  />
                  {screenParts.some((p) => p.status === "ready") ? (
                    <Button
                      size="xs"
                      variant={showScreen ? "secondary" : "outline"}
                      leftIcon={<MonitorUp />}
                      aria-pressed={showScreen}
                      onClick={() => setShowScreen(!showScreen)}
                      data-testid="recording-screen-toggle"
                    >
                      Shared screen
                    </Button>
                  ) : null}
                </div>
              </>
            ) : (
              <EmptyState size="sm" icon={<Video />} title="Not ready yet" description="The first part becomes playable shortly after it is processed." />
            )}
            {cameraParts.length > 1 || (cameraParts.length === 1 && cameraParts[0]!.status !== "ready") ? (
              <section aria-label="Parts" className="grid gap-2">
                <p className="text-xs text-fg-muted">
                  A new part starts whenever the candidate's browser restarts the recording (for example after a page reload).
                </p>
                <PartList parts={cameraParts} selectedId={selected?.partId ?? null} onSelect={(part) => selectPart(part)} testIdPrefix="recording-part" />
              </section>
            ) : null}
            {screenParts.length > 1 ? (
              <section aria-label="Screen parts" className="grid gap-2">
                <p className="text-xs text-fg-muted">Shared screen</p>
                <PartList parts={screenParts} selectedId={screenPart?.partId ?? null} onSelect={() => setShowScreen(true)} testIdPrefix="recording-screen-part" />
              </section>
            ) : null}
          </CardContent>
        </Card>

        <SyncedTranscript
          cues={cues}
          activeIndex={activeIndex}
          playing={playing}
          selectedPartId={selected?.partId ?? null}
          partNames={partNames}
          loading={transcript.isPending}
          onPick={pickCue}
        />
      </div>
    </>
  );
}
