/**
 * Server-Sent Events client for `GET /api/v1/sessions/{id}/live`.
 *
 * EventSource sends the same-origin auth cookies but cannot read HTTP status
 * codes, so when the stream errors we refresh the session once (an expired
 * access token is the usual cause) and reconnect with capped backoff. When
 * the interview has ended the stream is closed for good. Polling in the
 * session queries keeps the monitor accurate even if SSE never connects.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useReducer, useRef, useState } from "react";

import { api, refreshSession } from "@/lib/api";
import { queryKeys } from "@/lib/query";

import { initialLiveState, LIVE_EVENT_NAMES, liveReducer, parseLiveMessage } from "../lib/liveState";
import type { LiveConnectionState, LiveEvent, LiveState } from "./types";

export interface UseLiveSessionResult {
  state: LiveState;
  connection: LiveConnectionState;
  /** Consecutive failed connection attempts. */
  attempts: number;
}

const MAX_BACKOFF_MS = 15_000;

export function useLiveSession(sessionId: string, { enabled = true }: { enabled?: boolean } = {}): UseLiveSessionResult {
  const [state, dispatch] = useReducer(liveReducer, initialLiveState);
  const [connection, setConnection] = useState<LiveConnectionState>(enabled ? "connecting" : "idle");
  const [attempts, setAttempts] = useState(0);
  const endedRef = useRef(false);
  const client = useQueryClient();
  const invalidateTimer = useRef<number | null>(null);

  // Refresh stored session data (debounced) whenever the stream says it changed.
  const lastVersion = useRef(0);
  useEffect(() => {
    if (state.dataVersion === lastVersion.current) return;
    lastVersion.current = state.dataVersion;
    if (invalidateTimer.current) window.clearTimeout(invalidateTimer.current);
    invalidateTimer.current = window.setTimeout(() => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions.detail(sessionId) });
    }, 600);
  }, [state.dataVersion, client, sessionId]);

  useEffect(() => {
    endedRef.current = state.ended;
  }, [state.ended]);

  useEffect(() => {
    if (!enabled || !sessionId) {
      setConnection("idle");
      return;
    }
    if (typeof EventSource === "undefined") {
      setConnection("unsupported");
      return;
    }
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let disposed = false;
    let failures = 0;

    const handle = (name: string) => (message: MessageEvent<string>) => {
      const event: LiveEvent | null = parseLiveMessage(name, message.data);
      if (event) dispatch(event);
    };

    const connect = () => {
      if (disposed) return;
      setConnection(failures === 0 ? "connecting" : "reconnecting");
      source = new EventSource(api.url(`/sessions/${sessionId}/live`));
      source.onopen = () => {
        failures = 0;
        setAttempts(0);
        setConnection("open");
      };
      source.onmessage = handle("message");
      for (const name of LIVE_EVENT_NAMES) source.addEventListener(name, handle(name) as EventListener);
      source.onerror = () => {
        source?.close();
        source = null;
        if (disposed) return;
        if (endedRef.current) {
          setConnection("closed");
          return;
        }
        failures += 1;
        setAttempts(failures);
        setConnection("reconnecting");
        const delay = Math.min(MAX_BACKOFF_MS, 1_000 * 2 ** Math.min(failures - 1, 4));
        const retry = () => {
          retryTimer = window.setTimeout(connect, delay);
        };
        // The first failure of an outage may be an expired access token.
        if (failures === 1 || failures % 4 === 0) void refreshSession().finally(retry);
        else retry();
      };
    };

    connect();
    return () => {
      disposed = true;
      source?.close();
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, [sessionId, enabled]);

  useEffect(() => {
    if (state.ended && connection !== "closed" && connection !== "idle") setConnection("closed");
  }, [state.ended, connection]);

  useEffect(
    () => () => {
      if (invalidateTimer.current) window.clearTimeout(invalidateTimer.current);
    },
    [],
  );

  return { state, connection, attempts };
}
