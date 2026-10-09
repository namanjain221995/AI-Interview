import { describe, expect, it } from "vitest";

import type { IntegrityEventView } from "../../api/types";
import { describeDetails, facePresenceSummary, serverCheckText } from "../integrity";

function event(id: string, type: string, extra: Partial<IntegrityEventView> = {}): IntegrityEventView {
  return {
    id,
    session_id: "s-1",
    type,
    source: "client",
    severity: "info",
    occurred_at: "2026-10-08T10:00:00Z",
    count: 1,
    duration_ms: null,
    confidence: null,
    details: {},
    action: "log_only",
    review_status: "unreviewed",
    ...extra,
  } as IntegrityEventView;
}

describe("facePresenceSummary", () => {
  it("is null without face-check observations", () => {
    expect(facePresenceSummary([event("e1", "tab_hidden")])).toBeNull();
  });

  it("adds up page runs, intervals, server confirmations and pictures set aside", () => {
    const summary = facePresenceSummary([
      event("s1", "face_check_summary", {
        details: {
          runs: {
            run_a: { monitored_ms: 600_000, one_face_ms: 570_000, no_face_ms: 20_000, multi_face_ms: 10_000, static_faces: 1 },
            run_b: { monitored_ms: 300_000, one_face_ms: 300_000, no_face_ms: 0, multi_face_ms: 0, static_faces: 0 },
          },
        },
      }),
      event("n1", "face_not_visible", { count: 2, duration_ms: 20_000, details: { episodes: { nf_1: 8_000, nf_2: 12_000 }, server_checks: { nf_1: "confirmed", nf_2: "not_confirmed" } } }),
      event("m1", "multiple_faces_visible", { severity: "medium", duration_ms: 10_000, details: { episodes: { mf_1: 10_000 }, server_checks: { mf_1: "confirmed" } } }),
      event("st", "static_face_ignored", { details: { still_seconds: 30 } }),
    ]);
    expect(summary).toMatchObject({
      monitoredMs: 900_000,
      oneFaceMs: 870_000,
      pageRuns: 2,
      staticFacesIgnored: 1,
      noFace: { intervals: 2, totalMs: 20_000, longestMs: 12_000, confirmed: 1 },
      multipleFaces: { intervals: 1, totalMs: 10_000, longestMs: 10_000, confirmed: 1 },
    });
    expect(summary!.oneFaceShare).toBeCloseTo(870 / 900);
  });

  it("has no share when nothing was monitored yet", () => {
    expect(facePresenceSummary([event("st", "static_face_ignored")])).toMatchObject({ oneFaceShare: null, staticFacesIgnored: 1 });
  });
});

describe("face-check details for reviewers", () => {
  it("words the server check neutrally and hides detector plumbing", () => {
    expect(serverCheckText({ server_check: { status: "confirmed", faces: 2 } })).toBe("Confirmed: 2 faces found");
    expect(serverCheckText({ server_check: { status: "not_confirmed", faces: 1 } })).toBe("Not confirmed: 1 face found");
    expect(serverCheckText({ server_check: { status: "unavailable" } })).toBe("Could not run");
    expect(serverCheckText({})).toBeNull();
    const rows = describeDetails(
      event("m1", "multiple_faces_visible", {
        count: 2,
        duration_ms: 9_000,
        details: { episode: "mf_1", phase: "start", detector: "blazeface-short-range", fps: 4, faces: 2, basis: "face_detector", server_check: { status: "confirmed", faces: 2 } },
      }),
    );
    const labels = rows.map((r) => r.label);
    expect(labels).toContain("Intervals");
    expect(rows.find((r) => r.label === "Server check")?.value).toBe("Confirmed: 2 faces found");
    expect(rows.find((r) => r.value === "Face detector")).toBeTruthy();
    expect(JSON.stringify(rows)).not.toMatch(/mf_1|blazeface|"start"/);
  });
});
