import { CloudOff } from "lucide-react";
import { Outlet } from "react-router";

import { AppSplash, FullPageMessage } from "@/components/layout/FullPageStates";
import { StaffShell } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { RequireAuth, RequirePermission, useFeature } from "@/lib/auth";
import type { Permission } from "@/lib/types";

import { ForbiddenPanel, NotFoundPanel } from "./NotFound";

/** /app/*: signed-in staff only, inside the staff shell. */
export function StaffArea() {
  return (
    <RequireAuth
      loadingFallback={<AppSplash />}
      errorFallback={(error, retry) => (
        <FullPageMessage
          icon={<CloudOff />}
          title="Can't reach Swayam"
          description={error.message}
          actions={<Button onClick={retry}>Try again</Button>}
        >
          {error.requestId ? <p className="mt-4 text-xs text-fg-subtle">Request ID {error.requestId}</p> : null}
        </FullPageMessage>
      )}
    >
      <StaffShell />
    </RequireAuth>
  );
}

/** Pathless route element that renders child routes only for holders of `permission`. */
export function PermissionGate({ permission }: { permission: Permission }) {
  return <RequirePermission permission={permission} fallback={<ForbiddenPanel />} />;
}

/** Pages of a plan feature the organisation does not have look like they do not exist (G8). */
export function FeatureGate({ feature }: { feature: string }) {
  return useFeature(feature) ? <Outlet /> : <NotFoundPanel />;
}
