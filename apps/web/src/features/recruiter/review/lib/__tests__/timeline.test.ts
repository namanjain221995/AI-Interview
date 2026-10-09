import { describe, expect, it } from "vitest";

import { bucketSnapshots, competencyKeysInOrder, describeWindow, latestByCompetency, windowLabel, windowsFromServer } from "../timeline";

const snap = (elapsed_seconds: number, competency_key: string, score: number, confidence = 0.5) => ({ elapsed_seconds, competency_key, score, confidence, evidence_count: 1 });

describe("bucketSnapshots", () => {
  it("keeps the last value per competency per window", () => {
    const windows = bucketSnapshots([snap(60, "aws", 6), snap(300, "aws", 7.2), snap(420, "k8s", 6.5), snap(780, "aws", 7.6)], 10);
    expect(windows).toHaveLength(2);
    expect(windows[0]!.label).toBe("Minute 0–10");
    expect(windows[0]!.values.aws).toMatchObject({ score: 7.2, carried: false, delta: null });
    expect(windows[0]!.values.k8s).toMatchObject({ score: 6.5, carried: false });
    expect(windows[1]!.values.aws).toMatchObject({ score: 7.6, carried: false, delta: 0.4 });
  });

  it("carries earlier estimates forward and marks them", () => {
    const windows = bucketSnapshots([snap(60, "aws", 6), snap(700, "k8s", 5)], 10);
    expect(windows[1]!.values.aws).toMatchObject({ score: 6, carried: true, delta: 0 });
    expect(windows[1]!.updatedKeys).toEqual(["k8s"]);
  });

  it("can disable carry-forward", () => {
    const windows = bucketSnapshots([snap(60, "aws", 6), snap(700, "k8s", 5)], 10, { carryForward: false });
    expect(windows[1]!.values.aws).toBeUndefined();
  });

  it("places boundary snapshots in the window they start, like the timeline API (the last boundary opens a window)", () => {
    const windows = bucketSnapshots([snap(0, "aws", 5), snap(600, "aws", 6), snap(1200, "aws", 7)], 10);
    expect(windows).toHaveLength(3);
    expect(windows[0]!.values.aws!.score).toBe(5);
    expect(windows[1]!.values.aws!.score).toBe(6);
    expect(windows[2]!).toMatchObject({ label: "Minute 20–30", values: { aws: { score: 7 } } });
  });

  it("extends windows to the interview duration and handles empty input", () => {
    expect(bucketSnapshots([], 10)).toEqual([]);
    const windows = bucketSnapshots([snap(30, "aws", 6)], 10, { durationSeconds: 1800 });
    expect(windows.map((w) => w.label)).toEqual(["Minute 0–10", "Minute 10–20", "Minute 20–30"]);
    expect(bucketSnapshots([], 5, { durationSeconds: 600 })).toHaveLength(2);
  });

  it("orders unsorted input by time and lets the later element win on ties", () => {
    const windows = bucketSnapshots([snap(500, "aws", 8), snap(100, "aws", 4), snap(500, "aws", 9)], 10);
    expect(windows[0]!.values.aws!.score).toBe(9);
  });

  it("supports other window sizes and ignores invalid values", () => {
    const windows = bucketSnapshots([snap(100, "aws", 5), snap(400, "aws", 6), snap(Number.NaN, "aws", 9)], 5);
    expect(windows.map((w) => [w.startMinute, w.endMinute])).toEqual([
      [0, 5],
      [5, 10],
    ]);
    expect(windows[1]!.values.aws!.score).toBe(6);
  });
});

describe("timeline helpers", () => {
  it("describes a window in reviewer language", () => {
    const [first] = bucketSnapshots([snap(60, "aws", 7.2), snap(120, "k8s", 6.5)], 10);
    const names: Record<string, string> = { aws: "AWS", k8s: "Kubernetes" };
    expect(describeWindow(first!, (k) => names[k] ?? k)).toBe("Minute 0–10: AWS 7.2, Kubernetes 6.5");
    const [, second] = bucketSnapshots([snap(60, "aws", 7.2), snap(900, "k8s", 6.5)], 10);
    expect(describeWindow(second!, (k) => names[k] ?? k)).toBe("Minute 10–20: Kubernetes 6.5");
    expect(describeWindow(second!, (k) => names[k] ?? k, true)).toBe("Minute 10–20: AWS 7.2, Kubernetes 6.5");
    expect(windowLabel(20, 30)).toBe("Minute 20–30");
  });

  it("lists competencies in first-seen order and finds the latest values", () => {
    const data = [snap(300, "k8s", 5), snap(60, "aws", 6), snap(600, "aws", 7)];
    expect(competencyKeysInOrder(data)).toEqual(["aws", "k8s"]);
    expect(latestByCompetency(data).get("aws")!.score).toBe(7);
  });

  it("normalises server windows in map and list form", () => {
    const windows = windowsFromServer(
      [
        { start_minute: 0, end_minute: 10, competencies: { aws: 7.2, k8s: { score: 6.5, confidence: 0.4 } } },
        { index: 1, competencies: [{ competency_key: "aws", score: 7.8 }] },
      ],
      10,
    );
    expect(windows[0]!.values.k8s).toMatchObject({ score: 6.5, confidence: 0.4 });
    expect(windows[1]).toMatchObject({ startMinute: 10, endMinute: 20, label: "Minute 10–20" });
    expect(windows[1]!.values.aws).toMatchObject({ score: 7.8, delta: 0.6 });
    expect(windows[1]!.values.k8s).toMatchObject({ score: 6.5, carried: true });
  });

  it("reads the timeline API window shape (seconds, scores map, details)", () => {
    const windows = windowsFromServer(
      [
        { index: 0, start_seconds: 0, end_seconds: 600, label: "Minute 0-10", scores: { aws: 6.8 }, details: { aws: { score: 6.8, confidence: 0.41, evidence_count: 2 } } } as never,
        { index: 1, start_seconds: 600, end_seconds: 1200, label: "Minute 10-20", scores: {}, details: {} } as never,
      ],
      10,
    );
    expect(windows[0]).toMatchObject({ label: "Minute 0–10", values: { aws: { score: 6.8, confidence: 0.41, evidenceCount: 2, carried: false } } });
    expect(windows[1]!.values.aws).toMatchObject({ score: 6.8, carried: true });
    expect(windowsFromServer([{ index: 1, scores: {} } as never], 10, { carryForward: false })[0]!.values).toEqual({});
  });
});
