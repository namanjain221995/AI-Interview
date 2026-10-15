# Samples: catalog and demo content

Curated, validated content that seeds the platform. Everything here is loaded and validated by
`apps/api/app/seed/loaders.py`, checked by the validator (`python -m app.seed.validate`) and written
to the database by `apps/api/app/seed/catalog.py`. For current counts and coverage run the validator
with `--stats`.

| Path | Contents | Loaded into |
|------|----------|-------------|
| `catalog/competencies.yaml` | Global competencies with category, description and a rubric (behavioural anchors for levels 1, 3 and 5 plus evidence dimensions). New competencies are appended. | `competencies` (organization NULL) |
| `catalog/round_templates.yaml` | One default `RoundConfig` per round type (12) | `interview_templates` (`is_default`) |
| `catalog/roles.yaml` | The role vocabulary: `key`, `title`, `family`, `description`, `competency_keys`, `round_types` | validation only (not stored) |
| `question_bank/**/*.yaml` | Curated interview questions, any directory depth (for example `question_bank/<area>/<topic>.yaml`) | `question_bank` (by `external_key`) |
| `coding/<group>/<slug>.yaml` | One coding problem per file, grouped by topic (`arrays_hashing`, `sql`, ...); the original top-level `coding/<slug>.yaml` files stay valid | `coding_questions` (by `slug`) |
| `jobs/*.yaml` | 5 demo jobs (incl. the intake training-program job) with requirements, integrity and recording policies and rounds built from the templates | `jobs`, `job_requirements`, `rounds` |
| `resumes/` | Sample resumes in every supported format, including a scanned PDF that needs OCR | uploaded through the API |

Files are discovered recursively and sorted by path; dot-files are ignored and `*.yml` files are
reported as errors (use `.yaml`).

## Validating content

Run from `apps/api`:

```bash
uv run python -m app.seed.validate                         # the whole tree
uv run python -m app.seed.validate ../../samples/coding/arrays_hashing/two_sum.yaml
uv run python -m app.seed.validate coding/sql question_bank/backend   # paths relative to samples/ work too
uv run python -m app.seed.validate --stats                 # plus coverage (difficulty, kinds, round types, roles)
uv run python -m app.seed.validate --postgres coding/sql   # also run SQL references on the dev PostgreSQL
```

The whole tree is always loaded, so uniqueness and cross-references are checked against everything
even when only some files are named; findings are printed for the named files only (the summary
counts errors elsewhere). Exit status: `0` no errors (warnings allowed; `--strict` fails on
warnings), `1` errors, `2` usage problems. Other options: `--no-run` (skip executing code),
`--json` (machine-readable), `--timeout S` (per problem and code, default 10), `--jobs N` (runner
processes per language), `--max-per-file N`, `--samples-dir DIR`, `-v` (timings).

What it checks:

* **Schemas and cross-references**: every field below; competency keys, round types, pinned coding
  slugs; `external_key`, `slug` and coding titles unique across the whole tree; `role_tags` must be a
  key of `catalog/roles.yaml` or one of the original tags (`general, devops, sre, platform, cloud,
  security, backend, frontend, fullstack, data, ml, llm, engineering_manager, tech_lead`).
* **Coding structure**: exactly 3 graduated hints without code; 2-3 visible and 5-10 hidden tests,
  each with a staff-facing `description`; `Example` and (function problems) `### Constraints`
  sections in `prompt_markdown`.
* **Execution**: every Python and JavaScript reference solution must pass every test; every starter
  (written or generated) must load, never raise and must *not* already pass every test. Code runs
  batched (one Python and one Node process per batch, a per-problem timeout, sandbox-equivalent
  entry points and value normalisation; Python gets the standard library only, like the sandbox). SQL references run on SQLite with the sandbox rules
  (exactly one read-only statement, fresh database per test) and, with `--postgres`, inside a
  throwaway schema and role that are dropped afterwards.
* **Spoken questions**: `text` ends with `?` or `.`, has no markdown or line breaks; at least 3
  `expected_concepts` (except `recruiter`/`intro` kinds), at least one `common_errors` entry,
  distinct rubric anchors.
* **Fairness**: questions, problems, rubrics and competency anchors never mention or evaluate age,
  date of birth, marital or family status, pregnancy, children, health, disability, religion, race,
  ethnicity, national origin, citizenship or visa status, sexual orientation, gender identity, salary
  history, appearance or accent, and rubrics never judge emotion or personality ("confident",
  "calm", "enthusiastic", "honest", "culture fit"). Technical words (race condition, health check,
  child node, max-age, accent color, confidence interval) are fine.
* **Near-duplicates** within a competency (rapidfuzz `token_set_ratio` on the question text, on the
  problem statement for coding): `>= 92` is an error, `>= 85` a warning. Identical texts are errors.

Parsed YAML and clean run results are cached (`.cache/seed-validate` at the repository root, override with
`SEED_VALIDATE_CACHE_DIR`, disable with `--no-cache`), so re-validating one edited file of a
10,000-question / 1,000-problem tree takes about 2 s; the whole tree about 8 s cold and 3 s warm
(measured on a synthetic tree of that size, trivial reference solutions).

YAML gotcha: inside flow mappings (`{id: t1, description: ...}`) a value containing `: ` or `, `
must be quoted, otherwise the text after the colon becomes an extra key (reported as
`tests.0.<text>: Extra inputs are not permitted`).

## Seeding

```bash
cd apps/api
uv run python -m app.seed            # core accounts + global catalog (+ embedding jobs)
uv run python -m app.seed --demo     # ... plus the demo jobs for the demo organization
```

`seed_catalog(db, organization_id=None)` and `load_demo_jobs(db, organization_id, created_by_id)` are
idempotent: unchanged rows are left alone, changed rows are updated in place (ids stay stable) and
nothing is deleted. New rows are bulk-inserted and existing rows are loaded once without decoding
their vectors: a 10,000-question / 1,000-problem catalog seeds in about 2 s (plus 4-6 s to parse the
YAML), re-seeds in under 2 s. Rows whose embedded text changed get `question_bank.embed` jobs (64
rows per job; the worker sends 32 texts per embedding request). Set `SAMPLES_DIR` to load this tree
from another location (for example a read-only mount inside a container).

## Editing rules

* Every file has `version: 1`. Validation runs in the validator, in the unit tests
  (`tests/unit/seed`) and before every seed; errors name the file and the record.
* Competency keys are snake_case and referenced everywhere else; add new competencies (append) before
  using them.
* Round-template rubric weights sum to 100; rubric `competency_keys` must be competencies of the round.
* Question bank files may define `defaults` (merged into each question; question keys win). The only
  top-level keys are `version`, `defaults` and `questions`. `external_key` is permanent: change the
  text, not the key, so sessions that reference the question keep their link.
* Coding problems: one file per problem, file name = `slug`, in `coding/<group>/` (one group
  directory). `starter_code` is optional per language: missing function-language starters are
  generated from the signature (`app.seed.starters.starters_for`, the title as the comment); written
  starters win and are validated. `reference_solutions` needs `python` and `javascript`; other
  languages are optional (verified by the slow unit tests when the toolchain is installed). SQL
  problems keep their `sql` starter and reference, `sql_setup` with `CREATE TABLE` statements and
  `expected_rows` tests. Hidden-test descriptions are for staff only and never shown to candidates.
* Signature types: `int, long, float, bool, string, int[], long[], float[], string[], bool[], int[][],
  string[][]`. Comparison modes: `exact`, `unordered` (arrays compared after sorting at every nesting
  level) and `float_tolerance` (`|actual - expected| <= tolerance * max(1, |expected|)`, default 1e-6).
* SQL problems are portable SQL verified on SQLite (and PostgreSQL with `--postgres`).
* Content is job-related only (see Fairness above).
