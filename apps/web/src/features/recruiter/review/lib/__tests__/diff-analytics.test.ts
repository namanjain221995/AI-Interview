import { describe, expect, it } from "vitest";

import type { SessionSummary } from "@/lib/types";

import { bucketsFromServer, completionStats, funnelSteps, groupSessions, groupsFromServer, histogram, integrityByType, quantile, summarize } from "../analytics";
import { assistanceFactor } from "../coding";
import { collapseUnchanged, diffLines, diffStats, splitLines } from "../diff";

describe("diffLines", () => {
  it("returns only unchanged lines for identical input", () => {
    const lines = diffLines("a\nb\n", "a\nb");
    expect(lines.every((l) => l.kind === "same")).toBe(true);
    expect(lines).toHaveLength(2);
  });

  it("finds insertions, deletions and replacements with line numbers", () => {
    const before = "def f(xs):\n    out = []\n    for x in xs:\n        out.append(x)\n    return out";
    const after = "def f(xs):\n    seen = set()\n    out = []\n    for x in xs:\n        if x not in seen:\n            out.append(x)\n    return out";
    const lines = diffLines(before, after);
    expect(diffStats(lines)).toEqual({ added: 3, removed: 1 });
    expect(lines[1]).toEqual({ kind: "add", text: "    seen = set()", oldNumber: null, newNumber: 2 });
    const removed = lines.find((l) => l.kind === "del")!;
    expect(removed).toMatchObject({ text: "        out.append(x)", oldNumber: 4 });
    // Reconstruct both sides from the diff.
    expect(lines.filter((l) => l.kind !== "add").map((l) => l.text)).toEqual(splitLines(before));
    expect(lines.filter((l) => l.kind !== "del").map((l) => l.text)).toEqual(splitLines(after));
  });

  it("handles empty sides", () => {
    expect(diffStats(diffLines("", "a\nb"))).toEqual({ added: 2, removed: 0 });
    expect(diffStats(diffLines("a", ""))).toEqual({ added: 0, removed: 1 });
    expect(diffLines("", "")).toEqual([]);
  });

  it("collapses long unchanged runs around changes", () => {
    const before = Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n");
    const after = before.replace("line 10", "line ten");
    const display = collapseUnchanged(diffLines(before, after), 2);
    expect(display.filter((l) => l.kind === "gap").map((l) => (l.kind === "gap" ? l.count : 0))).toEqual([8, 7]);
    expect(display.filter((l) => l.kind === "add" || l.kind === "del")).toHaveLength(2);
  });
});

describe("coding assistance factor", () => {
  it("lowers the counted coding score by 7% of the estimate per hint, never below zero", () => {
    expect(assistanceFactor(0)).toBe(1);
    expect(assistanceFactor(1)).toBeCloseTo(0.93);
    expect(assistanceFactor(3)).toBeCloseTo(0.79);
    expect(assistanceFactor(-2)).toBe(1);
    expect(assistanceFactor(40)).toBe(0);
  });
});

describe("analytics transforms", () => {
  it("keeps the API's funnel order, labels known stages and computes conversion between shown stages", () => {
    const api = [
      { stage: "invited", label: "Invited", count: 60 },
      { stage: "opened", label: "Opened invite", count: 55 },
      { stage: "verified", label: "Verified identity", count: 50 },
      { stage: "started", label: "Started interview", count: 40 },
      { stage: "completed", label: "Completed", count: 30 },
      { stage: "reviewed", label: "Human decision recorded", count: 12 },
      { stage: "report_released", label: "Feedback released", count: 6 },
      { stage: "custom", label: "Custom stage", count: 3 },
    ];
    const all = funnelSteps(api);
    expect(all.map((s) => s.label)).toEqual(["Invited", "Opened invite", "Verified", "Started", "Completed", "Reviewed", "Feedback shared", "Custom stage"]);
    expect(all[1]!.fromPrevious).toBeCloseTo(55 / 60);
    const core = funnelSteps(api, { only: ["invited", "started", "completed", "reviewed"] });
    expect(core.map((s) => s.stage)).toEqual(["invited", "started", "completed", "reviewed"]);
    expect(core[1]!.fromPrevious).toBeCloseTo(40 / 60);
    expect(core[3]!.fromStart).toBeCloseTo(0.2);
    expect(core[0]!.fromPrevious).toBeNull();
    expect(funnelSteps([{ stage: "invited", count: 0 }, { stage: "started", count: 0 }])[1]!.fromPrevious).toBe(0);
  });

  it("bins scores into ten-point buckets with 100 in the last bucket", () => {
    const buckets = histogram([0, 9.9, 10, 55, 99, 100, Number.NaN, 140]);
    expect(buckets).toHaveLength(10);
    expect(buckets[0]!.count).toBe(2);
    expect(buckets[1]!.count).toBe(1);
    expect(buckets[5]!.count).toBe(1);
    expect(buckets[9]!.count).toBe(3);
    expect(buckets[9]!.label).toBe("90–100");
  });

  it("parses server histograms in list and map form onto the full 0-100 grid", () => {
    const fromMap = bucketsFromServer({ "60-70": 4, "50-60": 2 })!;
    expect(fromMap).toHaveLength(10);
    expect(fromMap.filter((b) => b.count).map((b) => [b.min, b.count])).toEqual([
      [50, 2],
      [60, 4],
    ]);
    expect(bucketsFromServer([{ min: 0, max: 10, count: 1 }, { bucket: "10–20", count: 3 }])!.slice(0, 2).map((b) => [b.label, b.count])).toEqual([
      ["0–10", 1],
      ["10–20", 3],
    ]);
    expect(bucketsFromServer([{ min: 0, max: 50, count: 1 }, { min: 50, max: 100, count: 2 }])!.map((b) => b.label)).toEqual(["0–50", "50–100"]);
    expect(bucketsFromServer(undefined)).toBeNull();
    expect(bucketsFromServer([{ label: "n/a", count: 2 }])).toBeNull();
  });

  it("summarises with interpolated quantiles", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([], 0.5)).toBeNull();
    expect(summarize([70, 50, 90])).toMatchObject({ count: 3, mean: 70, median: 70, min: 50, max: 90, p25: 60, p75: 80 });
    expect(summarize([])).toMatchObject({ count: 0, mean: null });
  });

  const session = (over: Partial<SessionSummary>): SessionSummary =>
    ({
      id: Math.random().toString(36),
      interview_id: "i",
      organization_id: "o",
      candidate_id: "c",
      job_id: "j1",
      round_id: "r1",
      status: "completed",
      started_at: null,
      ended_at: null,
      end_reason: null,
      elapsed_seconds: 1800,
      reconnect_count: 0,
      created_at: "",
      updated_at: "",
      job: { id: "j1", title: "DevOps" },
      round: { id: "r1", name: "Combined AI Round", round_type: "combined" },
      overall_score: 70,
      integrity_flags: 1,
      ...over,
    }) as SessionSummary;

  it("groups a session sample by job and by round type", () => {
    const sample = [
      session({}),
      session({ overall_score: 50, elapsed_seconds: 1200 }),
      session({ status: "aborted", overall_score: null }),
      session({ status: "live", overall_score: null }),
      session({ job_id: "j2", job: { id: "j2", title: "Backend" }, round: { id: "r2", name: "Tech screen", round_type: "technical_screening" }, round_id: "r2" }),
    ];
    const byJob = groupSessions(sample, "job");
    expect(byJob[0]).toMatchObject({ key: "j1", label: "DevOps", started: 4, completed: 2, integrityFlags: 4 });
    expect(byJob[0]!.completionRate).toBeCloseTo(2 / 3);
    expect(byJob[0]!.summary).toMatchObject({ count: 2, median: 60 });
    expect(byJob[0]!.medianDurationSeconds).toBe(1500);
    const byRound = groupSessions(sample, "round");
    expect(byRound.map((g) => g.label)).toEqual(["Combined", "Technical screening"]);
    expect(completionStats(sample)).toEqual({ finished: 4, completed: 3, rate: 0.75 });
  });

  it("normalises server breakdowns and integrity counts", () => {
    const groups = groupsFromServer([{ job_id: "j", title: "DevOps", count: 4, average_score: 68, scores: [60, 70, 80] }]);
    expect(groups![0]).toMatchObject({ key: "j", label: "DevOps", started: 4 });
    expect(groups![0]!.summary).toMatchObject({ mean: 68, median: 70 });
    expect(groupsFromServer([])).toBeNull();
    expect(integrityByType({ tab_hidden: 3, copy_event: 0, focus_lost: 5 })).toEqual([
      { type: "focus_lost", count: 5 },
      { type: "tab_hidden", count: 3 },
    ]);
  });
});
