import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { ThemeProvider } from "@/components/theme/ThemeProvider";
import { ConfirmDialogHost } from "@/components/ui/AlertDialog";
import { Toaster } from "@/components/ui/Toaster";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { useAuthStore } from "@/lib/auth";
import { createQueryClient } from "@/lib/query";

let browserQueryClient: QueryClient | undefined;

/** One QueryClient per browser tab (tests create their own). */
export function getQueryClient(): QueryClient {
  browserQueryClient ??= createQueryClient();
  return browserQueryClient;
}

/** Drops all cached server data when the staff session ends, so nothing leaks to the next account. */
function useClearCacheOnSignOut(client: QueryClient) {
  useEffect(
    () =>
      useAuthStore.subscribe((state, previous) => {
        if (previous.status === "authenticated" && state.status !== "authenticated") client.clear();
      }),
    [client],
  );
}

export function AppProviders({ children, queryClient }: { children: ReactNode; queryClient?: QueryClient }) {
  const [client] = useState(() => queryClient ?? getQueryClient());
  useClearCacheOnSignOut(client);
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider delayDuration={350} skipDelayDuration={200}>
          {children}
          <ConfirmDialogHost />
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
