/**
 * End-to-end: the complete candidate journey through the REAL stack.
 *
 * Staff API creates an invite -> candidate verifies, consents, uploads a
 * resume (real extraction + LLM profile), passes the system check, starts the
 * interview -> the 3D interviewer speaks (real TTS) -> the candidate answers
 * through a fake microphone fed with recorded speech (real AudioWorklet ->
 * WebSocket -> VAD -> Whisper -> engine) -> follow-ups -> coding with a hint ->
 * closing -> evidence-based scoring -> candidate + recruiter reports.
 *
 * Requires: the full compose stack on E2E_BASE_URL (default http://localhost:3200)
 * with ENABLE_TEST_HOOKS=true, seeded demo data, live local models.
 */
import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

import { apiGet, apiPost, poll, staffLogin, testHookPost, type StaffSession } from "../helpers/api";
import { installFakeMedia, say } from "../helpers/media";

const ROOT = path.resolve(__dirname, "../../..");
const RESUME = path.join(ROOT, "samples/resumes/jane_doe_devops.pdf");
const RECRUITER_EMAIL = process.env.E2E_RECRUITER_EMAIL ?? "recruiter@demo.local";
const PASSWORD = process.env.E2E_PASSWORD ?? readEnv("SEED_PASSWORD");
const HOOKS_TOKEN = process.env.E2E_TEST_HOOKS_TOKEN ?? readEnv("TEST_HOOKS_TOKEN");

function readEnv(key: string): string {
  const env = fs.readFileSync(path.join(ROOT, ".env"), "utf8");
  const line = env.split("\n").find((l) => l.startsWith(`${key}=`));
  if (!line) throw new Error(`${key} missing from .env`);
  return line.slice(key.length + 1).trim();
}

interface Page_<T> {
  items: T[];
  total: number;
}

async function findDevOpsJob(staff: StaffSession): Promise<{ jobId: string; roundId: string }> {
  const jobs = await apiGet<Page_<{ id: string; title: string }>>(staff, "/api/v1/jobs?limit=100");
  const job = jobs.items.find((j) => j.title === "Senior DevOps Engineer");
  expect(job, "seeded demo job").toBeTruthy();
  const rounds = await apiGet<Page_<{ id: string; name: string; round_type: string }> | Array<{ id: string; name: string; round_type: string }>>(
    staff,
    `/api/v1/jobs/${job!.id}/rounds`,
  );
  const list = Array.isArray(rounds) ? rounds : rounds.items;
  const round = list.find((r) => r.round_type === "combined") ?? list[0];
  return { jobId: job!.id, roundId: round.id };
}

async function waitForInterviewer(page: Page, timeoutMs = 90_000): Promise<string> {
  // Wait until the interviewer has spoken a new utterance and the floor is ours.
  await expect(page.getByTestId("interview-room")).toHaveAttribute("data-turn-state", /listening/, { timeout: timeoutMs });
  return (await page.getByTestId("subtitle-interviewer").innerText()).trim();
}

test("candidate completes an adaptive voice interview with coding, hint and reports", async ({ page, playwright, baseURL }) => {
  test.setTimeout(30 * 60 * 1000);
  const request = await playwright.request.newContext({ baseURL });
  const staff = await staffLogin(request, RECRUITER_EMAIL, PASSWORD);

  // ---------------------------------------------------------------- invite
  const { jobId, roundId } = await findDevOpsJob(staff);
  const email = `e2e.candidate.${Date.now()}@example.com`;
  const candidate = await apiPost<{ id: string }>(staff, "/api/v1/candidates", { full_name: "Jane Doe", email });
  const invite = await apiPost<{ invite_url: string; interview: { id: string } }>(staff, "/api/v1/interviews", {
    candidate_id: candidate.id,
    job_id: jobId,
    round_id: roundId,
    allow_round_selection: false,
    expires_in_hours: 24,
    require_access_code: false,
  });
  const invitePath = new URL(invite.invite_url).pathname;

  // ---------------------------------------------------------------- journey
  await installFakeMedia(page);
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.goto(invitePath);
  await page.getByTestId("invite-email-input").fill(email);
  await page.getByTestId("invite-verify-button").click();
  await page.getByTestId("welcome-continue").click();
  await page.getByTestId("instructions-continue").click();

  const items = page.locator('[data-testid^="consent-item-"]');
  await expect(items.first()).toBeVisible();
  for (let i = 0; i < (await items.count()); i++) await items.nth(i).check();
  await page.getByTestId("consent-accept-button").click();

  await page.getByTestId("resume-file-input").setInputFiles(RESUME);
  await expect(page.getByTestId("resume-continue")).toBeEnabled({ timeout: 4 * 60_000 });
  await expect(page.getByTestId("resume-summary")).toContainText(/DevOps|Kubernetes/i, { timeout: 5 * 60_000 });
  await page.getByTestId("resume-continue").click();

  await page.getByTestId("syscheck-run").click();
  await expect(page.getByTestId("syscheck-continue")).toBeEnabled({ timeout: 90_000 });
  await page.getByTestId("syscheck-continue").click();

  // ---------------------------------------------------------------- interview
  await page.getByTestId("interview-begin-button").click();
  const room = page.getByTestId("interview-room");
  await expect(room).toHaveAttribute("data-session-status", /live/, { timeout: 60_000 });

  const sessions = await poll(
    async () => {
      const res = await apiGet<Page_<{ id: string; candidate?: { id: string }; candidate_id?: string }>>(
        staff,
        `/api/v1/sessions?candidate_id=${candidate.id}`,
      );
      return res.items.length ? res.items : null;
    },
    { timeoutMs: 30_000, what: "session row" },
  );
  const sessionId = sessions[0].id;

  const asked: string[] = [];
  asked.push(await waitForInterviewer(page, 120_000));
  for (const clip of ["intro", "kubernetes_strong", "cost_vague", "cost_specific"]) {
    await say(page, clip);
    const next = await waitForInterviewer(page);
    expect(next.length).toBeGreaterThan(10);
    asked.push(next);
  }
  // Adaptive behaviour: the vague, off-topic cost answer must draw either a specificity follow-up on the
  // cost claim or a redirect to the unanswered scaling question (both are what a human interviewer does).
  const adaptive = /\b(spend|baseline|before|after|month|measure|which|how much|percent|specific|karpenter|node|scal|threshold|metric)/i;
  expect(adaptive.test(asked[3]) || adaptive.test(asked[4])).toBe(true);

  // ---------------------------------------------------------------- coding
  await testHookPost(staff, `/api/v1/test/sessions/${sessionId}/force-coding`, {}, HOOKS_TOKEN);
  await say(page, "generic_medium");
  await expect(page.getByTestId("coding-workspace")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("coding-hints-remaining")).toHaveText(/Hints remaining: 3/);
  await page.getByTestId("coding-hint-button").click();
  await expect(page.getByTestId("coding-hints-remaining")).toHaveText(/Hints remaining: 2/, { timeout: 60_000 });
  await expect(page.getByTestId("coding-hint-1")).toBeVisible();
  await say(page, "coding_think_aloud");
  await page.getByTestId("coding-run-tests").click();
  await expect(page.getByTestId("coding-results")).toHaveAttribute("data-status", /passed|failed|error|compile_error/, { timeout: 60_000 });
  await page.getByTestId("coding-submit").click();

  // ---------------------------------------------------------------- closing
  await testHookPost(staff, `/api/v1/test/sessions/${sessionId}/advance-clock`, { seconds: 3600 }, HOOKS_TOKEN);
  await say(page, "complexity");
  await expect(page.getByTestId("complete-page")).toBeVisible({ timeout: 5 * 60_000 });

  // ---------------------------------------------------------------- evidence + reports
  const detail = await poll(
    async () => {
      const d = await apiGet<{ status: string }>(staff, `/api/v1/sessions/${sessionId}`);
      return d.status === "completed" ? d : null;
    },
    { timeoutMs: 6 * 60_000, what: "session completed" },
  );
  expect(detail.status).toBe("completed");
  const transcript = await apiGet<unknown[] | { items: unknown[] }>(staff, `/api/v1/sessions/${sessionId}/transcript`);
  expect((Array.isArray(transcript) ? transcript : transcript.items).length).toBeGreaterThan(6);
  const report = await poll(
    async () => {
      const r = await apiGet<{ status: string; content?: unknown }>(staff, `/api/v1/sessions/${sessionId}/reports/recruiter`);
      return r.status === "ready" ? r : null;
    },
    { timeoutMs: 6 * 60_000, what: "recruiter report" },
  );
  expect(report.content).toBeTruthy();

  await page.getByTestId("report-link").click();
  await expect(page.getByTestId("candidate-report")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("report-overall-score")).toContainText(/\d/);

  fs.mkdirSync(path.join(__dirname, "../test-results"), { recursive: true });
  fs.writeFileSync(
    path.join(__dirname, "../test-results/e2e-session.json"),
    JSON.stringify({ sessionId, asked, consoleErrors }, null, 2),
  );
});
