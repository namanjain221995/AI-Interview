import { describe, expect, it } from "vitest";

import { roomError } from "../pages/InterviewPage";

describe("roomError", () => {
  it("only surfaces an error that left a query without data (a failed background refetch keeps the room)", () => {
    const err = new Error("offline");
    expect(roomError({ data: undefined, error: err }, { data: undefined, error: null })).toBe(err);
    expect(roomError({ data: { id: "i1" }, error: err }, { data: { sessionId: "s1" }, error: null })).toBeNull();
    expect(roomError({ data: { id: "i1" }, error: null }, { data: undefined, error: err })).toBe(err);
    expect(roomError({ data: { id: "i1" }, error: null }, { data: { sessionId: "s1" }, error: err })).toBeNull();
  });
});
