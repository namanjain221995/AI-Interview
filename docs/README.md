# Documentation index

Written for: engineers looking for the right document. Operational and behavioural documentation lives at the
repository root; `docs/design/` holds the design-time contracts the modules were built against.

## Root documents (describe the code as implemented)

| Document | Read it when you need to |
|---|---|
| [README.md](../README.md) | get the stack running, find URLs, credentials and test commands |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | understand services, networking, data stores, the data model and the key decisions |
| [LOCAL_SETUP.md](../LOCAL_SETUP.md) | set up a workstation or the DGX host, run in development mode, test from LAN devices |
| [DEPLOYMENT.md](../DEPLOYMENT.md) | deploy, harden, monitor, back up, restore or scale the platform |
| [AI_PIPELINE.md](../AI_PIPELINE.md) | change a model call, routing, structured outputs, STT, TTS or retrieval |
| [INTERVIEW_ENGINE.md](../INTERVIEW_ENGINE.md) | change interview behaviour, scoring, reports or the review flow |
| [AVATAR_SYSTEM.md](../AVATAR_SYSTEM.md) | change the interviewer presence (voice orb or optional 3D avatar), its states or speaking sync |
| [CODING_INTERVIEW.md](../CODING_INTERVIEW.md) | add a coding problem or language, or review sandbox isolation |
| [INTEGRITY_MONITORING.md](../INTEGRITY_MONITORING.md) | change integrity signals, rules, consent text or retention |
| [SECURITY.md](../SECURITY.md) | review the threat model and controls before shipping a change |

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

`docs/design/briefs/` holds the work briefs given to the build agents; they are historical and not maintained.

Code-level contracts are authoritative where they exist (`apps/api/app/core/config.py`,
`apps/api/app/domain/enums.py`, `apps/api/app/db/models/`, `apps/api/app/schemas/`, the `contracts.py` files of
each service package, and `apps/web/src/lib/realtime/protocol.ts`).
