import { Film, VideoOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";

import { useSessionRecordings } from "@/features/recruiter/review/api/recordings";
import { RecordingVideo, type RecordingVideoHandle } from "@/features/recruiter/review/components/media";
import { locateSessionMs } from "@/features/recruiter/review/lib/recording";

import { formatClockMs } from "../../lib/review";

export interface RecordingMomentProps {
  sessionId: string;
  /** Interview-clock ms (pauses excluded), as answers and transcripts use. */
  startMs: number;
  label: string;
}

/** "Play in recording": opens the session recording at the answer's start. */
export function RecordingMomentButton({ sessionId, startMs, label }: RecordingMomentProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="xs" variant="outline" leftIcon={<Film />} onClick={() => setOpen(true)} data-testid="intake-play-recording">
        Play in recording
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle>Recording at {formatClockMs(startMs)}</DialogTitle>
            <DialogDescription>{label}</DialogDescription>
          </DialogHeader>
          <DialogBody>{open ? <MomentPlayer sessionId={sessionId} startMs={startMs} /> : null}</DialogBody>
        </DialogContent>
      </Dialog>
    </>
  );
}

function MomentPlayer({ sessionId, startMs }: { sessionId: string; startMs: number }) {
  const recordings = useSessionRecordings(sessionId, { live: false });
  const player = useRef<RecordingVideoHandle>(null);
  const located = recordings.data ? locateSessionMs(recordings.data.parts, Math.max(0, startMs - 1500), recordings.data.sessionStartedAt) : null;

  useEffect(() => {
    if (located) player.current?.seek(located.mediaMs, false);
  }, [located?.part.partId, located?.mediaMs]); // eslint-disable-line react-hooks/exhaustive-deps

  if (recordings.isPending) return <Skeleton className="aspect-video w-full" />;
  if (!located) {
    return (
      <EmptyState
        size="sm"
        icon={<VideoOff />}
        title="This moment isn't in the recording"
        description={recordings.data?.parts.length ? "The candidate's browser wasn't recording at this point (for example, during a reconnection)." : "No recording was made for this interview, or it is still processing."}
      />
    );
  }
  return (
    <div className="grid gap-2">
      <RecordingVideo ref={player} sessionId={sessionId} part={located.part} label="Interview recording" videoTestId="intake-recording-video" />
      <p className="text-xs text-fg-muted">Starts a moment before the answer. Every playback is logged.</p>
    </div>
  );
}
