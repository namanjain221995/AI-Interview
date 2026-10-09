import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

import {
  consentBody,
  deriveSteps,
  normalizeCandidateReport,
  normalizeDisclosure,
  normalizeInterview,
  normalizeInvite,
  normalizeResume,
  normalizeStepKey,
  postSystemCheck,
  type PortalInterviewView,
} from "../api";
import { completedSteps, journeySteps, requiredRedirect, resumeStep, stepAfter, stepBefore } from "../journey";

function interview(over: Partial<PortalInterviewView> = {}): PortalInterviewView {
  return { ...base(), ...over };
}

function base(): PortalInterviewView {
  return normalizeInterview({
    id: "i1",
    status: "invited",
    organization: { name: "Demo Corp" },
    job: { id: "j1", title: "Senior DevOps Engineer" },
    rounds_offered: [{ id: "r1", name: "Combined AI Round", round_type: "combined", duration_minutes: 30, expected_difficulty: "medium", coding_enabled: true }],
    selected_round_id: "r1",
    allow_round_selection: false,
    policy: {},
    steps_completed: [],
    expires_at: "2026-10-09T00:00:00Z",
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("journey", () => {
  it("omits round selection unless the candidate can choose between rounds", () => {
    expect(journeySteps(interview()).map((s) => s.key)).toEqual(["welcome", "instructions", "consent", "resume", "system-check", "interview"]);
    const choosing = interview({
      allowRoundSelection: true,
      rounds: [interview().rounds[0]!, { ...interview().rounds[0]!, id: "r2", name: "Technical" }],
    });
    expect(journeySteps(choosing).map((s) => s.key)).toContain("round");
    expect(stepAfter("resume", interview())).toBe("system-check");
    expect(stepAfter("resume", choosing)).toBe("round");
    expect(stepBefore("system-check", interview())).toBe("resume");
    expect(stepBefore("welcome", interview())).toBeNull();
  });

  it("resumes at the first step still to do", () => {
    expect(resumeStep(interview())).toBe("welcome");
    expect(resumeStep(interview(), ["welcome", "instructions"])).toBe("consent");
    expect(resumeStep(interview({ stepsCompleted: ["consent"] }))).toBe("resume");
    expect(resumeStep(interview({ stepsCompleted: ["consent", "resume"] }))).toBe("system-check");
    expect(resumeStep(interview({ stepsCompleted: ["consent", "resume", "system-check"] }))).toBe("interview");
    expect(resumeStep(interview({ latestSession: { id: "s", status: "paused" } }))).toBe("interview");
    expect(resumeStep(interview({ latestSession: { id: "s", status: "completed" } }))).toBe("complete");
    expect(resumeStep(interview({ status: "completed" }))).toBe("complete");
  });

  it("counts the informational steps as done once consent is given", () => {
    expect(completedSteps(interview({ stepsCompleted: ["consent"] }))).toEqual(["welcome", "instructions", "consent"]);
  });

  it("sends steps that need consent back to the consent page", () => {
    expect(requiredRedirect("resume", { stepsCompleted: [] })).toBe("consent");
    expect(requiredRedirect("interview", { stepsCompleted: ["welcome"] })).toBe("consent");
    expect(requiredRedirect("resume", { stepsCompleted: ["consent"] })).toBeNull();
    expect(requiredRedirect("instructions", { stepsCompleted: [] })).toBeNull();
  });

  it("normalises server step names", () => {
    expect(["consent_given", "resume_uploaded", "round_selected", "system_check", "session_started"].map(normalizeStepKey)).toEqual([
      "consent",
      "resume",
      "round",
      "system-check",
      "interview",
    ]);
    expect(base().stepsCompleted).toEqual([]);
    expect(normalizeInterview({ id: "x", steps_completed: { consent: true, resume: false, system_check: true } }).stepsCompleted).toEqual(["consent", "system-check"]);
  });
});

describe("portal normalisers", () => {
  it("reads invite info and its closed flags", () => {
    expect(normalizeInvite({ organization_name: "Demo Corp", job_title: "SRE", requires_access_code: true, status: "invited" })).toMatchObject({
      organizationName: "Demo Corp",
      jobTitle: "SRE",
      requiresAccessCode: true,
      status: "invited",
    });
    expect(normalizeInvite({ expired: true }).status).toBe("expired");
    expect(normalizeInvite({ cancelled: true }).status).toBe("cancelled");
  });

  it("reads the itemised disclosure and builds the consent body", () => {
    const d = normalizeDisclosure({
      policy_version: "2026-10-01",
      policy_hash: "abc",
      title: "Before you begin",
      intro: "Here is what happens.",
      items: [
        { key: "microphone", title: "Microphone", description: "Recorded for transcription.", required: true, enabled: true },
        { key: "camera_frames", title: "Camera checks", description: "Every 10 s.", required: false, enabled: true },
        { key: "screen_share", title: "Screen", description: "", required: true, enabled: false },
      ],
      retention_summary: "Audio 90 days.",
      purpose: "Assess your answers.",
      human_review_statement: "A person decides.",
      limitations_statement: "A browser can't lock your computer.",
    });
    expect(d.items.map((i) => i.key)).toEqual(["microphone", "camera_frames"]);
    expect(d.mapShaped).toBe(false);
    expect(consentBody(d, { microphone: true })).toEqual({ policy_hash: "abc", items: { microphone: true, camera_frames: false } });
  });

  it("uses required_items for validation and passes the consent_items template through", () => {
    const d = normalizeDisclosure({
      policy_version: "2026-10-01",
      policy_hash: "h2",
      items: [
        { key: "microphone", title: "Microphone", description: "", required: false, enabled: true },
        { key: "camera_frames", title: "Camera checks", description: "", required: true, enabled: true },
      ],
      required_items: ["microphone"],
      consent_items: { microphone: true, camera_frames: true, retention_days: 90, screen_share: false },
      text_markdown: "Full text",
    });
    expect(d.items.map((i) => [i.key, i.required])).toEqual([
      ["microphone", true],
      ["camera_frames", false],
    ]);
    // Shown items always come from the checkboxes; unshown template keys pass through (booleans never auto-granted).
    expect(consentBody(d, { microphone: true }).items).toEqual({ retention_days: 90, screen_share: false, microphone: true, camera_frames: false });
  });

  it("accepts the boolean-map disclosure shape too", () => {
    const d = normalizeDisclosure({ policy_hash: "h", policy_version: "1", text_markdown: "# Policy", items: { microphone: true, camera: true, screen_share: false, retention_days: 90 } });
    expect(d.items.map((i) => i.key)).toEqual(["microphone", "camera"]);
    expect(d.items[0]!.title).toBe("Microphone");
    expect(consentBody(d, { microphone: true, camera: true }).items).toEqual({ microphone: true, camera: true, retention_days: 90 });
  });

  it("reads resume progress from either response shape", () => {
    const nested = normalizeResume({
      resume: { id: "r1", original_filename: "jane.pdf", size_bytes: 1000, status: "analyzing", progress: { percent: 60 } },
      profile_summary: null,
    });
    expect(nested).toMatchObject({ resumeId: "r1", filename: "jane.pdf", status: "analyzing", profileReady: false, percent: 60 });
    expect(nested.steps.find((s) => s.state === "active")?.label).toBe("Understanding your experience");

    const flat = normalizeResume({
      resume_id: "r2",
      status: "ready",
      profile_ready: true,
      steps: [{ key: "extract", label: "Reading", state: "done" }],
      summary: { name: "Jane Doe", headline: "DevOps engineer", experience_years: 8, roles: [{ title: "SRE", company: "Acme", start: "2019" }], skills: { cloud: [{ name: "AWS" }], containers: [{ name: "Kubernetes" }] } },
    });
    expect(flat.profileReady).toBe(true);
    expect(flat.summary).toMatchObject({ name: "Jane Doe", experienceYears: 8, skills: ["AWS", "Kubernetes"] });
    expect(flat.summary?.roles[0]).toEqual({ title: "SRE", company: "Acme", period: "2019 – Present" });

    expect(normalizeResume({ resume: null }).status).toBe("none");
    expect(normalizeResume({ status: "ready", profile_ready: false }).steps.find((s) => s.state === "active")?.key).toBe("profile");
  });

  it("reads the real candidate_view shape: released report, 0-100 score, questions and coding", () => {
    const r = normalizeCandidateReport({
      session_id: "s1",
      report_id: "rep1",
      version: 1,
      generated_at: "2026-10-07T10:00:00Z",
      released_at: "2026-10-07T10:00:05Z",
      artifact_url: "/api/v1/media/signed/abc",
      content: {
        report_type: "candidate",
        session: { id: "s1", elapsed_seconds: 1710 },
        job: { title: "Senior DevOps Engineer" },
        round: { name: "Combined AI Round", round_type: "combined" },
        overall_score: 8,
        competencies: [{ key: "kubernetes", name: "Kubernetes", score: 6.5, confidence: 0.7, evidence_count: 4, unanswered_areas: ["autoscaling"] }],
        questions: [
          { id: "q1", sequence: 1, text: "Walk me through your cluster.", parent_question_id: null, feedback: { summary: "Clear overview.", missing_details: ["node counts"] } },
          { id: "q2", sequence: 2, text: "How many nodes?", parent_question_id: "q1", feedback: null },
        ],
        coding: [{ title: "Most frequent error", language: "python", hints_used: 1, tests_passed: 6, tests_total: 8, correctness_summary: "Handles the main cases.", weaknesses: ["Empty input"] }],
        narrative: null,
        note: "This report is feedback, not a decision.",
      },
    });
    expect(r.status).toBe("released");
    expect(r.overallScore).toBe(8); // a low score on the 0-100 scale is never inflated
    expect(r.durationMinutes).toBe(29);
    expect(r.topics[0]).toEqual({ key: "kubernetes", label: "Kubernetes", score: 6.5, unanswered: ["autoscaling"] });
    expect(r.questions.map((q) => [q.id, q.summary, q.followUp])).toEqual([
      ["q1", "Clear overview.", false],
      ["q2", null, true],
    ]);
    expect(r.coding[0]).toMatchObject({ summary: "Handles the main cases.", improvements: ["Empty input"], testsPassed: 6, testsTotal: 8 });
    expect(r.artifactUrl).toBe("/api/v1/media/signed/abc");
    expect(r.note).toBe("This report is feedback, not a decision.");
  });

  it("reads resume steps nested under resume, hiding skipped stages", () => {
    const v = normalizeResume({
      resume: {
        id: "r1",
        original_filename: "cv.pdf",
        size_bytes: 10,
        status: "ready",
        profile_ready: false,
        steps: [
          { key: "uploaded", label: "Uploaded", state: "done" },
          { key: "ocr", label: "Scanning pages", state: "skipped" },
          { key: "analyzing", label: "Analyzing experience and skills", state: "done" },
          { key: "ready", label: "Ready", state: "done" },
        ],
      },
      profile_summary: { candidate_name: "Jane Doe", top_skills: ["Kubernetes", "Terraform"], certifications: ["CKA"], roles: [{ title: "SRE", company: "Acme", start: "2020", end: "2024" }] },
    });
    expect(v.steps.map((s) => [s.key, s.state])).toEqual([
      ["uploaded", "done"],
      ["analyzing", "done"],
      ["profile", "active"],
    ]);
    expect(v.profileReady).toBe(false);
    expect(v.summary).toMatchObject({ name: "Jane Doe", skills: ["Kubernetes", "Terraform"], certifications: ["CKA"] });
    expect(v.summary?.roles[0]?.period).toBe("2020 – 2024");
  });

  it("lets the candidate continue once the text is extracted, before the profile is ready", () => {
    const analysing = normalizeResume({ resume: { id: "r", original_filename: "cv.pdf", size_bytes: 1, status: "analyzing", profile_ready: false, steps: [{ key: "extracting", label: "Reading the document", state: "done" }, { key: "analyzing", label: "Analyzing experience and skills", state: "active" }] } });
    expect(analysing).toMatchObject({ extracted: true, profileReady: false });
    const reading = normalizeResume({ resume: { id: "r", original_filename: "cv.pdf", size_bytes: 1, status: "extracting", steps: [{ key: "extracting", label: "Reading the document", state: "active" }, { key: "analyzing", label: "Analyzing", state: "pending" }] } });
    expect(reading.extracted).toBe(false);
    const failed = normalizeResume({ resume: { id: "r", original_filename: "cv.pdf", size_bytes: 1, status: "failed", error: "We couldn't read this file." } });
    expect(failed).toMatchObject({ extracted: false, error: "We couldn't read this file." });
  });

  it("names pipeline steps for candidates and keeps their timestamps", () => {
    const v = normalizeResume({
      resume: {
        id: "r",
        original_filename: "cv.pdf",
        size_bytes: 1,
        status: "analyzing",
        created_at: "2026-10-07T12:20:15Z",
        steps: [
          { key: "validating", label: "Security and format checks", state: "done", at: "2026-10-07T12:20:15Z" },
          { key: "analyzing", label: "Analyzing experience and skills", state: "active", at: "2026-10-07T12:20:16Z" },
          { key: "ready", label: "Ready", state: "pending" },
        ],
      },
    });
    // Consistent, candidate-friendly wording; the terminal "Ready" marker is not a step to show.
    expect(v.steps.map((s) => s.label)).toEqual(["Security check", "Understanding your experience"]);
    expect(v.steps[1]!.at).toBe("2026-10-07T12:20:16Z");
    expect(v.createdAt).toBe("2026-10-07T12:20:15Z");
    expect(v.extracted).toBe(true);
  });

  it("re-sends the system check without the avatar fields to an API that predates them", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(new ApiError({ status: 422, code: "validation_failed", message: "The request is invalid.", details: { errors: [{ loc: ["body", "avatar_mode"], msg: "Extra inputs are not permitted" }] } }))
      .mockResolvedValueOnce({ passed: true } as never);
    await postSystemCheck("i1", { camera: true, microphone: true, speakers: true, webgl: true, avatar_mode: "3d", avatar_fps: 58 });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0]![1]).toMatchObject({ avatar_mode: "3d", avatar_fps: 58 });
    expect(post.mock.calls[1]![1]).not.toHaveProperty("avatar_mode");
    expect(post.mock.calls[1]![1]).not.toHaveProperty("avatar_fps");
  });

  it("does not hide other system-check errors", async () => {
    vi.spyOn(api, "post").mockRejectedValue(new ApiError({ status: 422, code: "validation_failed", message: "invalid", details: { errors: [{ loc: ["body", "displays"], msg: "too big" }] } }));
    await expect(postSystemCheck("i1", { camera: true, microphone: true, speakers: true, webgl: true, avatar_mode: "2d" })).rejects.toBeInstanceOf(ApiError);
  });

  it("derives pipeline steps for failures", () => {
    expect(deriveSteps("failed", false).some((s) => s.state === "failed")).toBe(true);
    expect(deriveSteps("ready", true).every((s) => s.state === "done")).toBe(true);
  });

  it("reads the candidate report and keeps it candidate-safe", () => {
    const r = normalizeCandidateReport({
      status: "ready",
      overall_score: 72.4,
      recommendation: "proceed",
      content: {
        narrative: { overall_summary: "Solid", technical_strengths: [{ title: "Kubernetes", detail: "Clear", evidence_ids: [], competency_key: "k8s" }] },
        competencies: [
          { key: "kubernetes", name: "Kubernetes", score: 7.6 },
          { key: "aws", score: 81 },
        ],
        coding: [{ title: "Most frequent", language: "python", tests_passed: 4, tests_total: 5, hints_used: 1 }],
        questions: [{ id: "q1", text: "How do you scale?" }],
      },
    });
    expect(r.overallScore).toBe(72.4);
    expect(r.topics).toEqual([
      { key: "kubernetes", label: "Kubernetes", score: 7.6, unanswered: [] },
      { key: "aws", label: "Aws", score: 8.1, unanswered: [] },
    ]);
    expect(r.coding[0]).toMatchObject({ testsPassed: 4, testsTotal: 5, hintsUsed: 1 });
    expect(r.questionText.q1).toBe("How do you scale?");
    expect(JSON.stringify(r)).not.toContain("proceed");
  });
});
