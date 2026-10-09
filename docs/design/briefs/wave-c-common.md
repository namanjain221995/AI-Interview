# Wave C — common brief (read first)

You are a senior engineer doing a **hardening / quality pass** on the AI Interview Platform at
`/home/techsphere/Documents/project/Interview`. Waves A and B built and verified every module; the full stack runs in
Docker (compose project `interview`) and a live end-to-end run (real voice → Whisper → engine → coding → reports)
works. Your job is to make one dimension of the product excellent. Several Wave C agents work **in parallel with
strictly disjoint file ownership** (listed in your own brief).

## Read first
- `docs/design/CONTRACTS.md`, `docs/design/ENGINE_DESIGN.md`, `docs/design/REALTIME_DESIGN.md`, `docs/design/UI_TEST_IDS.md`
- The memory notes in `~/.claude/projects/-home-techsphere-Documents-project-Interview/memory/` (one per module: platform core,
  AI layer, sandbox, TTS, web foundation, avatar, infra, catalog, resume/jobs, engine, scoring/reports, realtime, coding,
  integrity, web candidate, recruiter admin, recruiter review). They record measured numbers and gotchas.

## Environment
- DGX Spark aarch64, shared with another production AI stack (load average 10-15 is normal). Compose stack is running:
  edge `http://localhost:3200` (HTTPS `:3443`), api `127.0.0.1:8200` (**do not stop/restart it unless your brief says so**;
  it serves the live stack), worker `:8201`, tts `:8210`, sandbox `:8220`, postgres `:5442`, redis `:6390`, prometheus
  `:9191`, grafana `:3301`. Credentials in `.env` (never print secrets). Dev seed users: `admin@demo.local`,
  `recruiter@demo.local`, `interviewer@demo.local`, `superadmin@demo.local`, password = `SEED_PASSWORD` in `.env`.
- Local models (shared — keep live calls modest): main LLM :8000 (35B, thinking off for latency), vision :8002 (8B, slow),
  embeddings :8003, reranker :8005, Whisper `172.17.0.1:30007` + replica `192.168.9.68:30007` (RTF 0.3-0.5, one request at a
  time each), OCR `192.168.9.68:30004`.
- Python: `cd apps/api && uv run ...`. Node: `cd apps/web && npm ...`. Do NOT edit `pyproject.toml`, `package.json` or lock
  files; report needed dependencies.
- For your own verification run separate processes on spare ports (API 8400-8499, Vite 5200-5299) against a throwaway
  database (see how `tests/fixtures/core.py` creates `interview_test_*` databases, or reuse the dev DB read-mostly).
  Kill everything you start (by PID) before you finish.
- Storage is local filesystem only (owner's explicit requirement: **no S3/MinIO/boto3**).
- Never `docker compose down -v`, never delete volumes, never touch other compose projects or anything outside the repo
  (except writing your memory note). No git.

## Rules
- Production quality; keep behaviour backwards compatible unless the change is the fix. Keep contracts intact (additive only).
- Run tests for everything you touch and the full suite of the area you own (`uv run pytest tests/...`, `npm run test`,
  `npm run typecheck`, `npm run build`). Report exact commands and counts.
- Run formatters/linters only on files you own.
- Fairness: never infer or score protected traits; integrity signals are observations for humans, never verdicts.
- When done, write/update a short memory note for what changed (the user's global CLAUDE.md asks for it) and add one
  index line to `MEMORY.md` if the note is new.

## Final answer format
Sections: SUMMARY, CHANGES (files created/modified), TESTS (commands + counts), VERIFICATION (measured numbers,
screenshots inspected), FINDINGS NOT FIXED (with severity and the exact file/line), REQUESTS FOR OTHER OWNERS, KNOWN ISSUES.
