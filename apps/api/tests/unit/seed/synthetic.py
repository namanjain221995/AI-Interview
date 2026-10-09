"""Synthetic, valid catalog content at production scale (tests and benchmarks).

``build_tree(out, questions=10_000, problems=1_000)`` copies the real
``samples/`` catalog files (competencies, templates, roles) and demo jobs
(with the coding problems they pin) into ``out`` - or, with
``full_base=True``, the whole current samples tree - and adds:

* 28 roles (appended to ``catalog/roles.yaml``) and 20 appended competencies,
* ``question_bank/<round_type>/<role>.yaml`` files with ``questions`` new
  questions (distinct random wording, so near-duplicate detection stays quiet),
* ``coding/<group>/<slug>.yaml`` files with ``problems`` new problems (about
  5 % SQL): Python and JavaScript references that agree with the expected
  values (computed by executing the Python reference), mostly generated
  starters.

Nothing is written into the repository. CLI::

    uv run python -m tests.unit.seed.synthetic OUT_DIR [--questions N] [--problems N] [--seed S] [--full-base]
"""

from __future__ import annotations

import argparse
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.domain.enums import RoundType
from app.seed.loaders import default_samples_dir

ROLES: tuple[tuple[str, str, str], ...] = (
    ("backend_engineer", "Backend Engineer", "engineering"),
    ("frontend_engineer", "Frontend Engineer", "engineering"),
    ("fullstack_engineer", "Full-Stack Engineer", "engineering"),
    ("devops_engineer", "DevOps Engineer", "infrastructure"),
    ("sre", "Site Reliability Engineer", "infrastructure"),
    ("platform_engineer", "Platform Engineer", "infrastructure"),
    ("cloud_engineer", "Cloud Engineer", "infrastructure"),
    ("security_engineer", "Security Engineer", "security"),
    ("data_engineer", "Data Engineer", "data"),
    ("data_scientist", "Data Scientist", "data"),
    ("ml_engineer", "ML Engineer", "ai"),
    ("llm_engineer", "LLM Engineer", "ai"),
    ("mobile_engineer", "Mobile Engineer", "engineering"),
    ("qa_engineer", "QA Engineer", "quality"),
    ("sdet", "Software Engineer in Test", "quality"),
    ("embedded_engineer", "Embedded Engineer", "engineering"),
    ("engineering_manager", "Engineering Manager", "management"),
    ("tech_lead", "Tech Lead", "management"),
    ("solutions_architect", "Solutions Architect", "architecture"),
    ("product_manager", "Product Manager", "product"),
    ("technical_program_manager", "Technical Program Manager", "management"),
    ("database_administrator", "Database Administrator", "data"),
    ("network_engineer", "Network Engineer", "infrastructure"),
    ("support_engineer", "Support Engineer", "operations"),
    ("analytics_engineer", "Analytics Engineer", "data"),
    ("game_developer", "Game Developer", "engineering"),
    ("blockchain_engineer", "Blockchain Engineer", "engineering"),
    ("technical_writer", "Technical Writer", "product"),
)
GROUPS = (
    "arrays_hashing", "two_pointers", "sliding_window", "stacks", "binary_search", "linked_lists",
    "trees", "graphs", "dynamic_programming", "greedy", "intervals", "math", "strings",
)  # fmt: skip
SQL_GROUP = "sql"

_SYLLABLES = (
    "ka", "lo", "mi", "ne", "su", "ta", "vo", "ri", "pe", "xu", "zan", "bel", "cor", "dun", "fal", "gim",
    "hol", "jun", "kel", "lor", "mur", "nix", "oth", "pra", "qui", "ros", "sel", "tor", "ulm", "vex",
)  # fmt: skip


def _vocabulary(rng: random.Random, size: int) -> list[str]:
    words: set[str] = set()
    while len(words) < size:
        words.add("".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 4))))
    return sorted(words)


def _sentence(rng: random.Random, vocab: list[str], low: int, high: int, end: str = ".") -> str:
    words = rng.sample(vocab, rng.randint(low, high))
    return (" ".join(words)).capitalize() + end


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            data,
            Dumper=getattr(yaml, "CSafeDumper", yaml.SafeDumper),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=None,
            width=120,
        ),
        encoding="utf-8",
    )


# =============================================================================
# Catalog: competencies + roles
# =============================================================================


def _extend_competencies(root: Path, rng: random.Random, vocab: list[str], count: int) -> list[str]:
    path = root / "catalog" / "competencies.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    categories = ["technical", "problem_solving", "system_design", "behavioral", "leadership", "domain"]
    added = []
    existing = {c["key"] for c in data["competencies"]}
    for index in range(count):
        key = f"synthetic_skill_{index:02d}"
        if key in existing:
            continue
        data["competencies"].append(
            {
                "key": key,
                "name": f"Synthetic Skill {index:02d}",
                "category": categories[index % len(categories)],
                "description": _sentence(rng, vocab, 10, 16),
                "rubric": {
                    "levels": {str(level): _sentence(rng, vocab, 10, 16) for level in (1, 3, 5)},
                    "dimensions": [f"dimension_{d}" for d in range(3)],
                },
            }
        )
        added.append(key)
    _dump(path, data)
    return [c["key"] for c in data["competencies"]]


def _write_roles(root: Path, rng: random.Random, vocab: list[str], competencies: list[str]) -> list[str]:
    rounds = [r.value for r in RoundType if r not in (RoundType.CUSTOM, RoundType.RESUME_SCREENING)]
    path = root / "catalog" / "roles.yaml"
    roles = yaml.safe_load(path.read_text(encoding="utf-8"))["roles"] if path.exists() else []
    existing = {r["key"] for r in roles}
    for key, title, family in ROLES:
        if key in existing:
            continue
        roles.append(
            {
                "key": key,
                "title": title,
                "family": family,
                "description": _sentence(rng, vocab, 10, 18),
                "competency_keys": rng.sample(competencies, 8),
                "round_types": sorted(rng.sample(rounds, 5)),
            }
        )
    _dump(path, {"version": 1, "roles": roles})
    return [key for key, _, _ in ROLES]


# =============================================================================
# Questions
# =============================================================================


def _question(
    rng: random.Random, vocab: list[str], key: str, competency: str, round_type: str, roles: list[str]
) -> dict[str, Any]:
    difficulty = rng.choice(("easy", "medium", "medium", "hard"))
    kind = {
        "behavioral": "behavioral",
        "leadership": "leadership",
        "managerial": "leadership",
        "recruiter_screening": "recruiter",
        "system_design": "system_design",
    }.get(round_type, rng.choice(("technical", "scenario")))
    return {
        "external_key": key,
        "role_tags": sorted(set(roles)),
        "round_types": [round_type],
        "competency_key": competency,
        "topic": _sentence(rng, vocab, 2, 4, end="")[:100],
        "difficulty": difficulty,
        "kind": kind,
        "text": "Walk me through " + _sentence(rng, vocab, 14, 22, end="?").lower(),
        "expected_concepts": [_sentence(rng, vocab, 3, 5, end="") for _ in range(4)],
        "rubric": {
            "excellent": _sentence(rng, vocab, 12, 18),
            "good": _sentence(rng, vocab, 10, 14),
            "weak": _sentence(rng, vocab, 8, 12),
        },
        "followups": [_sentence(rng, vocab, 8, 12, end="?") for _ in range(2)],
        "common_errors": [_sentence(rng, vocab, 8, 12) for _ in range(2)],
    }


def _write_questions(
    root: Path, rng: random.Random, vocab: list[str], count: int, competencies: list[str], roles: list[str]
) -> int:
    rounds = [r.value for r in RoundType if r not in (RoundType.CUSTOM, RoundType.CODING)]
    per_file = 30
    written = 0
    file_index = 0
    while written < count:
        round_type = rounds[file_index % len(rounds)]
        role = roles[(file_index // len(rounds)) % len(roles)]
        batch = min(per_file, count - written)
        questions = [
            _question(
                rng,
                vocab,
                f"synthetic.{round_type}.q{written + i:05d}",
                rng.choice(competencies),
                round_type,
                [role, *rng.sample(roles, 1)],
            )
            for i in range(batch)
        ]
        path = root / "question_bank" / round_type / f"{role}_{file_index:04d}.yaml"
        _dump(path, {"version": 1, "defaults": {"role_tags": [role]}, "questions": questions})
        written += batch
        file_index += 1
    return written


# =============================================================================
# Coding problems
# =============================================================================


@dataclass(frozen=True)
class _Family:
    params: list[dict[str, str]]
    return_type: str
    comparison: str
    python: str
    javascript: str
    make_args: Any  # (rng) -> list


def _families(m: int, r: int, c: int) -> list[_Family]:
    def ints(rng: random.Random, n: int, lo: int = -1000, hi: int = 1000) -> list[int]:
        return [rng.randint(lo, hi) for _ in range(n)]

    return [
        _Family(
            [{"name": "nums", "type": "int[]"}, {"name": "k", "type": "int"}],
            "int",
            "exact",
            f"def solve_it(nums, k):\n    total = 0\n    for x in nums:\n        if x % {m} == {r}:\n            total += x * {c}\n    return total + k\n",
            f"function solveIt(nums, k) {{\n  let total = 0;\n  for (const x of nums) {{\n    if (((x % {m}) + {m}) % {m} === {r}) total += x * {c};\n  }}\n  return total + k;\n}}\n",
            lambda rng: [ints(rng, rng.choice((5, 20, 400))), rng.randint(-50, 50)],
        ),
        _Family(
            [{"name": "nums", "type": "int[]"}],
            "int[]",
            "exact",
            f"def solve_it(nums):\n    return sorted({{x for x in nums if x % {m} != {r}}})\n",
            f"function solveIt(nums) {{\n  const s = [...new Set(nums.filter((x) => ((x % {m}) + {m}) % {m} !== {r}))];\n  return s.sort((a, b) => a - b);\n}}\n",
            lambda rng: [ints(rng, rng.choice((6, 30, 300)), -200, 200)],
        ),
        _Family(
            [{"name": "words", "type": "string[]"}],
            "string[]",
            "unordered",
            f"def solve_it(words):\n    return [w for w in words if len(w) % {m} == {r}]\n",
            f"function solveIt(words) {{\n  return words.filter((w) => w.length % {m} === {r});\n}}\n",
            lambda rng: [
                ["".join(rng.choice("abcdef") for _ in range(rng.randint(1, 9))) for _ in range(12)]
            ],
        ),
        _Family(
            [{"name": "values", "type": "float[]"}],
            "float",
            "float_tolerance",
            f"def solve_it(values):\n    if not values:\n        return 0.0\n    return sum(values) / len(values) * {c}\n",
            f"function solveIt(values) {{\n  if (values.length === 0) return 0;\n  return values.reduce((a, b) => a + b, 0) / values.length * {c};\n}}\n",
            lambda rng: [[round(rng.uniform(-100, 100), 3) for _ in range(rng.randint(1, 40))]],
        ),
        _Family(
            [{"name": "grid", "type": "int[][]"}],
            "int",
            "exact",
            f"def solve_it(grid):\n    best = 0\n    for row in grid:\n        best = max(best, sum(v for v in row if v % {m} == {r}))\n    return best\n",
            f"function solveIt(grid) {{\n  let best = 0;\n  for (const row of grid) {{\n    let s = 0;\n    for (const v of row) if (((v % {m}) + {m}) % {m} === {r}) s += v;\n    best = Math.max(best, s);\n  }}\n  return best;\n}}\n",
            lambda rng: [[ints(rng, 6, 0, 99) for _ in range(rng.randint(1, 8))]],
        ),
        _Family(
            [{"name": "s", "type": "string"}, {"name": "k", "type": "int"}],
            "bool",
            "exact",
            f"def solve_it(s, k):\n    return sum(1 for ch in s if ord(ch) % {m} == {r}) >= k\n",
            f"function solveIt(s, k) {{\n  let n = 0;\n  for (const ch of s) if (ch.charCodeAt(0) % {m} === {r}) n++;\n  return n >= k;\n}}\n",
            lambda rng: [
                "".join(rng.choice("abcdefghij") for _ in range(rng.randint(3, 30))),
                rng.randint(0, 6),
            ],
        ),
    ]


def _run_python(code: str, args: list[Any]) -> Any:
    namespace: dict[str, Any] = {}
    exec(code, namespace)  # noqa: S102 - generated code
    return namespace["solve_it"](*args)


def _default_value(return_type: str) -> Any:
    return {"int": 0, "float": 0.0, "bool": False, "string": ""}.get(return_type, [])


def _function_problem(
    rng: random.Random, vocab: list[str], slug: str, competencies: list[str]
) -> dict[str, Any]:
    m = rng.randint(2, 7)
    r = rng.randint(0, m - 1)
    c = rng.randint(1, 9)
    family = rng.choice(_families(m, r, c))
    tests = []
    attempts = 0
    while len(tests) < 10 and attempts < 200:
        attempts += 1
        args = family.make_args(rng)
        expected = _run_python(family.python, args)
        hidden = len(tests) >= 3
        if not hidden and expected == _default_value(family.return_type):
            continue  # visible tests discriminate the generated starter
        tests.append(
            {
                "id": f"t{len(tests) + 1}",
                "args": args,
                "expected": expected,
                "hidden": hidden,
                "description": _sentence(rng, vocab, 3, 6, end=""),
            }
        )
    title = " ".join(w.capitalize() for w in rng.sample(vocab, 3))
    statement = _sentence(rng, vocab, 25, 40)
    example = tests[0]
    prompt = (
        f"## {title}\n\n{statement}\n\n### Example 1\n```\nInput:  {example['args']}\nOutput: {example['expected']}\n```\n\n"
        "### Constraints\n- `1 <= n <= 1000`\n"
    )
    problem: dict[str, Any] = {
        "version": 1,
        "slug": slug,
        "title": title,
        "difficulty": rng.choice(("easy", "medium", "hard")),
        "competency_keys": sorted(set(rng.sample(competencies, 2))),
        "role_tags": ["general", "backend"],
        "time_limit_minutes": 20,
        "prompt_markdown": prompt,
        "languages": ["python", "javascript", "typescript", "java", "go", "cpp"],
        "signature": {
            "function_name": "solve_it",
            "params": family.params,
            "return_type": family.return_type,
            "comparison": family.comparison,
        },
        "reference_solutions": {"python": family.python, "javascript": family.javascript},
        "tests": tests,
        "expected_concepts": [_sentence(rng, vocab, 3, 5, end="") for _ in range(3)],
        "optimal_complexity": {"time": "O(n)", "space": "O(1)"},
        "rubric": {
            "excellent": _sentence(rng, vocab, 12, 18),
            "good": _sentence(rng, vocab, 10, 14),
            "weak": _sentence(rng, vocab, 8, 12),
            "criteria": {
                name: _sentence(rng, vocab, 6, 10) for name in ("correctness", "efficiency", "code_quality")
            },
        },
        "hints": [_sentence(rng, vocab, 6, 8), _sentence(rng, vocab, 9, 12), _sentence(rng, vocab, 13, 18)],
        "followups": [_sentence(rng, vocab, 8, 12, end="?")],
        "common_errors": [_sentence(rng, vocab, 8, 12)],
    }
    if rng.random() < 0.1:  # a few problems ship an explicit Python starter
        problem["starter_code"] = {
            "python": "def solve_it(*args):\n    # Explicit starter.\n    return None\n"
        }
    return problem


def _sql_problem(rng: random.Random, vocab: list[str], slug: str) -> dict[str, Any]:
    table = f"t_{slug[-6:]}"
    threshold = rng.randint(10, 90)
    reference = f"SELECT name FROM {table} WHERE score > {threshold} ORDER BY name;"
    tests = []
    for index in range(9):
        rows = [(i, f"n{rng.randint(0, 999)}", rng.randint(0, 100)) for i in range(rng.randint(1, 12))]
        values = ", ".join(f"({i}, '{n}', {s})" for i, n, s in rows)
        expected = sorted([[n] for _, n, s in rows if s > threshold])
        tests.append(
            {
                "id": f"t{index + 1}",
                "description": _sentence(rng, vocab, 3, 6, end=""),
                "setup_sql": f"INSERT INTO {table} (id, name, score) VALUES {values};",
                "expected_rows": expected,
                "ordered": True,
                "hidden": index >= 3,
            }
        )
    title = " ".join(w.capitalize() for w in rng.sample(vocab, 3))
    return {
        "version": 1,
        "slug": slug,
        "title": title,
        "difficulty": "easy",
        "competency_keys": ["sql", "databases"],
        "role_tags": ["backend", "data"],
        "time_limit_minutes": 10,
        "prompt_markdown": (
            f"## {title}\n\n{_sentence(rng, vocab, 25, 35)}\n\n### Example\n```\nname\n----\nn1\n```\n\n"
            "Write standard SQL that runs on both PostgreSQL and SQLite.\n"
        ),
        "languages": ["sql"],
        "starter_code": {"sql": f"SELECT name FROM {table};\n"},
        "reference_solutions": {"sql": reference},
        "sql_setup": f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name VARCHAR(40) NOT NULL, score INTEGER NOT NULL);\n",
        "tests": tests,
        "expected_concepts": [_sentence(rng, vocab, 3, 5, end="") for _ in range(3)],
        "optimal_complexity": {"time": "O(n log n)", "space": "O(n)"},
        "rubric": {
            "excellent": _sentence(rng, vocab, 12, 18),
            "good": _sentence(rng, vocab, 10, 14),
            "weak": _sentence(rng, vocab, 8, 12),
            "criteria": {
                name: _sentence(rng, vocab, 6, 10) for name in ("correctness", "efficiency", "readability")
            },
        },
        "hints": [_sentence(rng, vocab, 6, 8), _sentence(rng, vocab, 9, 12), _sentence(rng, vocab, 13, 18)],
        "followups": [_sentence(rng, vocab, 8, 12, end="?")],
        "common_errors": [_sentence(rng, vocab, 8, 12)],
    }


def _write_problems(root: Path, rng: random.Random, vocab: list[str], count: int) -> int:
    competencies = ["algorithms", "data_structures", "problem_solving", "python", "javascript"]
    for index in range(count):
        if index % 20 == 19:
            slug = f"synthetic_sql_{index:05d}"
            data = _sql_problem(rng, vocab, slug)
            group = SQL_GROUP
        else:
            slug = f"synthetic_fn_{index:05d}"
            data = _function_problem(rng, vocab, slug, competencies)
            group = GROUPS[index % len(GROUPS)]
        _dump(root / "coding" / group / f"{slug}.yaml", data)
    return count


# =============================================================================
# Entry points
# =============================================================================


def _copy_base(source: Path, out: Path, *, full: bool) -> None:
    if full:
        for part in ("catalog", "question_bank", "coding", "jobs"):
            shutil.copytree(source / part, out / part, dirs_exist_ok=True)
        return
    shutil.copytree(source / "catalog", out / "catalog", dirs_exist_ok=True)
    shutil.copytree(source / "jobs", out / "jobs", dirs_exist_ok=True)
    (out / "question_bank").mkdir(exist_ok=True)
    (out / "coding").mkdir(exist_ok=True)
    jobs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((source / "jobs").glob("*.yaml"))]
    pinned = {
        slug for job in jobs for r in job.get("rounds", []) for slug in r.get("pinned_coding_slugs", [])
    }
    for path in sorted((source / "coding").rglob("*.yaml")):
        if path.stem in pinned:
            target = out / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def build_tree(
    out: Path, *, questions: int = 10_000, problems: int = 1_000, seed: int = 7, full_base: bool = False
) -> Path:
    """Write a synthetic samples tree to ``out`` (created; must not exist or be empty)."""
    rng = random.Random(seed)
    vocab = _vocabulary(rng, 6000)
    source = default_samples_dir()
    out.mkdir(parents=True, exist_ok=True)
    _copy_base(source, out, full=full_base)
    competencies = _extend_competencies(out, rng, vocab, 20)
    roles = _write_roles(out, rng, vocab, competencies)
    _write_questions(out, rng, vocab, questions, competencies, roles)
    _write_problems(out, rng, vocab, problems)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    parser.add_argument("--problems", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--full-base", action="store_true", help="start from the whole current samples tree")
    args = parser.parse_args(argv)
    build_tree(
        args.out, questions=args.questions, problems=args.problems, seed=args.seed, full_base=args.full_base
    )
    print(f"wrote {args.questions} questions and {args.problems} problems below {args.out}")


if __name__ == "__main__":
    main()
