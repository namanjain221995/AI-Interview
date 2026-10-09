import { ClipboardX, ServerOff } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Callout } from "@/components/ui/Callout";
import { EmptyState } from "@/components/ui/EmptyState";
import { errorMessage, isApiError } from "@/lib/api";

import { isIntakeDisabled, isIntakeUnavailable } from "../api";
import { DISABLED_MESSAGE } from "../labels";

/** One empty state for the feature flag (design 11.1): every intake page uses it. */
export function IntakeDisabledState({ className }: { className?: string }) {
  return (
    <EmptyState
      bordered
      className={className}
      icon={<ClipboardX />}
      title={DISABLED_MESSAGE}
      description="An administrator can turn them on in the platform configuration. Interviews that already started finish normally."
    >
      <span data-testid="intake-disabled" />
    </EmptyState>
  );
}

/**
 * Page-level failure: the disabled flag, a server without the intake routes, or a
 * plain error with a retry.
 */
export function IntakeLoadError({ error, onRetry, title = "Couldn't load intake interviews", notFound }: { error: unknown; onRetry?: () => void; title?: string; notFound?: ReactNode }) {
  if (isIntakeDisabled(error)) return <IntakeDisabledState />;
  if (notFound && isApiError(error) && error.status === 404) return <>{notFound}</>;
  if (isIntakeUnavailable(error)) {
    return (
      <EmptyState
        bordered
        icon={<ServerOff />}
        title="Intake interviews aren't available on this server yet"
        description="The server didn't recognise the intake endpoints. Ask an administrator to update the platform."
      />
    );
  }
  return (
    <Callout
      tone="danger"
      title={title}
      actions={
        onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      {errorMessage(error)}
      {isApiError(error) && error.requestId ? <span className="mt-1 block text-xs">Request ID {error.requestId}</span> : null}
    </Callout>
  );
}
