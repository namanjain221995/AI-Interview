import { Link2, Plus } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { usePermission } from "@/lib/auth";

import { useLinks } from "../api";
import { LinksTable } from "./LinksTable";

export interface JobLinksPanelProps {
  jobId: string;
  /** Closed and archived jobs can't get new links. */
  jobOpen: boolean;
}

/** Job detail "Links" tab: the job's open interview links and a create action. */
export function JobLinksPanel({ jobId, jobOpen }: JobLinksPanelProps) {
  const canInvite = usePermission("interviews:invite");
  const links = useLinks({ job_id: jobId, limit: 100 });
  const create = canInvite ? (
    <Button asChild size="sm" leftIcon={<Plus />} disabled={!jobOpen} data-testid="job-link-create-button">
      <Link to={`/app/links/new?job_id=${encodeURIComponent(jobId)}`}>Create link for this job</Link>
    </Button>
  ) : null;
  return (
    <div className="grid grid-cols-1 gap-3" data-testid="job-links-panel">
      {links.data?.items.length && canInvite && jobOpen ? <div className="flex justify-end">{create}</div> : null}
      <Card className="overflow-hidden">
        <LinksTable
          rows={links.data?.items}
          loading={links.isLoading}
          fetching={links.isFetching && !links.isLoading}
          error={links.error}
          onRetry={() => void links.refetch()}
          hideJob
          empty={
            <EmptyState
              icon={<Link2 />}
              title="No links for this job yet"
              description={
                jobOpen
                  ? "Create one shareable link for this job's rounds. Everyone who registers gets their own interview, planned from their résumé."
                  : "Reopen the job to create a shareable link."
              }
              actions={jobOpen ? create : undefined}
            />
          }
        />
      </Card>
    </div>
  );
}
