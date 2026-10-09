"""``python -m app.seed.validate``: scoping, findings, exit codes, caches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rapidfuzz import fuzz, utils

from app.seed.validate import main, validate

from .trees import edit_yaml, minimal_tree, read_yaml, source, write_yaml


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return minimal_tree(tmp_path / "samples")


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "cache"
    monkeypatch.setenv("SEED_VALIDATE_CACHE_DIR", str(directory))
    return directory


def run(tree: Path, *args: str) -> int:
    return main(["--samples-dir", str(tree), *args])


def _question(tree: Path, key: str) -> dict:
    data = read_yaml(tree / source(key))
    question = next(q for q in data["questions"] if q["external_key"] == key)
    return {**data.get("defaults", {}), **question}


def _new_questions(tree: Path, name: str, *questions: dict) -> Path:
    return write_yaml(tree / "question_bank" / "drafts" / name, {"version": 1, "questions": list(questions)})


def test_clean_tree_exits_zero(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tree, "--no-cache") == 0
    out = capsys.readouterr().out
    assert "0 errors, 0 warnings" in out and "code checks" in out


def test_single_file_mode_reports_the_file_against_the_whole_tree(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _question(tree, "devops.kubernetes.crashloop_backoff")
    draft = _new_questions(
        tree,
        "k8s.yaml",
        {
            **base,
            "external_key": "drafts.k8s.crashloop",
            "text": base["text"] + " Be specific about the order.",
        },
        {**base},  # duplicate key of a question in another file
    )
    assert run(tree, "--no-cache", str(draft)) == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "question_bank/drafts/k8s.yaml"
    assert "drafts.k8s.crashloop: near-duplicate question of devops.kubernetes.crashloop_backoff" in out
    assert "duplicate external_key 'devops.kubernetes.crashloop_backoff' (defined 2 times:" in out
    assert "in other files" in out  # the other side of each pair belongs to devops_sre.yaml
    assert source("devops.kubernetes.crashloop_backoff").as_posix() not in out.splitlines()[0]


def test_near_duplicate_thresholds(tree: Path) -> None:
    base = _question(tree, "devops.kubernetes.az_failure")
    words = base["text"].split()
    warning_text = None
    for cut in range(1, len(words)):
        candidate = " ".join(words[:-cut] + ["service", "mesh", "ingress", "quota"][: cut % 4 + 1]) + "?"
        score = fuzz.token_set_ratio(utils.default_process(candidate), utils.default_process(base["text"]))
        if 85 <= score < 92:
            warning_text = candidate
            break
    assert warning_text is not None
    draft = _new_questions(
        tree,
        "dupes.yaml",
        {**base, "external_key": "drafts.az.copy", "text": "Be precise. " + base["text"]},
        {
            **base,
            "external_key": "drafts.az.close",
            "competency_key": base["competency_key"],
            "text": warning_text,
        },
    )
    report = validate(tree, [str(draft)], run=False, use_cache=False)
    pairs = {
        (f.message.split(":")[0], f.message.split(" of ")[1].split(" ")[0], f.severity)
        for f in report.findings
        if "near-duplicate" in f.message
    }
    assert ("drafts.az.copy", "devops.kubernetes.az_failure", "error") in pairs
    assert ("drafts.az.close", "devops.kubernetes.az_failure", "warning") in pairs


def test_fairness_and_spoken_style_findings(tree: Path) -> None:
    base = _question(tree, "devops.kubernetes.crashloop_backoff")
    _new_questions(
        tree,
        "style.yaml",
        {
            **base,
            "external_key": "drafts.oncall.balance",
            "topic": "On-call balance",
            "text": "How do you balance on-call weeks with your children and **family commitments**",
            "rubric": {**base["rubric"], "good": "Answers confidently about the rotation."},
        },
    )
    report = validate(tree, ["question_bank/drafts/style.yaml"], run=False, use_cache=False)
    messages = [f.message for f in report.findings]
    assert any("fairness: text: mentions children" in m for m in messages)
    assert any("fairness: text: mentions marital or family status" in m for m in messages)
    assert any("fairness: rubric.good: mentions emotion or personality judgement" in m for m in messages)
    assert any("must end with '?' or '.'" in m for m in messages)
    assert any("no markdown" in m for m in messages)


def test_coding_findings_and_no_run(tree: Path) -> None:
    path = tree / source("two_sum")
    edit_yaml(path, lambda d: d["tests"][3].update({"expected": [1, 0, 5]}))
    edit_yaml(path, lambda d: d["hints"].__setitem__(1, "Use a dict: seen[x] = i;"))
    edit_yaml(path, lambda d: d.pop("starter_code"))
    report = validate(tree, [str(path)], use_cache=False)
    messages = [f.message for f in report.findings]
    assert "two_sum: hint 2 contains code; hints explain ideas in words" in messages
    assert any(
        m.startswith("two_sum: [python reference] t4: expected [1, 0, 5], got [0, 1]") for m in messages
    )
    assert report.counts["run_jobs"] >= 2
    skipped = validate(tree, [str(path)], run=False, use_cache=False)
    assert not any("[python reference]" in f.message for f in skipped.findings)
    assert "run_jobs" not in skipped.counts


def test_grouped_layout_titles_and_role_tags(tree: Path) -> None:
    original = tree / source("valid_parentheses")
    nested = tree / "coding" / "Stacks" / "deep" / "valid_parentheses.yaml"
    nested.parent.mkdir(parents=True)
    original.rename(nested)
    copy = read_yaml(nested)
    write_yaml(tree / "coding" / "stacks" / "balanced_brackets.yaml", {**copy, "slug": "balanced_brackets"})
    report = validate(tree, use_cache=False, run=False)
    messages = {(f.file, f.message) for f in report.findings}
    assert (
        "coding/Stacks/deep/valid_parentheses.yaml",
        "valid_parentheses: coding problems live in coding/<group>/<slug>.yaml (one group directory)",
    ) in messages
    assert any(m.startswith("balanced_brackets: same title as valid_parentheses") for _, m in messages)
    assert any("balanced_brackets: near-duplicate problem of valid_parentheses" in m for _, m in messages)


def test_usage_errors_exit_2(tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outside = write_yaml(tmp_path / "elsewhere.yaml", {"version": 1})
    stray = write_yaml(tree / "catalog" / "notes.yaml", {"version": 1})
    yml = tree / "coding" / "x.yml"
    yml.write_text("slug: x\n")
    for args in (["missing.yaml"], [str(outside)], [str(stray)], [str(yml)]):
        assert run(tree, "--no-cache", *args) == 2, args
    err = capsys.readouterr().err
    assert "path not found: missing.yaml" in err and "outside the samples tree" in err
    assert "is not a catalog file" in err and "use the .yaml extension" in err


def test_paths_relative_to_the_samples_root_and_directories(tree: Path) -> None:
    report = validate(tree, ["coding", "catalog/competencies.yaml"], run=False, use_cache=False)
    assert "catalog/competencies.yaml" in report.targets
    assert all(t.startswith("coding/") or t.startswith("catalog/") for t in report.targets)
    assert len([t for t in report.targets if t.startswith("coding/")]) == report.counts["coding"]


def test_json_output_and_strict_warnings(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    nested = tree / "coding" / "a" / "b"
    nested.mkdir(parents=True)
    path = tree / source("two_sum")
    path.rename(nested / "two_sum.yaml")
    assert run(tree, "--no-cache", "--no-run", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"] == 0 and payload["warnings"] == 1
    assert payload["findings"][0]["file"] == "coding/a/b/two_sum.yaml"
    assert run(tree, "--no-cache", "--no-run", "--strict") == 1


def test_caches_skip_unchanged_work_and_notice_edits(tree: Path) -> None:
    first = validate(tree)
    assert first.errors == 0 and first.counts["parsed_files"] == first.counts["files"]
    assert first.counts["run_cached"] == 0 and first.counts["run_jobs"] > 0
    second = validate(tree)
    assert second.counts["parsed_files"] == 0
    assert second.counts["run_jobs"] == 0 and second.counts["run_cached"] == second.counts["coding"]
    edit_yaml(tree / source("two_sum"), lambda d: d.update({"title": "Two Sum (pairs)"}))
    third = validate(tree)
    assert third.counts["parsed_files"] == 1
    assert third.counts["run_cached"] == second.counts["coding"] - 1 and third.counts["run_jobs"] >= 2


def test_stats(tree: Path) -> None:
    report = validate(tree, run=False, use_cache=False, stats=True)
    assert any(line.startswith("questions by difficulty:") for line in report.stats)
    assert any(line.startswith("coding problems:") for line in report.stats)
