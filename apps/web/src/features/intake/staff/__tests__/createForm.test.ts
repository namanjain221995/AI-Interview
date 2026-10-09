import { describe, expect, it } from "vitest";

import { DEFAULT_INTAKE_DEFAULTS } from "../api";
import { createPayload, defaultSingleForm, draftUpdatePayload, formFromDraft, fromLocalInput, toLocalInput, validateSettings, validateSingle, workAuthorizationOf } from "../lib/createForm";
import { DETAIL } from "./fixtures";

const NOW = new Date("2026-10-09T10:00:00Z");

describe("create form defaults and validation", () => {
  it("starts from the organisation defaults (45 minutes, orb, 10 reconnects, 7 days)", () => {
    const form = defaultSingleForm(DEFAULT_INTAKE_DEFAULTS, { requireAccessCode: true, jobId: "j1", roundId: "r1" });
    expect(form.settings).toMatchObject({ duration_minutes: 45, presence: "orb", max_reconnects: 10, expires_in_hours: 168, require_access_code: true, resume_required: true, mq1_mode: "inherit" });
    expect(form).toMatchObject({ job_id: "j1", round_id: "r1", target_role: "candidate", candidate_mode: "existing" });
    // Out-of-range organisation defaults are clamped into 35-50.
    expect(defaultSingleForm({ ...DEFAULT_INTAKE_DEFAULTS, duration_minutes: 90 }).settings.duration_minutes).toBe(50);
  });

  it("requires a candidate and a job, and checks new candidates' details", () => {
    const form = defaultSingleForm(DEFAULT_INTAKE_DEFAULTS);
    expect(validateSingle(form, { now: NOW })).toMatchObject({ candidate: expect.any(String), job_id: expect.any(String) });
    const newOne = { ...form, candidate_mode: "new" as const, job_id: "j1", new_candidate: { full_name: "A", email: "nope", phone: "" } };
    expect(validateSingle(newOne, { now: NOW })).toMatchObject({ full_name: expect.any(String), email: expect.stringContaining("valid email") });
  });

  it("mirrors the server ranges for settings", () => {
    const base = defaultSingleForm(DEFAULT_INTAKE_DEFAULTS).settings;
    expect(validateSettings({ ...base, duration_minutes: 34 }, NOW).duration_minutes).toContain("35 to 50");
    expect(validateSettings({ ...base, max_reconnects: 21 }, NOW).max_reconnects).toBeDefined();
    expect(validateSettings({ ...base, scheduled_for: "2026-10-01T09:00" }, NOW).scheduled_for).toContain("future");
    expect(validateSettings({ ...base, expires_in_hours: 24, scheduled_for: toLocalInput("2026-10-12T09:00:00Z") }, NOW).scheduled_for).toContain("expires before");
    expect(validateSettings({ ...base, mq1_mode: "ask", mq1_country: " " }, NOW).mq1_country).toBeDefined();
    expect(validateSettings({ ...base, mq1_mode: "ask", mq1_country: "the United States" }, NOW)).toEqual({});
  });

  it("lets a draft skip the settings checks", () => {
    const form = { ...defaultSingleForm(DEFAULT_INTAKE_DEFAULTS), job_id: "j1", candidate: { id: "c1", full_name: "Asha", email: "a@example.com" } };
    const broken = { ...form, settings: { ...form.settings, duration_minutes: 20 } };
    expect(validateSingle(broken, { draft: true, now: NOW })).toEqual({});
    expect(validateSingle(broken, { now: NOW }).duration_minutes).toBeDefined();
  });
});

describe("create payloads", () => {
  const base = { ...defaultSingleForm(DEFAULT_INTAKE_DEFAULTS), job_id: "j1", round_id: "r1" };

  it("sends an existing candidate by id and lets the candidate choose the role", () => {
    const body = createPayload({ ...base, candidate: { id: "c1", full_name: "Asha", email: "a@example.com" } }, true);
    expect(body).toEqual({
      candidate_id: "c1",
      candidate: null,
      job_id: "j1",
      round_id: "r1",
      target_role: null,
      title: null,
      settings: { duration_minutes: 45, presence: "orb", max_reconnects: 10, expires_in_hours: 168, scheduled_for: null, require_access_code: false, resume_required: true, assigned_reviewer_id: null, notify_on_completion: true, work_authorization: null },
      notes: null,
      send: true,
    });
  });

  it("creates a new candidate with a trimmed, lower-case email and a draft when not sending", () => {
    const body = createPayload({ ...base, candidate_mode: "new", target_role: "data_analyst", notes: "  met at a meetup ", new_candidate: { full_name: " Chen Li ", email: " Chen@Example.com ", phone: "" } }, false);
    expect(body).toMatchObject({ candidate_id: null, candidate: { full_name: "Chen Li", email: "chen@example.com", phone: null }, target_role: "data_analyst", notes: "met at a meetup", send: false });
  });

  it("maps the eligibility choice: inherit, ask with a country, or off", () => {
    expect(workAuthorizationOf({ ...base.settings, mq1_mode: "inherit" })).toBeNull();
    expect(workAuthorizationOf({ ...base.settings, mq1_mode: "off" })).toEqual({ enabled: false, country: null, ask_sponsorship: true, variant: "default" });
    expect(workAuthorizationOf({ ...base.settings, mq1_mode: "ask", mq1_country: " Canada ", mq1_sponsorship: false })).toEqual({ enabled: true, country: "Canada", ask_sponsorship: false, variant: "default" });
  });

  it("round-trips a saved draft into the form and back into a PATCH body", () => {
    const draft = { ...DETAIL, status: "draft" as const, settings: { ...DETAIL.settings, scheduled_for: "2026-10-12T14:00:00Z", duration_minutes: 40 } };
    const form = formFromDraft(draft, DEFAULT_INTAKE_DEFAULTS);
    expect(form.candidate?.id).toBe(DETAIL.candidate.id);
    expect(form.settings).toMatchObject({ duration_minutes: 40, mq1_mode: "ask", mq1_country: "the United States", assigned_reviewer_id: DETAIL.settings.assigned_reviewer_id });
    expect(fromLocalInput(form.settings.scheduled_for)).toBe("2026-10-12T14:00:00.000Z");
    const patch = draftUpdatePayload(form);
    expect(patch).toMatchObject({ target_role: "ai_ml", job_id: DETAIL.job.id, round_id: DETAIL.round!.id, notes: DETAIL.notes, settings: { duration_minutes: 40, work_authorization: { enabled: true, country: "the United States" } } });
  });
});
