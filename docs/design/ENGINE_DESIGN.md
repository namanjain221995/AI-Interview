# Interview engine design

Written for: engineers implementing the adaptive interview engine, scoring and reporting (`app/services/interview`,
`app/services/scoring`, `app/services/reports`). Read with `CONTRACTS.md`, `app/services/interview/contracts.py`,
`app/services/interview/state.py`, `app/services/scoring/contracts.py` and `app/schemas/ai.py`.

Status: design intent. The implemented behaviour, its constants and the deliberate deviations from this document are
in [`INTERVIEW_ENGINE.md`](../../INTERVIEW_ENGINE.md) (see "Documented deviations from the design").

## 1. Principles

1. **Adaptive, not scripted.** No fixed question list. Every move is chosen from the resume, job, round, accumulated
   evidence, the answer just given, time, and claims still unverified.
2. **Deterministic guardrails, generative phrasing.** A deterministic policy layer computes what is allowed and recommended
   (time, sufficiency, coverage, difficulty, claims, contradictions, coding). The model chooses within those constraints and
   phrases it like a senior human interviewer. Every model output is schema-validated; violations are corrected or logged.
3. **Evidence is atomic and attributable.** Scores are computed from evidence records that point at questions, answers and
   transcript spans. Nothing in a report exists without evidence behind it.
4. **Latency budget.** The turn-critical path makes exactly one streaming LLM call (speech-first `TurnDecision`). Deep
   evaluation is asynchronous and feeds the *next* decision.
5. **Human oversight.** Recommendations are assistive; the human decision is a separate record (`review_decisions`).

## 2. Components

| File | Responsibility |
|---|---|
| `engine.py` | `InterviewEngine` implementing `TurnEngine`; per-session `asyncio.Lock`; orchestrates everything below |
| `sessions.py` | `SessionService.create_or_resume`, `candidate_view` |
| `state_store.py` | Redis hot copy (version-guarded Lua CAS) + Postgres snapshot (`interview_sessions.state`), debounced flush every 15 s and at each committed turn; event log (`interview_events`, per-session seq) |
| `planner.py` | `InterviewPlan` via main model (structured, thinking optional), deterministic fallback plan from `RoundConfig` |
| `policy.py` | `PolicyDirective` computation (pure function of state + round config + optional quick assessment) |
| `difficulty.py` | Difficulty adaptation and target-distribution balancing |
| `selection.py` | Bank question retrieval (embeddings + reranker via `app.services.retrieval`), near-duplicate prevention (cosine >= 0.88 against asked questions) |
| `turns.py` | Prompt assembly, speech-first streaming with `StreamingJsonFieldExtractor`, post-validation, question persistence |
| `special.py` | Fast heuristics: repeat requests, "let me think", "I don't know", backchannels, candidate questions |
| `evaluation.py` | `answer.evaluate` job: `AnswerEvaluation` -> evidence rows, claim verification, contradictions, follow-ups -> recompute competency states -> snapshot -> publish `evaluation.ready` |
| `memory.py` | Bounded conversation window (last 8 exchanges verbatim) + rolling summary (`memory.summarize` job on the 8B when > 12 exchanges) |
| `opening.py` / `closing.py` | Introduction and closing utterances (generated during `session.prepare` / at close; template fallbacks) |
| `coding_bridge.py` | Start-coding flow, code observation decisions, hint speech (uses `CodingService` + `coding.observer`) |
| `prompts.py` | All engine prompts, versioned (`PROMPT_VERSION`), always including `FAIRNESS_RULES` |
| `app/services/scoring/*` | Pure scoring math + `recompute_session_scores` persistence (scores, score_snapshots) |
| `app/services/reports/*` | Report assembly, narrative generation, HTML artifact (Jinja2, print CSS) |

## 3. Session lifecycle

`created` (portal creates the row) -> `preparing` (`session.prepare`: load profile + job + round, plan, opening text,
initial bank candidates, warm caches) -> `ready` -> `live` (`TurnEngine.start`) <-> `paused` (disconnect; clock paused)
-> `completing` (closing spoken; `scoring.finalize` + `report.generate` enqueued) -> `completed`. Failures: `failed` with
`error`; `aborted` when cancelled. `start()` must work even if preparation has not finished: it waits up to ~8 s for the plan,
then falls back to the deterministic plan.

## 4. One candidate turn

```
handle_candidate_turn(turn):
  lock(session)
  persist Answer + Transcript (+ answer audio via storage when policy + consent allow)
  special = special.classify(turn.text)                 # regex heuristics, ~0 ms
  directive = policy.compute(state, round, quick=prep_cache.get(turn.text))
  if directive.must_close        -> closing flow (stream closing utterance) -> SessionClosing
  elif directive.start_coding    -> coding flow (select problem, CodingService.start, transition speech) -> CodingStarted
  else:
     yield UtteranceBegin(provisional behaviour from directive.recommended_action)
     stream TurnDecision (speech first) -> yield SpeechDelta as the "speech" field streams
     at end: validate TurnDecision; post-check against directive (log deviations; correct state)
     create Question row unless action in {clarify_question, acknowledge_and_wait, answer_candidate_question, redirect}
     update state: difficulty counts, current competency/topic, follow-up counters, claim in_progress,
                   contradiction verifying, pending follow-ups consumed, awaiting answer after delivery
     yield UtteranceMeta(final behaviour, question_id, decision)
  enqueue answer.evaluate (substantive answers only)
  persist state (Redis now, Postgres at commit)
```

LLM failure (timeout, circuit open, invalid after repair): retry once with a trimmed context; then **fallback question** —
the top retrieved bank question for the directive's target competency and difficulty, prefixed with a short varied
acknowledgement template, emitted as a non-streamed `UtteranceBegin(text=...)`. The interview never stalls.

## 5. Policy directive

Computed before the call, sent to the model, and used to validate the result.

* `must_close`: remaining <= `closing_reserve_seconds`, max questions reached, or recruiter stopping condition.
* `should_wrap_up`: every required competency sufficient, elapsed >= `min_duration_minutes`, questions >= `min_questions`
  -> `invite_candidate_questions` once (if allowed and time permits), then close.
* `coding_due`: coding enabled, not done, remaining >= coding duration + reserve, and either the plan's coding point is
  reached or technical competencies have >= 60 % of their minimum evidence.
* `open_contradiction`: severity >= medium, not yet probed -> `verify_contradiction` (respectful, curious phrasing).
* Answer-quality rules (from the quick assessment when ready, otherwise given to the model as instructions and checked
  post-hoc against its reported `answer_quality`):
  vague -> `request_specificity` (name what is missing: baseline, time range, scope, technique, measurement);
  partial -> `ask_followup` on the same topic and difficulty; strong -> `increase_difficulty` while the competency is not
  sufficient, otherwise `change_topic`; incorrect -> `clarify_question`/`simplify` (max 2 attempts at a level) then move on;
  no_answer -> `simplify` once, then `change_topic` with encouragement; off_topic -> `redirect`;
  clarification_request -> `clarify_question` (rephrase, same question id); candidate_question ->
  `answer_candidate_question` (job/process facts only, never evaluation); wants_time -> `acknowledge_and_wait`.
* `claim_to_verify`: highest-priority unverified claim (resume validation enabled, under `max_claims`) with its evidence
  topics (e.g. architecture, node count, autoscaling, ingress, HA, monitoring, failure recovery, measurable scale).
* `target_competencies`: ranked by `weight x (required ? 1 : 0.5) x (1 - confidence) x (sufficient ? 0.15 : 1)`, each with
  target difficulty and the unanswered expected-evidence topics. `blocked_competencies`: sufficient ones (avoid redundancy
  unless verifying a contradiction).
* `max_followups_reached`: follow-ups on the current topic >= `question_strategy.max_followup_depth` (4 by default; up to
  4-5 extra questions only while the competency remains uncertain).
* `candidate_bank_questions`: top 3 retrieved for the leading target (ids, text, expected concepts) excluding near-duplicates.
* Time: elapsed, remaining, current segment budget and whether it is overrun.

## 6. Difficulty

Per competency `current_difficulty`; a new competency starts at the plan's `starting_difficulty`, nudged toward whichever
level has the largest deficit against the target distribution (30/40/30 by default) but never above what the candidate's
running average supports (avg < 5 -> easy/medium only; avg >= 7 -> medium/hard). Step up one level after a strong answer;
step down after two weak answers at a level; never jump two levels; record each question's difficulty.

## 7. Evidence sufficiency and scoring (pure functions, `app/services/scoring`)

* Observation per evaluated answer per competency: weighted mean of applicable dimensions (correctness .25, depth .20,
  practical_experience .15, specificity .15, reasoning .15, completeness .10); caps by difficulty (easy 8.0, medium 9.3,
  hard 10) so top scores need hard evidence. Weight `r = evaluation_confidence x {easy .85, medium 1.0, hard 1.2}`.
* Score = Bayesian mean with prior 5.0 (weight 1.0) over observations, plus evidence balance
  `0.8 x mean(signal x strength x confidence)` over non-neutral evidence; clamp 0-10.
* Coding: `CodingEvaluation.score_estimate x assistance_factor` (1 - 0.07 per hint used; 3 hints -> 0.79), recorded
  transparently as evidence (`source=hint_usage`, dimension `assistance`) — hints never fail a candidate.
* Confidence = `(1 - exp(-R/2.2)) x (1 - min(.35, 1.2 x std/10)) + .05 if >= 2 difficulty levels seen`, capped .98,
  where R = sum of weights (+0.3 per strong evidence item, capped).
* `evidence_count` = non-neutral evidence items. `sufficient_evidence` = count >= min evidence AND confidence >= round
  threshold AND (last two score deltas < 0.8 OR count >= min + 2).
* `unanswered_questions` = expected-evidence topics of questions asked on the competency that no positive evidence
  dimension covered, plus plan focus areas never touched.
* Rubric categories (0-100) = weighted average of their competencies' scores x 10 and their dimension averages x 10;
  overall = sum of category weight% x category score; overall confidence = weight-averaged competency confidence x coverage.
* Recommendation (assistive, from `PassingGuidance`): low confidence or coverage below `min_required_coverage` ->
  `insufficient_evidence` (or `borderline_evidence` when time ran out with partial coverage); unreviewed integrity flags or
  open high-severity contradictions -> `human_review`; else by overall score: >= strong_proceed_min -> `strong_proceed`,
  >= proceed_min -> `proceed`, >= borderline_min -> `borderline_evidence`, below -> `human_review` (never an automatic
  reject). Always include rationale and the human-decision note.
* Timeline: a `score_snapshots` row per competency after each applied evaluation; the timeline API buckets by window
  (default 10 minutes, last value per window) for charts like "Minute 0-10: AWS 7.2, Kubernetes 6.5".

## 8. Deep evaluation (`answer.evaluate`)

Inputs: question (text, competency, difficulty, expected evidence, rubric of the bank question if any), the answer, the
last 6 exchanges, relevant prior evidence (retrieval), resume claims touched, earlier answers on the same topic
(contradiction check), round rubric. Output `AnswerEvaluation`. Apply: insert `answer_evaluations` (unique per answer),
insert `competency_evidence` (embedding computed), update claim statuses, add contradictions, push suggested follow-ups,
recompute competency states, write snapshots, mark answer evaluated, publish `evaluation.ready`. Idempotent by answer id.
Fallback after retries: neutral evaluation with `is_fallback=True`, confidence 0.2, flagged for human review.
Retries follow `OUTAGE_RETRY_SCHEDULE` (15 s, 1, 3, 10, 20 min; ~34 min in all) and honour a circuit breaker's
`retry_after`, so an LLM outage delays evaluations instead of producing fallbacks (S11). A fallback is never
evidence: scoring and the live competency state skip it (it counts as unassessed). `answer.reevaluate` evaluates
it again and replaces the row in place, then `report.generate` (reason `reevaluation`) refreshes scores and
reports; it is queued by staff (`POST /sessions/{id}/reevaluate`) or by `evaluation.fallback_sweep` (every 10 min,
only while the main model is healthy, at most 3 tries per answer) (S12).

## 9. Prompts

Order static -> dynamic so vLLM prefix caching helps: [persona + rules + FAIRNESS_RULES] [job + round summary]
[candidate profile compact + claims] [plan] | [competency table, coverage, time] [retrieved evidence] [recent conversation]
[last answer] [directive]. Speech rules: conversational spoken English, 1-3 short sentences (typically <= 45 words),
exactly one question, brief specific acknowledgement (no generic praise on every turn, no "Great answer!"), never reveal
scores or evaluation, no hints outside the coding hint flow, no markdown or lists, natural transitions ("Let's shift
gears to..."), use the candidate's first name occasionally, verify contradictions respectfully ("Earlier you mentioned
X — help me reconcile that with Y"). Example of the expected style for a vague cost claim: "What was the approximate
monthly spend before and after the optimization, and which change contributed most to the reduction?"

## 10. Coding inside the interview

`start_coding` -> `CodingService.select_question` (pinned > retrieved > generated) -> `CodingService.start` ->
transition speech (one or two sentences introducing the problem; the full statement is on screen) -> `CodingStarted`.
While coding, the engine stays silent by default; `handle_coding_activity` consults `coding.observer.decide()` which
applies deterministic triggers (run finished, repeated compile errors, structural change after a quiet period, long idle,
time warnings) and rate limits (`min_seconds_between_comments`, never while typing in the last ~6 s unless a run just
finished, max ~6 comments per problem) before asking the main model for a `CodeObservation`. Examples of good comments:
"You've chosen a nested loop here. What is the current time complexity?" / "Your tests pass for the standard case. What
happens with an empty input?". Hint requests (button or voice) -> `CodingService.request_hint` -> `CodingHintIssued` +
short spoken hint. Submission or time limit -> `CodingService.finish` (enqueues `coding.evaluate`) -> `CodingEnded` ->
the interview resumes with a follow-up about the solution (complexity, trade-offs, edge cases) when time permits.

## 11. Reports (`report.generate`)

Deterministic assembly first (scores, rubric, recommendation, evidence per competency, questions with evaluations,
coding sessions with runs and hints, claims verified/unverified, contradictions, integrity summary as objective counts
and reviewed status, timeline, duration). Then two narrative calls (main model, structured): `CandidateReportNarrative`
and `RecruiterReportNarrative`, both given evidence ids; ids not in the evidence set are dropped. Store `reports` rows
(candidate + recruiter), render HTML artifacts (Jinja2 autoescape, print CSS) via storage (`media_assets` kind
`report_artifact`), release the candidate report immediately when `candidate_report_mode == immediate`, publish
`report.ready`. Candidate-facing content never includes integrity details, the recommendation, or recruiter notes.
