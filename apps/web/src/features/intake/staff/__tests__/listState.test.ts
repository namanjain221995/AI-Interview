import { describe, expect, it } from "vitest";

import { BUCKET_TABS } from "../labels";
import { bucketCount, dayBoundary, hasExtraFilters, listParams, readListState, statsParams, visibleBuckets, writeListState } from "../lib/buckets";
import { STATS } from "./fixtures";

describe("intake list URL state", () => {
  it("reads defaults from an empty URL", () => {
    expect(readListState(new URLSearchParams())).toEqual({
      page: 0,
      filters: { bucket: "all", q: undefined, job_id: undefined, target_role: undefined, band: undefined, outcome: undefined, reviewer_id: undefined, batch_id: undefined, created_from: undefined, created_to: undefined, sort: "-created_at" },
    });
  });

  it("round-trips every filter and drops invalid values", () => {
    const params = new URLSearchParams("bucket=awaiting_review&q=asha&job=j1&role=ai_ml&band=borderline&outcome=reject&reviewer=u1&batch=b1&from=2026-10-01&to=2026-10-09&sort=-score&page=2");
    const state = readListState(params);
    expect(state.filters).toMatchObject({ bucket: "awaiting_review", q: "asha", job_id: "j1", target_role: "ai_ml", band: "borderline", outcome: "reject", reviewer_id: "u1", batch_id: "b1", sort: "-score" });
    expect(state.page).toBe(2);
    expect(writeListState(state).toString()).toBe(params.toString());

    const bad = readListState(new URLSearchParams("bucket=nope&role=wizard&band=great&sort=random&from=yesterday&page=-3"));
    expect(bad.filters).toMatchObject({ bucket: "all", target_role: undefined, band: undefined, sort: "-created_at", created_from: undefined });
    expect(bad.page).toBe(0);
  });

  it("builds API params: defaults stay out, dates become local-day boundaries", () => {
    const state = readListState(new URLSearchParams("from=2026-10-01&to=2026-10-09"));
    const params = listParams({ ...state.filters, limit: 25, offset: 50 });
    expect(params.bucket).toBeUndefined();
    expect(params.sort).toBeUndefined();
    expect(params.offset).toBe(50);
    expect(params.created_from).toBe(dayBoundary("2026-10-01"));
    expect(params.created_to).toBe(dayBoundary("2026-10-09", 1));
    expect(listParams({ ...state.filters, bucket: "training", sort: "candidate", limit: 25, offset: 0 })).toMatchObject({ bucket: "training", sort: "candidate" });
  });

  it("gives stats the same filters without bucket, sort or paging", () => {
    const state = readListState(new URLSearchParams("bucket=accepted&q=asha&sort=-score&page=3"));
    expect(statsParams(state.filters)).toMatchObject({ q: "asha" });
    expect(statsParams(state.filters)).not.toHaveProperty("bucket");
    expect(statsParams(state.filters)).not.toHaveProperty("limit");
    expect(hasExtraFilters(state.filters)).toBe(true);
    expect(hasExtraFilters(readListState(new URLSearchParams("bucket=accepted")).filters)).toBe(false);
  });

  it("counts buckets and hides empty optional buckets unless selected", () => {
    expect(bucketCount(STATS, "all")).toBe(STATS.totals.invitations + STATS.buckets.drafts);
    expect(bucketCount(STATS, "awaiting_review")).toBe(1);
    expect(bucketCount(undefined, "drafts")).toBeUndefined();
    const visible = visibleBuckets(BUCKET_TABS, STATS, "all");
    expect(visible).toContain("expired");
    expect(visible).not.toContain("cancelled");
    expect(visibleBuckets(BUCKET_TABS, STATS, "cancelled")).toContain("cancelled");
  });
});
