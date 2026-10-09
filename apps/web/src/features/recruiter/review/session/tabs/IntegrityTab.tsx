import { ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Progress } from "@/components/ui/Progress";
import { usePermission } from "@/lib/auth";
import { formatDuration, formatPercent, pluralize } from "@/lib/format";
import { INTEGRITY_EVENT_LABELS, INTEGRITY_SEVERITY_LABELS, labelFor } from "@/lib/labels";

import { IntegrityEventRow, ObservationsBanner } from "../../components/IntegrityReview";
import { PanelSkeleton, QueryError, SegmentedControl } from "../../components/primitives";
import { countByType, facePresenceSummary, SEVERITY_ORDER, sortEvents, type FaceIntervalStats, type FacePresenceSummary } from "../../lib/integrity";
import { useSessionContext } from "../context";

const POLICY_NOTES: Array<{ key: string; on: string; off: string }> = [
  { key: "monitor_focus", on: "Tab and window focus were monitored.", off: "Focus was not monitored." },
  { key: "monitor_clipboard", on: "Copy and paste were monitored.", off: "Clipboard was not monitored." },
  { key: "camera_frame_analysis", on: "Camera frames were analysed for objective signals only (no identity, emotion or appearance).", off: "Camera frames were not analysed." },
  {
    key: "face_presence_check",
    on: "The candidate's device checked that one face was visible; a snapshot was sent only when a check needed review.",
    off: "The on-device face check was off.",
  },
  { key: "screen_share_required", on: "Screen sharing was required.", off: "Screen sharing was not required." },
];

export function IntegrityTab() {
  const { integrity, integrityQuery } = useSessionContext();
  const canReview = usePermission("integrity:review");
  const [filter, setFilter] = useState<"all" | "unreviewed">("all");
  const [order, setOrder] = useState<"time" | "severity">("time");

  // The face-check summary rows feed the "Face check" card instead of the list.
  const events = useMemo(() => {
    const list = (integrity?.events ?? []).filter((e) => e.type !== "face_check_summary");
    return sortEvents(filter === "unreviewed" ? list.filter((e) => e.review_status === "unreviewed") : list, order);
  }, [integrity?.events, filter, order]);
  const facePresence = useMemo(() => facePresenceSummary(integrity?.events ?? []), [integrity?.events]);

  if (integrityQuery.isPending) return <Card><PanelSkeleton rows={4} /></Card>;
  if (integrityQuery.isError && !integrity) {
    return (
      <div className="grid gap-4">
        <ObservationsBanner />
        <Card className="p-5">
          <QueryError error={integrityQuery.error} title="Couldn't load integrity observations" onRetry={() => void integrityQuery.refetch()} />
        </Card>
      </div>
    );
  }

  const all = (integrity?.events ?? []).filter((e) => e.type !== "face_check_summary");
  const unreviewed = all.filter((e) => e.review_status === "unreviewed").length;
  const types = countByType(all);
  const policy = integrity?.policy ?? null;
  const faceCheckOn = policy ? (policy as Record<string, unknown>).face_presence_check === true && policy.enabled !== false : false;

  return (
    <div className="grid grid-cols-1 gap-6">
      <ObservationsBanner />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <Card className="min-w-0">
          <CardHeader
            title="Observations"
            description={all.length ? `${pluralize(all.length, "observation")}, ${unreviewed} not reviewed yet.` : undefined}
            action={
              all.length ? (
                <div className="flex flex-wrap items-center gap-2">
                  <SegmentedControl
                    label="Show"
                    value={filter}
                    onChange={setFilter}
                    options={[
                      { value: "all", label: "All" },
                      { value: "unreviewed", label: `Not reviewed (${unreviewed})` },
                    ]}
                  />
                  <SegmentedControl
                    label="Order"
                    value={order}
                    onChange={setOrder}
                    options={[
                      { value: "time", label: "By time" },
                      { value: "severity", label: "By severity" },
                    ]}
                  />
                </div>
              ) : null
            }
          />
          <CardContent className="pt-1">
            {all.length === 0 ? (
              <EmptyState size="sm" icon={<ShieldCheck />} title="No observations recorded" description="Nothing in this interview needed a second look." />
            ) : events.length === 0 ? (
              <p className="py-8 text-center text-sm text-fg-muted">Every observation has been reviewed.</p>
            ) : (
              <ol className="divide-y divide-line-subtle">
                {events.map((event) => (
                  <IntegrityEventRow key={event.id} event={event} canReview={canReview} />
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        <div className="grid content-start gap-6">
          <Card>
            <CardHeader title="Summary" />
            <CardContent className="grid gap-4">
              <dl className="grid grid-cols-2 gap-3">
                {SEVERITY_ORDER.map((severity) => (
                  <div key={severity} className="grid gap-0.5">
                    <dt className="text-xs text-fg-muted">{INTEGRITY_SEVERITY_LABELS[severity]} severity</dt>
                    <dd className="text-lg font-semibold text-fg tabular-nums">{integrity?.countsBySeverity[severity] ?? 0}</dd>
                  </div>
                ))}
              </dl>
              {types.length ? (
                <ul className="grid gap-1.5 border-t border-line-subtle pt-3">
                  {types.map((t) => (
                    <li key={t.type} className="flex items-baseline justify-between gap-3 text-[13px]">
                      <span className="text-fg-muted">{labelFor(INTEGRITY_EVENT_LABELS, t.type)}</span>
                      <span className="font-medium text-fg tabular-nums">{t.count}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </CardContent>
          </Card>
          {facePresence || faceCheckOn ? <FacePresenceCard summary={facePresence} /> : null}
          {policy ? (
            <Card>
              <CardHeader title="What was monitored" description="From the policy the candidate agreed to." />
              <CardContent>
                <ul className="grid gap-1.5 text-[13px] text-fg-muted">
                  {POLICY_NOTES.filter((n) => typeof (policy as Record<string, unknown>)[n.key] === "boolean").map((n) => (
                    <li key={n.key}>{(policy as Record<string, unknown>)[n.key] ? n.on : n.off}</li>
                  ))}
                  <li>A browser cannot lock the computer; focus and fullscreen are monitored, not enforced.</li>
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function IntervalLine({ label, stats, testId }: { label: string; stats: FaceIntervalStats; testId: string }) {
  return (
    <div data-testid={testId} className="grid gap-0.5">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-[13px] text-fg">
        {stats.intervals ? (
          <>
            <span className="font-medium tabular-nums">{pluralize(stats.intervals, "interval")}</span>
            <span className="text-fg-muted"> · {formatDuration(stats.totalMs / 1000)} in total</span>
            {stats.intervals > 1 ? <span className="text-fg-muted"> · longest {formatDuration(stats.longestMs / 1000)}</span> : null}
            {stats.confirmed ? <span className="block text-xs text-fg-muted">{pluralize(stats.confirmed, "snapshot")} confirmed by the server's face detector</span> : null}
          </>
        ) : (
          <span className="text-fg-muted">None</span>
        )}
      </dd>
    </div>
  );
}

/** Face presence over the session (on-device face check), worded as observations. */
function FacePresenceCard({ summary }: { summary: FacePresenceSummary | null }) {
  return (
    <Card data-testid="integrity-face-presence">
      <CardHeader title="Face check" description="Checked on the candidate's device; snapshots double-checked on the server." />
      <CardContent className="grid gap-4">
        {summary ? (
          <>
            {summary.oneFaceShare !== null ? (
              <Progress
                data-testid="integrity-face-share"
                value={summary.oneFaceShare * 100}
                tone="accent"
                label="One face visible"
                valueLabel={formatPercent(summary.oneFaceShare)}
                aria-label={`One face visible for ${formatPercent(summary.oneFaceShare)} of the monitored time`}
              />
            ) : null}
            {summary.monitoredMs > 0 ? (
              <p className="-mt-2 text-xs text-fg-muted">Of {formatDuration(summary.monitoredMs / 1000)} monitored (time with the interview tab hidden is not included).</p>
            ) : null}
            <dl className="grid gap-3 border-t border-line-subtle pt-3">
              <IntervalLine label="No face visible" stats={summary.noFace} testId="integrity-face-no-face" />
              <IntervalLine label="More than one face visible" stats={summary.multipleFaces} testId="integrity-face-multiple" />
              <div data-testid="integrity-face-static" className="grid gap-0.5">
                <dt className="text-xs text-fg-muted">Pictures set aside</dt>
                <dd className="text-[13px] text-fg">
                  {summary.staticFacesIgnored ? (
                    <>
                      <span className="font-medium tabular-nums">{summary.staticFacesIgnored}</span>
                      <span className="text-fg-muted"> motionless {summary.staticFacesIgnored === 1 ? "face" : "faces"} (a photo, poster or screen), not counted</span>
                    </>
                  ) : (
                    <span className="text-fg-muted">None</span>
                  )}
                </dd>
              </div>
            </dl>
            <p className="text-xs leading-5 text-fg-subtle">Stepping out of view or looking away briefly is normal. Review each interval in context.</p>
          </>
        ) : (
          <p className="text-[13px] text-fg-muted">No face-check data was recorded for this session.</p>
        )}
      </CardContent>
    </Card>
  );
}
