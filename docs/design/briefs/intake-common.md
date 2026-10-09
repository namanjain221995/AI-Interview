# Intake Interview module — common brief (read first)

Product: AI-powered **Intake Interview** (`round_type: intake`, user-facing "Intake Interview") for screening candidates
before a training program, built INSIDE the existing AI Interview Studio at `/home/techsphere/Documents/project/Interview`
(git repo — do NOT commit, push or rewrite history; the coordinator decides about commits). Source requirements:
`Intake_Interview_Kit.docx` (repo root; full extraction in document order:
`/tmp/claude-1000/-home-techsphere-Documents-project-Interview/701114dc-1458-4aa1-a843-dbecc46944b2/scratchpad/intake/kit.txt`).
Design contract: `docs/design/INTAKE_INTERVIEW.md` (written by the design agent; it is authoritative once it exists).

## Environment and rules
- Read `docs/design/briefs/wave-c-common.md` (environment, ports, shared GPU, test DBs, no S3, no `docker compose down -v`) and the
  memory notes in `~/.claude/projects/-home-techsphere-Documents-project-Interview/memory/` for the modules you touch.
- The docker stack is live (edge :3200, api :8200, worker :8201). Do not restart services; the coordinator redeploys.
  For live checks run your own processes on spare ports (API 8470-8499, Vite 5270-5299) against a throwaway
  `interview_test_*` database and a separate Redis DB index. Kill what you start, by PID.
- The owner's GPU is often ~95% busy: use the mock AI registry, test hooks and deterministic fixtures; at most a few live
  model calls, one at a time, only to prove something essential.
- Reuse: auth/RBAC, candidates, jobs/rounds (`RoundConfig`), invitations + open links, résumé pipeline (quick + full
  profile), interview engine (`apps/api/app/services/interview/**`), realtime WS runtime (VAD, Whisper segments, TTS,
  barge-in, reconnect/hand-over, outbox), coding bridge pattern (server-driven workspace start), integrity, recordings,
  reports, review decisions, analytics, voice orb presence, dark/light theme, TechSara branding.
- Shared files: small additive edits, re-read right before each edit, never reformat whole shared files, format only files
  you own. Keep every existing `data-testid` and every existing interview type working.
- Tests for everything you add; run the suites of every area you touch. Report exact commands and counts.

## Non-negotiable product decisions (coordinator)
1. **No misrepresentation.** Never generate, store or present inflated experience. The kit's MQ4 is reframed: "This program
   prepares people for roles that usually expect around five years of hands-on experience. Are you comfortable with the
   responsibilities and skills expected at that level, and can you commit to the training needed?" Record the candidate's
   real experience. Drop the kit question "How do you feel about our interview-support model?".
2. Competency labels (kit label kept only as source metadata in the kit YAML and docs):
   | key | UI label | kit label | weight |
   |---|---|---|---|
   | `communication` | Communication & Presentation | Communication & presentation | 0.20 |
   | `technical_reading` | Technical Reading & Relay | Doc-reading / proxy ability | 0.20 |
   | `structured_answer` | Structured Answer / Gap-Filling | Gap-filling / structured answer | 0.15 |
   | `technical` | Actual Technical Ability | Technical ability (actual) | 0.15 |
   | `trainability` | Trainability & Learning Attitude | Trainability / learning attitude | 0.15 |
   | `composure` | Confidence & Composure | Confidence & composure | 0.10 |
   | `adaptability` | Niche-Fit & Adaptability | Niche-fit / adaptability | 0.05 |
   Scores 1-5 per competency (anchors from the kit's rubric table, with the sanitised wording below).
   `technical_reading` = transparent combination of Drill A (reading delivery) and Drill B (comprehension/relay), both
   stored and reported separately; the formula is documented (default 0.4 × Drill A + 0.6 × Drill B, each 1-5).
3. Sanitised anchors: structured answer 5 = "Connects all points into a natural flow and handles gaps honestly and
   gracefully" (never reward bluffing or confident misinformation); composure = observable behaviour only (keeps going
   when unsure, handles follow-ups and challenges, recovers from mistakes) — never emotion, voice affect, energy, eye
   contact or facial analysis; communication = clarity, structure, relevance — never accent or appearance.
4. **Weighted score** = Σ score × weight (max 5.0), computed with Decimal, never rounded before banding. Bands are half-open
   with no gaps: total ≥ 4.20 → `strong_accept`; 3.30 ≤ t < 4.20 → `accept_with_notes`; 2.50 ≤ t < 3.30 → `borderline`;
   t < 2.50 → `reject`. If any competency lacks sufficient evidence → `insufficient_evidence` (show the partial total
   re-normalised over the scored competencies, clearly labelled). Optional "out of 100" = total / 5 × 100, labelled as a
   conversion.
5. **Recommendations only.** The band is an AI recommendation. Kit "automatic red flags" (dishonest claims / self-rating
   collapses, cannot read a simple passage aloud, fails the mandatory gate, communication too unclear) become **human
   review items with evidence**, never automatic rejection. Human decisions: `accept`, `accept_with_notes`,
   `second_interview`, `reject`, `request_evidence` with rationale + reviewer + timestamp, audited.
6. **Work authorization (MQ1)** is optional and policy-configured per organisation/job/jurisdiction. Default wording asks
   only: "Are you legally authorised to work in <country> for this role?" and "Will you need sponsorship now or in the
   future?" — never citizenship, nationality or visa type unless the organisation explicitly enables a reviewed variant.
   MQ1 answers are stored for human review and are NEVER sent to any model or used in any score.
7. **Pronunciation** is "not assessed" (no audio pronunciation model is deployed). Drill A delivery uses alignment of the
   final transcript to the passage (normalised WER, completeness, omissions/substitutions/insertions/repetitions, tolerant
   to ASR near-misses, spelling variants and contractions) plus timing (duration, words per minute, pauses from VAD /
   segment timings). Never penalise accent.
8. Candidates never see rubrics, scores, bands or reports (org default `candidate_report_mode: never`). The Drill B
   passage is shown for exactly 30 s (server-authoritative timer), then removed from the client and never appears in the
   candidate transcript; it cannot be reopened during the relay.
9. Stage flow is a deterministic server-side state machine (7 stages, kit timings, mandatory items, gate checks, follow-up
   limits, time budget). The LLM phrases questions and personalises them inside a stage but can never skip mandatory
   items, change stage rules, scores, weights or permissions. Résumé and answers are untrusted (existing fences).
10. Default duration 35-50 min (target 45). Timers: Drill A ≤ 3 min, Drill B read 30 s, topic card prepare 60 s + speak 120 s.

## Shared module paths (fixed so parallel agents agree)
- Kit content: `samples/intake/intake_kit_v1.yaml`; loader/models `apps/api/app/services/intake/kit.py` (`load_intake_kit()`
  -> `IntakeKit`: stages, mandatory questions MQ1-MQ4 (sanitised), language verification questions, résumé probe ladder +
  claim-type probes + honesty signals, scenario bank, passages A-F (exact original text, paragraphs, level, suggested
  use), topic cards 1-8 (exact title + 4 bullets + type), rubric (keys/labels/kit labels/weights/anchors 1/3/5), bands,
  review-flag definitions, source {file, sha256, version}).
- Reading engine: `apps/api/app/services/intake/reading.py` (`align_reading(reference, hypothesis, *, segments=None,
  pauses=None) -> ReadingMetrics`).
- Scoring: `apps/api/app/services/intake/scoring.py` (`INTAKE_COMPETENCIES`, `weighted_total()`, `decision_band()`,
  `technical_reading_score()`).
- Everything else intake-specific lives under `apps/api/app/services/intake/**`, `apps/api/tests/**/intake/**`,
  `apps/web/src/features/intake/**`, unless the design contract assigns otherwise.
