# Documentation index

Written for: engineers looking for the right document. Operational and behavioural documentation lives at the
repository root; `docs/design/` holds the design-time contracts the modules were built against.

## Project overview (README)

The [README](../README.md) is the landing page for the whole project:

| Section | What it covers |
|---|---|
| [Features](../README.md#features) | candidate experience, AI interviewer, intake module, coding, integrity, recordings, scoring and reports, open links, recruiter app, content library |
| [Screenshots](../README.md#screenshots) | candidate room, coding round, intake exercises and review, recording, open links, dark theme, phone layouts (images in [`images/`](images/)) |
| [Architecture](../README.md#architecture) | system diagram, models and their roles, deployment topology with ports, tech stack |
| [System flows](../README.md#system-flows) | candidate journey, one voice turn, intake stages, evaluation and report pipeline (Mermaid diagrams) |
| [Measured performance](../README.md#measured-performance) | latency of the voice loop, models, code execution, résumé and reports |
| [Quick start](../README.md#quick-start), [URLs and ports](../README.md#urls-and-ports), [Development credentials](../README.md#development-credentials) | running the stack locally |
| [Running tests](../README.md#running-tests) | every test suite and what it needs |
| [Privacy, fairness and human oversight](../README.md#privacy-fairness-and-human-oversight), [Limitations](../README.md#limitations) | principles the code enforces and what it does not do |

## Root documents (describe the code as implemented)

| Document | Read it when you need to |
|---|---|
| [README.md](../README.md) | see what the platform does, get the stack running, find URLs, credentials and test commands |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | understand services, networking, data stores, the data model and the key decisions |
| [LOCAL_SETUP.md](../LOCAL_SETUP.md) | set up a workstation or the DGX host, run in development mode, test from LAN devices |
| [DEPLOYMENT.md](../DEPLOYMENT.md) | deploy, harden, monitor, back up, restore or scale the platform |
| [AI_PIPELINE.md](../AI_PIPELINE.md) | change a model call, routing, structured outputs, STT, TTS or retrieval |
| [INTERVIEW_ENGINE.md](../INTERVIEW_ENGINE.md) | change interview behaviour, scoring, reports or the review flow |
| [AVATAR_SYSTEM.md](../AVATAR_SYSTEM.md) | change the interviewer presence (voice orb or optional 3D avatar), its states or speaking sync |
| [CODING_INTERVIEW.md](../CODING_INTERVIEW.md) | add a coding problem or language, or review sandbox isolation |
| [INTEGRITY_MONITORING.md](../INTEGRITY_MONITORING.md) | change integrity signals, rules, consent text or retention |
| [SECURITY.md](../SECURITY.md) | review the threat model and controls before shipping a change |
| [INTAKE_INTERVIEW_GUIDE.md](INTAKE_INTERVIEW_GUIDE.md) | run intake interviews (create, share, review, decide) or work on the intake module (architecture pointers, commands, the mock-AI end-to-end test) |
| [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md) | read the build summary for the owner: what was built, feature status and test results as of 2026-10-08 (a snapshot; the root documents are kept current) |

## Design-time documents (`docs/design/`)

These were written before and during the build so that modules could be developed in parallel. They state
intent; where the implementation deliberately differs, the root documents say so (for example the scoring
formulas in [INTERVIEW_ENGINE.md](../INTERVIEW_ENGINE.md#documented-deviations-from-the-design)).

| Document | Contents |
|---|---|
| [CONTRACTS.md](design/CONTRACTS.md) | module contracts: layout, conventions, auth and RBAC matrix, REST surface, jobs, Redis keys, storage, metrics, model routing, frontend conventions |
| [ENGINE_DESIGN.md](design/ENGINE_DESIGN.md) | intended engine design: lifecycle, turn algorithm, policy directive, difficulty, scoring, evaluation, prompts, coding, reports |
| [REALTIME_DESIGN.md](design/REALTIME_DESIGN.md) | intended voice pipeline: WebSocket rationale, runtime structure, audio in, end of turn, speech out, barge-in, reconnection |
| [UI_TEST_IDS.md](design/UI_TEST_IDS.md) | `data-testid` contract used by the Playwright suite (stable API, do not rename) |
| [OPEN_LINKS.md](design/OPEN_LINKS.md) | open interview links contract: data model and migration `0004`, registration form builder, public flow, one attempt per person, multiple rounds, recruiter and candidate UX, security and privacy, API |
| [INTAKE_INTERVIEW.md](design/INTAKE_INTERVIEW.md) | intake module contract: decisions, data model and migration `0005`, stage machine, activity protocol, evaluation, APIs, web, security, test plan, per-track deviations |

`docs/design/briefs/` holds the work briefs given to the build agents; they are historical and not maintained.

Code-level contracts are authoritative where they exist (`apps/api/app/core/config.py`,
`apps/api/app/domain/enums.py`, `apps/api/app/db/models/`, `apps/api/app/schemas/`, the `contracts.py` files of
each service package, and `apps/web/src/lib/realtime/protocol.ts`).
