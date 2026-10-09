"""WebSocket close codes used by ``/ws/sessions/{session_id}``.

What the client does (``apps/web/src/features/realtime/client.ts``):

* reconnect with backoff, fresh ticket and ``hello.last_seq``: 1001, 1006,
  1011, 1012 and 4008 (heartbeat timeout);
* 4001 (ticket missing, used or expired): fetch a fresh ticket and retry
  once - a second 4001 is final;
* 4009 (another instance still owns the session): retry every few seconds
  for about a minute while the old owner hands over;
* 4029 (rate limited): retry after a longer backoff;
* final, never reconnect: 1000, 4003, 4004, 4010 (a newer connection - e.g.
  another tab - took over this session), 4011, 4013 and 4400.

An ``error`` message with the same ``code`` string precedes the close when the
socket was accepted.
"""

from __future__ import annotations

NORMAL = 1000  # session ended normally
GOING_AWAY = 1001  # server shutting down (reconnect)
PROTOCOL_ERROR = 1002
UNSUPPORTED_DATA = 1003  # e.g. unknown binary frame type
POLICY_VIOLATION = 1008
MESSAGE_TOO_BIG = 1009
INTERNAL_ERROR = 1011  # reconnect with backoff
SERVICE_RESTART = 1012  # instance restarting: reconnect

UNAUTHORIZED = 4001  # missing/invalid/expired ticket: fetch a new one and retry once
FORBIDDEN_ORIGIN = 4003
SESSION_NOT_FOUND = 4004
HEARTBEAT_TIMEOUT = 4008  # no client traffic for 3 heartbeat intervals: reconnect
ACTIVE_ELSEWHERE = 4009  # another instance owns the session lock: retry shortly
REPLACED = 4010  # a newer connection for the same session took over (do not reconnect this tab)
SESSION_ENDED = 4011  # session completed / aborted / not resumable
TOO_MANY_RECONNECTS = 4013  # integrity policy max_reconnects exceeded
RATE_LIMITED = 4029
BAD_REQUEST = 4400  # protocol violation (no hello, bad version, bad sample rate)

CLOSE_REASONS: dict[int, str] = {
    NORMAL: "session_ended",
    GOING_AWAY: "server_shutdown",
    UNSUPPORTED_DATA: "unsupported_data",
    MESSAGE_TOO_BIG: "message_too_big",
    INTERNAL_ERROR: "internal_error",
    SERVICE_RESTART: "service_restart",
    UNAUTHORIZED: "unauthorized",
    FORBIDDEN_ORIGIN: "forbidden_origin",
    SESSION_NOT_FOUND: "session_not_found",
    HEARTBEAT_TIMEOUT: "heartbeat_timeout",
    ACTIVE_ELSEWHERE: "session_active_elsewhere",
    REPLACED: "replaced_by_new_connection",
    SESSION_ENDED: "session_ended",
    TOO_MANY_RECONNECTS: "too_many_reconnects",
    RATE_LIMITED: "rate_limited",
    BAD_REQUEST: "bad_request",
}


def reason_for(code: int) -> str:
    return CLOSE_REASONS.get(code, "closed")
