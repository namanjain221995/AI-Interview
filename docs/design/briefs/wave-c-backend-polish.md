# Wave C brief: backend polish (catalog, candidates, coding, sessions, platform leftovers)

Read `wave-c-common.md` first.

YOU OWN: `apps/api/app/api/routers/{candidates,coding_questions,coding,sessions,reports,reviews,analytics,interviews,jobs,rounds,templates,question_bank,competencies,resumes}.py`,
`apps/api/app/services/{candidates,coding,jobs,question_bank,scoring}/**`, `apps/api/app/services/reports/**` EXCEPT
`prompts.py` and `narratives.py` (interview-quality agent), `apps/api/app/core/deps.py`, `apps/api/app/schemas/api/{candidates,coding,sessions,reports,interviews,jobs,catalog,question_bank,analytics,reviews}.py`,
and their tests.

Tasks:
1. Candidates: add archive (soft delete) + restore for candidates (hidden from lists by default, `?include_archived=true`),
   and a hard-delete endpoint for ADMIN that removes the candidate's data incl. stored files through
   `app.services.storage.media.delete_media` (data-subject deletion), audited. Needs an Alembic migration (create
   `apps/api/alembic/versions/0003_*.py`) if you add columns.
2. Coding: after a PATCH clears a coding question's embedding, enqueue `question_bank.embed` via
   `app.services.question_bank.embeddings.enqueue_embedding(db, coding_question_ids=[...])`.
3. Sessions: add `GET /sessions?needs_review=true` (completed, no human review decision yet) used by the review UI.
4. `Pagination.apply` typing: make it generic over the select type (`def apply[S: Select](self, stmt: S) -> S`).
5. Review all list endpoints for N+1 queries and missing indexes on common filters (measure with the dev DB, add
   indexes via the 0003 migration if needed).
6. Generate the demo completed report artifacts once a completed session exists in the dev DB: run
   `uv run python -m app.services.reports.demo --latest --out ../../samples/reports/completed_report.json --html-dir ../../samples/reports/`
   (create `samples/reports/`), then inspect the output for quality. (A completed E2E session already exists.)
Keep all tests green (`uv run pytest -q` for the areas you touch + the full suite once at the end).
