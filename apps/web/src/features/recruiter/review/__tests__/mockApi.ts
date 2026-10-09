/**
 * Routes `api.get/post/patch` to the demo fixtures, mirroring the backend's
 * response shapes (Page envelopes, transcript `answer_audio`, timeline
 * windows, report documents). Every call is recorded for assertions.
 */

import { vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { permissionsForRole, useAuthStore } from "@/lib/auth";
import type { StaffRole } from "@/lib/types";

import * as demo from "../__fixtures__/demoData";

export interface RecordedCall {
  method: "GET" | "POST" | "PATCH";
  path: string;
  params?: Record<string, unknown>;
  body?: unknown;
}

function notFound(path: string): never {
  throw new ApiError({ status: 404, code: "not_found", message: `No mock for ${path}` });
}

function get(path: string, params: Record<string, unknown> = {}): unknown {
  if (path === "/auth/me") return demo.demoMe;
  if (path === "/sessions") {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue;
      for (const v of Array.isArray(value) ? value : [value]) search.append(key, String(v));
    }
    return demo.demoSessionsPage(search);
  }
  const match = /^\/sessions\/([^/]+)(?:\/(.+))?$/.exec(path);
  if (match) {
    const [, id, part] = match;
    const completed = id === demo.DEMO_IDS.completed;
    const live = id === demo.DEMO_IDS.live;
    if (!completed && !live) notFound(path);
    if (!part) return completed ? demo.demoSessionDetail : demo.demoLiveDetail;
    const questions = completed ? demo.demoQuestions : demo.demoLiveQuestions;
    switch (part) {
      case "transcript": {
        const items = completed ? demo.demoTranscript : demo.demoLiveTranscript;
        const answerAudio: Record<string, string> = {};
        for (const q of questions) for (const a of q.answers) if (a.audio_media_id) answerAudio[a.id] = a.audio_media_id;
        return { session_id: id, items, total: items.length, limit: 5000, offset: 0, interruptions: 1, answer_audio: answerAudio };
      }
      case "questions":
        return { items: questions, total: questions.length, limit: questions.length, offset: 0 };
      case "evidence": {
        const items = completed ? demo.demoEvidence : demo.demoLiveEvidence;
        return { items, total: items.length, limit: 200, offset: 0 };
      }
      case "scores":
        return completed ? { ...demo.demoScores, is_final: true, computed_at: null } : { session_id: id, source: "live", overall: null, rubric: [], competencies: [], recommendation: null };
      case "timeline":
        return completed ? demo.demoTimelineResponse(Number(params.window_minutes ?? 10)) : { windows: [], snapshots: [], events: [], window_minutes: 10 };
      case "coding":
        return { items: completed ? demo.demoCoding : [], total: completed ? 1 : 0, limit: 50, offset: 0 };
      case "integrity":
        return { session_id: id, events: completed ? demo.demoIntegrity : demo.demoLiveIntegrity, counts: {}, counts_by_severity: {}, policy: { monitor_focus: true } };
      case "reviews":
        return { session_id: id, items: completed ? demo.demoReviews : [], total: completed ? 1 : 0, limit: 50, offset: 0, current_ai_recommendation: "proceed" };
      case "reports/recruiter":
        return completed ? demo.demoReportResponse() : notFound(path);
      case "reports/candidate":
        return completed ? demo.demoCandidateReport : notFound(path);
      default:
        notFound(path);
    }
  }
  if (path === "/analytics/overview") return demo.demoOverview;
  if (path === "/analytics/funnel") return demo.demoFunnel;
  if (path === "/analytics/scores") return demo.demoScoreDistributions;
  if (path === "/analytics/competencies") return demo.demoCompetencyAnalytics;
  if (path === "/analytics/completion") return demo.demoCompletion;
  if (path === "/analytics/integrity") return demo.demoIntegrityAnalytics;
  if (path === "/analytics/hints") return demo.demoHints;
  if (path === "/analytics/model-usage") return demo.demoModelUsage;
  if (path === "/integrity/events") {
    const statuses = ([] as unknown[]).concat(params.status ?? []).map(String);
    const items = demo.demoIntegrityQueue.filter((e) => !statuses.length || statuses.includes(e.review_status));
    return { items, total: items.length, limit: 25, offset: 0 };
  }
  if (path === "/jobs") return { items: demo.demoJobs, total: demo.demoJobs.length };
  if (/^\/jobs\/[^/]+\/rounds$/.test(path)) return { items: [], total: 0 };
  if (path === "/competencies") return { items: demo.demoCatalog, total: demo.demoCatalog.length };
  if (/^\/media\/[^/]+\/url$/.test(path)) return { url: "/api/v1/media/signed/token", expires_at: new Date(Date.now() + 60_000).toISOString() };
  return notFound(path);
}

export function installMockApi() {
  const calls: RecordedCall[] = [];
  vi.spyOn(api, "get").mockImplementation(async (path: string, options?: { params?: Record<string, unknown> }) => {
    calls.push({ method: "GET", path, params: options?.params });
    return get(path, options?.params) as never;
  });
  vi.spyOn(api, "post").mockImplementation(async (path: string, body?: unknown) => {
    calls.push({ method: "POST", path, body });
    if (/\/reviews$/.test(path)) {
      const b = body as { decision: string; notes?: string | null };
      return { id: "rev-new", session_id: demo.DEMO_IDS.completed, reviewer_id: "u-sam", reviewer: { id: "u-sam", full_name: "Sam Rivera" }, decision: b.decision, notes: b.notes ?? null, ai_recommendation_at_review: "proceed", overrides_ai: false, created_at: new Date().toISOString(), candidate_report_released: null } as never;
    }
    if (path === "/integrity/events/review") return { updated: (body as { event_ids: string[] }).event_ids.length, items: [], not_found: [] } as never;
    return { ok: true } as never;
  });
  vi.spyOn(api, "patch").mockImplementation(async (path: string, body?: unknown) => {
    calls.push({ method: "PATCH", path, body });
    return { ...demo.demoIntegrity[0], ...(body as object) } as never;
  });
  return calls;
}

export function signInAs(role: StaffRole = "RECRUITER") {
  useAuthStore.getState().setSession({
    user: { ...demo.demoMe.user, role },
    organization: demo.demoMe.organization,
    permissions: permissionsForRole(role),
  });
}
