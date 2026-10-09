# Intake Interview guide

Written for: recruiters and admins who run intake interviews (first half), and engineers who operate or change the
module (second half). The design contract is [docs/design/INTAKE_INTERVIEW.md](design/INTAKE_INTERVIEW.md); this
guide describes the module as built.

An intake interview (`round_type: intake`, shown as "Intake interview") screens candidates for a training program.
The AI interviewer runs a 35-50 minute voice interview (45 by default) in seven fixed parts with three short
exercises, then proposes scores with quoted evidence. A person on the training team reviews the interview and makes
every decision.

## Contents

* [For recruiters and admins](#for-recruiters-and-admins): [set up](#set-up-admins),
  [create and share](#create-and-share), [the candidate's experience](#the-candidates-experience),
  [review](#review), [decisions](#decisions), [what the AI does and does not decide](#what-the-ai-does-and-does-not-decide),
  [privacy](#privacy)
* [For engineers](#for-engineers): [architecture pointers](#architecture-pointers), [commands](#commands),
  [end-to-end test](#end-to-end-test-mock-ai), [settings and flag](#settings-and-feature-flag),
  [limitations](#known-limitations)

## For recruiters and admins

### Set up (admins)

| Where | What to set |
|---|---|
| Intake interviews > Settings (`/app/intake/settings`, admins) | program name; the work-authorisation default (off, or ask with the country and optionally the sponsorship question); approved program information (up to 30 topic + answer pairs: the only facts the interviewer may state when a candidate asks about the program; checked by the fairness lint); defaults for duration, allowed reconnections, link expiry, interviewer voice orb and "résumé required" |
| A job with an Intake interview round | the demo seed ships "AI/ML Training Program — Intake" (`make demo`). Any job can get one: add a round of type "Intake interview" to the job; the round editor's Intake tab sets the duration (35-50 min), follow-up limits, a work-authorisation override and optional passage / topic-card choices (intake rounds never include coding) |

### Create and share

Intake interviews > Create (`/app/intake/new`; admins and recruiters):

* **One candidate**: pick an existing candidate or enter a name and email; choose the job (its intake round) and
  either preset the target role or let the candidate choose; optionally attach a résumé; adjust the interview
  settings (duration 35-50 min, voice orb, reconnections 0-20, link expiry, earliest start, access code, résumé
  required), the work-authorisation questions (organisation default, ask with a country, or don't ask), the assigned
  reviewer and private notes. "Create invitation" returns the link (and access code, if any) **once**: copy it, copy
  the ready-made invitation message, or open an email draft. The platform sends no email itself. "Save as draft"
  keeps everything without a link; send it later from the list.
* **Batch**: paste or upload `name,email[,target_role]` (up to 200 rows; duplicates and bad rows are shown before
  submitting). The results table lists each link once and offers a CSV download of the links.
* The list (`/app/intake`) groups interviews into views (drafts, active, scheduled, in progress, completed, awaiting
  review, accepted, borderline, rejected, training recommendations, expired, cancelled) with search and filters.
  Drafts open in the create page for editing; an intake's page offers "Generate a new invite link" until the
  interview starts (the old link stops working) and "Cancel intake" (ends a running interview).

### The candidate's experience

The link (`/i/<token>`) opens: identity check (email, plus the access code when one was issued) -> welcome (the seven
parts, how long it takes, what is recorded, "a person decides; you won't see scores or a report") -> instructions
-> consent -> résumé upload -> **your details** (target role, technical background, best programming language and a
1-10 self-rating, scripting, data/ML libraries, years of hands-on experience, comfort with the experience level the
program prepares for, training commitment, weekly hours, availability; the work-authorisation questions only when
the organisation turned them on) -> **résumé check** (confirm or correct what was read from the résumé; "Continue
without reviewing" appears after 3 minutes of processing) -> system check -> interview.

| Part (typical minutes) | What happens |
|---|---|
| 1 Introduction (5) | self-introduction and the project they are proudest of, up to two follow-ups |
| 2 Your experience (8-10) | two or three résumé claims, each explored from "what did you personally do" to "how did it work" to "what would break" |
| 3 Standard questions (3-5) | target role, best language with one short language check question, self-rating, comfort with the expected experience level and the training commitment (confirming the form answers rather than re-asking them) |
| 4 Reading exercise (7-8) | **Drill A**: read a technical passage aloud (up to 3 minutes, "Finish reading"); **Drill B**: read a different passage silently for 30 seconds, after which the server hides it for good, then explain it in your own words and answer three follow-ups |
| 5 Speaking exercise (7-8) | a topic card with four points: 60 seconds to prepare ("Start now" to begin early), up to 2 minutes to speak ("I'm done"), then a follow-up on the weakest point |
| 6 Learning and growth (5) | how they would learn in three weeks of intensive training, and teaching something they learned recently |
| 7 Your questions (3-5) | answered only from the approved program information; anything else goes to the hiring team |

A dropped connection pauses the interview clock; the candidate can rejoin as many times as the invitation allows.
A reading or speaking exercise interrupted by a disconnect restarts (at most twice); the Drill B passage is never
shown for more than 30 seconds in total. At the end the candidate sees a thank-you page, never a score or a report.

### Review

The evaluation starts about 30 seconds after the interview ends and takes a few minutes (about eight model calls on
the shared GPU). The review page (`/app/intake/<id>`; the session page's "Open intake review" leads there) has
these tabs:

| Tab | Content |
|---|---|
| Overview | candidate summary, form answers, the work-authorisation answers in a block marked "for human review only", stage progress (time per stage against its budget), review items |
| Stages & answers | every question and answer with timestamps; "Play in recording" jumps to the moment |
| Reading | Drill A: the passage with omitted / substituted / inserted words marked, completeness, word error rate, words per minute, pauses, the delivery score and its basis ("Pronunciation: not assessed"); Drill B: concepts covered with quotes, paraphrase, follow-ups, score |
| Topic card | the four points with coverage and quotes, flow, examples, how gaps were handled |
| Scorecard | seven competencies: score 1-5 or "Insufficient evidence", weight, contribution, evidence quotes, anchors; weighted total, "out of 100" conversion, and the band labelled **AI recommendation** |
| Recording / Integrity | the existing session tabs (integrity signals are observations, never verdicts) |
| Decision | the decision form and history |

| Competency | Weight |
|---|---|
| Communication & Presentation | 0.20 |
| Technical Reading & Relay (0.4 × Drill A + 0.6 × Drill B) | 0.20 |
| Structured Answer / Gap-Filling | 0.15 |
| Actual Technical Ability | 0.15 |
| Trainability & Learning Attitude | 0.15 |
| Confidence & Composure (observable behaviour only) | 0.10 |
| Niche-Fit & Adaptability | 0.05 |

Weighted total = Σ score × weight (1-5, never rounded before banding). Bands: ≥ 4.20 strong accept; 3.30-4.19 accept
with notes; 2.50-3.29 borderline; below 2.50 reject. When any competency lacks evidence the band is "insufficient
evidence" and the total shown is a partial total over the scored competencies, labelled as such. The printable
report (`/app/reports/<session id>`, sections A-K, also as an HTML file) is never shared with the candidate.
"Re-evaluate" creates a new evaluation version.

### Decisions

Outcomes: **Accept**, **Accept with notes**, **Second interview**, **Reject**, **Request evidence** (which lists the
evidence asked for). A rationale (10-5000 characters) is required; each review item can be marked confirmed,
dismissed or needs follow-up, and competencies can carry notes. Decisions are append-only, recorded with the
reviewer's name and time, audited, and flagged when they differ from the AI recommendation (counted in analytics,
never blocked). Each decision is mirrored into the session's generic decision (accept / accept with notes ->
advance, second interview -> hold, reject -> reject, request evidence -> needs more information), so dashboards and
the sessions list stay consistent. Record intake decisions on the intake review page: the generic session decision
form points there, and the generic API refuses a plain review of an intake session (409 `intake_decision_required`).

Analytics (`/app/intake/analytics`): funnel, bands, decisions and override rate, stage completion, competency
averages, common technical weaknesses, training needs, by target role.

### What the AI does and does not decide

| The AI does | The AI never does |
|---|---|
| asks the questions in the fixed order (fixed kit wording for most; it phrases résumé questions and follow-ups inside limits the system sets) | skip a mandatory question, change the order, the timers, the weights or the bands |
| keeps time and runs the exercises (the server enforces every timer) | decide, reject or rank a candidate: the band is a recommendation and kit "red flags" become review items for a person |
| transcribes, aligns the reading to the passage, proposes scores that quote the answers they rest on | assess pronunciation, accent, appearance, emotion, voice, energy or eye contact |
| lists review items and drafts a training plan | score an honest "I don't know" below a confident wrong answer |
| answers candidate questions from the approved program information only | ask about, hear or see work authorisation, citizenship, nationality or visa status |

### Privacy

* **Work authorisation** is asked only in the details form, only when enabled, with the default wording ("Are you
  legally authorised to work in <country> for this role?" and optionally "Will you need sponsorship now or in the
  future?", each with "I'd rather discuss this with the hiring team"). The answers are visible to staff on the
  Overview tab only; they never reach any model, score, report, export or analytics.
* Candidate names are replaced by `[name]` before any answer reaches an evaluator.
* Recording happens only with consent and follows the organisation's retention settings; downloads are admin-only and
  audited. Erasing a candidate removes their intake rows.
* Candidates never see rubrics, scores, bands or reports.

Access: every staff role can view intake interviews, re-evaluate and record decisions; admins and recruiters create,
send, cancel and rotate links; only admins change the organisation's intake settings. Candidates reach only their own
form, résumé check and interview.

## For engineers

### Architecture pointers

| Topic | Where |
|---|---|
| Contract, data model, APIs, protocol, deviations per track (10.7, 10.8, 18) | [docs/design/INTAKE_INTERVIEW.md](design/INTAKE_INTERVIEW.md) |
| Stage machine, conductor, engine hook points | [INTERVIEW_ENGINE.md "Intake mode"](../INTERVIEW_ENGINE.md#intake-mode-round_type-intake), `apps/api/app/services/intake/{stages,selection,conductor,prompts,turn_schema}.py` |
| Exercises over the WebSocket (capture mode, server timers, Drill B secrecy) | [REALTIME_DESIGN.md section 10](design/REALTIME_DESIGN.md#10-intake-activities-round_type-intake), `apps/api/app/realtime/runtime.py`, `app/services/intake/{activities,capture_store,transcribe}.py` |
| Evaluation, validator, scoring, report, decisions | [AI_PIPELINE.md "Intake evaluation"](../AI_PIPELINE.md#intake-evaluation), `app/services/intake/{evidence,evaluators,validator,scoring,reading,relay,pipeline,report,decisions}.py`, workers `app/workers/intake.py` (`intake.evaluate`, `intake.report`) and `app/workers/intake_capture.py` (`intake.activity_transcribe`) |
| Admin, onboarding, résumé review, listing | `app/services/intake/{invitations,onboarding,resume_review,listing,org_settings,access}.py`, routers `app/api/routers/{intake,intake_review,portal_intake}.py` |
| Security controls | [SECURITY.md "Intake interviews"](../SECURITY.md#intake-interviews-evaluation-reports-decisions) |
| Kit content (stages, questions, passages A-F, topic cards 1-8, rubric) | `samples/intake/intake_kit_v1.yaml` (loader `app/services/intake/kit.py`; passages stay verbatim, tests re-extract `Intake_Interview_Kit.docx`) |
| Web | candidate `apps/web/src/features/intake/candidate/**`, staff `apps/web/src/features/intake/staff/**`; test ids in [UI_TEST_IDS.md](design/UI_TEST_IDS.md) |
| Migration | `apps/api/alembic/versions/0005_intake_interviews.py` (eight `intake_*` tables; [DEPLOYMENT.md](../DEPLOYMENT.md#intake-interviews-migration-0005)) |

### Commands

Run from `apps/api` unless noted.

| Purpose | Command |
|---|---|
| Intake unit tests (no services) | `uv run pytest tests/unit/intake tests/unit/realtime/test_activity_capture.py -q` |
| Intake integration tests (postgres + redis) | `uv run pytest tests/integration/intake -q -m "not live"` |
| Regression of the shared areas | `uv run pytest tests/integration/interview tests/integration/realtime tests/integration/reports tests/integration/scoring tests/integration/jobs tests/integration/candidates tests/integration/open_links tests/integration/platform -q -m "not live"` |
| Web tests (in `apps/web`) | `npx vitest run src/features/intake`; gates `npm run typecheck`, `npm run build` |
| Completed demo intakes without models | `uv run python -m app.services.intake.demo --org-slug demo [--variant incomplete] [--decide] [--count N]` |
| Catalog check (template, competencies, demo job) | `uv run python -m app.seed.validate` |

Test hooks (only with `ENABLE_TEST_HOOKS=true`, never in production, header `X-Test-Hooks-Token`):
`POST /api/v1/test/intake/sessions/{id}/jump {stage}`, `.../capture {activity_id, phase, text, ...}` (inject an
exercise capture), `.../activity-deadline {activity_id}` (end the current phase now).

### End-to-end test (mock AI)

`tests/e2e/specs/intake.spec.ts` drives the whole journey in Chromium: staff create the intake in the staff app ->
the candidate verifies, consents, uploads `samples/resumes/jane_doe_devops.pdf`, fills in the details form,
confirms the résumé check, passes the system check -> all seven stages in order (stage indicator), Drill A with the
passage on screen, Drill B with a different passage that the server hides at 30 s (then absent from the DOM, the
WebSocket traffic after its single phase message and the transcript), topic card timers 60 s / 120 s (ended early
with "Start now" / "I'm done") -> closing -> `intake.evaluate` and `intake.report` -> scorecard (seven competencies,
weighted total, band labelled AI recommendation), report sections A-K -> a recorded decision mirrored into the
generic review. Ordinary answers are typed; the opening answer and the spoken exercises go through the fake
microphone (`fixtures/fake-media.js` + recorded clips -> AudioWorklet -> WebSocket -> server VAD -> mock speech
recognition). It needs no test hook. About 4 minutes.

```bash
cd apps/api && uv run python ../../tests/e2e/mock-stack/stack.py --run specs/intake.spec.ts
```

`tests/e2e/mock-stack/stack.py` builds a throwaway stack and removes it afterwards: an `interview_test_*` database on
the development Postgres (migrated, seeded with `python -m app.seed --demo`, dropped on exit), a leased Redis DB index,
a temporary storage root, the API on 127.0.0.1:8480 (`ENABLE_TEST_HOOKS=true`), the worker (metrics on 8481) and Vite
on 127.0.0.1:5280 proxying to the API. Both processes use the mock AI registry (`tests/e2e/mock-stack/mock_ai.py`:
kit questions for intake turns, validator-proof evaluator outputs that quote the real answers, fixed speech
recognition, mock TTS); model endpoint settings point at a closed port, so nothing reaches the shared GPU. Without
`--run` the stack stays up for manual runs (`E2E_MOCK_STACK=1 E2E_BASE_URL=http://127.0.0.1:5280 npx playwright
test specs/intake.spec.ts` in `tests/e2e`) until Ctrl+C. Elsewhere (for example `make test-e2e` against the live
stack) the spec is skipped unless `E2E_INTAKE_LIVE=1`, because with live models one run sends about 30 turns and 8
evaluator calls to the shared GPU. Logs: `tests/e2e/mock-stack/.out/`; screenshots:
`E2E_SCREENSHOT_DIR` (default `tests/e2e/test-results/intake/`); run summary: `tests/e2e/test-results/e2e-intake.json`.
Requirements: postgres and redis running (`make infra-up`), `.env` with `SEED_PASSWORD` and `TEST_HOOKS_TOKEN`,
`npm ci` in `apps/web` and `tests/e2e`.

### Settings and feature flag

`INTAKE_ENABLED` (default `true`; `false` answers 404 `intake_disabled` on every intake route and 409 for new intake
sessions, while running sessions finish), `INTAKE_EVAL_START_DELAY_SECONDS` (30), `INTAKE_EVAL_CONCURRENCY` (2),
`INTAKE_ACTIVITY_FINAL_PASS_WAIT_SECONDS` (60), `INTAKE_BATCH_MAX_ROWS` (200): see
[DEPLOYMENT.md](../DEPLOYMENT.md#intake-interviews-migration-0005).

### Known limitations

From the contract (section 17): the AI never interrupts a long answer; pronunciation is not assessed; no outbound
email; one default work-authorisation wording; language checks cover the kit's five languages (others fall back to a
clarifier, then SQL/Python); a candidate can photograph the Drill B passage during its 30 seconds; the evaluation
takes minutes on the shared GPU. Real-model latency of the live intake turns and of the evaluators has not been
measured yet (the GPU was busy during the build).
