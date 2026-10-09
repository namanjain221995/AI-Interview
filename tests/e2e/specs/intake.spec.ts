/**
 * End-to-end: the Intake Interview (round_type `intake`) through real services with the mock AI registry.
 *
 * Staff create the intake in the staff app -> the candidate opens the link, verifies, consents, uploads a
 * résumé, fills in the details form, confirms the résumé review, passes the system check and starts -> the
 * room walks the seven stages in order (stage indicator), with the three exercises: Drill A (passage shown
 * while reading aloud), Drill B (a different passage, hidden by the server at 30 s and never in the DOM, the
 * WebSocket traffic after its one phase message, or the transcript), the topic card (60 s preparation, 120 s
 * speaking) -> closing -> `intake.evaluate` and `intake.report` run in the worker -> the staff review page shows
 * the scorecard (seven competencies, weighted total, band labelled as the AI recommendation) and the report
 * sections -> a reviewer records a decision.
 *
 * Answers: typed answers for ordinary questions, and the fake microphone (fixtures/fake-media.js + recorded
 * clips -> real AudioWorklet -> WebSocket -> server VAD -> mock speech recognition) for the opening answer and
 * the spoken exercises. No test hook is needed: the exercises are finished with their own buttons.
 *
 * Runs on the mock-AI stack: `cd apps/api && uv run python ../../tests/e2e/mock-stack/stack.py --run
 * specs/intake.spec.ts` (sets E2E_BASE_URL and E2E_MOCK_STACK=1). Against another stack seeded with
 * `python -m app.seed --demo` it is skipped unless E2E_INTAKE_LIVE=1: with live models one run sends about 30 turns
 * and 8 evaluator calls to the shared GPU (run it only when the GPU is below 80 %).
 */
import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";

import { installFakeMedia, say } from "../helpers/media";

const ROOT = path.resolve(__dirname, "../../..");
const RESUME = path.join(ROOT, "samples/resumes/jane_doe_devops.pdf");
const RECRUITER_EMAIL = process.env.E2E_RECRUITER_EMAIL ?? "recruiter@demo.local";
const SHOTS = process.env.E2E_SCREENSHOT_DIR ?? path.join(__dirname, "../test-results/intake");
const INTAKE_JOB = "AI/ML Training Program — Intake";
const STAGES = ["greeting", "resume", "mandatory", "reading", "topic", "learning", "close"] as const;
const COMPETENCIES = ["communication", "technical_reading", "structured_answer", "technical", "trainability", "composure", "adaptability"];

function readEnv(key: string): string {
  const env = fs.readFileSync(path.join(ROOT, ".env"), "utf8");
  const line = env.split("\n").find((l) => l.startsWith(`${key}=`));
  if (!line) throw new Error(`${key} missing from .env`);
  return line.slice(key.length + 1).trim();
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function shot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}

/** Typed answers by stage: specific, ordinary and long enough to count as evidence (25+ words). */
const ANSWERS: Record<string, string[]> = {
  greeting: [
    "I'm proudest of an internal reporting pipeline I built last year. I wrote the Python scripts that pull the raw exports, clean them with pandas and load them into Postgres, and I set up the nightly schedule myself.",
    "My own part was the extraction and cleaning code and the schedule; a colleague built the dashboard on top. The hardest part was duplicate records, which I fixed by adding a unique key and an upsert step.",
  ],
  resume: [
    "On that project I personally wrote the Terraform modules for the networking and the cluster, and I reviewed every change through pull requests. The team owned the application charts, so my part was the infrastructure layer.",
    "It worked like this: a pipeline ran terraform plan on every pull request, a reviewer approved it, and the apply step ran from the main branch with a locked remote state, so two people could never apply at once.",
    "If the traffic doubled, the first thing to break would be the node group sizing, because we scaled it by hand. Today I would add the cluster autoscaler and alerts on pending pods before changing anything else.",
  ],
  mandatory: [
    "I'm aiming for an AI and machine learning engineering role. I have been doing data and infrastructure work, and I want to move closer to building and deploying models, which this program prepares people for.",
    "Python is the language I know best. I use it every week for scripts that clean data, call APIs and automate deployments, and I would rate myself around a seven because I still look up some advanced features.",
    "A list is mutable, so you can append or change items after you create it, while a tuple is immutable. Tuples can be dictionary keys because they are hashable, and they signal that the data should not change.",
    "Someone at nine out of ten would know the standard library deeply, write their own decorators and context managers, and profile slow code instead of guessing. I'm still building that depth through practice.",
    "Yes, I understand that these roles usually expect several years of hands-on experience. I have about three years myself, and I can commit to the full training schedule and the extra practice it needs.",
    "The biggest gap is machine learning itself: I have used pandas and scikit-learn in small experiments only. I plan to close it with the program's projects and about twenty extra hours a week.",
  ],
  reading: [
    "The main point was how the system handles requests between programs, and the steps it follows when something fails. I would check the logs first and then retry the request in a controlled way.",
    "I remember the part about why it matters: it keeps the services independent, so one team can change their side without breaking the others, as long as they keep the agreed format.",
    "If I had to explain it to a new colleague, I would use the example of a restaurant: you order from the menu through the waiter and never walk into the kitchen yourself.",
  ],
  topic: [
    "The point I was least confident about was the trade-offs. I would say the main trade-off is speed against accuracy: a simpler approach is faster to build and explain, but it can miss cases a richer one catches.",
    "A different analogy would be a library catalogue: you don't read every book to find one, you use the index, which is faster but only as good as the way the books were labelled in the first place.",
  ],
  learning: [
    "I would start with the fundamentals for the first week, then build one small project end to end, and ask for code reviews every few days so I find my mistakes early instead of at the end of the three weeks.",
    "Recently I taught myself Docker. I read the official tutorial, containerised one of my own scripts, broke it a few times, and then explained the steps to a teammate, which showed me which parts I really understood.",
    "When I get stuck I write down exactly what I expected and what happened, search the documentation and the error message, and if I'm still stuck after an hour I ask someone with a minimal example.",
    "I know I have really learned something when I can explain it without notes and use it on a new problem. Recognising it in a tutorial is not the same, so I always build something small afterwards.",
  ],
  close: ["No, I don't have any more questions. Thank you for your time, this was helpful."],
};

interface StaffApi {
  request: APIRequestContext; // the signed-in staff context (reads only: no CSRF token needed)
}

async function staffApi(browser: Browser, baseURL: string): Promise<{ page: Page; api: StaffApi; close: () => Promise<void> }> {
  const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByTestId("login-email").fill(RECRUITER_EMAIL);
  await page.getByTestId("login-password").fill(process.env.E2E_PASSWORD ?? readEnv("SEED_PASSWORD"));
  await page.getByTestId("login-submit").click();
  await page.waitForURL(/\/app(\/|$)/, { timeout: 60_000 });
  return { page, api: { request: context.request }, close: () => context.close() };
}

async function getJson<T>(api: StaffApi, url: string): Promise<T> {
  const res = await api.request.get(url);
  expect(res.ok(), `${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

async function poll<T>(fn: () => Promise<T | null | undefined>, opts: { timeoutMs: number; what: string; intervalMs?: number }): Promise<T> {
  const deadline = Date.now() + opts.timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      const value = await fn();
      if (value) return value;
    } catch (err) {
      last = err;
    }
    await sleep(opts.intervalMs ?? 2000);
  }
  throw new Error(`timed out waiting for ${opts.what}${last ? `: ${String(last)}` : ""}`);
}

/** Records stage changes and Drill B passage visibility in the page itself (100 ms resolution, no polling gaps). */
const RECORDER = `(() => {
  const rec = { stages: [], drillB: { shownAt: null, hiddenAt: null, text: "" }, drillA: { text: "" } };
  window.__intakeE2E = rec;
  setInterval(() => {
    const stage = document.querySelector('[data-testid="intake-stage-indicator"]')?.getAttribute("data-stage");
    if (stage && rec.stages[rec.stages.length - 1] !== stage) rec.stages.push(stage);
    const panel = document.querySelector('[data-testid="activity-panel"]');
    const kind = panel?.getAttribute("data-kind");
    const passage = panel?.querySelector('[data-testid="activity-passage"]');
    const text = passage ? passage.innerText.trim() : "";
    if (kind === "reading_aloud" && text && !rec.drillA.text) rec.drillA.text = text;
    if (kind === "read_relay" && text && rec.drillB.shownAt === null) { rec.drillB.shownAt = performance.now(); rec.drillB.text = text; }
    if (rec.drillB.shownAt !== null && rec.drillB.hiddenAt === null && !(kind === "read_relay" && text)) rec.drillB.hiddenAt = performance.now();
  }, 100);
})();`;

interface Recorded {
  stages: string[];
  drillB: { shownAt: number | null; hiddenAt: number | null; text: string };
  drillA: { text: string };
}

async function recorded(page: Page): Promise<Recorded> {
  return page.evaluate(() => (window as unknown as { __intakeE2E: Recorded }).__intakeE2E);
}

interface RoomSnapshot {
  complete: boolean;
  turn: string | null;
  stage: string | null;
  panel: { kind: string; phase: string; status: string; remainingS: number | null } | null;
}

/** The room's state in one synchronous DOM read (no auto-waiting locator between reads). */
async function readRoom(page: Page): Promise<RoomSnapshot> {
  return page.evaluate(() => {
    const q = (id: string) => document.querySelector(`[data-testid="${id}"]`);
    const panel = q("activity-panel");
    const countdown = q("activity-countdown");
    return {
      complete: Boolean(q("complete-page")),
      turn: q("interview-room")?.getAttribute("data-turn-state") ?? null,
      stage: q("intake-stage-indicator")?.getAttribute("data-stage") ?? null,
      panel: panel
        ? {
            kind: panel.getAttribute("data-kind") ?? "",
            phase: panel.getAttribute("data-phase") ?? "",
            status: panel.getAttribute("data-status") ?? "",
            remainingS: countdown ? Number(countdown.getAttribute("data-remaining-s")) : null,
          }
        : null,
    };
  });
}

/**
 * A distinctive run of eight plain ASCII words from the first paragraph of a passage, to look for in the DOM,
 * the WebSocket frames (JSON may escape other characters) and the transcripts.
 */
function snippet(text: string): string {
  const words = (text.split(/\n+/)[0] ?? text).trim().split(/\s+/);
  for (let i = 0; i + 8 <= words.length; i++) {
    const run = words.slice(i, i + 8);
    if (run.every((w) => /^[A-Za-z0-9,.;:()-]+$/.test(w))) return run.join(" ");
  }
  throw new Error("no plain-ASCII run of eight words in the passage");
}

test.use({ actionTimeout: 45_000 });

test.skip(
  process.env.E2E_MOCK_STACK !== "1" && process.env.E2E_INTAKE_LIVE !== "1",
  "runs on the mock-AI stack (tests/e2e/mock-stack/stack.py --run specs/intake.spec.ts) or with E2E_INTAKE_LIVE=1",
);

test("intake interview: staff create -> candidate journey -> seven stages and exercises -> evaluation, report and decision", async ({ page, browser, baseURL }) => {
  test.setTimeout(40 * 60 * 1000);
  const staff = await staffApi(browser, baseURL!);
  const email = `e2e.intake.${Date.now()}@example.com`;

  // ------------------------------------------------------------------ staff creates the intake (staff app)
  await staff.page.goto("/app/intake/new");
  await expect(staff.page.getByTestId("intake-create-page")).toBeVisible();
  await staff.page.getByRole("tab", { name: /New candidate/ }).click();
  await staff.page.getByTestId("intake-candidate-name").fill("Jane Doe");
  await staff.page.getByTestId("intake-candidate-email").fill(email);
  const jobSelect = staff.page.getByTestId("intake-job-select");
  if (!(await jobSelect.innerText()).includes(INTAKE_JOB)) {
    await jobSelect.click();
    await staff.page.getByRole("option", { name: INTAKE_JOB }).click();
  }
  await shot(staff.page, "01-staff-create");
  await staff.page.getByTestId("intake-send").click();
  const link = staff.page.getByTestId("intake-invite-link");
  await expect(link).toBeVisible({ timeout: 30_000 });
  const inviteUrl = ((await link.inputValue().catch(() => "")) || (await link.innerText())).trim();
  expect(inviteUrl).toMatch(/\/i\//);
  await shot(staff.page, "02-staff-invite-ready");

  // ------------------------------------------------------------------ candidate journey
  await installFakeMedia(page);
  await page.addInitScript({ content: RECORDER });
  const frames: string[] = [];
  page.on("websocket", (ws) => ws.on("framereceived", (f) => typeof f.payload === "string" && frames.push(f.payload)));
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.goto(new URL(inviteUrl).pathname);
  await page.getByTestId("invite-email-input").fill(email);
  await page.getByTestId("invite-verify-button").click();
  await expect(page.getByTestId("intake-overview")).toBeVisible({ timeout: 30_000 });
  await shot(page, "03-candidate-welcome");
  await page.getByTestId("welcome-continue").click();
  await expect(page.getByTestId("intake-instructions")).toBeVisible();
  await page.getByTestId("instructions-continue").click();

  const consent = page.locator('[data-testid^="consent-item-"]');
  await expect(consent.first()).toBeVisible();
  for (let i = 0; i < (await consent.count()); i++) await consent.nth(i).check();
  await page.getByTestId("consent-accept-button").click();

  await page.getByTestId("resume-file-input").setInputFiles(RESUME);
  await expect(page.getByTestId("resume-continue")).toBeEnabled({ timeout: 2 * 60_000 });
  await page.getByTestId("resume-continue").click();

  // Details form (MQ1 questions only when the organisation asks them).
  await expect(page.getByTestId("intake-details-form")).toBeVisible({ timeout: 30_000 });
  const field = (name: string) => page.getByTestId(`intake-field-${name}`);
  if (await field("target_role-ai_ml").isVisible()) await field("target_role-ai_ml").click();
  if (await field("open_to_ai_ml-yes").isVisible()) await field("open_to_ai_ml-yes").click();
  await field("technical_background").fill(
    "Three years of DevOps and data work: Python scripts for data cleaning, Terraform and Kubernetes on AWS, and SQL reporting for the operations team.",
  );
  await field("primary_language-python").click();
  await field("self_rating-7").click();
  await field("scripting-regular").click();
  await field("years_hands_on").fill("3");
  await field("experience_level_comfort-yes").click();
  await field("training_commitment-full").click();
  await field("weekly_hours").fill("20");
  await field("availability-immediately").click();
  if (await field("work_authorized-yes").isVisible()) await field("work_authorized-yes").click();
  if (await field("needs_sponsorship-no").isVisible()) await field("needs_sponsorship-no").click();
  await field("accuracy_confirmed").check();
  await shot(page, "04-candidate-details");
  await page.getByTestId("intake-details-submit").click();

  // Résumé review: confirm what was read from the résumé.
  await expect(page.getByTestId("intake-resume-review")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("resume-review-confirm")).toBeEnabled({ timeout: 3 * 60_000 });
  await shot(page, "05-candidate-resume-review");
  await page.getByTestId("resume-review-confirm").click();

  await page.getByTestId("syscheck-run").click();
  await expect(page.getByTestId("syscheck-continue")).toBeEnabled({ timeout: 90_000 });
  await page.getByTestId("syscheck-continue").click();

  // ------------------------------------------------------------------ interview room
  await page.getByTestId("interview-begin-button").click();
  const room = page.getByTestId("interview-room");
  await expect(room).toHaveAttribute("data-session-status", /live/, { timeout: 90_000 });
  const indicator = page.getByTestId("intake-stage-indicator");
  await expect(indicator).toHaveAttribute("data-stage", "greeting", { timeout: 60_000 });
  await shot(page, "06-room-greeting");

  const answered: Record<string, number> = {};
  const steps: string[] = []; // what the loop did, for the run summary (and the failure message on a stall)
  const step = (what: string) => steps.push(`${new Date().toISOString().slice(11, 19)} ${what}`);
  const typedAnswer = async (stage: string) => {
    const pool = ANSWERS[stage] ?? ANSWERS.learning;
    const text = pool[Math.min(answered[stage] ?? 0, pool.length - 1)];
    answered[stage] = (answered[stage] ?? 0) + 1;
    const input = page.getByTestId("typed-answer-input");
    // The dock button toggles the panel: open it only when it is not open already.
    if (!(await input.isVisible().catch(() => false))) await page.getByTestId("type-answer-button").click();
    await input.fill(text);
    await page.getByTestId("typed-answer-submit").click();
    step(`typed ${stage} #${answered[stage]}`);
  };
  const speak = async (clip: string) => {
    const limitMs = 90_000;
    await Promise.race([say(page, clip), sleep(limitMs).then(() => Promise.reject(new Error(`clip ${clip} still playing after ${limitMs} ms`)))]);
    step(`spoke ${clip}`);
  };

  const handled = new Set<string>();
  const timers: Record<string, number> = {};
  let lastTurn = "";
  let spokeOpening = false;
  let progressAt = Date.now();
  let progressKey = "";
  const deadline = Date.now() + 30 * 60_000;
  while (Date.now() < deadline) {
    // One synchronous DOM read per tick: locators auto-wait, and the activity panel can disappear between two
    // reads (its brief "Done" state is dismissed after 1.4 s), which would block a getAttribute until the next one.
    const snap = await readRoom(page);
    if (snap.complete) break;
    const key = snap.panel ? `${snap.panel.kind}:${snap.panel.phase}` : "";
    const progress = `${snap.stage}|${key}|${snap.panel?.status ?? ""}|${steps.length}`; // silence prompts do not count
    if (progress !== progressKey) {
      progressKey = progress;
      progressAt = Date.now();
    } else if (Date.now() - progressAt > 150_000) {
      await shot(page, "stall");
      throw new Error(`no progress for 150 s (room ${JSON.stringify(snap)}); last steps:\n${steps.slice(-12).join("\n")}`);
    }
    if (snap.panel && snap.panel.status === "running" && !handled.has(key)) {
      handled.add(key);
      timers[key] = snap.panel.remainingS ?? Number.NaN;
      step(`activity ${key} (${timers[key]} s)`);
      const countdown = page.getByTestId("activity-countdown");
      const finish = page.getByTestId("activity-finish-button");
      if (key === "reading_aloud:read") {
        await expect(page.getByTestId("activity-passage")).toBeVisible();
        await shot(page, "07-drill-a-reading");
        await speak("kubernetes_strong");
        await expect(finish).toBeEnabled({ timeout: 30_000 });
        await finish.click(); // "Finish reading"
        await expect(page.getByTestId("activity-passage")).toHaveCount(0, { timeout: 30_000 });
      } else if (key === "read_relay:read") {
        await expect(page.getByTestId("activity-passage")).toBeVisible();
        await shot(page, "08-drill-b-reading");
        // Never press "I'm ready": the server hides the passage at 30 s.
        await expect(page.getByTestId("activity-passage")).toHaveCount(0, { timeout: 40_000 });
        await shot(page, "09-drill-b-hidden");
      } else if (key === "read_relay:relay") {
        await typedAnswer("reading"); // a typed explanation ends the relay capture
      } else if (key === "topic_card:prepare") {
        await expect(page.getByTestId("activity-card-bullets")).toBeVisible();
        await shot(page, "10-topic-prepare");
        await expect.poll(async () => Number(await countdown.getAttribute("data-remaining-s")), { timeout: 10_000 }).toBeLessThanOrEqual(timers[key] - 2);
        await expect(finish).toBeEnabled({ timeout: 30_000 });
        await finish.click(); // "Start now"
      } else if (key === "topic_card:speak") {
        await shot(page, "11-topic-speak");
        await speak("generic_medium");
        await speak("incident");
        expect(Number(await countdown.getAttribute("data-remaining-s"))).toBeLessThanOrEqual(timers[key] - 30);
        await expect(finish).toBeEnabled({ timeout: 30_000 });
        await finish.click(); // "I'm done"
      }
      continue;
    }
    if (snap.panel) {
      // Armed, between two phases, a handled phase, or the brief "Done": nothing to answer yet. Ordinary questions
      // come after activity.end (the room may already be listening while the next phase's instructions queue up).
      await sleep(250);
      continue;
    }
    if (snap.turn === "listening" && lastTurn !== "listening") {
      if (snap.stage === "greeting" && !spokeOpening) {
        spokeOpening = true;
        await speak("intro");
        const done = page.getByTestId("done-answering-button");
        if (await done.isEnabled().catch(() => false)) await done.click();
      } else {
        await typedAnswer(snap.stage ?? "greeting");
      }
      lastTurn = "answered";
      await sleep(500);
      continue;
    }
    lastTurn = snap.turn ?? "";
    await sleep(250);
  }
  await expect(page.getByTestId("complete-page")).toBeVisible({ timeout: 2 * 60_000 });
  await shot(page, "12-candidate-complete");

  // ------------------------------------------------------------------ what the room showed
  const rec = await recorded(page);
  expect(rec.stages).toEqual([...STAGES]);
  expect(rec.drillA.text.length).toBeGreaterThan(200);
  expect(rec.drillB.text.length).toBeGreaterThan(100);
  expect(rec.drillB.text).not.toEqual(rec.drillA.text);
  const shownMs = (rec.drillB.hiddenAt ?? 0) - (rec.drillB.shownAt ?? 0);
  expect(shownMs, "Drill B passage on screen (ms)").toBeGreaterThan(26_000);
  expect(shownMs, "Drill B passage on screen (ms)").toBeLessThan(33_500);
  for (const phase of ["reading_aloud:read", "read_relay:read", "read_relay:relay", "topic_card:prepare", "topic_card:speak"]) {
    expect(timers[phase], `phase ${phase} handled while running`).toBeDefined();
  }
  expect(timers["topic_card:prepare"]).toBeGreaterThanOrEqual(55);
  expect(timers["topic_card:prepare"]).toBeLessThanOrEqual(60);
  expect(timers["topic_card:speak"]).toBeGreaterThanOrEqual(110);
  expect(timers["topic_card:speak"]).toBeLessThanOrEqual(120);
  const secret = snippet(rec.drillB.text);
  expect(await page.content()).not.toContain(secret);
  // Contract 7.9: the passage travels only in the read phase's start message (never in activity.start,
  // session.state, subtitles, transcripts or the replay outbox).
  const carrying = frames.filter((f) => f.includes(secret)).map((f) => JSON.parse(f) as { type: string; phase?: string; status?: string });
  expect(carrying.length, "WebSocket frames carrying the Drill B passage").toBeGreaterThanOrEqual(1);
  for (const message of carrying) expect([message.type, message.phase, message.status]).toEqual(["activity.phase", "read", "running"]);

  // ------------------------------------------------------------------ evaluation, report, review, decision
  const sessions = await getJson<{ items: Array<{ id: string; candidate?: { email?: string } }> }>(staff.api, `/api/v1/sessions?round_type=intake&limit=100`);
  const session = sessions.items.find((s) => s.candidate?.email === email);
  expect(session, "intake session in the sessions list").toBeTruthy();
  const sessionId = session!.id;
  const intake = await getJson<{ items: Array<{ id: string }> }>(staff.api, `/api/v1/intake/interviews?session_id=${sessionId}`);
  expect(intake.items).toHaveLength(1);
  const intakeId = intake.items[0].id;
  const evaluation = await poll(
    async () => {
      const res = await staff.api.request.get(`/api/v1/intake/interviews/${intakeId}/evaluation`);
      return res.ok() ? ((await res.json()) as { status: string; band: string; weighted_total: string | null; competencies: unknown[] }) : null;
    },
    { timeoutMs: 6 * 60_000, what: "intake evaluation" },
  );
  expect(evaluation.status).toBe("ready");
  const report = await poll(
    async () => {
      const r = await getJson<{ status: string; content?: { kind?: string } }>(staff.api, `/api/v1/sessions/${sessionId}/reports/recruiter`);
      return r.status === "ready" ? r : null;
    },
    { timeoutMs: 6 * 60_000, what: "intake report" },
  );
  expect(report.content?.kind).toBe("intake");
  const detail = await getJson<{ status: string }>(staff.api, `/api/v1/sessions/${sessionId}`);
  expect(detail.status).toBe("completed");
  const transcript = await getJson<unknown[] | { items: unknown[] }>(staff.api, `/api/v1/sessions/${sessionId}/transcript`);
  expect(JSON.stringify(transcript)).not.toContain(secret);

  const review = staff.page;
  await review.goto(`/app/intake/session/${sessionId}?tab=scorecard`);
  await expect(review).toHaveURL(new RegExp(`/app/intake/${intakeId}`), { timeout: 30_000 });
  const scorecard = review.getByTestId("intake-scorecard");
  await expect(scorecard).toBeVisible({ timeout: 30_000 });
  for (const key of COMPETENCIES) await expect(review.getByTestId(`intake-competency-${key}`)).toBeVisible();
  await expect(review.getByTestId("intake-weighted-total")).toBeVisible();
  const band = review.getByTestId("intake-band");
  await expect(band).toHaveAttribute("data-band", evaluation.band);
  await expect(scorecard).toContainText("AI recommendation");
  await shot(review, "13-staff-scorecard");

  await review.goto(`/app/reports/${sessionId}`);
  const printable = review.getByTestId("intake-report");
  await expect(printable).toBeVisible({ timeout: 30_000 });
  const sections = await printable.locator("section[data-section]").evaluateAll((els) => els.map((e) => e.getAttribute("data-section")));
  expect(sections).toEqual(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]);
  await shot(review, "14-staff-report");

  await review.goto(`/app/intake/${intakeId}?tab=decision`);
  await expect(review.getByTestId("intake-decision-form")).toBeVisible({ timeout: 30_000 });
  await review.getByTestId("intake-decision-outcome-second_interview").click();
  await review.getByTestId("intake-decision-rationale").fill("Clear explanations and a solid relay; a second conversation should probe the technical depth.");
  await review.getByTestId("intake-decision-submit").click();
  const entry = review.getByTestId("intake-decision-entry").first();
  await expect(entry).toHaveAttribute("data-outcome", "second_interview", { timeout: 30_000 });
  await entry.scrollIntoViewIfNeeded();
  await shot(review, "15-staff-decision");
  const decisions = await getJson<{ total: number; items: Array<{ outcome: string }> }>(staff.api, `/api/v1/intake/interviews/${intakeId}/decisions`);
  expect(decisions.items[0].outcome).toBe("second_interview");
  const mirrored = await getJson<{ items: Array<{ decision: string }> }>(staff.api, `/api/v1/sessions/${sessionId}/reviews`);
  expect(mirrored.items[0].decision).toBe("hold");

  fs.mkdirSync(path.join(__dirname, "../test-results"), { recursive: true });
  fs.writeFileSync(
    path.join(__dirname, "../test-results/e2e-intake.json"),
    JSON.stringify({ sessionId, intakeId, band: evaluation.band, weightedTotal: evaluation.weighted_total, stages: rec.stages, drillBShownMs: Math.round(shownMs), timers, answered, steps, consoleErrors }, null, 2),
  );
  await staff.close();
});
