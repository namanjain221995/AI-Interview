import { useEffect } from "react";

import { endPortalSession } from "./api";
import type { SessionOutcome } from "./reportAccess";

/**
 * "End interview" pressed while the network was down (S6 review): the room's REST call may have failed outright,
 * so the request is remembered for this tab and the complete page sends it again (idempotent on the server)
 * until the session has closed. Without it the session waited for the abandonment sweep and was recorded as a
 * disconnect instead of the candidate's own ending.
 */
const key = (sessionId: string) => `interview:end-requested:${sessionId}`;
const RETRY_MS = 15_000;

export function rememberEndRequest(sessionId: string, reason: string): void {
  try {
    sessionStorage.setItem(key(sessionId), reason);
  } catch {
    /* storage unavailable: the request is still sent once */
  }
}

function pendingEndRequest(sessionId: string): string | null {
  try {
    return sessionStorage.getItem(key(sessionId));
  } catch {
    return null;
  }
}

function forgetEndRequest(sessionId: string): void {
  try {
    sessionStorage.removeItem(key(sessionId));
  } catch {
    /* nothing to forget */
  }
}

/** On the complete page: re-send a remembered end request while the session is still open. */
export function useRetryEndRequest(sessionId: string | null, outcome: SessionOutcome): void {
  useEffect(() => {
    if (!sessionId) return;
    if (outcome !== "wrapping_up") {
      if (outcome !== "unknown") forgetEndRequest(sessionId); // closed (or never started): nothing left to send
      return;
    }
    const reason = pendingEndRequest(sessionId);
    if (!reason) return;
    const send = () => void endPortalSession(sessionId, reason).catch(() => undefined);
    send();
    const timer = window.setInterval(send, RETRY_MS);
    window.addEventListener("online", send);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("online", send);
    };
  }, [sessionId, outcome]);
}
