/**
 * Batches integrity observations: one `integrity.events` WebSocket message
 * (max 50 events) every ~1.5 s, falling back to the REST endpoint when the
 * socket is down. Failed REST batches are retried with backoff; the queue is
 * bounded so a long outage cannot grow memory without limit.
 */
import type { ClientIntegrityEvent } from "@/lib/realtime/protocol";

export interface IntegrityBatcherOptions {
  /** Returns true when the batch was written to an open socket. */
  sendWs: (events: ClientIntegrityEvent[]) => boolean;
  sendRest: (events: ClientIntegrityEvent[], options?: { keepalive?: boolean }) => Promise<void>;
  flushMs?: number;
  maxBatch?: number;
  maxQueue?: number;
}

export class IntegrityBatcher {
  private queue: ClientIntegrityEvent[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight = false;
  private failures = 0;
  private stopped = false;
  private readonly flushMs: number;
  private readonly maxBatch: number;
  private readonly maxQueue: number;

  constructor(private readonly opts: IntegrityBatcherOptions) {
    this.flushMs = opts.flushMs ?? 1500;
    this.maxBatch = Math.min(50, opts.maxBatch ?? 50);
    this.maxQueue = opts.maxQueue ?? 500;
  }

  get pending(): number {
    return this.queue.length;
  }

  push(event: ClientIntegrityEvent): void {
    if (this.stopped) return;
    this.queue.push(event);
    if (this.queue.length > this.maxQueue) this.queue.splice(0, this.queue.length - this.maxQueue);
    if (this.queue.length >= 20) void this.flush();
    else this.schedule(this.flushMs);
  }

  private schedule(delay: number): void {
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delay);
  }

  async flush(options: { keepalive?: boolean } = {}): Promise<void> {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.inFlight || this.queue.length === 0) return;
    const batch = this.queue.splice(0, this.maxBatch);
    if (this.opts.sendWs(batch)) {
      this.failures = 0;
      if (this.queue.length) this.schedule(50);
      return;
    }
    this.inFlight = true;
    try {
      await this.opts.sendRest(batch, options);
      this.failures = 0;
    } catch {
      this.failures += 1;
      this.queue.unshift(...batch);
      if (this.queue.length > this.maxQueue) this.queue.length = this.maxQueue;
    } finally {
      this.inFlight = false;
    }
    if (this.queue.length && !this.stopped) {
      this.schedule(this.failures ? Math.min(30_000, 1000 * 2 ** this.failures) : 50);
    }
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
