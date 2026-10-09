"""Loader behaviour: error aggregation, merging, typing helpers, the grouped
layout (coding/<group>/<slug>.yaml, question_bank/**), generated starters,
roles.yaml and the in-process catalog cache."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from app.domain.enums import CodeLanguage, RoundType
from app.seed.loaders import (
    FUNCTION_LANGUAGES,
    KIND_CODING,
    KIND_COMPETENCIES,
    KIND_JOBS,
    KIND_QUESTION_BANK,
    KIND_ROLES,
    KIND_ROUND_TEMPLATES,
    JobRoundSeed,
    JobSeed,
    SeedCatalog,
    SeedDataNotFound,
    SeedValidationError,
    deep_merge,
    default_samples_dir,
    entry_point_name,
    file_kind,
    iter_yaml_files,
    load_catalog,
    load_coding_questions,
    load_question_bank,
    resolve_round,
    to_lower_camel,
    value_matches_type,
)
from app.seed.starters import starters_for

from .trees import edit_yaml, minimal_tree, read_yaml, source, write_yaml


@pytest.fixture
def samples_copy(tmp_path: Path) -> Path:
    """A small writable, valid samples tree (see trees.minimal_tree)."""
    return minimal_tree(tmp_path / "samples")


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> SeedCatalog:
    """Templates and jobs from a minimal tree (independent of content being authored)."""
    return load_catalog(minimal_tree(tmp_path_factory.mktemp("base") / "samples"))


def _problems(root: Path) -> str:
    with pytest.raises(SeedValidationError) as info:
        load_catalog(root, cache=False)
    return "\n".join(info.value.problems)


# ----------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("name", "camel"),
    [("two_sum", "twoSum"), ("top_k_frequent", "topKFrequent"), ("can_finish", "canFinish"), ("x", "x")],
)
def test_to_lower_camel(name: str, camel: str) -> None:
    assert to_lower_camel(name) == camel


def test_entry_point_name_per_language() -> None:
    assert (
        entry_point_name("length_of_longest_substring", CodeLanguage.PYTHON) == "length_of_longest_substring"
    )
    for language in (
        CodeLanguage.JAVASCRIPT,
        CodeLanguage.TYPESCRIPT,
        CodeLanguage.JAVA,
        CodeLanguage.GO,
        CodeLanguage.CPP,
    ):
        assert entry_point_name("length_of_longest_substring", language) == "lengthOfLongestSubstring"


@pytest.mark.parametrize(
    ("value", "type_name", "ok"),
    [
        (5, "int", True),
        (True, "int", False),
        (2**31, "int", False),
        (2**31, "long", True),
        (2**53, "long", False),
        (1, "float", True),
        (float("inf"), "float", False),
        ("x", "string", True),
        ([[1, 2], []], "int[][]", True),
        ([[1, "2"]], "int[][]", False),
        ([["a"]], "string[][]", True),
        ([True, False], "bool[]", True),
        ([1, 0], "bool[]", False),
        ([], "long[]", True),
    ],
)
def test_value_matches_type(value: object, type_name: str, ok: bool) -> None:
    assert value_matches_type(value, type_name) is ok


def test_deep_merge_merges_mappings_and_replaces_lists() -> None:
    base: dict[str, Any] = {
        "a": 1,
        "coding": {"enabled": False, "languages": ["python", "go"]},
        "rubric": [{"key": "x"}],
    }
    merged = deep_merge(base, {"coding": {"enabled": True, "languages": ["python"]}, "rubric": []})
    assert merged == {"a": 1, "coding": {"enabled": True, "languages": ["python"]}, "rubric": []}
    assert base["coding"]["enabled"] is False  # input not mutated


def test_resolve_round_applies_overrides(catalog: SeedCatalog) -> None:
    seed = JobRoundSeed(
        template=RoundType.COMBINED,
        name="Short Combined",
        overrides={"duration_minutes": 25, "coding": {"duration_minutes": 8}},
    )
    resolved = resolve_round(seed, catalog.templates_by_type(), sequence=2)
    assert resolved.config.name == "Short Combined" and resolved.sequence == 2
    assert resolved.config.duration_minutes == 25
    assert resolved.config.coding.duration_minutes == 8 and resolved.config.coding.enabled
    row = resolved.to_row(pinned_coding_ids=["11111111-1111-1111-1111-111111111111"])
    assert row["config"]["coding"]["question_ids"] == ["11111111-1111-1111-1111-111111111111"]
    assert row["duration_minutes"] == 25 and row["round_type"] == "combined"


def test_round_identity_cannot_be_overridden() -> None:
    with pytest.raises(ValueError, match="round itself"):
        JobRoundSeed(template=RoundType.CODING, name="x", overrides={"round_type": "behavioral"})


def test_selectable_rounds_require_the_job_flag(catalog: SeedCatalog) -> None:
    job = next(j for j in catalog.jobs if j.allow_candidate_round_selection)
    data = job.model_dump(mode="json")
    data["allow_candidate_round_selection"] = False
    with pytest.raises(ValueError, match="allow_candidate_round_selection"):
        JobSeed.model_validate(data)


# ------------------------------------------------------- directory handling


def test_samples_dir_env_override(monkeypatch: pytest.MonkeyPatch, samples_copy: Path) -> None:
    monkeypatch.setenv("SAMPLES_DIR", str(samples_copy))
    assert default_samples_dir() == samples_copy.resolve()
    monkeypatch.setenv("SAMPLES_DIR", str(samples_copy / "missing"))
    with pytest.raises(SeedDataNotFound):
        default_samples_dir()


def test_copy_of_samples_loads(samples_copy: Path) -> None:
    catalog = load_catalog(samples_copy)
    assert catalog.root == samples_copy.resolve()
    assert catalog.sources["two_sum"].name == "two_sum.yaml"
    assert catalog.locations[("coding", "two_sum")] == [catalog.sources["two_sum"]]


def test_file_kinds(samples_copy: Path) -> None:
    root = samples_copy.resolve()
    assert file_kind(root / "catalog/competencies.yaml", root) == KIND_COMPETENCIES
    assert file_kind(root / "catalog/round_templates.yaml", root) == KIND_ROUND_TEMPLATES
    assert file_kind(root / "catalog/roles.yaml", root) == KIND_ROLES
    assert file_kind(root / "question_bank/a/b/c.yaml", root) == KIND_QUESTION_BANK
    assert file_kind(root / "coding/graphs/x.yaml", root) == KIND_CODING
    assert file_kind(root / "jobs/x.yaml", root) == KIND_JOBS
    assert file_kind(root / "coding/x.yml", root) is None
    assert file_kind(root / "catalog/other.yaml", root) is None
    assert file_kind(root / "question_bank.yaml", root) is None


def test_dot_files_are_skipped_and_yml_files_reported(samples_copy: Path) -> None:
    write_yaml(samples_copy / "coding" / ".draft.yaml", {"broken": True})
    write_yaml(samples_copy / "question_bank" / ".hidden" / "x.yaml", {"broken": True})
    problems: list[str] = []
    files = iter_yaml_files(samples_copy / "coding", samples_copy, problems)
    assert all(not p.name.startswith(".") for p in files) and problems == []
    load_catalog(samples_copy, cache=False)  # hidden files are not loaded
    write_yaml(samples_copy / "coding" / "graphs" / "draft.yml", {"slug": "draft"})
    assert "coding/graphs/draft.yml: not loaded: rename it to *.yaml" in _problems(samples_copy)


# ------------------------------------------------------------- grouped layout


def test_coding_problems_load_from_group_directories(samples_copy: Path) -> None:
    old = samples_copy / source("two_sum")
    new = samples_copy / "coding" / "arrays_hashing" / "two_sum.yaml"
    new.parent.mkdir(exist_ok=True)
    old.rename(new)
    catalog = load_catalog(samples_copy, cache=False)
    assert catalog.sources["two_sum"] == new.resolve()
    assert "two_sum" in catalog.coding_by_slug()


def test_slugs_are_unique_across_the_tree(samples_copy: Path) -> None:
    original = samples_copy / source("two_sum")
    duplicate = samples_copy / "coding" / "hashing" / "two_sum.yaml"
    duplicate.parent.mkdir()
    shutil.copy2(original, duplicate)
    messages = _problems(samples_copy)
    assert "duplicate slug 'two_sum' (defined 2 times:" in messages
    assert "coding/hashing/two_sum.yaml" in messages and source("two_sum").as_posix() in messages


def test_question_bank_files_load_at_any_depth_with_unique_keys(samples_copy: Path) -> None:
    deep = samples_copy / "question_bank" / "platform" / "kubernetes" / "extra.yaml"
    question = next(
        q
        for q in read_yaml(samples_copy / source("devops.kubernetes.az_failure"))["questions"]
        if q["external_key"] == "devops.kubernetes.az_failure"
    )
    write_yaml(
        deep,
        {
            "version": 1,
            "defaults": {"role_tags": ["devops"], "round_types": ["technical_screening"]},
            "questions": [
                {
                    **question,
                    "external_key": "platform.kubernetes.az_drill",
                    "text": question["text"] + " Be concrete.",
                }
            ],
        },
    )
    keys = {q.external_key for q in load_question_bank(samples_copy)}
    assert "platform.kubernetes.az_drill" in keys
    write_yaml(
        deep.with_name("again.yaml"),
        {
            "version": 1,
            "defaults": {"role_tags": ["devops"], "round_types": ["deep_technical"]},
            "questions": [question],
        },
    )
    messages = _problems(samples_copy)
    assert "duplicate external_key 'devops.kubernetes.az_failure' (defined 2 times:" in messages
    assert "question_bank/platform/kubernetes/again.yaml" in messages


def test_unknown_top_level_keys_are_reported(samples_copy: Path) -> None:
    path = samples_copy / source("devops.kubernetes.crashloop_backoff")
    edit_yaml(path, lambda d: d.update({"default": d.pop("defaults")}))
    messages = _problems(samples_copy)
    assert "unknown top-level keys ['default']" in messages


# ------------------------------------------------------------ coding schema


def test_missing_starters_are_generated_from_the_signature(samples_copy: Path) -> None:
    path = samples_copy / source("two_sum")
    edit_yaml(path, lambda d: d.pop("starter_code"))
    problem = next(p for p in load_coding_questions(samples_copy) if p.slug == "two_sum")
    assert problem.generated_starter_languages == frozenset(FUNCTION_LANGUAGES)
    assert problem.explicit_starter_code == {}
    signature = problem.signature.model_dump(mode="json", exclude_none=True)
    expected = starters_for(signature, description=problem.title)
    assert {lang.value: code for lang, code in problem.starter_code.items()} == expected
    assert set(problem.to_row()["starter_code"]) == {lang.value for lang in FUNCTION_LANGUAGES}


def test_explicit_starters_win_and_are_validated(samples_copy: Path) -> None:
    path = samples_copy / source("two_sum")
    edit_yaml(path, lambda d: d.update({"starter_code": {"python": d["starter_code"]["python"]}}))
    problem = next(p for p in load_coding_questions(samples_copy) if p.slug == "two_sum")
    assert CodeLanguage.PYTHON not in problem.generated_starter_languages
    assert problem.starter_code[CodeLanguage.PYTHON] == read_yaml(path)["starter_code"]["python"]
    assert len(problem.generated_starter_languages) == 5
    edit_yaml(path, lambda d: d.update({"starter_code": {"go": "package main\n\nfunc other() {}\n"}}))
    with pytest.raises(SeedValidationError, match="starter_code.go does not define entry point 'twoSum'"):
        load_coding_questions(samples_copy)


def test_reference_solutions_need_python_and_javascript_only(samples_copy: Path) -> None:
    path = samples_copy / source("two_sum")
    edit_yaml(
        path,
        lambda d: d.update(
            {"reference_solutions": {k: d["reference_solutions"][k] for k in ("python", "javascript")}}
        ),
    )
    problem = next(p for p in load_coding_questions(samples_copy) if p.slug == "two_sum")
    assert set(problem.reference_solutions) == {CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT}
    edit_yaml(path, lambda d: d["reference_solutions"].pop("javascript"))
    with pytest.raises(SeedValidationError, match=r"reference_solutions missing \['javascript'\]"):
        load_coding_questions(samples_copy)


def test_sql_problems_still_need_a_sql_starter(samples_copy: Path) -> None:
    edit_yaml(samples_copy / source("second_highest_salary"), lambda d: d.pop("starter_code"))
    with pytest.raises(SeedValidationError, match="starter_code must contain exactly the 'sql' entry"):
        load_coding_questions(samples_copy)


# ------------------------------------------------------------------- roles


def _roles(*roles: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "roles": list(roles)}


ROLE = {
    "key": "mobile_engineer",
    "title": "Mobile Engineer",
    "family": "engineering",
    "description": "Builds and ships native and cross-platform mobile applications.",
    "competency_keys": ["javascript", "testing"],
    "round_types": ["technical_screening", "behavioral"],
}


def test_role_tags_accept_role_keys_and_legacy_tags(samples_copy: Path) -> None:
    write_yaml(samples_copy / "catalog" / "roles.yaml", _roles(ROLE))
    path = samples_copy / source("devops.kubernetes.crashloop_backoff")
    edit_yaml(path, lambda d: d["questions"][0].update({"role_tags": ["mobile_engineer", "devops"]}))
    catalog = load_catalog(samples_copy, cache=False)
    assert [r.key for r in catalog.roles] == ["mobile_engineer"]
    assert "mobile_engineer" in catalog.allowed_role_tags() and "devops" in catalog.allowed_role_tags()
    edit_yaml(path, lambda d: d["questions"][0].update({"role_tags": ["astronaut"]}))
    assert "unknown role tags ['astronaut'] (not a key of catalog/roles.yaml or a legacy tag)" in _problems(
        samples_copy
    )


def test_without_roles_file_only_legacy_tags_are_allowed(samples_copy: Path) -> None:
    (samples_copy / "catalog" / "roles.yaml").unlink(missing_ok=True)
    edit_yaml(samples_copy / source("two_sum"), lambda d: d.update({"role_tags": ["mobile_engineer"]}))
    assert "unknown role tags ['mobile_engineer'] (not a legacy tag)" in _problems(samples_copy)


def test_roles_file_is_validated(samples_copy: Path) -> None:
    bad = {**ROLE, "competency_keys": ["javascript", "quantum_computing"]}
    write_yaml(samples_copy / "catalog" / "roles.yaml", _roles(bad, {**ROLE, "round_types": ["warp_drive"]}))
    messages = _problems(samples_copy)
    assert "role 'mobile_engineer': unknown competency keys ['quantum_computing']" in messages
    assert "duplicate role keys" not in messages  # the invalid second record is reported, not double counted
    assert "round_types.0" in messages


# ------------------------------------------------------------------ cache


def test_load_catalog_cache_returns_copies_and_sees_edits(samples_copy: Path) -> None:
    first = load_catalog(samples_copy)
    second = load_catalog(samples_copy)
    assert first.questions == second.questions and first.questions is not second.questions
    first.questions.clear()
    assert load_catalog(samples_copy).questions == second.questions
    path = samples_copy / source("devops.kubernetes.crashloop_backoff")
    edit_yaml(path, lambda d: d["questions"][0].update({"topic": "Crash loops"}))
    reloaded = load_catalog(samples_copy)
    assert (
        next(q for q in reloaded.questions if q.external_key == "devops.kubernetes.crashloop_backoff").topic
        == "Crash loops"
    )


# ---------------------------------------------------- validation errors


def test_errors_are_aggregated_with_file_and_record(samples_copy: Path) -> None:
    two_sum = source("two_sum").as_posix()
    question_file = source("devops.kubernetes.crashloop_backoff").as_posix()
    edit_yaml(samples_copy / two_sum, lambda d: d["hints"].pop())
    edit_yaml(
        samples_copy / question_file,
        lambda d: next(
            q for q in d["questions"] if q["external_key"] == "devops.kubernetes.crashloop_backoff"
        ).update({"competency_key": "not_a_competency", "difficulty": "trivial"}),
    )
    edit_yaml(
        samples_copy / "catalog" / "round_templates.yaml",
        lambda d: d["templates"][2]["config"]["rubric"][0].update({"weight": 31}),
    )
    with pytest.raises(SeedValidationError) as info:
        load_catalog(samples_copy, cache=False)
    messages = "\n".join(info.value.problems)
    assert f"{two_sum}: slug='two_sum': hints" in messages
    assert f"{question_file}: external_key='devops.kubernetes.crashloop_backoff': difficulty" in messages
    assert "rubric weights must sum to 100" in messages
    assert len(info.value.problems) >= 3


def test_unknown_competency_reference_is_reported(samples_copy: Path) -> None:
    edit_yaml(
        samples_copy / source("devops.kubernetes.crashloop_backoff"),
        lambda d: next(
            q for q in d["questions"] if q["external_key"] == "devops.kubernetes.crashloop_backoff"
        ).update({"competency_key": "not_a_competency"}),
    )
    with pytest.raises(SeedValidationError, match="unknown competency 'not_a_competency'"):
        load_catalog(samples_copy, cache=False)


def test_test_case_types_are_checked_against_the_signature(samples_copy: Path) -> None:
    edit_yaml(samples_copy / source("two_sum"), lambda d: d["tests"][0].update({"args": [[1, 2], "9"]}))
    with pytest.raises(SeedValidationError, match="argument 'target' is not a valid int"):
        load_coding_questions(samples_copy)


def test_file_name_must_match_slug(samples_copy: Path) -> None:
    path = samples_copy / source("two_sum")
    path.rename(path.with_name("pair_sum.yaml"))
    with pytest.raises(SeedValidationError, match="file name must match slug"):
        load_coding_questions(samples_copy)


def test_invalid_yaml_and_version_are_reported(samples_copy: Path) -> None:
    (samples_copy / "jobs" / "broken.yaml").write_text("key: [unclosed\n")
    edit_yaml(samples_copy / "catalog" / "competencies.yaml", lambda d: d.update({"version": 2}))
    messages = _problems(samples_copy)
    assert "jobs/broken.yaml: invalid YAML" in messages
    assert "unsupported version 2" in messages


def test_pinned_coding_slug_must_exist(samples_copy: Path) -> None:
    edit_yaml(
        samples_copy / "jobs" / "senior_devops_engineer.yaml",
        lambda d: d["rounds"][0].update({"pinned_coding_slugs": ["does_not_exist"]}),
    )
    with pytest.raises(SeedValidationError, match="pinned coding slug 'does_not_exist' not found"):
        load_catalog(samples_copy, cache=False)


def test_template_coverage_is_enforced(samples_copy: Path) -> None:
    edit_yaml(
        samples_copy / "catalog" / "round_templates.yaml",
        lambda d: d.update({"templates": [t for t in d["templates"] if t["round_type"] != "custom"]}),
    )
    with pytest.raises(SeedValidationError, match=r"no default template for \['custom'\]"):
        load_catalog(samples_copy, cache=False)
