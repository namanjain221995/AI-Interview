import { CloudOff, RefreshCw, TriangleAlert } from "lucide-react";
import { isRouteErrorResponse, Link, useRouteError } from "react-router";

import { FullPageMessage } from "@/components/layout/FullPageStates";
import { PageContainer } from "@/components/layout/StaffShell";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { isApiError } from "@/lib/api";

import { NotFoundPage, NotFoundPanel } from "./NotFound";

const CHUNK_ERROR = /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload CSS/i;

interface Described {
  kind: "not_found" | "new_version" | "offline" | "error";
  title: string;
  description: string;
  requestId?: string | null;
  detail?: string;
}

export function describeRouteError(error: unknown): Described {
  if (isRouteErrorResponse(error) && error.status === 404) {
    return { kind: "not_found", title: "Page not found", description: "" };
  }
  if (error instanceof Error && CHUNK_ERROR.test(error.message)) {
    return {
      kind: "new_version",
      title: "A newer version is available",
      description: "Swayam was updated while this tab was open. Reload to continue.",
    };
  }
  if (isApiError(error)) {
    if (error.isNotFound) return { kind: "not_found", title: "Not found", description: error.message };
    if (error.isNetworkError) {
      return { kind: "offline", title: "Can't reach the server", description: error.message, requestId: error.requestId };
    }
    return { kind: "error", title: "This page couldn't load", description: error.message, requestId: error.requestId };
  }
  return {
    kind: "error",
    title: "Something went wrong on this page",
    description: "Reload to try again. If it keeps happening, share the details below with your administrator.",
    detail: error instanceof Error ? error.message : isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : undefined,
  };
}

export interface RouteErrorBoundaryProps {
  /** `panel` renders inside a shell; `page` takes the whole viewport. */
  variant?: "page" | "panel";
}

export function RouteErrorBoundary({ variant = "page" }: RouteErrorBoundaryProps) {
  const error = useRouteError();
  const info = describeRouteError(error);

  if (info.kind === "not_found") return variant === "panel" ? <NotFoundPanel /> : <NotFoundPage />;

  const reload = () => window.location.reload();
  const icon = info.kind === "offline" ? <CloudOff /> : info.kind === "new_version" ? <RefreshCw /> : <TriangleAlert />;
  const meta =
    info.requestId || (import.meta.env.DEV && info.detail) ? (
      <div className="mt-4 grid gap-1 text-xs text-fg-subtle">
        {info.requestId ? <p>Request ID {info.requestId}</p> : null}
        {import.meta.env.DEV && info.detail ? <p className="font-mono break-all">{info.detail}</p> : null}
      </div>
    ) : null;

  if (variant === "panel") {
    return (
      <PageContainer>
        <EmptyState
          bordered
          icon={icon}
          title={info.title}
          description={info.description}
          actions={
            <>
              <Button onClick={reload}>Reload page</Button>
              <Button variant="outline" asChild>
                <Link to="/app/dashboard">Go to dashboard</Link>
              </Button>
            </>
          }
        >
          {meta}
        </EmptyState>
      </PageContainer>
    );
  }

  return (
    <FullPageMessage icon={icon} title={info.title} description={info.description} actions={<Button onClick={reload}>Reload page</Button>}>
      {meta}
    </FullPageMessage>
  );
}
