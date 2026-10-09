# Wave C brief: documentation

Read `wave-c-common.md` first.

YOU OWN (create/update): `README.md`, `ARCHITECTURE.md`, `LOCAL_SETUP.md`, `AI_PIPELINE.md`, `INTERVIEW_ENGINE.md`,
`AVATAR_SYSTEM.md`, `CODING_INTERVIEW.md`, `INTEGRITY_MONITORING.md`, `SECURITY.md`, `DEPLOYMENT.md` (all at the repo root)
and `docs/**` except `docs/design/briefs/**`.

Write documentation for engineers who operate, extend and review the platform. Every statement must be true of the code
as it is now: verify commands, ports, env vars, endpoints, file paths, metrics and job names against the source (grep),
and run the commands that are safe to run (e.g. `make help`, `docker compose config -q`, `uv run python -m app.seed --help`).
Use the module memory notes and `docs/design/*` as sources but re-verify against code. Include Mermaid diagrams
(architecture, request/turn sequence, data model, integrity pipeline, coding sandbox flow, avatar behaviour state machine).

Required content:
- README: what it is, features, quick start (make setup/up/seed/demo), URLs, dev credentials location, project layout,
  how to run tests (unit/integration/live/e2e), links to the other docs.
- ARCHITECTURE: services, networking model (host networking rationale), data stores, key decisions with rationale (WebSocket
  vs WebRTC/Pipecat, speech-first decisions, model routing with measured numbers, local storage, client-side avatar).
- LOCAL_SETUP and DEPLOYMENT: prerequisites, step by step, HTTPS for LAN candidates (export edge CA), backups/restore,
  scaling notes (multiple API instances + session locks, workers, Whisper replicas, TTS replicas, Kubernetes path),
  security checklist for production (disable test hooks, rotate secrets, sandbox hardening options, gVisor), monitoring.
- AI_PIPELINE: each model's role, routing table, structured outputs + repair, streaming extractor, measured latencies,
  embeddings/reranker usage, OCR, STT segmentation/partials, TTS + visemes, fallbacks.
- INTERVIEW_ENGINE: state, policy directive, difficulty, sufficiency + scoring formulas (as implemented, including the
  documented deviations), special turns, coding integration, reports, human review.
- AVATAR_SYSTEM: rendering, rig adapters, animation layers, behaviour controller states, lip-sync, fallbacks, asset
  licensing (CC0 MPFB), performance.
- CODING_INTERVIEW: languages, signature conventions, sandbox isolation (with attack-test results), hints + assistance
  factor, observer triggers, evaluation caps, redaction of hidden tests.
- INTEGRITY_MONITORING: two tiers (browser mode now, future desktop Secure Interview Client design), signals, rules and
  thresholds, vision prompt constraints, never-terminate-from-vision rule, consent + disclosure, retention, honest
  limitations, reviewer workflow.
- SECURITY: threat model, controls (auth, RBAC, CSRF, rate limits, uploads, sandbox, CSP, secrets, audit), data
  protection, responsible-AI/fairness constraints.
Keep docs concise and scannable; no marketing language.
