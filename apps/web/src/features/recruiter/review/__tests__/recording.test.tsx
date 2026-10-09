/**
 * Recording tab on the session detail page: parts, synced transcript, neutral
 * integrity markers, playback speed, process now, admin-only download.
 */
import { QueryClient } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/router";
import { api } from "@/lib/api";
import { resetAuthStore } from "@/lib/auth";

import { DEMO_IDS, demoSessionDetail } from "../__fixtures__/demoData";
import { installMockApi, signInAs, type RecordedCall } from "./mockApi";

const START = Date.parse(demoSessionDetail.started_at as string);
const at = (seconds: number) => new Date(START + seconds * 1000).toISOString();

function recordings(canDownload: boolean, extra: object[] = []) {
  return {
    session_id: DEMO_IDS.completed,
    status: "completed",
    session_started_at: at(0),
    session_ended_at: at(1838),
    policy: { record_video: true, record_screen: true, video_days: 30, consented: true },
    can_download: canDownload,
    processing: extra.length > 0,
    parts: [
      { part_id: "part-a", kind: "camera", ordinal: 1, status: "ready", started_at: at(0), ended_at: at(1000), duration_ms: 1_000_000, media_id: "m-a", content_type: "video/webm", size_bytes: 52_000_000, seekable: true, segments: 100, anchors: [{ media_ms: 0, session_ms: 0 }, { media_ms: 1_000_000, session_ms: 1_000_000 }] },
      { part_id: "part-b", kind: "camera", ordinal: 2, status: "ready", started_at: at(1010), ended_at: at(1838), duration_ms: 828_000, media_id: "m-b", content_type: "video/webm", size_bytes: 40_000_000, seekable: false, segments: 83, anchors: [{ media_ms: 0, session_ms: 1_010_000 }] },
      { part_id: "screen-a", kind: "screen", ordinal: 1, status: "ready", started_at: at(0), ended_at: at(600), duration_ms: 600_000, media_id: "m-s", content_type: "video/webm", seekable: true, anchors: [] },
      ...extra,
    ],
  };
}

let calls: RecordedCall[] = [];

function renderTab(data: object) {
  const base = vi.mocked(api.get).getMockImplementation()!;
  vi.spyOn(api, "get").mockImplementation(async (path: string, options?: Parameters<typeof api.get>[1]) => {
    if (path.endsWith("/recordings")) {
      calls.push({ method: "GET", path });
      return data as never;
    }
    const signed = /\/recordings\/([^/]+)\/(url|download)$/.exec(path);
    if (signed) {
      calls.push({ method: "GET", path });
      return { part_id: signed[1], url: `/api/v1/media/signed/${signed[1]}${signed[2] === "download" ? "?download=1" : ""}`, expires_at: new Date(Date.now() + 900_000).toISOString(), content_type: "video/webm", filename: `${signed[1]}.webm` } as never;
    }
    return base(path, options);
  });
  const router = createMemoryRouter(routes, { initialEntries: [`/app/sessions/${DEMO_IDS.completed}?tab=recording`] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  render(
    <AppProviders queryClient={queryClient}>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

beforeEach(() => {
  resetAuthStore();
  calls = installMockApi();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  resetAuthStore();
  vi.restoreAllMocks();
});

describe("session recording tab", () => {
  it("plays parts with the transcript in sync and neutral observation markers", async () => {
    act(() => signInAs("INTERVIEWER"));
    renderTab(recordings(false));
    expect(await screen.findByTestId("recording-tab", undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByTestId("session-tab-recording")).toHaveAttribute("data-state", "active");
    expect(await screen.findByTestId("recording-notice")).toHaveTextContent("Kept for 30 days");
    expect(screen.queryByTestId("recording-download")).not.toBeInTheDocument();
    // The selected part plays through a signed URL (audited by the API).
    const video = await screen.findByTestId("recording-video");
    await waitFor(() => expect(video.getAttribute("src")).toBe("/api/v1/media/signed/part-a"));
    expect(calls.some((c) => c.path.endsWith("/recordings/part-a/url"))).toBe(true);
    expect(screen.getByTestId("recording-part-1")).toHaveAttribute("aria-pressed", "true");
    // One observation (448 s) falls inside part 1; part 2 holds the other three.
    expect(screen.getAllByTestId("recording-marker")).toHaveLength(1);
    expect(screen.getByTestId("recording-timeline").parentElement).toHaveTextContent("Observations, not determinations.");

    // Playing at 448 s highlights the line being heard.
    const lines = await screen.findAllByTestId("recording-line");
    expect(lines.length).toBeGreaterThan(5);
    Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: 448 });
    fireEvent.timeUpdate(video);
    await waitFor(() => expect(screen.getAllByTestId("recording-line").filter((l) => l.dataset.active === "true")).toHaveLength(1));

    // Playback speed is remembered on the player.
    const speed = screen.getByRole("radiogroup", { name: "Playback speed" });
    fireEvent.click(within(speed).getByRole("radio", { name: "1.5×" }));
    expect((video as HTMLVideoElement).playbackRate).toBe(1.5);

    // The shared screen plays as a second stream next to the camera.
    expect(screen.getByTestId("recording-screen-video")).toBeInTheDocument();

    // A transcript line from the second part switches parts.
    // Lines recorded in another part name that part instead of a time.
    const later = screen.getAllByTestId("recording-line").find((l) => (l.textContent ?? "").startsWith("Part 2"));
    expect(later).toBeDefined();
    fireEvent.click(later!);
    await waitFor(() => expect(screen.getByTestId("recording-part-2")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getAllByTestId("recording-marker")).toHaveLength(3);
    await waitFor(() => expect(screen.getByTestId("recording-video").getAttribute("src")).toBe("/api/v1/media/signed/part-b"));
    expect(screen.getByText(/couldn't be indexed/)).toBeInTheDocument();
  });

  it("lets admins download (audited by the API) and anyone with review rights process pending parts", async () => {
    act(() => signInAs("ADMIN"));
    renderTab(recordings(true, [{ part_id: "part-c", kind: "camera", ordinal: 3, status: "processing", segments: 4, pending_segments: 4 }]));
    const download = await screen.findByTestId("recording-download", undefined, { timeout: 8000 });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fireEvent.click(download);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/recordings/part-a/download"))).toBe(true));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(screen.getByTestId("recording-processing")).toHaveTextContent("Preparing the recording for playback");
    fireEvent.click(screen.getByTestId("recording-finalize"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/recordings/finalize"))).toBe(true));
    expect(screen.getByTestId("recording-part-3")).toBeDisabled();
  });

  it("explains why there is no recording", async () => {
    act(() => signInAs("RECRUITER"));
    renderTab({ ...recordings(false), parts: [], policy: { record_video: false, record_screen: false, video_days: 30, consented: false } });
    expect(await screen.findByTestId("recording-empty", undefined, { timeout: 8000 })).toHaveTextContent("Recording was off for this interview");
  });
});
