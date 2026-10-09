import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, refreshSession: vi.fn(async () => "refreshed") };
});

import { refreshSession } from "@/lib/api";

import { useLiveSession } from "../api/live";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  listeners = new Map<string, Array<(e: MessageEvent<string>) => void>>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    if (name === "message") this.onmessage?.(event);
    for (const fn of this.listeners.get(name) ?? []) fn(event);
  }
}

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useLiveSession", () => {
  it("connects to the session's live endpoint and applies named events", () => {
    const { result } = renderHook(() => useLiveSession("s-1"), { wrapper });
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toBe("/api/v1/sessions/s-1/live");
    expect(result.current.connection).toBe("connecting");
    act(() => source.onopen?.());
    expect(result.current.connection).toBe("open");
    act(() =>
      source.emit("state", {
        session_status: "live",
        turn_state: "candidate_speaking",
        elapsed_s: 120,
        remaining_s: 1680,
        current_question: { id: "q1", text: "How do you roll back?" },
        competencies: [{ key: "aws", name: "AWS", score_estimate: 6.2, confidence: 0.4, evidence_count: 2, sufficient_evidence: false }],
        conversation: [{ speaker: "interviewer", text: "How do you roll back?", at_s: 100 }],
        partial_transcript: "We keep the previous",
      }),
    );
    expect(result.current.state).toMatchObject({ status: "live", turnState: "candidate_speaking", currentQuestion: { text: "How do you roll back?" } });
    expect(result.current.state.transcript).toHaveLength(1);
    expect(result.current.state.partials.candidate?.text).toBe("We keep the previous");
    act(() => source.emit("evaluation.ready", { answer_id: "a1", quality: "adequate", competencies: { aws: { score: 6.8, confidence: 0.5, evidence_count: 3, sufficient: false } } }));
    expect(result.current.state.competencies[0]).toMatchObject({ key: "aws", name: "AWS", score_estimate: 6.8 });
    expect(result.current.state.previousScores.aws).toBe(6.2);
  });

  it("refreshes the session and reconnects with backoff when the stream fails", async () => {
    const { result } = renderHook(() => useLiveSession("s-1"), { wrapper });
    const first = FakeEventSource.instances[0]!;
    act(() => first.onopen?.());
    await act(async () => {
      first.onerror?.();
    });
    expect(first.closed).toBe(true);
    expect(result.current.connection).toBe("reconnecting");
    expect(refreshSession).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
  });

  it("stops for good after the end event", async () => {
    const { result } = renderHook(() => useLiveSession("s-1"), { wrapper });
    const source = FakeEventSource.instances[0]!;
    act(() => source.onopen?.());
    act(() => source.emit("end", { reason: "session_ended", status: "completed" }));
    expect(result.current.state.ended).toBe(true);
    await act(async () => {
      source.onerror?.();
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(result.current.connection).toBe("closed");
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("does nothing when disabled", () => {
    const { result } = renderHook(() => useLiveSession("s-1", { enabled: false }), { wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current.connection).toBe("idle");
  });
});
