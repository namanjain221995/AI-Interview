import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { useMemo, useState } from "react";

import { ScoreTimelineChart } from "@/components/charts/ScoreTimelineChart";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { usePersistentState } from "@/lib/hooks";
import { formatClock, humanize, truncate } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useSessionTimeline } from "../../api/hooks";
import { isRecord, stringOrNull } from "../../api/normalize";
import { PanelSkeleton, QueryError, SegmentedControl } from "../../components/primitives";
import { bucketSnapshots, DEFAULT_WINDOW_MINUTES, describeWindow, WINDOW_OPTIONS, windowsFromServer, type TimelineWindow } from "../../lib/timeline";
import { useSessionContext } from "../context";

const EVENT_LABELS: Record<string, string> = {
  "session.started": "Interview started",
  "session.paused": "Interview paused",
  "session.resumed": "Interview resumed",
  "session.completed": "Interview completed",
  "session.ended": "Interview ended",
  "question.asked": "Question asked",
  "answer.received": "Answer received",
  "answer.evaluated": "Answer evaluated",
  "evaluation.applied": "Answer evaluated",
  "coding.started": "Coding exercise started",
  "coding.ended": "Coding exercise finished",
  "coding.hint": "Hint given",
  "hint.issued": "Hint given",
  "time.warning": "Time reminder",
  disconnect: "Connection lost",
  reconnect: "Reconnected",
  barge_in: "Candidate spoke over the interviewer",
  "contradiction.found": "Statements to reconcile",
  "claim.verified": "Resume claim discussed",
};

function eventLabel(event: TimelineEvent): string {
  return EVENT_LABELS[event.type] ?? humanize(event.type.replace(/\./g, " "));
}

function eventDetail(event: TimelineEvent): string | null {
  const p = isRecord(event.payload) ? event.payload : {};
  const text = stringOrNull(p.text) ?? stringOrNull(p.question_focus) ?? stringOrNull(p.message) ?? stringOrNull(p.reason) ?? stringOrNull(p.summary);
  return text ? truncate(text, 140) : null;
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return null;
  if (Math.abs(value) < 0.05) return <Minus className="size-3 text-fg-subtle" aria-label="unchanged" />;
  return value > 0 ? (
    <span className="inline-flex items-center text-[11px] text-success-fg tabular-nums">
      <ArrowUpRight className="size-3" aria-hidden="true" />
      {value.toFixed(1)}
    </span>
  ) : (
    <span className="inline-flex items-center text-[11px] text-danger-fg tabular-nums">
      <ArrowDownRight className="size-3" aria-hidden="true" />
      {Math.abs(value).toFixed(1)}
    </span>
  );
}

function WindowRow({ window }: { window: TimelineWindow }) {
  const { index } = useSessionContext();
  const entries = index.order.filter((key) => window.values[key]).map((key) => [key, window.values[key]!] as const);
  const extra = Object.keys(window.values).filter((key) => !index.order.includes(key));
  const all = [...entries, ...extra.map((key) => [key, window.values[key]!] as const)];
  return (
    <li className="grid grid-cols-1 gap-2 py-3 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-4">
      <span className="text-[13px] font-medium text-fg tabular-nums">{window.label}</span>
      <span className="sr-only">{describeWindow(window, index.nameOf, true)}</span>
      {all.length ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-1.5" aria-hidden="true">
          {all.map(([key, value]) => (
            <li key={key} className={cn("inline-flex items-center gap-1.5 text-[13px]", value.carried && "opacity-55")} title={value.carried ? "No new evidence in this window" : undefined}>
              <span className="size-2 rounded-full" style={{ background: index.colorOf(key) }} />
              <span className="text-fg-muted">{index.nameOf(key)}</span>
              <span className="font-semibold text-fg tabular-nums">{value.score.toFixed(1)}</span>
              {value.carried ? <span className="text-[11px] text-fg-subtle">unchanged</span> : <Delta value={value.delta} />}
            </li>
          ))}
        </ul>
      ) : (
        <span className="text-[13px] text-fg-subtle">No evidence yet</span>
      )}
    </li>
  );
}

export function TimelineTab() {
  const { sessionId, index, durationSeconds, inProgress, plannedSeconds } = useSessionContext();
  const [windowMinutes, setWindowMinutes] = usePersistentState<number>("review.timeline.window", DEFAULT_WINDOW_MINUTES);
  const minutes = (WINDOW_OPTIONS as readonly number[]).includes(windowMinutes) ? windowMinutes : DEFAULT_WINDOW_MINUTES;
  const timeline = useSessionTimeline(sessionId, minutes, { live: inProgress });
  const [showAllEvents, setShowAllEvents] = useState(false);

  const data = timeline.data;
  // The API's own windows win (same bucketing everywhere); raw snapshots are bucketed locally otherwise.
  const windows = useMemo(() => {
    if (!data) return [];
    const serverMatches = data.windows.length > 0 && (data.windowMinutes === null || Math.round(data.windowMinutes) === minutes);
    if (serverMatches) return windowsFromServer(data.windows, minutes);
    if (data.snapshots.length) return bucketSnapshots(data.snapshots, minutes, { durationSeconds: inProgress ? durationSeconds : Math.max(durationSeconds, data.durationSeconds ?? 0) });
    return [];
  }, [data, minutes, durationSeconds, inProgress]);

  const competencies = useMemo(() => {
    const keys = new Set<string>([...index.order, ...(data?.snapshots ?? []).map((s) => s.competency_key)]);
    return [...keys].map((key) => ({ key, label: index.nameOf(key) }));
  }, [index, data?.snapshots]);

  // Charts need points: when only windows exist, plot each window's values at its end.
  const chartSnapshots = useMemo(() => {
    if (!data) return [];
    if (data.snapshots.length) return data.snapshots;
    return windows.flatMap((w) => Object.entries(w.values).map(([key, v]) => ({ elapsed_seconds: w.endMinute * 60, competency_key: key, score: v.score })));
  }, [data, windows]);

  const events = (data?.events ?? []).filter((e) => e.type !== "heartbeat");
  const shownEvents = showAllEvents ? events : events.slice(0, 12);

  if (timeline.isPending) return <Card><PanelSkeleton rows={6} /></Card>;
  if (timeline.isError && !data) {
    return (
      <Card className="p-5">
        <QueryError error={timeline.error} title="Couldn't load the timeline" onRetry={() => void timeline.refetch()} />
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-6">
      <Card>
        <CardHeader
          title="Competency estimates over time"
          description="How each score moved as evidence came in. Scores are out of 10."
          action={
            <SegmentedControl
              label="Window size"
              value={String(minutes)}
              onChange={(v) => setWindowMinutes(Number(v))}
              options={WINDOW_OPTIONS.map((option) => ({ value: String(option), label: `${option} min` }))}
            />
          }
        />
        <CardContent className="pt-4">
          {chartSnapshots.length ? (
            <div data-testid="timeline-chart">
              <ScoreTimelineChart snapshots={chartSnapshots} competencies={competencies} durationSeconds={inProgress ? undefined : Math.max(durationSeconds, plannedSeconds ?? 0)} height={300} />
            </div>
          ) : (
            <div data-testid="timeline-chart">
              <EmptyState size="sm" title="No score updates yet" description="The chart fills in as answers are evaluated." />
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title={`${minutes}-minute windows`} description="Last estimate per competency in each window. Faded values carried over without new evidence." />
          <CardContent className="pt-2">
            {windows.length ? (
              <ol className="divide-y divide-line-subtle">
                {windows.map((w) => (
                  <WindowRow key={w.index} window={w} />
                ))}
              </ol>
            ) : (
              <p className="py-6 text-center text-sm text-fg-muted">No windows yet.</p>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Interview events" description="What happened, in order." />
          <CardContent className="pt-3">
            {events.length ? (
              <>
                <ol className="relative grid grid-cols-1 gap-3 border-l border-line pl-4">
                  {shownEvents.map((event) => {
                    const detail = eventDetail(event);
                    return (
                      <li key={`${event.seq}-${event.type}`} className="relative grid grid-cols-1 gap-0.5">
                        <span className="absolute top-1.5 -left-[21px] size-2 rounded-full border-2 border-surface bg-line-emphasis" aria-hidden="true" />
                        <span className="flex items-baseline gap-2">
                          <span className="text-xs text-fg-subtle tabular-nums">{formatClock(event.elapsed_seconds)}</span>
                          <span className="text-[13px] font-medium text-fg">{eventLabel(event)}</span>
                        </span>
                        {detail ? <span className="text-xs text-fg-muted">{detail}</span> : null}
                      </li>
                    );
                  })}
                </ol>
                {events.length > shownEvents.length || showAllEvents ? (
                  <button type="button" className="mt-3 text-xs font-medium text-accent hover:underline" onClick={() => setShowAllEvents((v) => !v)}>
                    {showAllEvents ? "Show fewer" : `Show all ${events.length} events`}
                  </button>
                ) : null}
              </>
            ) : (
              <p className="py-6 text-center text-sm text-fg-muted">No events recorded.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
