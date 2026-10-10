import { describe, expect, it } from "vitest";

import { sessionOutcome } from "../reportAccess";

describe("sessionOutcome", () => {
  it("tells wrapping up, complete, early and cancelled endings apart", () => {
    expect(sessionOutcome(null, null)).toBe("unknown");
    expect(sessionOutcome("live", null)).toBe("wrapping_up");
    expect(sessionOutcome("paused", null)).toBe("wrapping_up");
    expect(sessionOutcome("completing", "candidate_ended")).toBe("complete");
    expect(sessionOutcome("completed", "sufficient_evidence")).toBe("complete");
    expect(sessionOutcome("completed", "disconnect_timeout")).toBe("ended_early");
    expect(sessionOutcome("aborted", "technical_failure")).toBe("ended_early");
    expect(sessionOutcome("aborted", "cancelled")).toBe("cancelled");
  });
});
