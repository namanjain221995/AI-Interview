/**
 * RecordingVideo keeps playing across short-lived signed URLs: a fresh URL is
 * swapped in at the next pause (never mid-playback), and a failed request
 * (expired link) re-signs and resumes at the same position.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { recordingsKey } from "../api/recordings";
import { RecordingVideo } from "../components/media";

let issued = 0;
let client: QueryClient;

beforeEach(() => {
  issued = 0;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(api, "get").mockImplementation(async (path: string) => {
    issued += 1;
    expect(path).toBe("/sessions/s1/recordings/p1/url");
    return { url: `/api/v1/media/signed/t${issued}`, expires_at: new Date(Date.now() + 900_000).toISOString(), content_type: "video/webm" } as never;
  });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(() => Promise.resolve());
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderPlayer() {
  render(
    <QueryClientProvider client={client}>
      <RecordingVideo sessionId="s1" part={{ partId: "p1", audioOnly: false }} label="Interview recording" />
    </QueryClientProvider>,
  );
  return screen.getByTestId("recording-video") as HTMLVideoElement;
}

function setTime(video: HTMLVideoElement, seconds: number) {
  Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: seconds });
}

describe("RecordingVideo", () => {
  it("swaps a re-signed URL in at the next pause, keeping the position", async () => {
    const video = renderPlayer();
    await waitFor(() => expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t1"));
    fireEvent.play(video);
    setTime(video, 42);
    await act(() => client.refetchQueries({ queryKey: [...recordingsKey("s1"), "p1", "url"] }));
    // TanStack notifies subscribers on a timer: let the player render the new URL.
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));
    expect(issued).toBe(2);
    expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t1"); // still playing: no reload
    fireEvent.pause(video);
    expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t2");
    setTime(video, 0);
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(42);
  });

  it("re-signs after a failed request and resumes playback where it was", async () => {
    const video = renderPlayer();
    await waitFor(() => expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t1"));
    fireEvent.play(video);
    setTime(video, 61.5);
    Object.defineProperty(video, "error", { configurable: true, value: { code: 2 } });
    const play = vi.mocked(HTMLMediaElement.prototype.play);
    play.mockClear();
    fireEvent.error(video);
    await waitFor(() => expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t2"));
    setTime(video, 0);
    fireEvent.loadedMetadata(video);
    expect(video.currentTime).toBe(61.5);
    expect(play).toHaveBeenCalled();
  });

  it("gives up after repeated failures with a retry action", async () => {
    const video = renderPlayer();
    await waitFor(() => expect(video.getAttribute("src")).toBe("/api/v1/media/signed/t1"));
    Object.defineProperty(video, "error", { configurable: true, value: { code: 4 } });
    for (let i = 0; i < 4; i++) {
      fireEvent.error(video);
      await waitFor(() => expect(issued).toBeGreaterThanOrEqual(Math.min(i + 2, 4)));
    }
    expect(await screen.findByRole("alert")).toHaveTextContent("can't be played right now");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});
