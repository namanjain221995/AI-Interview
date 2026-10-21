import { isApiError } from "@/lib/api";

export interface LoginErrorView {
  title: string;
  message: string;
  /** Seconds the form should stay disabled (rate limit / lockout). */
  retryAfterSeconds?: number;
  requestId?: string | null;
  tone: "danger" | "warning";
}

function minutesText(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.ceil(seconds))} seconds`;
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

function lockSeconds(details: Record<string, unknown>, retryAfter: number | null): number | null {
  if (retryAfter !== null) return retryAfter;
  const until = details.locked_until;
  if (typeof until === "string") {
    const ms = Date.parse(until) - Date.now();
    if (Number.isFinite(ms) && ms > 0) return Math.ceil(ms / 1000);
  }
  return null;
}

/** Maps a failed sign-in to plain, actionable copy. Never reveals whether the email exists. */
export function describeLoginError(error: unknown): LoginErrorView {
  if (!isApiError(error)) {
    return { tone: "danger", title: "Sign-in failed", message: "Something went wrong. Try again." };
  }
  const code = error.code;
  const details = error.details;

  if (error.isNetworkError || code === "timeout") {
    return { tone: "danger", title: "Can't reach Swayam", message: "Check your connection and try again." };
  }

  if (error.status === 423 || code === "account_locked" || code === "locked") {
    const seconds = lockSeconds(details, error.retryAfterSeconds);
    return {
      tone: "warning",
      title: "Account temporarily locked",
      message: `Too many failed attempts. Try again in ${seconds ? minutesText(seconds) : "a few minutes"}, or ask a workspace admin to unlock your account.`,
      retryAfterSeconds: seconds ?? undefined,
    };
  }

  if (error.isRateLimited) {
    const seconds = error.retryAfterSeconds ?? 60;
    return {
      tone: "warning",
      title: "Too many attempts",
      message: `Sign-in is paused for this network. Try again in ${minutesText(seconds)}.`,
      retryAfterSeconds: seconds,
    };
  }

  if (code === "account_disabled" || code === "account_inactive" || code === "user_inactive") {
    return { tone: "danger", title: "Account deactivated", message: "Ask a workspace admin to restore your access." };
  }

  if (error.status === 401 || error.status === 400 || code === "invalid_credentials") {
    const remaining = typeof details.remaining_attempts === "number" ? details.remaining_attempts : null;
    return {
      tone: "danger",
      title: "Email or password is incorrect",
      message:
        remaining !== null && remaining <= 3
          ? `Check them and try again. ${remaining === 0 ? "The next failed attempt locks the account." : `${remaining} ${remaining === 1 ? "attempt" : "attempts"} left before the account is locked.`}`
          : "Check them and try again. Passwords are case-sensitive.",
    };
  }

  if (error.isServerError) {
    return {
      tone: "danger",
      title: "Sign-in is unavailable right now",
      message: "Try again in a moment.",
      requestId: error.requestId,
    };
  }

  return { tone: "danger", title: "Sign-in failed", message: error.message, requestId: error.requestId };
}
