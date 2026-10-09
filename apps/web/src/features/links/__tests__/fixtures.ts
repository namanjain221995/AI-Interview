/** Shared API fixtures for the open-links tests. */
import { defaultSchema } from "../formSchema";

export const NOW = "2026-10-08T09:00:00Z";

export const LINK = {
  id: "l1",
  name: "Campus drive, Pune",
  job: { id: "j1", title: "Senior DevOps Engineer", status: "open" },
  status: "active",
  state: "open",
  url: "https://interview.acme.test/j/AbCdEfGhIjKlMnOpQrStUvWxYz012345",
  rounds: [
    { id: "r1", name: "Screening", round_type: "technical_screening", sequence: 0, duration_minutes: 20, missing: false },
    { id: "r2", name: "Deep dive", round_type: "deep_technical", sequence: 1, duration_minutes: 40, missing: false },
  ],
  progression: "after_review",
  opens_at: null,
  closes_at: null,
  max_registrations: 100,
  counts: { registrations: 3, in_progress: 0, awaiting_review: 2, completed: 0, flagged: 1, blocked: 0 },
  created_at: NOW,
  updated_at: NOW,
  description: null,
  round_window_hours: 72,
  require_resume: true,
  requires_access_code: false,
  per_ip_hourly_limit: 1000,
  duplicate_policy: { scope: "job", device: "flag", network: "flag" },
  form_schema: defaultSchema(),
  acknowledged_warnings: [],
  policy_summary: {
    video_recording: true,
    audio_recording: true,
    screen_recording: false,
    camera_monitoring: true,
    screen_monitoring: true,
    candidate_report_mode: "after_review",
    retention_days: { resume: 180, audio: 90, video: 30, transcript: 365, report: 365 },
  },
  warnings: [],
  created_by: { id: "u1", full_name: "Dana Whitfield" },
};
