"""Content rules beyond the schemas, shared by the validator CLI
(:mod:`app.seed.validate`) and the unit tests.

* coding structure: exactly three graduated, code-free hints; 2-3 visible and
  5-10 hidden tests, each with a staff-facing description; ``Example`` and
  ``### Constraints`` sections; code within the sandbox size limit;
* spoken questions: end with ``?``/``.``, no markdown, enough expected
  concepts, at least one common error, distinct rubric anchors;
* fairness: which text of each record is linted, and whether it is
  evaluator-facing (:mod:`app.seed.fairness`);
* near-duplicates: rapidfuzz ``token_set_ratio`` within a competency
  (>= 92 error, >= 85 warning);
* coverage statistics for ``--stats``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from rapidfuzz import fuzz, process, utils

from app.domain.enums import Difficulty, QuestionKind, RoundType
from app.seed.fairness import strip_code
from app.seed.loaders import (
    BankQuestionSeed,
    CodingQuestionSeed,
    CompetencySeed,
    JobSeed,
    RoleSeed,
    RoundTemplateSeed,
    SeedCatalog,
)

NEAR_DUPLICATE_ERROR = 92
NEAR_DUPLICATE_WARNING = 85
VISIBLE_TESTS = (2, 3)
HIDDEN_TESTS = (5, 10)
#: ``ExecuteRequest.code`` max length in the sandbox contract.
SANDBOX_CODE_LIMIT = 200_000
_CHUNK_ROWS = 2048

CODE_LIKE = re.compile(r"```|\n|\b(def|function|func)\s+\w+\s*\(|=>|==|!=|\+\+|:=|[{};]\s*$|\bfor\s*\(")
MARKDOWN_IN_SPEECH = re.compile(r"[*#`]|\n")
GROUP_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HEADING = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)

Field = tuple[str, str | None, bool]  # (field name, text, evaluator-facing)


# =============================================================================
# Structure and style
# =============================================================================


def coding_structure_problems(problem: CodingQuestionSeed) -> list[str]:
    """Structure rules every curated coding problem follows (errors)."""
    out: list[str] = []
    if len(problem.hints) != 3:
        out.append(f"needs exactly 3 hints (got {len(problem.hints)})")
    for index, hint in enumerate(problem.hints, start=1):
        if CODE_LIKE.search(hint):
            out.append(f"hint {index} contains code; hints explain ideas in words")
    if len(problem.hints) == 3 and len(problem.hints[2]) < len(problem.hints[0]):
        out.append("hints must be graduated: the third hint is the most detailed")
    visible, hidden = len(problem.visible_tests), len(problem.hidden_tests)
    if not VISIBLE_TESTS[0] <= visible <= VISIBLE_TESTS[1]:
        out.append(f"needs {VISIBLE_TESTS[0]}-{VISIBLE_TESTS[1]} visible tests (got {visible})")
    if not HIDDEN_TESTS[0] <= hidden <= HIDDEN_TESTS[1]:
        out.append(f"needs {HIDDEN_TESTS[0]}-{HIDDEN_TESTS[1]} hidden tests (got {hidden})")
    undescribed = [t.id for t in problem.tests if not (t.description or "").strip()]
    if undescribed:
        out.append(f"tests {undescribed} need a staff-facing description")
    if "Example" not in problem.prompt_markdown:
        out.append("prompt_markdown needs an Example section")
    if not problem.is_sql and "### Constraints" not in problem.prompt_markdown:
        out.append("prompt_markdown needs a '### Constraints' section")
    for label, codes in (
        ("starter_code", problem.starter_code),
        ("reference_solutions", problem.reference_solutions),
    ):
        for language, code in codes.items():
            if len(code) > SANDBOX_CODE_LIMIT:
                out.append(
                    f"{label}.{language.value} exceeds the sandbox limit of {SANDBOX_CODE_LIMIT} characters"
                )
    return out


def question_style_problems(question: BankQuestionSeed) -> list[str]:
    """Spoken-question rules (errors)."""
    out: list[str] = []
    text = question.text.strip()
    if text[-1:] not in ("?", "."):
        out.append("text must end with '?' or '.' (it is spoken)")
    if MARKDOWN_IN_SPEECH.search(question.text):
        out.append("text is spoken: no markdown (*, #, `) and no line breaks")
    if len(question.expected_concepts) < 3 and question.kind not in (
        QuestionKind.RECRUITER,
        QuestionKind.INTRO,
    ):
        out.append("needs at least 3 expected_concepts")
    if not question.common_errors:
        out.append("needs at least one common_errors entry")
    if question.rubric.excellent.strip() == question.rubric.weak.strip():
        out.append("rubric.excellent and rubric.weak must differ")
    return out


def coding_location_warnings(rel_path: str) -> list[str]:
    """``coding/<group>/<slug>.yaml`` layout hints (warnings)."""
    parts = rel_path.split("/")
    out: list[str] = []
    if len(parts) > 3:
        out.append("coding problems live in coding/<group>/<slug>.yaml (one group directory)")
    if len(parts) >= 3 and not GROUP_NAME.match(parts[1]):
        out.append(f"group directory {parts[1]!r} should be snake_case")
    return out


# =============================================================================
# Fairness: which text is linted
# =============================================================================


def question_fields(question: BankQuestionSeed) -> list[Field]:
    rubric = question.rubric
    fields: list[Field] = [("text", question.text, False), ("topic", question.topic, False)]
    fields += [(f"followups[{i}]", text, False) for i, text in enumerate(question.followups)]
    fields += [(f"expected_concepts[{i}]", text, True) for i, text in enumerate(question.expected_concepts)]
    fields += [(f"common_errors[{i}]", text, True) for i, text in enumerate(question.common_errors)]
    fields += [("rubric.excellent", rubric.excellent, True), ("rubric.good", rubric.good, True)]
    fields += [("rubric.weak", rubric.weak, True)]
    fields += [(f"rubric.dimensions.{k}", v, True) for k, v in (rubric.dimensions or {}).items()]
    return fields


def coding_fields(problem: CodingQuestionSeed) -> list[Field]:
    rubric = problem.rubric
    fields: list[Field] = [
        ("title", problem.title, False),
        ("prompt_markdown", strip_code(problem.prompt_markdown), False),
    ]
    fields += [(f"hints[{i}]", text, False) for i, text in enumerate(problem.hints)]
    fields += [(f"followups[{i}]", text, False) for i, text in enumerate(problem.followups)]
    fields += [(f"tests.{t.id}.description", t.description, False) for t in problem.tests]
    fields += [(f"expected_concepts[{i}]", text, True) for i, text in enumerate(problem.expected_concepts)]
    fields += [(f"common_errors[{i}]", text, True) for i, text in enumerate(problem.common_errors)]
    fields += [("rubric.excellent", rubric.excellent, True), ("rubric.good", rubric.good, True)]
    fields += [("rubric.weak", rubric.weak, True)]
    fields += [(f"rubric.criteria.{k}", v, True) for k, v in rubric.criteria.items()]
    return fields


def competency_fields(competency: CompetencySeed) -> list[Field]:
    fields: list[Field] = [("name", competency.name, False), ("description", competency.description, True)]
    fields += [(f"rubric.levels.{k}", v, True) for k, v in competency.rubric.levels.items()]
    return fields


def role_fields(role: RoleSeed) -> list[Field]:
    return [("title", role.title, False), ("description", role.description, False)]


def job_fields(job: JobSeed) -> list[Field]:
    fields: list[Field] = [("title", job.title, False), ("description", job.description, False)]
    fields.append(("interview_instructions", job.interview_instructions, False))
    fields += [
        (f"requirements[{i}]", f"{r.name}. {r.description or ''}", False)
        for i, r in enumerate(job.requirements)
    ]
    return fields


def template_fields(template: RoundTemplateSeed) -> list[Field]:
    # Candidate-facing instructions only: interviewer notes deliberately list
    # the prohibited topics ("never ask about age, family, ...").
    return [("config.instructions", template.config.instructions, False)]


# =============================================================================
# Near-duplicates
# =============================================================================


@dataclass(frozen=True, slots=True)
class DuplicatePair:
    group: str
    a: str
    b: str
    score: int

    @property
    def is_error(self) -> bool:
        return self.score >= NEAR_DUPLICATE_ERROR


def problem_statement(problem: CodingQuestionSeed) -> str:
    """Title plus the statement part of the prompt (before the first ``###``
    section; code, headings and examples removed) used for near-duplicates."""
    body = strip_code(problem.prompt_markdown)
    head = re.split(r"^###\s", body, maxsplit=1, flags=re.MULTILINE)[0]
    return f"{problem.title}. {_HEADING.sub(' ', head)}"


def near_duplicate_pairs(
    groups: Mapping[str, Sequence[tuple[str, str]]],
    *,
    targets: Iterable[str] | None = None,
    cutoff: int = NEAR_DUPLICATE_WARNING,
) -> list[DuplicatePair]:
    """Pairs of items inside the same group whose texts score
    ``token_set_ratio >= cutoff``. ``groups`` maps a group (competency) to
    ``(id, text)`` items. With ``targets`` only pairs involving a target id
    are computed (single-file mode). Each unordered pair is reported once."""
    wanted = set(targets) if targets is not None else None
    seen: set[tuple[str, str]] = set()
    pairs: list[DuplicatePair] = []
    for group, items in groups.items():
        if len(items) < 2:
            continue
        ids = [item_id for item_id, _ in items]
        texts = [utils.default_process(text) for _, text in items]
        rows = (
            list(range(len(ids)))
            if wanted is None
            else [i for i, item_id in enumerate(ids) if item_id in wanted]
        )
        for start in range(0, len(rows), _CHUNK_ROWS):
            chunk = rows[start : start + _CHUNK_ROWS]
            matrix = process.cdist(
                [texts[i] for i in chunk],
                texts,
                scorer=fuzz.token_set_ratio,
                processor=None,
                score_cutoff=cutoff,
                dtype=np.uint8,
                workers=-1,
            )
            for r, c in zip(*np.nonzero(matrix), strict=True):
                i, j = chunk[int(r)], int(c)
                if i == j or ids[i] == ids[j]:
                    continue
                key = (ids[i], ids[j]) if ids[i] < ids[j] else (ids[j], ids[i])
                if key in seen:
                    continue
                seen.add(key)
                pairs.append(DuplicatePair(group, key[0], key[1], int(matrix[r, c])))
    pairs.sort(key=lambda p: (-p.score, p.group, p.a, p.b))
    return pairs


def exact_duplicates(items: Iterable[tuple[str, str]]) -> list[list[str]]:
    """Groups of ids whose normalised texts are identical."""
    by_text: dict[str, list[str]] = defaultdict(list)
    for item_id, text in items:
        by_text[utils.default_process(text)].append(item_id)
    return [ids for ids in by_text.values() if len(set(ids)) > 1]


# =============================================================================
# Coverage statistics
# =============================================================================


def coverage_lines(catalog: SeedCatalog) -> list[str]:
    """Human-readable coverage of the catalog (``--stats``)."""
    lines: list[str] = []
    questions = catalog.questions
    total = len(questions) or 1
    by_difficulty = Counter(q.difficulty for q in questions)
    lines.append(
        "questions by difficulty: "
        + ", ".join(f"{d.value} {by_difficulty[d]} ({by_difficulty[d] / total:.0%})" for d in Difficulty)
    )
    by_kind = Counter(q.kind.value for q in questions)
    lines.append("questions by kind: " + ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items())))
    by_round: Counter[str] = Counter(r.value for q in questions for r in q.round_types)
    lines.append(
        "questions by round type: " + ", ".join(f"{r.value} {by_round.get(r.value, 0)}" for r in RoundType)
    )
    per_competency = Counter(q.competency_key for q in questions)
    empty = sorted(c.key for c in catalog.competencies if per_competency[c.key] == 0)
    lines.append(
        f"competencies: {len(catalog.competencies)}, with questions {len(catalog.competencies) - len(empty)}"
        + (f"; none for {empty}" if empty else "")
    )
    if catalog.roles:
        tagged: dict[str, Counter[str]] = defaultdict(Counter)
        for q in questions:
            for tag in q.role_tags:
                for r in q.round_types:
                    tagged[tag][r.value] += 1
        lines.append(f"roles: {len(catalog.roles)} (questions per declared round type)")
        for role in catalog.roles:
            cells = [f"{r.value} {tagged[role.key][r.value]}" for r in role.round_types]
            gaps = [r.value for r in role.round_types if tagged[role.key][r.value] == 0]
            lines.append(
                f"  {role.key}: " + ", ".join(cells) + (f"  <- no questions for {gaps}" if gaps else "")
            )
    coding = catalog.coding_questions
    sql = sum(1 for p in coding if p.is_sql)
    lines.append(
        f"coding problems: {len(coding)} ({len(coding) - sql} function, {sql} SQL); by difficulty: "
        + ", ".join(f"{d.value} {sum(1 for p in coding if p.difficulty == d)}" for d in Difficulty)
    )
    generated = sum(1 for p in coding if p.generated_starter_languages)
    lines.append(f"coding problems with generated starters: {generated}")
    return lines


__all__ = [
    "CODE_LIKE",
    "HIDDEN_TESTS",
    "NEAR_DUPLICATE_ERROR",
    "NEAR_DUPLICATE_WARNING",
    "SANDBOX_CODE_LIMIT",
    "VISIBLE_TESTS",
    "DuplicatePair",
    "coding_fields",
    "coding_location_warnings",
    "coding_structure_problems",
    "competency_fields",
    "coverage_lines",
    "exact_duplicates",
    "job_fields",
    "near_duplicate_pairs",
    "problem_statement",
    "question_fields",
    "question_style_problems",
    "role_fields",
    "template_fields",
]
