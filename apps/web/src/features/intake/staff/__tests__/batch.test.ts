import { describe, expect, it } from "vitest";

import { BATCH_MAX_ROWS, batchPayload, linksCsv, mergeBatchResults, parseBatch, parseRole, splitLine } from "../lib/batch";

describe("batch parsing", () => {
  it("parses name, email and an optional role, with the email in either column", () => {
    const parsed = parseBatch("Asha Rao, asha@example.com, AI/ML\nbilal@example.com, Bilal Khan\n\n  Chen Li , CHEN@example.com , Data analyst ");
    expect(parsed.header).toBe(false);
    expect(parsed.rows.map((r) => [r.row, r.full_name, r.email, r.target_role, r.error])).toEqual([
      [1, "Asha Rao", "asha@example.com", "ai_ml", null],
      [2, "Bilal Khan", "bilal@example.com", null, null],
      [3, "Chen Li", "chen@example.com", "data_analyst", null],
    ]);
  });

  it("understands a header row, tabs and quoted names", () => {
    const parsed = parseBatch('email\tname\trole\n"rao@example.com"\t"Rao, Asha"\tTechnical PM');
    expect(parsed.header).toBe(true);
    expect(parsed.valid).toHaveLength(1);
    expect(parsed.valid[0]).toMatchObject({ full_name: "Rao, Asha", email: "rao@example.com", target_role: "product_manager_technical" });
    expect(splitLine('"Doe, Jane",jane@example.com,"He said ""hi"""', ",")).toEqual(["Doe, Jane", "jane@example.com", 'He said "hi"']);
  });

  it("flags bad emails, missing names, duplicates and unknown roles per row", () => {
    const parsed = parseBatch("Asha, asha@example.com\nBilal, not-an-email\n, carol@example.com\nAsha Again, ASHA@example.com\nDan, dan@example.com, wizard");
    expect(parsed.rows.map((r) => r.error)).toEqual([
      null,
      "This email address doesn't look right.",
      "Add the candidate's name.",
      "Same email as row 1.",
      expect.stringContaining("Unknown role “wizard”"),
    ]);
    expect(parsed.valid.map((r) => r.row)).toEqual([1]);
    expect(parseBatch("Just A Name").rows[0]!.error).toBe("Add an email address.");
  });

  it("refuses more than the batch limit", () => {
    const text = Array.from({ length: BATCH_MAX_ROWS + 1 }, (_, i) => `Person ${i}, p${i}@example.com`).join("\n");
    const parsed = parseBatch(text);
    expect(parsed.problems[0]).toContain(`up to ${BATCH_MAX_ROWS} rows`);
  });

  it("maps role labels and aliases", () => {
    expect(parseRole("Growth PM")).toBe("product_manager_growth");
    expect(parseRole("ai_ml")).toBe("ai_ml");
    expect(parseRole("Other role")).toBe("other");
    expect(parseRole("")).toBeNull();
    expect(parseRole("astronaut")).toBeNull();
  });
});

describe("batch results", () => {
  const sent = batchPayload(parseBatch("Asha Rao, asha@example.com, AI/ML\nBilal Khan, bilal@example.com").valid);

  it("sends only the fields the API expects", () => {
    expect(sent).toEqual([
      { full_name: "Asha Rao", email: "asha@example.com", target_role: "ai_ml" },
      { full_name: "Bilal Khan", email: "bilal@example.com", target_role: null },
    ]);
  });

  it("merges created and failed rows back into input order", () => {
    const rows = mergeBatchResults(
      sent,
      [{ row: 0, intake_id: "i1", candidate_id: "c1", email: "asha@example.com", candidate_reused: true, invite_url: "https://x.test/i/abc", access_code: "K7P2", expires_at: "2026-10-16T09:00:00Z" }],
      [{ row: 1, email: "bilal@example.com", code: "interview_exists", message: "Already invited to this job." }],
    );
    expect(rows.map((r) => [r.row, r.status, r.message])).toEqual([
      [1, "created", "Existing candidate"],
      [2, "failed", "Already invited to this job."],
    ]);
  });

  it("writes a CSV that quotes separators and neutralises spreadsheet formulas", () => {
    const csv = linksCsv([
      { row: 1, full_name: "Rao, Asha", email: "asha@example.com", status: "created", invite_url: "https://x.test/i/abc", access_code: "K7P2", expires_at: null, message: null },
      { row: 2, full_name: "=HYPERLINK(1)", email: "b@example.com", status: "failed", invite_url: null, access_code: null, expires_at: null, message: 'Said "no"' },
    ]);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("row,name,email,status,invite_link,access_code,expires_at,note");
    expect(lines[1]).toBe('1,"Rao, Asha",asha@example.com,created,https://x.test/i/abc,K7P2,,');
    expect(lines[2]).toBe(`2,'=HYPERLINK(1),b@example.com,failed,,,,"Said ""no"""`);
  });
});
