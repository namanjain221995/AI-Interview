import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerMessage } from "@/lib/realtime/protocol";

import { RealtimeClient, resolveSocketUrl, type WebSocketLike } from "../client";

class FakeSocket implements WebSocketLike {
  static instances: FakeSocket[] = [];
  binaryType: BinaryType = "blob";
  readyState = 0;
  bufferedAmount = 0;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  sent: Array<string | ArrayBuffer> = [];
  closed: { code?: number; reason?: string } | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string | ArrayBufferLike | ArrayBufferView): void {
    if (this.readyState !== 1) throw new Error("not open");
    this.sent.push(typeof data === "string" ? data : (data as ArrayBuffer));
  }
  close(code?: number, reason?: string): void {
    this.closed = { code, reason };
    this.readyState = 3;
  }
  // test helpers
  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  receive(msg: Partial<ServerMessage> & { type: string; seq?: number }): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(msg) }));
  }
  receiveBinary(buf: ArrayBuffer): void {
    this.onmessage?.(new MessageEvent("message", { data: buf }));
  }
  drop(code = 1006, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason } as CloseEvent);
  }
  json(): Array<Record<string, unknown>> {
    return this.sent.filter((d): d is string => typeof d === "string").map((d) => JSON.parse(d) as Record<string, unknown>);
  }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1]!;
const flush = () => vi.advanceTimersByTimeAsync(0);

function makeClient(overrides: Partial<ConstructorParameters<typeof RealtimeClient>[0]> = {}) {
  let tickets = 0;
  const fetchTicket = vi.fn(async () => ({ ticket: `t${++tickets}`, ws_url: "/ws/sessions/s1" }));
  let now = 0;
  const client = new RealtimeClient({
    sessionId: "s1",
    fetchTicket,
    clientInfo: () => ({ webgl: true, avatar_mode: "3d", tts_mode: "server", sample_rate: 16000 }),
    createSocket: (url) => new FakeSocket(url),
    resolveUrl: (wsUrl, ticket) => `ws://test${wsUrl}?ticket=${ticket}`,
    random: () => 0.5,
    now: () => now,
    watchNetwork: false,
    heartbeatIntervalMs: 1000,
    staleAfterMs: 5000,
    backoff: { baseMs: 100, maxMs: 1000, jitter: 0 },
    ...overrides,
  });
  return { client, fetchTicket, setNow: (v: number) => (now = v) };
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
});
afterEach(() => {
  vi.useRealTimers();
});

describe("RealtimeClient", () => {
  it("connects with a fresh ticket and says hello with last_seq", async () => {
    const { client, fetchTicket } = makeClient();
    const statuses: string[] = [];
    client.on("status", (s) => statuses.push(s));
    client.connect();
    await flush();
    expect(fetchTicket).toHaveBeenCalledTimes(1);
    expect(latest().url).toBe("ws://test/ws/sessions/s1?ticket=t1");
    latest().open();
    expect(latest().binaryType).toBe("arraybuffer");
    expect(latest().json()[0]).toMatchObject({ type: "hello", protocol: 1, last_seq: 0, client: { sample_rate: 16000 } });
    expect(statuses).toEqual(["connecting", "connected"]);
  });

  it("dispatches messages in order and drops replayed duplicates", async () => {
    const { client } = makeClient();
    const seen: number[] = [];
    client.on("message", (m) => seen.push(m.seq));
    client.connect();
    await flush();
    latest().open();
    latest().receive({ type: "turn.state", state: "listening", seq: 1 });
    latest().receive({ type: "turn.state", state: "processing", seq: 2 });
    latest().receive({ type: "turn.state", state: "processing", seq: 2 });
    latest().receive({ type: "heartbeat.ack", t: 0, server_time: 1, seq: 0 });
    expect(seen).toEqual([1, 2, 0]);
    expect(client.lastSeq).toBe(2);
  });

  it("reconnects with backoff, a new ticket and hello.last_seq, then accepts the replay", async () => {
    const { client, fetchTicket } = makeClient();
    const seen: Array<[string, number]> = [];
    const statuses: string[] = [];
    client.on("message", (m) => seen.push([m.type, m.seq]));
    client.on("status", (s) => statuses.push(s));
    client.connect();
    await flush();
    latest().open();
    latest().receive({ type: "turn.state", state: "listening", seq: 5 });
    latest().drop(1006);
    expect(client.status).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(99);
    expect(fetchTicket).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
    expect(latest().url).toContain("ticket=t2");
    latest().open();
    expect(latest().json()[0]).toMatchObject({ type: "hello", last_seq: 5 });
    // Replay after last_seq, plus an old message the server sent twice.
    latest().receive({ type: "turn.state", state: "processing", seq: 5 });
    latest().receive({ type: "turn.state", state: "ai_speaking", seq: 6 });
    expect(seen).toEqual([
      ["turn.state", 5],
      ["turn.state", 6],
    ]);
    expect(statuses).toEqual(["connecting", "connected", "reconnecting", "connected"]);
  });

  it("backs off exponentially across repeated failures", async () => {
    const { client, fetchTicket } = makeClient();
    client.connect();
    await flush();
    latest().drop(1006); // never opened
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
    latest().drop(1006);
    await vi.advanceTimersByTimeAsync(199);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchTicket).toHaveBeenCalledTimes(3);
    latest().drop(1006);
    await vi.advanceTimersByTimeAsync(400);
    expect(fetchTicket).toHaveBeenCalledTimes(4);
  });

  it("accepts a renumbered stream after a server restart (fresh session.state)", async () => {
    const { client } = makeClient();
    const seen: number[] = [];
    client.on("message", (m) => seen.push(m.seq));
    client.connect();
    await flush();
    latest().open();
    latest().receive({ type: "turn.state", state: "listening", seq: 40 });
    latest().drop();
    await vi.advanceTimersByTimeAsync(100);
    latest().open();
    latest().receive({ type: "session.state", session_id: "s1", status: "live", turn_state: "listening", seq: 1 } as never);
    latest().receive({ type: "turn.state", state: "processing", seq: 2 });
    expect(seen).toEqual([40, 1, 2]);
  });

  it("queues control messages while offline and flushes them after hello, coalescing code updates", async () => {
    const { client } = makeClient();
    client.connect();
    await flush();
    expect(client.send({ type: "control.repeat" })).toBe(false);
    client.send({ type: "code.update", coding_session_id: "c", language: "python", code: "a", version: 1 });
    client.send({ type: "code.update", coding_session_id: "c", language: "python", code: "ab", version: 2 });
    client.send({ type: "vad.local", speaking: true }); // transient: dropped
    client.send({ type: "playback.progress", utterance_id: "u", played_ms: 10 }); // transient: dropped
    expect(client.queuedCount).toBe(2);
    latest().open();
    const sent = latest().json().map((m) => m.type);
    expect(sent).toEqual(["hello", "control.repeat", "code.update"]);
    expect(latest().json()[2]).toMatchObject({ code: "ab", version: 2 });
    expect(client.queuedCount).toBe(0);
  });

  it("sends audio only while connected", async () => {
    const { client } = makeClient();
    const frame = new ArrayBuffer(16);
    client.connect();
    await flush();
    expect(client.sendBinary(frame)).toBe(false);
    latest().open();
    expect(client.sendBinary(frame)).toBe(true);
    latest().bufferedAmount = 10 * 1024 * 1024;
    expect(client.sendBinary(frame)).toBe(false);
  });

  it("passes binary frames through", async () => {
    const { client } = makeClient();
    const got: ArrayBuffer[] = [];
    client.on("binary", (b) => got.push(b));
    client.connect();
    await flush();
    latest().open();
    latest().receiveBinary(new ArrayBuffer(12));
    expect(got).toHaveLength(1);
  });

  it("sends heartbeats, measures RTT and treats a silent socket as dead", async () => {
    const { client, setNow, fetchTicket } = makeClient();
    const rtts: number[] = [];
    client.on("rtt", (r) => rtts.push(r));
    client.connect();
    await flush();
    const socket = latest();
    socket.open();
    setNow(1000);
    await vi.advanceTimersByTimeAsync(1000);
    const beat = socket.json().find((m) => m.type === "heartbeat");
    expect(beat).toMatchObject({ t: 1000 });
    setNow(1042);
    socket.receive({ type: "heartbeat.ack", t: 1000, server_time: 5, seq: 0 });
    expect(rtts).toEqual([42]);
    setNow(7000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(socket.closed?.reason).toBe("stale");
    expect(client.status).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
  });

  it("stops on terminal close codes and fatal ticket errors", async () => {
    const terminal = { ...makeClient(), statuses: [] as Array<[string, string | null]> };
    terminal.client.on("status", (st, info) => terminal.statuses.push([st, info.reason]));
    terminal.client.connect();
    await flush();
    latest().open();
    latest().drop(4010, "");
    expect(terminal.client.status).toBe("offline");
    expect(terminal.statuses.at(-1)).toEqual(["offline", "replaced_by_new_connection"]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(terminal.fetchTicket).toHaveBeenCalledTimes(1);

    const fatal = makeClient({
      fetchTicket: vi.fn(async () => {
        throw Object.assign(new Error("gone"), { status: 410 });
      }),
      isFatalTicketError: (e) => (e as { status?: number }).status === 410,
    });
    fatal.client.connect();
    await flush();
    expect(fatal.client.status).toBe("offline");
  });

  it("retries heartbeat timeouts, waits longer for 'active elsewhere' and rate limits, and retries a rejected ticket once", async () => {
    const { client, fetchTicket } = makeClient();
    client.connect();
    await flush();
    latest().open();
    latest().drop(4008, "heartbeat_timeout");
    expect(client.status).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(100);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
    latest().open();
    latest().drop(4009, "session_active_elsewhere");
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetchTicket).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchTicket).toHaveBeenCalledTimes(3);
    latest().drop(4029, "rate_limited");
    await vi.advanceTimersByTimeAsync(4999);
    expect(fetchTicket).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchTicket).toHaveBeenCalledTimes(4);
    latest().drop(4001);
    expect(client.status).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(1000);
    latest().drop(4001);
    expect(client.status).toBe("offline");
  });

  it("gives up on 'active elsewhere' after about a minute", async () => {
    const { client, setNow } = makeClient();
    client.connect();
    await flush();
    latest().drop(4009);
    setNow(30_000);
    await vi.advanceTimersByTimeAsync(5000);
    latest().drop(4009);
    expect(client.status).not.toBe("offline");
    setNow(61_000);
    await vi.advanceTimersByTimeAsync(5000);
    latest().drop(4009);
    expect(client.status).toBe("offline");
  });

  it("treats a normal close (1000) as the end of the session", async () => {
    const { client, fetchTicket } = makeClient();
    const statuses: Array<[string, string | null]> = [];
    client.on("status", (s, info) => statuses.push([s, info.reason]));
    client.connect();
    await flush();
    latest().open();
    latest().drop(1000, "");
    expect(statuses.at(-1)).toEqual(["offline", "session_ended"]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetchTicket).toHaveBeenCalledTimes(1);
  });

  it("continues from the seq an earlier connection already saw", async () => {
    const { client } = makeClient({ initialLastSeq: 7 });
    const seen: number[] = [];
    client.on("message", (m) => seen.push(m.seq));
    client.connect();
    await flush();
    latest().open();
    expect(latest().json()[0]).toMatchObject({ type: "hello", last_seq: 7 });
    latest().receive({ type: "session.state", session_id: "s1", status: "ready", turn_state: "idle", seq: 8 } as never);
    expect(seen).toEqual([8]);
  });

  it("does not reconnect after the session ended or an intentional close", async () => {
    const { client, fetchTicket } = makeClient();
    client.connect();
    await flush();
    latest().open();
    latest().receive({ type: "session.ended", reason: "time_limit", seq: 9 } as never);
    latest().drop(1000);
    expect(client.status).toBe("closed");
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchTicket).toHaveBeenCalledTimes(1);

    const second = makeClient();
    second.client.connect();
    await flush();
    latest().open();
    second.client.close();
    expect(second.client.status).toBe("closed");
    expect(latest().closed?.code).toBe(1000);
  });

  it("retryNow skips the backoff wait", async () => {
    const { client, fetchTicket } = makeClient({ backoff: { baseMs: 5000, maxMs: 5000, jitter: 0 } });
    client.connect();
    await flush();
    latest().drop();
    expect(fetchTicket).toHaveBeenCalledTimes(1);
    client.retryNow();
    await flush();
    expect(fetchTicket).toHaveBeenCalledTimes(2);
  });

  it("gives up after the maximum number of attempts", async () => {
    const { client } = makeClient({ maxReconnectAttempts: 2 });
    client.connect();
    await flush();
    latest().drop();
    await vi.advanceTimersByTimeAsync(100);
    latest().drop();
    await vi.advanceTimersByTimeAsync(200);
    latest().drop();
    expect(client.status).toBe("offline");
  });
});

describe("resolveSocketUrl", () => {
  it("always targets the page's own origin and carries the ticket", () => {
    expect(resolveSocketUrl("ws://127.0.0.1:8200/ws/sessions/abc?ticket=xyz", "xyz", "abc", { protocol: "https:", host: "192.168.1.5:3443" })).toBe(
      "wss://192.168.1.5:3443/ws/sessions/abc?ticket=xyz",
    );
    expect(resolveSocketUrl("/ws/sessions/abc", "t1", "abc", { protocol: "http:", host: "localhost:3200" })).toBe("ws://localhost:3200/ws/sessions/abc?ticket=t1");
    expect(resolveSocketUrl("", "t2", "s 1", { protocol: "http:", host: "h" })).toBe("ws://h/ws/sessions/s%201?ticket=t2");
  });
});
