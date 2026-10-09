import { CalendarClock, CalendarX2, CircleCheckBig, Hourglass, Link2Off, PauseCircle, UsersRound, type LucideIcon } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";
import { formatDateTime } from "@/lib/format";

import type { LinkState } from "../types";

export type LinkPanelState = Exclude<LinkState, "open"> | "not_found";

const PANELS: Record<LinkPanelState, { icon: LucideIcon; title: string; description: (opensAt: string | null) => string }> = {
  scheduled: {
    icon: CalendarClock,
    title: "Registration hasn't opened yet",
    description: (opensAt) => (opensAt ? `Registration opens on ${formatDateTime(opensAt)}. Come back to this link then.` : "Come back to this link a little later."),
  },
  paused: { icon: PauseCircle, title: "Registration is paused", description: () => "Try again later. The hiring team has paused new registrations for now." },
  closed: { icon: CalendarX2, title: "Registration has closed", description: () => "This link no longer accepts new registrations." },
  expired: { icon: CalendarX2, title: "Registration has closed", description: () => "The registration period for this link has ended." },
  full: { icon: UsersRound, title: "All places are taken", description: () => "This link has reached its limit of registrations." },
  not_found: { icon: Link2Off, title: "This link isn't valid", description: () => "Check you copied the whole link. If it still doesn't work, ask the person who shared it." },
};

/** Why the registration form isn't shown (link not open, or no such link). */
export function LinkStatePanel({ state, opensAt = null }: { state: LinkPanelState; opensAt?: string | null }) {
  const panel = PANELS[state] ?? PANELS.closed;
  const Icon = panel.icon;
  return <EmptyState bordered icon={<Icon />} title={panel.title} description={panel.description(opensAt)} />;
}

export type RegistrationOutcomePanel = "already_completed" | "already_registered" | "awaiting_review" | "already_invited" | "unavailable" | "wait";

/** Result panels for duplicate or refused registrations (OPEN_LINKS.md section 7.1). */
export function RegistrationOutcome({ kind, minutes, round }: { kind: RegistrationOutcomePanel; minutes?: number | null; round?: number | null }) {
  switch (kind) {
    case "already_completed":
      return (
        <EmptyState
          bordered
          icon={<CircleCheckBig className="text-success" />}
          title="You've already completed this interview"
          description="Thank you. The hiring team has your interview and will be in touch about next steps."
        />
      );
    case "awaiting_review":
      return (
        <EmptyState
          bordered
          icon={<Hourglass />}
          title={round ? `You've finished round ${round}` : "You've finished your current round"}
          description="The hiring team will review it. Open the personal link you saved at registration to check whether the next round is ready."
        />
      );
    case "already_registered":
      return (
        <EmptyState
          bordered
          icon={<Link2Off />}
          title="You're already registered"
          description="Continue with the personal link you saved when you registered, or on the device you registered from. Each person can register only once."
        />
      );
    case "already_invited":
      return (
        <EmptyState
          bordered
          icon={<Link2Off />}
          title="You already have an invitation for this role"
          description="Use the link in your invitation to take your interview."
        />
      );
    case "wait":
      return (
        <EmptyState
          bordered
          icon={<Hourglass />}
          title="Too many attempts"
          description={`For security, registration from this connection is paused${minutes ? ` for about ${minutes} minute${minutes === 1 ? "" : "s"}` : " for a few minutes"}. Try again later.`}
        />
      );
    default:
      return <EmptyState bordered icon={<Link2Off />} title="We couldn't register you" description="Please contact the hiring team." />;
  }
}
