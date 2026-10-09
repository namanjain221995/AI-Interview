import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import type { CandidateResume } from "@/lib/types";

import { normalizeGenerateResult, normalizeSearchResult, normalizeValidation } from "../api/library";
import { pipelineSteps } from "../candidates/ResumeUpload";
import { differingIntegrityKeys } from "../jobs/jobDraft";
import { roundConfigErrorsOf, unplacedMessages } from "../lib/serverErrors";
import { asList, asPage } from "../lib/utils";
import { defaultIntegrityPolicy } from "../settings/settingsModel";

describe("list envelopes", () => {
  it("reads pages, bare lists and keyed lists", () => {
    expect(asPage([{ id: 1 }])).toEqual({ items: [{ id: 1 }], total: 1 });
    expect(asPage({ items: [{ id: 1 }], total: 9, limit: 1, offset: 0 })).toEqual({ items: [{ id: 1 }], total: 9 });
    expect(asPage(null)).toEqual({ items: [], total: 0 });
    // GET /round-types is a page envelope
    expect(asList({ items: [{ round_type: "coding" }], total: 1, limit: 1, offset: 0 })).toEqual([{ round_type: "coding" }]);
    expect(asList({ created: [{ id: "q" }] }, "created")).toEqual([{ id: "q" }]);
  });
});

describe("question bank responses", () => {
  it("normalises POST /question-bank/generate ({created, skipped[], requested, generated})", () => {
    const result = normalizeGenerateResult({
      created: [{ id: "q1" }, { id: "q2" }],
      skipped: [{ text: "Duplicate?", reason: "near_duplicate", duplicate_of: "q0", similarity: 0.95 }],
      requested: 3,
      generated: 3,
    });
    expect(result.items.map((q) => q.id)).toEqual(["q1", "q2"]);
    expect(result.skipped).toBe(1);
    expect(result.skippedItems[0]).toEqual({ text: "Duplicate?", reason: "near_duplicate", duplicate_of: "q0", similarity: 0.95 });
  });

  it("normalises POST /question-bank/search ({items, total, mode, reranked})", () => {
    expect(normalizeSearchResult({ items: [{ id: "a", score: 0.9 }], total: 1, mode: "lexical", reranked: false })).toEqual({
      items: [{ id: "a", score: 0.9 }],
      mode: "lexical",
      reranked: false,
    });
    expect(normalizeSearchResult([{ id: "b" }]).mode).toBe("semantic");
  });
});

describe("coding validation", () => {
  it("normalises the validate response with failures, skipped languages and starters", () => {
    const summary = normalizeValidation({
      ok: false,
      coding_question_id: "c1",
      results: [
        { language: "python", status: "passed", passed: 10, total: 10, runtime_ms: 152, error: null, failures: [] },
        { language: "go", status: "failed", passed: 9, total: 10, runtime_ms: 12, failures: [{ id: "t9", hidden: true, expected: [0, 2], actual: [2, 0] }] },
        { language: "java", status: "skipped", passed: 0, total: 0 },
      ],
      starters: [{ language: "python", status: "failed", passed: 0, total: 3 }],
      duration_ms: 2700.5,
      validated_at: "2026-10-07T10:33:00Z",
    });
    expect(summary.ok).toBe(false);
    expect(summary.results.map((r) => r.status)).toEqual(["passed", "failed", "skipped"]);
    expect(summary.results[1]!.failures).toEqual([{ id: "t9", hidden: true, error: null, expected: [0, 2], actual: [2, 0] }]);
    expect(summary.starters).toHaveLength(1);
    expect(summary.duration_ms).toBe(2700.5);
  });

  it("accepts a language-keyed object and infers ok", () => {
    const summary = normalizeValidation({ results: { python: { passed: 3, total: 3 }, cpp: { passed: 3, total: 3, status: "passed" } } });
    expect(summary.ok).toBe(true);
    expect(summary.results.map((r) => r.language)).toEqual(["python", "cpp"]);
  });
});

describe("resume progress steps", () => {
  it("uses progress.steps from the API and hides skipped steps", () => {
    const resume = {
      status: "extracting",
      extraction_method: null,
      progress: {
        stage: "extracting",
        steps: [
          { key: "uploaded", label: "Uploaded", state: "done" },
          { key: "validating", label: "Security and format checks", state: "done" },
          { key: "extracting", label: "Reading the document", state: "active" },
          { key: "ocr", label: "Scanning pages", state: "skipped" },
          { key: "analyzing", label: "Analyzing experience and skills", state: "pending" },
          { key: "ready", label: "Ready", state: "pending" },
        ],
      },
    } as unknown as CandidateResume;
    const steps = pipelineSteps(resume);
    expect(steps.map((s) => s.key)).toEqual(["uploaded", "validating", "extracting", "analyzing", "ready"]);
    expect(steps.find((s) => s.state === "active")?.label).toBe("Reading the document");
  });

  it("derives steps from the status when the API sends none", () => {
    const steps = pipelineSteps({ status: "failed", extraction_method: null, progress: { stage: "extracting" } } as unknown as CandidateResume);
    expect(steps.find((s) => s.key === "extracting")?.state).toBe("failed");
    expect(steps.find((s) => s.key === "uploaded")?.state).toBe("done");
  });
});

describe("integrity overrides", () => {
  it("counts only overrides that differ from the organization default", () => {
    const defaults = defaultIntegrityPolicy();
    defaults.rules = [{ event_type: "tab_hidden", enabled: true, min_confidence: 0.6, min_duration_seconds: 0, min_occurrences: 3, window_seconds: 300, action: "warn_candidate", candidate_message: null }];
    const overrides = { ...defaults, require_fullscreen: false, rules: [...defaults.rules] };
    expect(differingIntegrityKeys(overrides, defaults)).toEqual(["require_fullscreen"]);
    overrides.rules = [{ ...defaults.rules[0]!, min_occurrences: 5 }];
    expect(differingIntegrityKeys(overrides, defaults).sort()).toEqual(["require_fullscreen", "rules"]);
  });
});

describe("server error placement", () => {
  it("maps the round reference check onto config fields", () => {
    const error = new ApiError({
      status: 422,
      code: "validation_failed",
      message: "The round configuration references unknown items.",
      details: { unknown_competency_keys: ["kubernetess"], unknown_coding_question_ids: ["c-1"] },
    });
    const errors = roundConfigErrorsOf(error);
    expect(errors.competencies).toBe("Not in your competency catalog: kubernetess.");
    expect(errors["coding.question_ids"]).toMatch(/1 pinned problem no longer exists/);
  });

  it("lists model-level messages that have no field", () => {
    const error = new ApiError({
      status: 422,
      code: "validation_failed",
      message: "The coding question is invalid.",
      details: {
        errors: [
          { loc: ["body"], msg: "Value error, test t3: expected 2 positional args" },
          { loc: ["body", "title"], msg: "String should have at least 3 characters" },
        ],
      },
    });
    expect(unplacedMessages(error, ["title"])).toEqual(["Test t3: expected 2 positional args"]);
  });
});
