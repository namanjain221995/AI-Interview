import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorMessage, isApiError } from "@/lib/api";

declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: {
      /** Toast this message (or the error's message when `true`) if the query fails. */
      errorToast?: string | boolean;
    };
    mutationMeta: {
      /** Opt out of the automatic error toast (the form shows the error inline). */
      silentError?: boolean;
      /** Toast this message on success. */
      successToast?: string;
    };
  }
}

function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isApiError(error)) {
    // Client errors are final; only transient failures are worth retrying.
    if (error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) return false;
    if (error.code === "session_refresh_unavailable") return false;
  }
  return failureCount < 2;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        const meta = query.meta?.errorToast;
        // Background refetch failures of data already on screen get a toast;
        // first-load failures are rendered by the page itself.
        const hadData = query.state.data !== undefined;
        if (meta === false || (!meta && !hadData)) return;
        if (isApiError(error) && error.status === 401) return;
        toast.error(typeof meta === "string" ? meta : errorMessage(error), {
          id: `query-error:${query.queryHash}`,
          description: isApiError(error) && error.requestId ? `Request ID ${error.requestId}` : undefined,
        });
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silentError) return;
        if (isApiError(error) && error.status === 401) return;
        toast.error(errorMessage(error), {
          description: isApiError(error) && error.requestId ? `Request ID ${error.requestId}` : undefined,
        });
      },
      onSuccess: (_data, _variables, _context, mutation) => {
        if (mutation.meta?.successToast) toast.success(mutation.meta.successToast);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

/**
 * Query-key factory. Keys are hierarchical so `invalidateQueries({queryKey:
 * queryKeys.sessions.all})` refreshes every session list and detail.
 */
export const queryKeys = {
  auth: { me: ["auth", "me"] as const },
  jobs: {
    all: ["jobs"] as const,
    list: (params?: object) => ["jobs", "list", params ?? {}] as const,
    detail: (id: string) => ["jobs", "detail", id] as const,
    requirements: (id: string) => ["jobs", "detail", id, "requirements"] as const,
    rounds: (id: string) => ["jobs", "detail", id, "rounds"] as const,
  },
  candidates: {
    all: ["candidates"] as const,
    list: (params?: object) => ["candidates", "list", params ?? {}] as const,
    detail: (id: string) => ["candidates", "detail", id] as const,
    resumes: (id: string) => ["candidates", "detail", id, "resumes"] as const,
    profile: (id: string) => ["candidates", "detail", id, "profile"] as const,
    interviews: (id: string) => ["candidates", "detail", id, "interviews"] as const,
  },
  resumes: { detail: (id: string) => ["resumes", id] as const },
  interviews: {
    all: ["interviews"] as const,
    list: (params?: object) => ["interviews", "list", params ?? {}] as const,
    detail: (id: string) => ["interviews", "detail", id] as const,
  },
  sessions: {
    all: ["sessions"] as const,
    list: (params?: object) => ["sessions", "list", params ?? {}] as const,
    detail: (id: string) => ["sessions", "detail", id] as const,
    part: (id: string, part: "transcript" | "questions" | "evidence" | "scores" | "timeline" | "coding" | "integrity" | "reviews") =>
      ["sessions", "detail", id, part] as const,
    report: (id: string, type: "recruiter" | "candidate") => ["sessions", "detail", id, "report", type] as const,
  },
  integrity: {
    all: ["integrity"] as const,
    events: (params?: object) => ["integrity", "events", params ?? {}] as const,
  },
  catalog: {
    competencies: ["catalog", "competencies"] as const,
    roundTypes: ["catalog", "round-types"] as const,
    templates: (params?: object) => ["catalog", "templates", params ?? {}] as const,
    questionBank: (params?: object) => ["catalog", "question-bank", params ?? {}] as const,
    codingQuestions: (params?: object) => ["catalog", "coding-questions", params ?? {}] as const,
  },
  analytics: {
    overview: (params?: object) => ["analytics", "overview", params ?? {}] as const,
    competencies: (params?: object) => ["analytics", "competencies", params ?? {}] as const,
    funnel: (params?: object) => ["analytics", "funnel", params ?? {}] as const,
    modelUsage: (params?: object) => ["analytics", "model-usage", params ?? {}] as const,
  },
  admin: {
    users: (params?: object) => ["admin", "users", params ?? {}] as const,
    settings: ["admin", "settings"] as const,
    auditLogs: (params?: object) => ["admin", "audit-logs", params ?? {}] as const,
    health: ["admin", "health"] as const,
  },
  portal: {
    me: ["portal", "me"] as const,
    interview: (id: string) => ["portal", "interview", id] as const,
    disclosure: (id: string) => ["portal", "interview", id, "disclosure"] as const,
    resume: (id: string) => ["portal", "interview", id, "resume"] as const,
    session: (id: string) => ["portal", "session", id] as const,
    report: (id: string) => ["portal", "session", id, "report"] as const,
  },
} as const;
