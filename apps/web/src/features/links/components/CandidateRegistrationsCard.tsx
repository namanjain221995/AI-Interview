import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { useCandidateRegistrations, useRegistration } from "../api";
import { formatAnswer } from "../formSchema";
import { REGISTRATION_STATUS_LABELS, REGISTRATION_STATUS_TONES, ROUND_STATE_LABELS, ROUND_STATE_TONES } from "../status";
import type { Registration } from "../types";

function Answers({ registration }: { registration: Registration }) {
  const detail = useRegistration(registration.link_id, registration.id);
  if (detail.isPending) return <Skeleton className="h-16 w-full" />;
  if (detail.error || !detail.data) return <p className="text-xs text-fg-muted">Answers couldn't be loaded.</p>;
  if (!detail.data.answers.length) return <p className="text-xs text-fg-muted">No answers stored.</p>;
  return (
    <dl className="grid grid-cols-1 gap-1.5 text-[13px]">
      {detail.data.answers.map((a) => (
        <div key={a.key} className="grid grid-cols-1 gap-0.5">
          <dt className="text-fg-muted">{a.label}</dt>
          <dd className="break-words text-fg">{formatAnswer(a.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function RegistrationItem({ registration }: { registration: Registration }) {
  const [open, setOpen] = useState(false);
  const link = registration.link;
  return (
    <li className="grid grid-cols-1 gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="grid min-w-0">
          <Link to={`/app/links/${registration.link_id}`} className="truncate text-sm font-medium text-fg hover:underline">
            {link?.name ?? "Interview link"}
          </Link>
          <span className="truncate text-xs text-fg-muted">
            {link?.job_title ? `${link.job_title}, ` : ""}registered {formatDate(registration.created_at)}
          </span>
        </div>
        <Badge tone={REGISTRATION_STATUS_TONES[registration.status]} size="sm" dot>
          {REGISTRATION_STATUS_LABELS[registration.status]}
        </Badge>
      </div>
      <ol className="grid grid-cols-1 gap-1">
        {registration.rounds.map((r, i) => (
          <li key={`${r.sequence}-${r.attempt}`} className="flex items-center justify-between gap-2 text-[13px]">
            <span className="min-w-0 truncate text-fg">
              {i + 1}. {r.name}
            </span>
            {r.session_id ? (
              <Link to={`/app/sessions/${r.session_id}`} className="shrink-0 rounded-sm">
                <Badge tone={ROUND_STATE_TONES[r.state]} size="sm">
                  {ROUND_STATE_LABELS[r.state]}
                </Badge>
              </Link>
            ) : (
              <Badge tone={ROUND_STATE_TONES[r.state]} size="sm">
                {ROUND_STATE_LABELS[r.state]}
              </Badge>
            )}
          </li>
        ))}
      </ol>
      <Button variant="ghost" size="xs" className="w-fit text-fg-muted" rightIcon={<ChevronDown className={cn("transition-transform", open && "rotate-180")} />} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Hide form answers" : "Form answers"}
      </Button>
      {open ? <Answers registration={registration} /> : null}
    </li>
  );
}

/** Candidate detail: the open links this person registered through. Renders nothing when there are none. */
export function CandidateRegistrationsCard({ candidateId }: { candidateId: string | undefined }) {
  const query = useCandidateRegistrations(candidateId);
  const items = query.data?.items ?? [];
  if (!items.length) return null;
  return (
    <Card data-testid="candidate-registrations-card">
      <CardHeader title="Registrations" description="Open links this person registered through." />
      <CardContent>
        <ul className="grid grid-cols-1 divide-y divide-line-subtle">
          {items.map((r) => (
            <RegistrationItem key={r.id} registration={r} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
