import { Clock3, Eye, FileLock2, Mic, UserCheck, Video } from "lucide-react";
import type { ReactNode } from "react";

import { Markdown } from "@/components/ui/Markdown";
import { formatList } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { PolicySummary } from "../types";

export interface DataUsePanelProps {
  dataUse: PolicySummary & { notice_markdown?: string };
  organizationName: string;
  className?: string;
  /** Rendered under the list (the acknowledgement checkbox). */
  children?: ReactNode;
}

function days(n: number): string {
  return n === 1 ? "1 day" : `${n} days`;
}

/** The plain-language data-use items, in reading order. */
export function dataUseItems(d: PolicySummary, org: string): Array<{ key: string; icon: ReactNode; text: string }> {
  const items: Array<{ key: string; icon: ReactNode; text: string }> = [];
  const recorded = [d.audio_recording && "your voice", d.video_recording && "your camera video", d.screen_recording && "your screen"].filter(Boolean) as string[];
  items.push({
    key: "recording",
    icon: d.video_recording || d.screen_recording ? <Video /> : <Mic />,
    text: recorded.length
      ? `The interview records ${formatList(recorded)} and keeps a written transcript of what you say.`
      : "What you say is turned into a written transcript. No audio or video is kept.",
  });
  const watched = [
    d.camera_monitoring && "your camera is checked from time to time to confirm you're the one answering",
    d.screen_monitoring && "switching tabs or windows is noted",
  ].filter(Boolean) as string[];
  if (watched.length) {
    items.push({
      key: "monitoring",
      icon: <Eye />,
      text: `During the interview ${formatList(watched)}. These are observations for a person to review, never an automatic decision.`,
    });
  }
  items.push({
    key: "ai",
    icon: <UserCheck />,
    text: `An AI interviewer asks the questions and drafts an assessment from your answers and résumé. A person at ${org} reviews it and makes every decision.`,
  });
  items.push({
    key: "answers",
    icon: <FileLock2 />,
    text: "Your answers on this form go only to the hiring team. The AI never sees them and they don't affect your assessment.",
  });
  const r = d.retention_days;
  const kept = [
    r.resume > 0 && `your résumé for ${days(r.resume)}`,
    d.audio_recording && r.audio > 0 && `audio for ${days(r.audio)}`,
    d.video_recording && r.video > 0 && `video for ${days(r.video)}`,
    r.transcript > 0 && `the transcript for ${days(r.transcript)}`,
    r.report > 0 && `the report for ${days(r.report)}`,
  ].filter(Boolean) as string[];
  if (kept.length) items.push({ key: "retention", icon: <Clock3 />, text: `Kept for a limited time, then deleted: ${formatList(kept)}.` });
  return items;
}

/** "How your data is used" disclosure shown before anyone types personal data. */
export function DataUsePanel({ dataUse, organizationName, className, children }: DataUsePanelProps) {
  const items = dataUseItems(dataUse, organizationName);
  return (
    <section aria-labelledby="data-use-heading" className={cn("rounded-xl border border-line bg-surface-subtle p-4 sm:p-5", className)} data-testid="open-link-data-use">
      <h2 id="data-use-heading" className="text-[15px] leading-6 font-semibold text-fg">
        How your data is used
      </h2>
      <ul className="mt-3 grid grid-cols-1 gap-3">
        {items.map((item) => (
          <li key={item.key} className="flex gap-3 text-sm leading-6 text-fg-muted">
            <span className="mt-1 flex size-4 shrink-0 text-fg-subtle [&_svg]:size-4" aria-hidden="true">
              {item.icon}
            </span>
            <span className="min-w-0">{item.text}</span>
          </li>
        ))}
      </ul>
      {dataUse.notice_markdown?.trim() ? (
        <div className="mt-4 border-t border-line pt-4">
          <p className="text-[13px] font-medium text-fg">A note from {organizationName}</p>
          <Markdown size="sm" className="mt-1 text-fg-muted">
            {dataUse.notice_markdown}
          </Markdown>
        </div>
      ) : null}
      <p className="mt-4 text-[13px] leading-5 text-fg-muted">You will review and accept the full details before the interview starts.</p>
      {children ? <div className="mt-4">{children}</div> : null}
    </section>
  );
}
