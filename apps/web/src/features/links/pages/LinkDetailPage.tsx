import { Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { PageContainer } from "@/components/layout/StaffShell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatCard, StatGroup } from "@/components/ui/StatCard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useHasRole, usePermission } from "@/lib/auth";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useDocumentTitle } from "@/lib/hooks";
import { CANDIDATE_REPORT_MODE_LABELS, labelFor, ROUND_TYPE_LABELS } from "@/lib/labels";
import type { CandidateReportMode } from "@/lib/types";

import { DetailSkeleton, Fact, LoadError } from "@/features/recruiter/admin/components/Controls";

import { useLink } from "../api";
import { FIELD_TYPE_LABELS } from "../formSchema";
import { DeleteLinkDialog } from "../components/DeleteLinkDialog";
import { RegistrationsTable } from "../components/RegistrationsTable";
import { AccessCodeDialog, ShareLinkCard } from "../components/ShareLinkCard";
import { LINK_STATE_LABELS, LINK_STATE_TONES, PROGRESSION_LABELS } from "../status";
import type { OpenLink } from "../types";

const TABS = ["registrations", "share", "settings"] as const;
type Tab = (typeof TABS)[number];

function SettingsSummary({ link, canManage }: { link: OpenLink; canManage: boolean }) {
  const p = link.policy_summary;
  const enabled = link.form_schema.fields.filter((f) => f.enabled);
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader
          title="Link settings"
          action={
            canManage && link.status !== "closed" ? (
              <Button asChild size="sm" variant="outline" leftIcon={<Pencil />}>
                <Link to={`/app/links/${link.id}/edit`}>Edit</Link>
              </Button>
            ) : undefined
          }
        />
        <CardContent className="pt-2">
          <dl className="divide-y divide-line-subtle">
            <Fact label="Job">
              <Link to={`/app/jobs/${link.job.id}`} className="text-accent hover:underline">
                {link.job.title}
              </Link>
            </Fact>
            <Fact label="Rounds">
              <ol className="grid gap-1">
                {link.rounds.map((r, i) => (
                  <li key={r.id} className={r.missing ? "text-fg-subtle line-through" : undefined}>
                    {i + 1}. {r.name} <span className="text-fg-muted">({labelFor<string>(ROUND_TYPE_LABELS, r.round_type)}, {r.duration_minutes} min)</span>
                  </li>
                ))}
              </ol>
            </Fact>
            {link.rounds.length > 1 ? <Fact label="Next round">{PROGRESSION_LABELS[link.progression]}</Fact> : null}
            <Fact label="Opens">{link.opens_at ? formatDateTime(link.opens_at) : "When created"}</Fact>
            <Fact label="Closes">{link.closes_at ? formatDateTime(link.closes_at) : "When you close it"}</Fact>
            <Fact label="Most registrations">{link.max_registrations ? formatNumber(link.max_registrations) : "No limit"}</Fact>
            <Fact label="Time to start a round">{link.round_window_hours} hours</Fact>
            <Fact label="Per network">{formatNumber(link.per_ip_hourly_limit)} registrations an hour</Fact>
            <Fact label="Résumé">{link.require_resume ? "Required" : "Optional"}</Fact>
            <Fact label="Access code">{link.requires_access_code ? "Required" : "Not required"}</Fact>
            <Fact label="Repeats checked across">{link.duplicate_policy.scope === "job" ? "Every link of this job" : "This link only"}</Fact>
            <Fact label="Same device">{link.duplicate_policy.device === "block" ? "Blocked" : "Flagged for review"}</Fact>
            <Fact label="Same network">{link.duplicate_policy.network === "off" ? "Not checked" : "Flagged, low priority"}</Fact>
            {link.created_by ? <Fact label="Created by">{link.created_by.full_name}</Fact> : null}
          </dl>
        </CardContent>
      </Card>
      <div className="grid grid-cols-1 content-start gap-6">
        <Card>
          <CardHeader title="Registration form" description={`${enabled.length} fields, version ${link.form_schema.version}`} />
          <CardContent>
            <ul className="grid grid-cols-1 gap-1.5 text-sm">
              {enabled.map((f) => (
                <li key={f.key} className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 truncate text-fg">{f.label}</span>
                  <span className="shrink-0 text-xs text-fg-muted">
                    {FIELD_TYPE_LABELS[f.type]}
                    {f.required ? ", required" : ""}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader title="Recording and monitoring" description="From the job's policy." />
          <CardContent className="pt-2">
            <dl className="divide-y divide-line-subtle">
              <Fact label="Video">{p.video_recording ? "Recorded" : "Off"}</Fact>
              <Fact label="Audio">{p.audio_recording ? "Recorded" : "Off"}</Fact>
              <Fact label="Screen">{p.screen_recording ? "Recorded" : "Off"}</Fact>
              <Fact label="Monitoring">{[p.camera_monitoring && "Camera", p.screen_monitoring && "Tabs and windows"].filter(Boolean).join(", ") || "Off"}</Fact>
              <Fact label="Candidate report">{labelFor(CANDIDATE_REPORT_MODE_LABELS, p.candidate_report_mode as CandidateReportMode)}</Fact>
            </dl>
            {!p.video_recording ? (
              <Callout tone="warning" className="mt-3" title="No full video">
                Video recording is off for this job.{" "}
                <Link to={`/app/jobs/${link.job.id}/edit`} className="font-medium underline">
                  Change the job's policy
                </Link>
              </Callout>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** /app/links/:linkId: registrations, share, settings. */
export default function LinkDetailPage() {
  const { linkId } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const link = useLink(linkId);
  const canManage = usePermission("interviews:invite");
  const canReviewFlags = usePermission("candidates:write");
  const requested = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(requested ?? "") ? (requested as Tab) : "registrations";
  const [accessCode, setAccessCode] = useState<string | null>(null);
  // Permanent deletion is for administrators only (the API enforces it too).
  const canDelete = useHasRole("ADMIN", "SUPER_ADMIN");
  const [deleteOpen, setDeleteOpen] = useState(false);
  useDocumentTitle(link.data?.name ?? "Interview link");

  // A code issued on create arrives once through navigation state; drop it from history right away.
  useEffect(() => {
    const code = (location.state as { accessCode?: string } | null)?.accessCode;
    if (code) {
      setAccessCode(code);
      navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
    }
  }, [location.state, location.pathname, location.search, navigate]);

  if (link.error) {
    return (
      <PageContainer>
        <PageHeader breadcrumbs={[{ label: "Interview links", to: "/app/links" }, { label: "Link" }]} title="Interview link" />
        <LoadError error={link.error} onRetry={() => void link.refetch()} />
      </PageContainer>
    );
  }
  if (!link.data) {
    return (
      <PageContainer>
        <DetailSkeleton />
      </PageContainer>
    );
  }
  const data = link.data;
  const c = data.counts;

  return (
    <PageContainer>
      <PageHeader
        breadcrumbs={[{ label: "Interview links", to: "/app/links" }, { label: data.name }]}
        title={data.name}
        titleAddon={
          <Badge tone={LINK_STATE_TONES[data.state]} dot data-testid="link-state">
            {LINK_STATE_LABELS[data.state]}
          </Badge>
        }
        description={
          <>
            <Link to={`/app/jobs/${data.job.id}`} className="hover:text-fg hover:underline">
              {data.job.title}
            </Link>
            {`, ${data.rounds.length === 1 ? data.rounds[0]?.name ?? "1 round" : `${data.rounds.length} rounds`}`}
          </>
        }
        actions={
          (canManage && data.status !== "closed") || canDelete ? (
            <div className="flex flex-wrap items-center gap-2">
              {canManage && data.status !== "closed" ? (
                <Button asChild variant="outline" leftIcon={<Pencil />}>
                  <Link to={`/app/links/${data.id}/edit`}>Edit</Link>
                </Button>
              ) : null}
              {canDelete ? (
                <Button variant="outline" leftIcon={<Trash2 />} className="text-danger-fg hover:bg-danger-soft" onClick={() => setDeleteOpen(true)} data-testid="link-delete-permanently">
                  Delete link…
                </Button>
              ) : null}
            </div>
          ) : null
        }
      />
      {canDelete ? <DeleteLinkDialog link={data} open={deleteOpen} onOpenChange={setDeleteOpen} /> : null}

      {data.warnings.includes("job_not_open") ? (
        <Callout tone="warning" className="mb-5" title="The job isn't open">
          Nobody new can register and next rounds don't unlock automatically until you reopen{" "}
          <Link to={`/app/jobs/${data.job.id}`} className="font-medium underline">
            {data.job.title}
          </Link>
          .
        </Callout>
      ) : null}
      {data.warnings.includes("round_missing") ? (
        <Callout tone="warning" className="mb-5" title="A round of this link no longer exists">
          Candidates skip it. Edit the link to choose the rounds again.
        </Callout>
      ) : null}

      <StatGroup columns={4} className="mb-6">
        <StatCard bare label="Registered" value={formatNumber(c.registrations)} hint={data.max_registrations ? `of ${formatNumber(data.max_registrations)} places` : "No limit"} />
        <StatCard bare label="In progress" value={formatNumber(c.in_progress)} hint="Ready to start or taking a round" />
        <StatCard bare label="Awaiting review" value={formatNumber(c.awaiting_review)} hint="Waiting for a person" />
        <StatCard bare label="Completed" value={formatNumber(c.completed)} hint={c.flagged ? `${formatNumber(c.flagged)} with signals to review` : "Every round done"} />
      </StatGroup>

      <Tabs
        value={tab}
        onValueChange={(next) => {
          const p = new URLSearchParams(params);
          if (next === "registrations") p.delete("tab");
          else p.set("tab", next);
          setParams(p, { replace: true });
        }}
      >
        <TabsList aria-label="Link sections">
          <TabsTrigger value="registrations" data-testid="link-tab-registrations">
            Registrations <span className="text-xs text-fg-subtle tabular-nums">{formatNumber(c.registrations)}</span>
          </TabsTrigger>
          <TabsTrigger value="share" data-testid="link-tab-share">
            Share
          </TabsTrigger>
          <TabsTrigger value="settings" data-testid="link-tab-settings">
            Settings
          </TabsTrigger>
        </TabsList>
        <TabsContent value="registrations">
          <Card className="overflow-hidden">
            <RegistrationsTable link={data} canManage={canManage} canReviewFlags={canReviewFlags} />
          </Card>
        </TabsContent>
        <TabsContent value="share">
          <ShareLinkCard link={data} canManage={canManage} />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsSummary link={data} canManage={canManage} />
        </TabsContent>
      </Tabs>
      <AccessCodeDialog code={accessCode} onOpenChange={(open) => !open && setAccessCode(null)} />
    </PageContainer>
  );
}
