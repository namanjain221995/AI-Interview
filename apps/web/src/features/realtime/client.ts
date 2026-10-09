/**
 * RealtimeClient: the browser side of the interview WebSocket
 * (/ws/sessions/{id}?ticket=..., protocol in lib/realtime/protocol.ts).
 *
 * - Every (re)connect fetches a fresh single-use ticket.
 * - The first message on a socket is `hello` with `last_seq`; the server
 *   replays buffered JSON after that seq. Messages at or below the last seen
 *   seq are dropped as duplicates; seq 0 (unsequenced) is always delivered.
 * - Heartbeats every few seconds; a socket with no inbound traffic for
 *   `staleAfterMs` is treated as dead (browsers can take minutes to notice).
 * - Drops reconnect with exponential backoff + jitter. Control messages sent
 *   while disconnected are queued (bounded, code.update coalesced) and flushed
 *   after `hello`; transient messages (audio, playback, VAD) are dropped.
 */
import { backoffDelay, type BackoffOptions } from "@/lib/realtime/backoff";
import { parseServerMessage } from "@/lib/realtime/parse";
import { PROTOCOL_VERSION, type ClientInfo, type ClientMessage, type ServerMessage } from "@/lib/realtime/protocol";

import type { ConnectionStatus } from "./store";

export interface WebSocketLike {
  binaryType: BinaryType;
  readonly readyState: number;
  readonly bufferedAmount: number;
  onopen: ((ev: Event) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  send(data: string | ArrayBufferLike | ArrayBufferView): void;
  close(code?: number, reason?: string): void;
}

export interface TicketResponse {
  ticket: string;
  ws_url: string;
}

export interface StatusInfo {
  attempt: number;
  reason: string | null;
}

export interface RealtimeClientOptions {
  sessionId: string;
  fetchTicket: () => Promise<TicketResponse>;
  clientInfo: () => ClientInfo;
  createSocket?: (url: string) => WebSocketLike;
  /** Defaults to same-origin resolution against `location`. */
  resolveUrl?: (wsUrl: string, ticket: string, sessionId: string) => string;
  heartbeatIntervalMs?: number;
  staleAfterMs?: number;
  connectTimeoutMs?: number;
  backoff?: BackoffOptions;
  maxReconnectAttempts?: number;
  maxQueue?: number;
  /** Ticket errors that must not be retried (e.g. the session ended). */
  isFatalTicketError?: (error: unknown) => boolean;
  random?: () => number;
  now?: () => number;
  /** Listen to window online/offline (default true in browsers). */
  watchNetwork?: boolean;
  /** Seq already seen by an earlier connection of this page (e.g. the system-check probe). */
  initialLastSeq?: number;
}

type Listener<T extends unknown[]> = (...args: T) => void;

interface Events {
  message: [ServerMessage];
  binary: [ArrayBuffer];
  status: [ConnectionStatus, StatusInfo];
  rtt: [number];
}

const WS_OPEN = 1;
/**
 * Close codes after which reconnecting cannot help (app/realtime/close_codes.py):
 * 1000 session ended, 4003 forbidden origin, 4004 session not found, 4010
 * replaced by a newer connection (another tab), 4011 ended / reload refused,
 * 4013 too many reconnects, 4400 protocol violation. 1001/1006/1011/1012 and
 * 4008 (heartbeat timeout) are retried with backoff, 4029 (rate limited) with a
 * longer one, 4009 (active elsewhere) for about a minute while the old owner
 * hands over; 4001 (ticket rejected) is retried once with a fresh ticket.
 */
export const TERMINAL_CLOSE_CODES = new Set([1000, 4003, 4004, 4010, 4011, 4013, 4400]);
const UNAUTHORIZED_CLOSE = 4001;
/** One retry with a fresh ticket, then 4001 is final. */
const MAX_UNAUTHORIZED_RETRIES = 1;
const ACTIVE_ELSEWHERE_CLOSE = 4009;
const RATE_LIMITED_CLOSE = 4029;
/** 4009: the previous owner hands over within ~30 s; keep trying for a minute. */
const ACTIVE_ELSEWHERE_WINDOW_MS = 60_000;
const CLOSE_REASONS: Record<number, string> = {
  1000: "session_ended",
  4009: "session_active_elsewhere",
  4029: "rate_limited",
  4001: "unauthorized",
  4003: "forbidden_origin",
  4004: "session_not_found",
  4010: "replaced_by_new_connection",
  4011: "session_ended",
  4013: "too_many_reconnects",
  4400: "bad_request",
};
const MAX_BUFFERED_AUDIO_BYTES = 512 * 1024;

const QUEUEABLE = new Set<ClientMessage["type"]>([
  "control.start",
  "control.end_turn",
  "control.repeat",
  "control.end_interview",
  "answer.text",
  "code.update",
  "code.run",
  "code.submit",
  "code.hint_request",
  "code.language",
  "integrity.events",
  "client.error",
]);

/** Build the socket URL on the page's own origin (the edge proxies /ws), keeping the server's path + query. */
export function resolveSocketUrl(wsUrl: string, ticket: string, sessionId: string, base: Pick<Location, "protocol" | "host"> = location): string {
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  let path = `/ws/sessions/${encodeURIComponent(sessionId)}`;
  let search = "";
  if (wsUrl) {
    try {
      const parsed = new URL(wsUrl, `${base.protocol}//${base.host}`);
      path = parsed.pathname;
      search = parsed.search;
    } catch {
      /* fall back to the canonical path */
    }
  }
  const params = new URLSearchParams(search);
  if (!params.get("ticket")) params.set("ticket", ticket);
  return `${protocol}//${base.host}${path}?${params.toString()}`;
}

export class RealtimeClient {
  private readonly opts: Required<Omit<RealtimeClientOptions, "resolveUrl" | "isFatalTicketError" | "backoff" | "initialLastSeq">> &
    Pick<RealtimeClientOptions, "resolveUrl" | "isFatalTicketError" | "backoff" | "initialLastSeq">;
  private socket: WebSocketLike | null = null;
  private listeners: { [K in keyof Events]: Set<Listener<Events[K]>> } = {
    message: new Set(),
    binary: new Set(),
    status: new Set(),
    rtt: new Set(),
  };
  private _status: ConnectionStatus = "idle";
  private _lastSeq = 0;
  private attempt = 0;
  private queue: ClientMessage[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private lastInboundAt = 0;
  private firstMessageOnSocket = true;
  private intentionalClose = false;
  private ended = false;
  private connectToken = 0;
  private everConnected = false;
  private unauthorizedCloses = 0;
  private activeElsewhereSince: number | null = null;
  private readonly onOnline = () => {
    if (this._status === "reconnecting" || this._status === "offline") this.retryNow();
  };

  constructor(options: RealtimeClientOptions) {
    this.opts = {
      createSocket: (url: string) => new WebSocket(url) as unknown as WebSocketLike,
      heartbeatIntervalMs: 5000,
      staleAfterMs: 20_000,
      connectTimeoutMs: 10_000,
      maxReconnectAttempts: 60,
      maxQueue: 200,
      random: Math.random,
      now: () => (typeof performance !== "undefined" ? performance.now() : Date.now()),
      watchNetwork: typeof window !== "undefined",
      ...options,
    };
    this._lastSeq = Math.max(0, Math.floor(options.initialLastSeq ?? 0));
  }

  get status(): ConnectionStatus {
    return this._status;
  }

  get lastSeq(): number {
    return this._lastSeq;
  }

  get queuedCount(): number {
    return this.queue.length;
  }

  on<K extends keyof Events>(event: K, listener: Listener<Events[K]>): () => void {
    this.listeners[event].add(listener);
    return () => this.listeners[event].delete(listener);
  }

  private emit<K extends keyof Events>(event: K, ...args: Events[K]): void {
    for (const listener of [...this.listeners[event]]) {
      try {
        listener(...args);
      } catch (error) {
        console.error(`[realtime] ${event} listener failed`, error);
      }
    }
  }

  private setStatus(status: ConnectionStatus, reason: string | null = null): void {
    if (this._status === status && status !== "reconnecting") return;
    this._status = status;
    this.emit("status", status, { attempt: this.attempt, reason });
  }

  /** Open the connection (idempotent while connecting/connected). */
  connect(): void {
    if (this.ended) return;
    if (this._status === "connecting" || this._status === "connected" || this._status === "reconnecting") return;
    this.intentionalClose = false;
    if (this.opts.watchNetwork) window.addEventListener("online", this.onOnline);
    this.setStatus(this.everConnected ? "reconnecting" : "connecting");
    void this.open();
  }

  /** Skip the backoff wait (user pressed "Try again" or the network came back). */
  retryNow(): void {
    if (this.ended || this.intentionalClose) return;
    if (this._status === "connected") return;
    this.clearReconnect();
    this.attempt = 0;
    this.setStatus(this.everConnected ? "reconnecting" : "connecting");
    void this.open();
  }

  /** The session is over: close and never reconnect. */
  markEnded(): void {
    this.ended = true;
  }

  close(code = 1000, reason = "client closed"): void {
    this.intentionalClose = true;
    this.clearReconnect();
    this.stopHeartbeat();
    this.clearConnectTimer();
    if (this.opts.watchNetwork) window.removeEventListener("online", this.onOnline);
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      this.detach(socket);
      try {
        socket.close(code, reason);
      } catch {
        /* already closed */
      }
    }
    this.setStatus("closed");
  }

  /**
   * Send a JSON message. Returns true when written to an open socket; queueable
   * messages are kept for the next connection, others are dropped.
   */
  send(message: ClientMessage): boolean {
    if (this.socket && this._status === "connected" && this.socket.readyState === WS_OPEN) {
      try {
        this.socket.send(JSON.stringify(message));
        return true;
      } catch {
        /* fall through to queueing */
      }
    }
    if (QUEUEABLE.has(message.type) && !this.ended) this.enqueue(message);
    return false;
  }

  /** Send a binary audio frame; dropped while disconnected or under backpressure. */
  sendBinary(data: ArrayBuffer): boolean {
    const socket = this.socket;
    if (!socket || this._status !== "connected" || socket.readyState !== WS_OPEN) return false;
    if (socket.bufferedAmount > MAX_BUFFERED_AUDIO_BYTES) return false;
    try {
      socket.send(data);
      return true;
    } catch {
      return false;
    }
  }

  private enqueue(message: ClientMessage): void {
    if (message.type === "code.update") {
      this.queue = this.queue.filter((m) => !(m.type === "code.update" && m.coding_session_id === message.coding_session_id));
    }
    this.queue.push(message);
    if (this.queue.length > this.opts.maxQueue) {
      // Keep control decisions; shed the oldest integrity batches first.
      const index = this.queue.findIndex((m) => m.type === "integrity.events" || m.type === "client.error");
      this.queue.splice(index === -1 ? 0 : index, 1);
    }
  }

  private flushQueue(): void {
    const pending = this.queue;
    this.queue = [];
    for (const message of pending) {
      if (!this.send(message)) {
        // Socket dropped mid-flush: send() re-queued it; keep the rest in order.
        const rest = pending.slice(pending.indexOf(message) + 1);
        this.queue.push(...rest.filter((m) => QUEUEABLE.has(m.type)));
        return;
      }
    }
  }

  private async open(): Promise<void> {
    const token = ++this.connectToken;
    const previous = this.socket;
    if (previous) {
      this.detach(previous);
      this.socket = null;
      try {
        previous.close(4000, "superseded");
      } catch {
        /* ignore */
      }
    }
    let ticket: TicketResponse;
    try {
      ticket = await this.opts.fetchTicket();
    } catch (error) {
      if (token !== this.connectToken || this.intentionalClose) return;
      if (this.opts.isFatalTicketError?.(error)) {
        this.ended = true;
        this.setStatus("offline", "ticket_rejected");
        return;
      }
      this.scheduleReconnect("ticket_failed");
      return;
    }
    if (token !== this.connectToken || this.intentionalClose || this.ended) return;

    const url = (this.opts.resolveUrl ?? resolveSocketUrl)(ticket.ws_url, ticket.ticket, this.opts.sessionId);
    let socket: WebSocketLike;
    try {
      socket = this.opts.createSocket(url);
    } catch {
      this.scheduleReconnect("socket_failed");
      return;
    }
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.firstMessageOnSocket = true;
    this.clearConnectTimer();
    this.connectTimer = setTimeout(() => {
      if (this.socket === socket && socket.readyState !== WS_OPEN) this.dropSocket(socket, "connect_timeout");
    }, this.opts.connectTimeoutMs);

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.clearConnectTimer();
      this.lastInboundAt = this.opts.now();
      try {
        socket.send(JSON.stringify({ type: "hello", protocol: PROTOCOL_VERSION, last_seq: this._lastSeq, client: this.opts.clientInfo() }));
      } catch {
        this.dropSocket(socket, "hello_failed");
        return;
      }
      this.attempt = 0;
      this.everConnected = true;
      this.setStatus("connected");
      this.startHeartbeat();
      this.flushQueue();
    };
    socket.onmessage = (event: MessageEvent) => {
      if (this.socket !== socket) return;
      this.lastInboundAt = this.opts.now();
      const data: unknown = event.data;
      if (typeof data === "string") this.handleText(data);
      else if (data instanceof ArrayBuffer) this.emit("binary", data);
      else if (typeof Blob !== "undefined" && data instanceof Blob) void data.arrayBuffer().then((buf) => this.emit("binary", buf));
    };
    socket.onerror = () => {
      /* onclose follows and drives the reconnect */
    };
    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket) return;
      this.handleClose(event.code, event.reason);
    };
  }

  private handleText(raw: string): void {
    const message = parseServerMessage(raw);
    if (!message) return;
    this.unauthorizedCloses = 0;
    this.activeElsewhereSince = null;
    const first = this.firstMessageOnSocket;
    this.firstMessageOnSocket = false;
    const seq = message.seq;
    if (seq > 0) {
      if (seq <= this._lastSeq) {
        // A fresh runtime (server restart, lost outbox) restarts numbering with a state sync.
        if (!(first && message.type === "session.state")) return;
      }
      this._lastSeq = seq;
    }
    if (message.type === "heartbeat.ack" && typeof message.t === "number") {
      const rtt = this.opts.now() - message.t;
      if (rtt >= 0 && rtt < 60_000) this.emit("rtt", rtt);
    }
    if (message.type === "session.ended") this.ended = true;
    this.emit("message", message);
  }

  private handleClose(code: number, reason: string): void {
    const socket = this.socket;
    if (socket) this.detach(socket);
    this.socket = null;
    this.stopHeartbeat();
    this.clearConnectTimer();
    if (this.intentionalClose) {
      this.setStatus("closed");
      return;
    }
    if (this.ended) {
      this.setStatus("closed", reason || null);
      return;
    }
    const why = CLOSE_REASONS[code] ?? (reason || `closed_${code}`);
    if (TERMINAL_CLOSE_CODES.has(code) || (code === UNAUTHORIZED_CLOSE && this.unauthorizedCloses >= MAX_UNAUTHORIZED_RETRIES)) {
      this.ended = true;
      this.setStatus("offline", why);
      return;
    }
    if (code === UNAUTHORIZED_CLOSE) this.unauthorizedCloses += 1;
    if (code === ACTIVE_ELSEWHERE_CLOSE) {
      const now = this.opts.now();
      this.activeElsewhereSince ??= now;
      if (now - this.activeElsewhereSince > ACTIVE_ELSEWHERE_WINDOW_MS) {
        this.ended = true;
        this.setStatus("offline", why);
        return;
      }
      this.scheduleReconnect(why, 5000);
      return;
    }
    this.scheduleReconnect(why, code === RATE_LIMITED_CLOSE ? 5000 : 0);
  }

  private dropSocket(socket: WebSocketLike, reason: string): void {
    this.detach(socket);
    try {
      socket.close(4000, reason);
    } catch {
      /* ignore */
    }
    if (this.socket === socket) {
      this.socket = null;
      this.stopHeartbeat();
      this.clearConnectTimer();
      if (!this.intentionalClose && !this.ended) this.scheduleReconnect(reason);
    }
  }

  private detach(socket: WebSocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  }

  private scheduleReconnect(reason: string, minDelayMs = 0): void {
    this.clearReconnect();
    if (this.attempt >= this.opts.maxReconnectAttempts) {
      this.setStatus("offline", "gave_up");
      return;
    }
    const delay = Math.max(minDelayMs, backoffDelay(this.attempt, this.opts.backoff, this.opts.random));
    this.attempt += 1;
    this.setStatus(this.everConnected ? "reconnecting" : "connecting", reason);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.open();
    }, delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      const socket = this.socket;
      if (!socket || this._status !== "connected") return;
      if (this.opts.now() - this.lastInboundAt > this.opts.staleAfterMs) {
        this.dropSocket(socket, "stale");
        return;
      }
      try {
        socket.send(JSON.stringify({ type: "heartbeat", t: this.opts.now() }));
      } catch {
        this.dropSocket(socket, "heartbeat_failed");
      }
    }, this.opts.heartbeatIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private clearConnectTimer(): void {
    if (this.connectTimer) clearTimeout(this.connectTimer);
    this.connectTimer = null;
  }
}
