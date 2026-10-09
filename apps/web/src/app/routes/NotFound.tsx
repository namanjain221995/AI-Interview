import { Compass, Lock } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { FullPageMessage } from "@/components/layout/FullPageStates";
import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuthStore } from "@/lib/auth";
import { useDocumentTitle } from "@/lib/hooks";
import { ROLE_LABELS } from "@/lib/labels";

export function NotFoundPage() {
  useDocumentTitle("Page not found");
  const navigate = useNavigate();
  const signedIn = useAuthStore((s) => s.status === "authenticated");
  return (
    <FullPageMessage
      icon={<Compass />}
      eyebrow="404"
      title="This page doesn't exist"
      description="The link may be outdated or mistyped. Candidates should open the exact link from their invitation email."
      actions={
        <>
          <Button asChild>
            <Link to={signedIn ? "/app/dashboard" : "/login"}>{signedIn ? "Go to dashboard" : "Go to sign in"}</Link>
          </Button>
          <Button variant="outline" onClick={() => navigate(-1)}>
            Go back
          </Button>
        </>
      }
    />
  );
}

/** Not-found state rendered inside a shell (the navigation stays usable). */
export function NotFoundPanel({ homePath = "/app/dashboard", homeLabel = "Go to dashboard" }: { homePath?: string; homeLabel?: string }) {
  useDocumentTitle("Page not found");
  return (
    <PageContainer>
      <EmptyState
        bordered
        icon={<Compass />}
        title="This page doesn't exist"
        description="The link may be outdated, or the item was removed."
        actions={
          <Button variant="outline" asChild>
            <Link to={homePath}>{homeLabel}</Link>
          </Button>
        }
      />
    </PageContainer>
  );
}

/** Shown when the signed-in role lacks the permission a page needs. */
export function ForbiddenPanel() {
  useDocumentTitle("No access");
  const role = useAuthStore((s) => s.user?.role);
  return (
    <PageContainer>
      <EmptyState
        bordered
        icon={<Lock />}
        title="You don't have access to this page"
        description={`${role ? `The ${ROLE_LABELS[role]} role` : "Your role"} can't open this page. Ask a workspace admin if you need access.`}
        actions={
          <Button variant="outline" asChild>
            <Link to="/app/dashboard">Go to dashboard</Link>
          </Button>
        }
      />
    </PageContainer>
  );
}
