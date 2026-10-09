"""Validate catalog content before it is seeded.

Usage (from ``apps/api``)::

    uv run python -m app.seed.validate                       # the whole samples tree
    uv run python -m app.seed.validate ../../samples/coding/arrays_hashing/two_sum.yaml
    uv run python -m app.seed.validate coding/sql question_bank/backend   # paths relative to samples/ work too
    uv run python -m app.seed.validate --postgres            # also run SQL references on PostgreSQL

The whole tree is always loaded, so uniqueness (``external_key``, ``slug``,
titles), cross-references (competencies, round types, role tags, pinned
slugs) and near-duplicates are checked against everything even when only a
few files are named; findings are reported for the named files only.

Checks: schemas and cross-references (:mod:`app.seed.loaders`), coding
structure and spoken-question style (:mod:`app.seed.checks`), fairness lint
(:mod:`app.seed.fairness`), near-duplicates within a competency (rapidfuzz
``token_set_ratio`` >= 92 error, >= 85 warning), and execution of every
Python and JavaScript reference solution and starter against all tests
(batched, :mod:`app.seed.runner`); SQL references run on SQLite and, with
``--postgres``, on the dev PostgreSQL inside a throwaway schema.

Parsed YAML documents and clean run results are cached in
``$SEED_VALIDATE_CACHE_DIR`` (default ``.cache/seed-validate`` at the repository root),
keyed by file size + mtime and by content hash, so re-validating one edited
file of a large tree takes about a second. ``--no-cache`` disables it.

Exit status: 0 = no errors (warnings allowed unless ``--strict``),
1 = errors, 2 = usage or setup problem.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import multiprocessing
import os
import pickle
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.seed import checks
from app.seed.fairness import FairnessHit, lint_fields
from app.seed.loaders import (
    KIND_CODING,
    KIND_COMPETENCIES,
    KIND_JOBS,
    KIND_QUESTION_BANK,
    KIND_ROLES,
    KIND_ROUND_TEMPLATES,
    CodingQuestionSeed,
    LoadedFile,
    SeedCatalog,
    SeedDataNotFound,
    assemble_catalog,
    catalog_files,
    default_samples_dir,
    file_kind,
    load_file,
    read_yaml,
    validate_catalog,
)
from app.seed.runner import (
    DEFAULT_TIMEOUT_S,
    RUN_LANGUAGES,
    RunReport,
    node_binary,
    run_function_problems,
    run_sql_problems_postgres,
    run_sql_problems_sqlite,
)

CACHE_DIR_ENV = "SEED_VALIDATE_CACHE_DIR"
_CACHE_FORMAT = 1
_PARALLEL_PARSE_MIN_FILES = 48
_ERROR = "error"
_WARNING = "warning"
_CATALOG = "<catalog>"


class UsageError(Exception):
    """Bad command-line input (exit status 2)."""


@dataclass(slots=True)
class Finding:
    file: str
    severity: str
    message: str


@dataclass(slots=True)
class Report:
    root: Path
    targets: list[str] | None
    findings: list[Finding] = field(default_factory=list)
    other_files_errors: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    stats: list[str] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == _ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == _WARNING)


# =============================================================================
# Caches
# =============================================================================


def cache_dir() -> Path:
    """``$SEED_VALIDATE_CACHE_DIR``, else ``.cache/seed-validate`` at the
    repository root (not inside a Docker build context), else next to the API
    code. An unwritable location just disables the cache."""
    configured = os.environ.get(CACHE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "apps" / "api").is_dir() and (parent / "samples").is_dir():
            return parent / ".cache" / "seed-validate"
    return here.parents[2] / ".cache" / "seed-validate"


def _root_tag(root: Path) -> str:
    return hashlib.sha256(str(root).encode()).hexdigest()[:12]


def _load_pickle(path: Path | None) -> Any:
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            return pickle.load(handle)  # noqa: S301 - written by this tool into the user's own cache dir
    except Exception:  # noqa: BLE001 - a corrupt cache is just a miss
        return None


def _save_pickle(path: Path | None, data: Any) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("wb") as handle:
            pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def _parse_for_cache(path: str) -> tuple[str, int, int, Any, bool]:
    """Process-pool worker: ``(path, size, mtime_ns, document, ok)``."""
    target = Path(path)
    try:
        stat = target.stat()
        return path, stat.st_size, stat.st_mtime_ns, read_yaml(target), True
    except Exception:  # noqa: BLE001 - the loader re-reads it and reports the error
        return path, 0, 0, None, False


class DocumentCache:
    """Parsed YAML documents keyed by path, size and mtime (``read`` is a
    drop-in for :func:`app.seed.loaders.read_yaml`)."""

    def __init__(self, root: Path, directory: Path | None) -> None:
        self.path = directory / f"documents-{_root_tag(root)}.pickle" if directory else None
        self.version = (_CACHE_FORMAT, yaml.__version__, bool(getattr(yaml, "__with_libyaml__", False)))
        self.entries: dict[str, tuple[int, int, Any]] = {}
        # path -> (size, mtime_ns, checks version, findings) for per-record checks
        self.findings: dict[str, tuple[int, int, str, list[tuple[str, str, str]]]] = {}
        self.dirty = False
        self.hits = 0
        self.misses = 0
        self.prefetched = 0
        stored = _load_pickle(self.path)
        if isinstance(stored, dict) and stored.get("version") == self.version:
            self.entries = stored.get("entries") or {}
            self.findings = stored.get("findings") or {}

    def cached_findings(self, path: Path, version: str) -> list[Finding] | None:
        entry = self.findings.get(str(path))
        stamp = self._fresh(path)
        if entry is None or stamp is None or entry[:3] != (*stamp, version):
            return None
        return [Finding(*item) for item in entry[3]]

    def store_findings(self, path: Path, version: str, findings: Sequence[Finding]) -> None:
        stamp = self._fresh(path)
        if stamp is None:
            return
        self.findings[str(path)] = (*stamp, version, [(f.file, f.severity, f.message) for f in findings])
        self.dirty = True

    def _fresh(self, path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        entry = self.entries.get(str(path))
        if entry is not None and entry[0] == stat.st_size and entry[1] == stat.st_mtime_ns:
            return entry[0], entry[1]
        return None

    def prefetch(self, paths: Iterable[Path], workers: int | None = None) -> None:
        stale = [str(p) for p in paths if self._fresh(p) is None]
        cpus = os.cpu_count() or 1
        if len(stale) < _PARALLEL_PARSE_MIN_FILES or cpus < 3:
            return  # read() parses them lazily
        try:
            context = multiprocessing.get_context("fork")
        except ValueError:
            return
        count = max(2, min(workers or cpus // 2, 16))
        with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
            for path, size, mtime, document, ok in pool.map(_parse_for_cache, stale, chunksize=8):
                if ok:
                    self.entries[path] = (size, mtime, document)
                    self.dirty = True
                    self.prefetched += 1

    def read(self, path: Path) -> Any:
        if not path.is_file():
            raise SeedDataNotFound(str(path))
        if self._fresh(path) is not None:
            self.hits += 1
            return self.entries[str(path)][2]
        stat = path.stat()
        document = read_yaml(path)  # parse errors propagate (and are not cached)
        self.entries[str(path)] = (stat.st_size, stat.st_mtime_ns, document)
        self.dirty = True
        self.misses += 1
        return document

    def save(self, live: Iterable[Path]) -> None:
        keep = {str(p) for p in live}
        if set(self.entries) - keep or set(self.findings) - keep:
            self.entries = {k: v for k, v in self.entries.items() if k in keep}
            self.findings = {k: v for k, v in self.findings.items() if k in keep}
            self.dirty = True
        if self.dirty:
            _save_pickle(
                self.path, {"version": self.version, "entries": self.entries, "findings": self.findings}
            )


def _code_version(*modules: str) -> str:
    digest = hashlib.sha256()
    for module in modules or ("runner.py", "loaders.py", "starters.py"):
        with contextlib.suppress(OSError):
            digest.update((Path(__file__).parent / module).read_bytes())
    return digest.hexdigest()[:16]


class RunCache:
    """Clean run results (no errors) keyed by the problem content, the runner
    code and the run options; a problem is re-run as soon as anything changes."""

    def __init__(self, root: Path, directory: Path | None, options: str) -> None:
        self.path = directory / f"runs-{_root_tag(root)}.pickle" if directory else None
        self.salt = f"{_code_version()}|{options}"
        self.entries: dict[str, list[str]] = {}
        self.dirty = False
        stored = _load_pickle(self.path)
        if isinstance(stored, dict) and stored.get("format") == _CACHE_FORMAT:
            self.entries = stored.get("entries") or {}

    def key(self, problem: CodingQuestionSeed) -> str:
        material = json.dumps(problem.to_row(), sort_keys=True, default=str)
        return hashlib.sha256(f"{self.salt}|{material}".encode()).hexdigest()

    def get(self, problem: CodingQuestionSeed) -> list[str] | None:
        return self.entries.get(self.key(problem))

    def put(self, problem: CodingQuestionSeed, warnings: list[str]) -> None:
        self.entries[self.key(problem)] = list(warnings)
        self.dirty = True

    def save(self, live: Iterable[CodingQuestionSeed] | None) -> None:
        if live is not None:
            keep = {self.key(p) for p in live}
            if set(self.entries) - keep:
                self.entries = {k: v for k, v in self.entries.items() if k in keep}
                self.dirty = True
        if self.dirty:
            _save_pickle(self.path, {"format": _CACHE_FORMAT, "entries": self.entries})


# =============================================================================
# Targets
# =============================================================================


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def resolve_targets(
    root: Path, args: Sequence[str], listing: Sequence[tuple[str, Path]]
) -> list[Path] | None:
    """Absolute catalog file paths named by ``args`` (files or directories,
    relative to the working directory or to the samples root); ``None`` when
    no path was given (whole tree)."""
    if not args:
        return None
    known = [path for _, path in listing]
    chosen: dict[Path, None] = {}
    for arg in args:
        candidates = [Path(arg).expanduser(), root / arg]
        found = next((c.resolve() for c in candidates if c.exists()), None)
        if found is None:
            raise UsageError(f"path not found: {arg}")
        if found != root and root not in found.parents:
            raise UsageError(f"{arg} is outside the samples tree {root}")
        if found.is_dir():
            matched = [p for p in known if p == found or found in p.parents]
            if not matched:
                raise UsageError(f"{arg} contains no catalog files")
            chosen.update(dict.fromkeys(matched))
            continue
        if found.suffix == ".yml":
            raise UsageError(f"{arg}: catalog files use the .yaml extension")
        if file_kind(found, root) is None:
            raise UsageError(
                f"{arg} is not a catalog file (catalog/competencies.yaml, catalog/round_templates.yaml, "
                "catalog/roles.yaml, question_bank/**/*.yaml, coding/**/*.yaml or jobs/*.yaml)"
            )
        chosen[found] = None
    return list(chosen)


# =============================================================================
# Validation
# =============================================================================


def _split_problem(message: str, known_files: set[str]) -> tuple[str, str]:
    head, sep, rest = message.partition(": ")
    if sep and (head in known_files or head.endswith((".yaml", ".yml"))):
        return head, rest
    return _CATALOG, message


def _fairness_findings(file: str, label: str, hits: Sequence[FairnessHit]) -> list[Finding]:
    return [Finding(file, _ERROR, f"{label}: fairness: {hit.describe()}") for hit in hits]


def _record_findings(loaded: LoadedFile, rel: str) -> list[Finding]:
    out: list[Finding] = []
    for record in loaded.records:
        if loaded.kind == KIND_QUESTION_BANK:
            key = record.external_key
            out += [Finding(rel, _ERROR, f"{key}: {m}") for m in checks.question_style_problems(record)]
            out += _fairness_findings(rel, key, lint_fields(checks.question_fields(record)))
        elif loaded.kind == KIND_CODING:
            out += [
                Finding(rel, _ERROR, f"{record.slug}: {m}") for m in checks.coding_structure_problems(record)
            ]
            out += [
                Finding(rel, _WARNING, f"{record.slug}: {m}") for m in checks.coding_location_warnings(rel)
            ]
            out += _fairness_findings(rel, record.slug, lint_fields(checks.coding_fields(record)))
        elif loaded.kind == KIND_COMPETENCIES:
            out += _fairness_findings(rel, record.key, lint_fields(checks.competency_fields(record)))
        elif loaded.kind == KIND_ROLES:
            out += _fairness_findings(rel, record.key, lint_fields(checks.role_fields(record)))
        elif loaded.kind == KIND_JOBS:
            out += _fairness_findings(rel, record.key, lint_fields(checks.job_fields(record)))
        elif loaded.kind == KIND_ROUND_TEMPLATES:
            out += _fairness_findings(
                rel, record.round_type.value, lint_fields(checks.template_fields(record))
            )
    return out


def _duplicate_findings(
    catalog: SeedCatalog,
    target_ids: set[str] | None,
    question_file: dict[str, str],
    coding_file: dict[str, str],
) -> list[Finding]:
    out: list[Finding] = []

    def wanted(record_id: str) -> bool:
        # Single-file mode reports the named files' side of each pair only.
        return target_ids is None or record_id in target_ids

    def report(kind: str, files: dict[str, str], pair: checks.DuplicatePair) -> None:
        severity = _ERROR if pair.is_error else _WARNING
        for this, other in ((pair.a, pair.b), (pair.b, pair.a)):
            if not wanted(this):
                continue
            out.append(
                Finding(
                    files.get(this, _CATALOG),
                    severity,
                    f"{this}: near-duplicate {kind} of {other} ({files.get(other, '?')}) "
                    f"in competency {pair.group}: token_set_ratio {pair.score}",
                )
            )

    question_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for q in catalog.questions:
        question_groups[q.competency_key].append((q.external_key, q.text))
    for pair in checks.near_duplicate_pairs(question_groups, targets=target_ids):
        report("question", question_file, pair)
    for ids in checks.exact_duplicates((q.external_key, q.text) for q in catalog.questions):
        unique = sorted(set(ids))
        for key in unique:
            if wanted(key):
                others = ", ".join(k for k in unique if k != key)
                out.append(Finding(question_file.get(key, _CATALOG), _ERROR, f"{key}: same text as {others}"))

    coding_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in catalog.coding_questions:
        statement = checks.problem_statement(p)
        for competency in p.competency_keys:
            coding_groups[competency].append((p.slug, statement))
    for pair in checks.near_duplicate_pairs(coding_groups, targets=target_ids):
        report("problem", coding_file, pair)
    titles: dict[str, list[str]] = defaultdict(list)
    for p in catalog.coding_questions:
        titles[p.title.strip().casefold()].append(p.slug)
    for slugs in titles.values():
        unique = sorted(set(slugs))
        if len(unique) < 2:
            continue
        for slug in unique:
            if wanted(slug):
                others = ", ".join(s for s in unique if s != slug)
                out.append(
                    Finding(coding_file.get(slug, _CATALOG), _ERROR, f"{slug}: same title as {others}")
                )
    return out


def _run_code(
    problems: Sequence[CodingQuestionSeed],
    coding_file: dict[str, str],
    *,
    postgres: bool,
    timeout: float,
    workers: int | None,
    cache: RunCache | None,
    report: Report,
) -> None:
    pending: list[CodingQuestionSeed] = []
    cached = 0
    for problem in problems:
        hit = cache.get(problem) if cache is not None else None
        if hit is None:
            pending.append(problem)
            continue
        cached += 1
        report.findings += [Finding(coding_file[problem.slug], _WARNING, f"{problem.slug}: {m}") for m in hit]
    run = RunReport()
    started = time.perf_counter()
    function_problems = [p for p in pending if not p.is_sql]
    sql_problems = [p for p in pending if p.is_sql]
    if function_problems:
        run.merge(run_function_problems(function_problems, timeout=timeout, workers=workers))
    if sql_problems:
        run.merge(run_sql_problems_sqlite(sql_problems))
        if postgres:
            run.merge(run_sql_problems_postgres(sql_problems))
    report.timings["run"] = round(time.perf_counter() - started, 3)
    report.counts["run_jobs"] = run.jobs
    report.counts["run_cached"] = cached
    report.notes += run.notes
    for slug, messages in run.errors.items():
        report.findings += [
            Finding(coding_file.get(slug, _CATALOG), _ERROR, f"{slug}: {m}") for m in messages
        ]
    for slug, messages in run.warnings.items():
        report.findings += [
            Finding(coding_file.get(slug, _CATALOG), _WARNING, f"{slug}: {m}") for m in messages
        ]
    if cache is not None:
        toolchains_complete = not any("not installed" in note for note in run.notes)
        for problem in pending:
            if problem.slug not in run.errors and toolchains_complete:
                cache.put(problem, run.warnings.get(problem.slug, []))


def validate(
    root: Path | None = None,
    paths: Sequence[str] = (),
    *,
    run: bool = True,
    postgres: bool = False,
    use_cache: bool = True,
    timeout: float = DEFAULT_TIMEOUT_S,
    workers: int | None = None,
    stats: bool = False,
) -> Report:
    """Validate the samples tree (or the named files against the whole tree)."""
    started = time.perf_counter()
    root = (root or default_samples_dir()).resolve()
    listing_problems: list[str] = []
    listing = catalog_files(root, listing_problems)
    targets = resolve_targets(root, paths, listing)
    report = Report(root=root, targets=[_rel(p, root) for p in targets] if targets is not None else None)
    directory = cache_dir() if use_cache else None

    documents = DocumentCache(root, directory)
    documents.prefetch(path for _, path in listing)
    loaded = [load_file(kind, path, root, read=documents.read) for kind, path in listing]
    catalog = assemble_catalog(root, loaded)
    cross = validate_catalog(catalog)
    report.timings["load"] = round(time.perf_counter() - started, 3)
    report.counts["files"] = len(listing)
    report.counts["parsed_files"] = documents.prefetched + documents.misses

    rel_files = {
        _rel(path, root): loaded_file for loaded_file, (_, path) in zip(loaded, listing, strict=True)
    }
    known = set(rel_files)
    in_scope = set(report.targets) if report.targets is not None else known

    findings: list[Finding] = []
    for message in [*listing_problems, *(p for f in loaded for p in f.problems), *cross]:
        file, text = _split_problem(message, known)
        findings.append(Finding(file, _ERROR, text))

    checked = time.perf_counter()
    checks_version = _code_version("checks.py", "fairness.py", "loaders.py", "starters.py", "validate.py")
    for rel in sorted(in_scope):
        loaded_file = rel_files.get(rel)
        if loaded_file is None:
            continue
        cached = documents.cached_findings(loaded_file.path, checks_version)
        if cached is None:
            cached = _record_findings(loaded_file, rel)
            documents.store_findings(loaded_file.path, checks_version, cached)
        findings += cached
    documents.save(path for _, path in listing)

    question_file = {
        key: _rel(paths_[0], root) for (kind, key), paths_ in catalog.locations.items() if kind == "question"
    }
    coding_file = {
        key: _rel(paths_[0], root) for (kind, key), paths_ in catalog.locations.items() if kind == "coding"
    }
    target_ids: set[str] | None = None
    if report.targets is not None:
        target_ids = set()
        for rel in in_scope:
            loaded_file = rel_files.get(rel)
            if loaded_file is None:
                continue
            if loaded_file.kind == KIND_QUESTION_BANK:
                target_ids.update(q.external_key for q in loaded_file.records)
            elif loaded_file.kind == KIND_CODING:
                target_ids.update(p.slug for p in loaded_file.records)
    findings += _duplicate_findings(catalog, target_ids, question_file, coding_file)
    report.timings["checks"] = round(time.perf_counter() - checked, 3)

    if report.targets is None:
        report.findings = findings
    else:
        report.findings = [f for f in findings if f.file in in_scope]
        report.other_files_errors = sum(
            1 for f in findings if f.severity == _ERROR and f.file not in in_scope
        )

    if run:
        problems = [
            p
            for rel in sorted(in_scope)
            if (f := rel_files.get(rel)) and f.kind == KIND_CODING
            for p in f.records
        ]
        options = (
            f"timeout={timeout}|postgres={postgres}|langs={','.join(lang.value for lang in RUN_LANGUAGES)}"
        )
        run_cache = RunCache(root, directory, options) if directory is not None else None
        _run_code(
            problems,
            coding_file,
            postgres=postgres,
            timeout=timeout,
            workers=workers,
            cache=run_cache,
            report=report,
        )
        if run_cache is not None:
            # Whole-tree runs prune results of changed or removed problems.
            run_cache.save(catalog.coding_questions if report.targets is None else None)

    report.counts.update(
        {
            "questions": len(catalog.questions),
            "coding": len(catalog.coding_questions),
            "coding_sql": sum(1 for p in catalog.coding_questions if p.is_sql),
            "competencies": len(catalog.competencies),
            "roles": len(catalog.roles),
            "jobs": len(catalog.jobs),
            "checked_files": len(in_scope),
        }
    )
    if stats:
        report.stats = checks.coverage_lines(catalog)
    if run and node_binary() is None:
        report.notes.append("install node to run the JavaScript references")
    report.timings["total"] = round(time.perf_counter() - started, 3)
    return report


# =============================================================================
# Output
# =============================================================================


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def summary_line(report: Report) -> str:
    c = report.counts
    tree = (
        f"{_plural(c.get('questions', 0), 'question')}, {_plural(c.get('coding', 0), 'coding problem')} "
        f"({c.get('coding_sql', 0)} SQL), {c.get('competencies', 0)} competencies, {c.get('roles', 0)} roles"
    )
    if report.targets is None:
        scope = f"validated {_plural(c.get('files', 0), 'file')}: {tree}"
    else:
        scope = f"validated {_plural(len(report.targets), 'file')} against the tree ({tree})"
    ran = ""
    if "run_jobs" in c:
        ran = f"; ran {_plural(c['run_jobs'], 'code check')}"
        if c.get("run_cached"):
            ran += f" ({_plural(c['run_cached'], 'problem')} unchanged since the last clean run)"
    result = f"{_plural(report.errors, 'error')}, {_plural(report.warnings, 'warning')}"
    tail = (
        f" ({_plural(report.other_files_errors, 'error')} in other files)"
        if report.other_files_errors
        else ""
    )
    return f"{scope}{ran}: {result}{tail} in {report.timings.get('total', 0):.1f} s"


def render_text(report: Report, *, max_per_file: int = 50, verbose: bool = False) -> str:
    lines: list[str] = []
    by_file: dict[str, list[Finding]] = defaultdict(list)
    for finding in report.findings:
        by_file[finding.file].append(finding)
    for file in sorted(by_file, key=lambda f: (f == _CATALOG, f)):
        items = sorted(by_file[file], key=lambda f: (f.severity != _ERROR,))
        lines.append(file)
        shown = items if max_per_file <= 0 else items[:max_per_file]
        lines += [f"  {f.severity:<8} {f.message}" for f in shown]
        if len(items) > len(shown):
            lines.append(f"  ... {len(items) - len(shown)} more (use --max-per-file 0)")
    for note in dict.fromkeys(report.notes):
        lines.append(f"note: {note}")
    if report.stats:
        lines.append("coverage:")
        lines += [f"  {line}" for line in report.stats]
    if verbose:
        lines.append("timings: " + ", ".join(f"{k} {v:.2f} s" for k, v in report.timings.items()))
    lines.append(summary_line(report))
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps(
        {
            "root": str(report.root),
            "targets": report.targets,
            "errors": report.errors,
            "warnings": report.warnings,
            "other_files_errors": report.other_files_errors,
            "counts": report.counts,
            "timings": report.timings,
            "notes": list(dict.fromkeys(report.notes)),
            "stats": report.stats,
            "findings": [
                {"file": f.file, "severity": f.severity, "message": f.message} for f in report.findings
            ],
        },
        indent=2,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed.validate",
        description="Validate catalog content (schemas, cross-references, fairness, near-duplicates, "
        "reference solutions). Without PATH the whole samples tree is validated.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="catalog files or directories to report on")
    parser.add_argument(
        "--postgres", action="store_true", help="also run SQL references on the dev PostgreSQL"
    )
    parser.add_argument("--no-run", action="store_true", help="skip running reference solutions and starters")
    parser.add_argument(
        "--no-cache", action="store_true", help=f"ignore and do not write the cache (${CACHE_DIR_ENV})"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds per problem and code"
    )
    parser.add_argument(
        "--jobs", type=int, default=None, help="runner processes per language (default: auto)"
    )
    parser.add_argument("--stats", action="store_true", help="print coverage statistics")
    parser.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--max-per-file", type=int, default=50, help="findings shown per file (0 = all)")
    parser.add_argument(
        "--samples-dir", type=Path, default=None, help="samples root (default: $SAMPLES_DIR or repo)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print timings")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = validate(
            args.samples_dir,
            args.paths,
            run=not args.no_run,
            postgres=args.postgres,
            use_cache=not args.no_cache,
            timeout=args.timeout,
            workers=args.jobs,
            stats=args.stats,
        )
    except (UsageError, SeedDataNotFound) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        render_json(report)
        if args.json
        else render_text(report, max_per_file=args.max_per_file, verbose=args.verbose)
    )
    if report.errors or (args.strict and report.warnings):
        return 1
    return 0


__all__ = [
    "CACHE_DIR_ENV",
    "DocumentCache",
    "Finding",
    "Report",
    "RunCache",
    "UsageError",
    "main",
    "render_json",
    "render_text",
    "resolve_targets",
    "summary_line",
    "validate",
]


if __name__ == "__main__":
    sys.exit(main())
