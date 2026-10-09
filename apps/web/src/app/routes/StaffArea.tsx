import { CloudOff } from "lucide-react";

import { AppSplash, FullPageMessage } from "@/components/layout/FullPageStates";
import { StaffShell } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { RequireAuth, RequirePermission } from "@/lib/auth";
import type { Permission } from "@/lib/types";

import { ForbiddenPanel } from "./NotFound";

/** /app/*: signed-in staff only, inside the staff shell. */
export function StaffArea() {
  return (
    <RequireAuth
      loadingFallback={<AppSplash />}
      errorFallback={(error, retry) => (
        <FullPageMessage
          icon={<CloudOff />}
          title="Can't reach Interview Studio"
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
