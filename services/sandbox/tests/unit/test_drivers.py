"""Job planning and result interpretation, using synthetic runner reports."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sandbox_service.config import Settings
from sandbox_service.drivers import ContainerOutcome, build_drivers
from sandbox_service.drivers.base import strip_comments_c_like
from sandbox_service.drivers.cpp import CXX_FLAGS, generate_glue
from sandbox_service.drivers.go import ensure_package_main, rename_main
from sandbox_service.drivers.java import JAVAC_JVM_FLAGS, public_type_name
from sandbox_service.drivers.node import NodeDriver
from sandbox_service.models import CodeLanguage, ExecuteRequest, RunStatus
from sandbox_service.signature import parse_signature

RUNTIMES = Path(__file__).resolve().parents[2] / "runtimes"
SETTINGS = Settings(shared_secret="unit-test-secret-0123456789abcdef")
DRIVERS = build_drivers(SETTINGS)
SIG = {
    "function_name": "two_sum",
    "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
    "return_type": "int[]",
}
TESTS = [
    {"id": "v1", "args": [[2, 7], 9], "expected": [0, 1]},
    {"id": "v2", "args": [[3, 3], 6], "expected": [0, 1]},
    {"id": "h1", "args": [[1, 2], 3], "expected": [0, 1], "hidden": True},
]
SENTINEL = "sentinel-hidden-expected-7731"
SENTINEL_TESTS = [{**t, "expected": [SENTINEL, i]} for i, t in enumerate(TESTS)]


def request(language: str, mode: str = "tests", **extra) -> ExecuteRequest:
    payload = {"request_id": "r1", "language": language, "code": "code", "mode": mode, **extra}
    if mode == "tests":
        payload.setdefault("signature", SIG)
        payload.setdefault("tests", TESTS)
    return ExecuteRequest.model_validate(payload)


def outcome(steps: list[dict], **extra) -> ContainerOutcome:
    return ContainerOutcome(
        runner={"protocol": 1, "ok": True, "steps": steps, "runner_ms": 50.0},
        image="img",
        container="abc",
        timings_ms={"create": 50.0},
        **extra,
    )


def harness_step(name: str, tests: list[dict], **extra) -> dict:
    return {
        "name": name,
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "wall_ms": 10.0,
        "max_rss_kb": 1000,
        "tests": tests,
        "fatal": None,
        **extra,
    }


# --------------------------------------------------------------------- planning


@pytest.mark.parametrize("language", [lang.value for lang in CodeLanguage if lang != CodeLanguage.SQL])
def test_plan_splits_visible_and_hidden_tests(language):
    driver = DRIVERS[CodeLanguage(language)]
    plan = driver.plan(request(language, tests=SENTINEL_TESTS))
    names = [s["name"] for s in plan.job["steps"]]
    assert names[-2:] == ["tests:visible", "tests:hidden"]
    visible, hidden = plan.job["steps"][-2:]
    assert visible["capture"] is True and hidden["capture"] is False
    assert [t["i"] for t in visible["harness"]["tests"]] == [0, 1]
    assert [t["i"] for t in hidden["harness"]["tests"]] == [2]
    # Expected values never leave the service.
    assert SENTINEL not in repr(plan.job)
    assert plan.test_groups == [("tests:visible", [0, 1]), ("tests:hidden", [2])]
    assert plan.container_memory_mb >= 256 + driver.baseline_mb
    assert plan.limits["network"] == "none"
    assert plan.job["deadline_ms"] < plan.hard_deadline_s * 1000


def test_plan_memory_and_deadline():
    java = DRIVERS[CodeLanguage.JAVA].plan(request("java", memory_limit_mb=64))
    assert java.container_memory_mb == 384 + SETTINGS.runtime_overhead_mb  # compile floor dominates
    assert java.run_memory_mb == 64 + 96
    python = DRIVERS[CodeLanguage.PYTHON].plan(request("python", mode="run", memory_limit_mb=512, time_limit_ms=2000))
    assert python.container_memory_mb == 512 + 32 + SETTINGS.runtime_overhead_mb
    assert python.hard_deadline_s == pytest.approx(15 + 2 + SETTINGS.deadline_grace_seconds)


def test_python_tests_mode_skips_separate_syntax_check():
    plan = DRIVERS[CodeLanguage.PYTHON].plan(request("python"))
    assert plan.job["steps"][0]["name"] == "tests:visible"
    run = DRIVERS[CodeLanguage.PYTHON].plan(request("python", mode="run", stdin="1 2"))
    assert [s["name"] for s in run.job["steps"]] == ["compile", "run"]
    assert run.job["steps"][1]["stdin"] == "1 2"


def test_sql_plan_uses_setup_and_requires_expected_rows():
    req = ExecuteRequest.model_validate(
        {
            "request_id": "r",
            "language": "sql",
            "code": "SELECT 1",
            "mode": "tests",
            "sql_setup": "CREATE TABLE t(x);",
            "tests": [{"id": "a", "setup_sql": "INSERT INTO t VALUES (1);", "expected_rows": [[1]]}],
        }
    )
    plan = DRIVERS[CodeLanguage.SQL].plan(req)
    step = plan.job["steps"][0]
    assert step["harness"]["input"]["setup"] == "CREATE TABLE t(x);"
    assert step["harness"]["tests"] == [{"i": 0, "setup_sql": "INSERT INTO t VALUES (1);"}]
    bad = req.model_copy(update={"tests": [req.tests[0].model_copy(update={"expected_rows": None})]})
    with pytest.raises(Exception, match="expected_rows"):
        DRIVERS[CodeLanguage.SQL].plan(bad)


def test_rejections_for_bad_requests():
    from sandbox_service.drivers import Rejected

    with pytest.raises(Rejected):
        DRIVERS[CodeLanguage.PYTHON].plan(request("python", signature={"function_name": "f"}))
    with pytest.raises(Rejected):
        DRIVERS[CodeLanguage.PYTHON].plan(request("python", tests=[]))
    with pytest.raises(Rejected, match="arguments"):
        DRIVERS[CodeLanguage.PYTHON].plan(request("python", tests=[{"id": "x", "args": [1], "expected": 1}]))


def test_go_files_and_entry_detection():
    driver = DRIVERS[CodeLanguage.GO]
    code = "package solution\n\n// func main() in a comment\nfunc TwoSum(nums []int, target int) []int { return nil }\nfunc main() {}\n"
    files, copies = driver.files(request("go", code=code), parse_signature(SIG))
    by_path = {f["path"]: f["content"] for f in files}
    assert by_path["solution.go"].startswith("package main")
    assert "func sbxCandidateMain()" in by_path["solution.go"] and "// func main() in a comment" in by_path["solution.go"]
    assert "sbxharness.Run(TwoSum)" in by_path["entry.go"]
    assert {"src": "/opt/sandbox/go/sbxharness/harness.go", "dst": "sbxharness/harness.go"} in copies
    assert "SetMaxStack" in by_path["sbx_init.go"]
    assert ensure_package_main("func f() {}") == "package main; func f() {}"
    assert rename_main('func main() { s := "func main()" }') == 'func sbxCandidateMain() { s := "func main()" }'


def test_strip_comments_preserves_positions():
    code = "a // c\nb /* x\ny */ \"s//t\" 'q' `r\nr`"
    stripped = strip_comments_c_like(code)
    assert len(stripped) == len(code) and stripped.count("\n") == code.count("\n")
    assert "c" not in stripped and "x" not in stripped and "s//t" not in stripped


def test_java_file_naming():
    assert public_type_name("import java.util.*;\npublic final class Main { }") == "Main"
    assert public_type_name("// public class Fake\nclass Solution { public static class Inner {} }") is None
    assert public_type_name("public record Point(int x) {}") == "Point"
    driver = DRIVERS[CodeLanguage.JAVA]
    assert driver.file_name(request("java", code="class Solution {}")) == "Solution.java"
    assert (
        driver.file_name(request("java", mode="run", code="class Foo { public static void main(String[] a) {} }")) == "Main.java"
    )
    assert driver.file_name(request("java", mode="run", code="public class App {}")) == "App.java"


def test_cpp_translation_unit():
    driver = DRIVERS[CodeLanguage.CPP]
    code = "class Solution {\npublic:\n  vector<int> twoSum(vector<int>& a, int t) { return a; }\n};\nint main() { return 0; }\n"
    tu = driver.translation_unit(request("cpp", code=code), parse_signature(SIG))
    assert tu.index('#line 1 "solution.cpp"') < tu.index("class Solution")
    assert "#define main sbx_candidate_main" in tu and "#undef main" in tu
    assert "sbx_sol->twoSum(sbx_a0, sbx_a1)" in tu
    assert "sbx::from_json<std::vector<int>>(sbx_t.args[0])" in tu
    free = driver.translation_unit(
        request("cpp", code="vector<int> two_sum(vector<int> a, int t) { return a; }"), parse_signature(SIG)
    )
    assert "two_sum(sbx_a0, sbx_a1)" in free and "make_unique" not in free
    compile_argv = driver.compile_step(request("cpp"), parse_signature(SIG))["argv"]
    assert compile_argv[-1] == "/opt/sandbox/cpp/sandbox_harness.o"


def test_cpp_glue_covers_all_types():
    params = [
        {"name": f"p{i}", "type": t}
        for i, t in enumerate(
            [
                "int",
                "long",
                "float",
                "bool",
                "string",
                "int[]",
                "long[]",
                "float[]",
                "string[]",
                "bool[]",
                "int[][]",
                "string[][]",
            ]
        )
    ]
    glue = generate_glue(parse_signature({"function_name": "f", "params": params, "return_type": "int"}), "f", True)
    for cpp_type in ("long long", "double", "bool", "std::string", "std::vector<bool>", "std::vector<std::vector<std::string>>"):
        assert f"sbx::from_json<{cpp_type}>" in glue
    assert "signed main()" in glue


def test_node_footer_only_uses_identifiers():
    footer = NodeDriver.footer(parse_signature(SIG))
    assert "twoSum(...args)" in footer and "two_sum(...args)" in footer and "new Solution().twoSum" in footer
    assert re.search(r"[;`$]{2}", footer) is None


def test_build_flags_match_dockerfiles():
    cpp_dockerfile = (RUNTIMES / "cpp" / "Dockerfile").read_text()
    assert f'ARG CXXFLAGS="{" ".join(CXX_FLAGS)}"' in cpp_dockerfile
    java_dockerfile = (RUNTIMES / "java" / "Dockerfile").read_text()
    assert " ".join(JAVAC_JVM_FLAGS) in " ".join(java_dockerfile.split())


# ---------------------------------------------------------------- interpretation


def test_all_passed_and_hidden_output_suppressed():
    driver = DRIVERS[CodeLanguage.PYTHON]
    req = request("python")
    plan = driver.plan(req)
    steps = [
        harness_step(
            "tests:visible",
            [
                {"t": "result", "i": 0, "ok": True, "actual": [0, 1], "ms": 0.2},
                {"t": "result", "i": 1, "ok": True, "actual": [0, 1]},
            ],
            stdout="visible print\n",
        ),
        harness_step("tests:hidden", [{"t": "result", "i": 2, "ok": True, "actual": [0, 1]}], stdout=""),
    ]
    response = driver.interpret(req, plan, outcome(steps))
    assert response.status == RunStatus.PASSED
    assert (response.passed, response.total) == (3, 3)
    assert response.stdout == "visible print\n"
    assert [t.hidden for t in response.tests] == [False, False, True]
    assert response.tests[0].runtime_ms == 0.2
    assert response.meta["steps"][0]["name"] == "tests:visible"
    assert response.meta["timings_ms"]["runner"] == 50.0


def test_wrong_answer_and_status_precedence():
    driver = DRIVERS[CodeLanguage.PYTHON]
    req = request("python")
    plan = driver.plan(req)
    wrong = [
        harness_step(
            "tests:visible",
            [{"t": "result", "i": 0, "ok": True, "actual": [1, 0]}, {"t": "result", "i": 1, "ok": True, "actual": [0, 1]}],
        ),
        harness_step("tests:hidden", [{"t": "result", "i": 2, "ok": True, "actual": [0, 1]}]),
    ]
    response = driver.interpret(req, plan, outcome(wrong))
    assert response.status == RunStatus.FAILED and response.passed == 2
    assert response.tests[0].actual == [1, 0] and response.tests[0].expected == [0, 1]
    errored = [
        harness_step(
            "tests:visible",
            [
                {"t": "result", "i": 0, "ok": False, "error": "IndexError: x", "error_type": "runtime"},
                {"t": "result", "i": 1, "ok": True, "actual": [9]},
            ],
        ),
        harness_step(
            "tests:hidden", [{"t": "result", "i": 2, "ok": False, "error": "terminated by SIGKILL", "error_type": "memory"}]
        ),
    ]
    response = driver.interpret(req, plan, outcome(errored))
    assert response.status == RunStatus.MEMORY_EXCEEDED
    assert response.tests[0].error == "IndexError: x"
    assert response.tests[2].error.startswith("Memory limit exceeded")
    timed = [
        harness_step(
            "tests:visible",
            [
                {"t": "result", "i": 0, "ok": True, "actual": [0, 1]},
                {"t": "result", "i": 1, "ok": False, "error": "time limit exceeded", "error_type": "timeout"},
            ],
        ),
        harness_step(
            "tests:hidden",
            [{"t": "result", "i": 2, "ok": False, "error": "not executed: time limit exceeded", "error_type": "not_run"}],
        ),
    ]
    response = driver.interpret(req, plan, outcome(timed))
    assert response.status == RunStatus.TIMEOUT
    assert response.tests[2].error == "Not executed: an earlier test exhausted the time limit"


def test_crash_messages_are_explained():
    driver = DRIVERS[CodeLanguage.CPP]
    req = request("cpp")
    plan = driver.plan(req)
    steps = [
        {"name": "compile", "exit_code": 0, "wall_ms": 300.0, "stdout": "", "stderr": ""},
        harness_step(
            "tests:visible",
            [
                {"t": "result", "i": 0, "ok": False, "error": "terminated by SIGSEGV\nstderr tail", "error_type": "crash"},
                {"t": "result", "i": 1, "ok": True, "actual": [0, 1]},
            ],
        ),
        harness_step("tests:hidden", [{"t": "result", "i": 2, "ok": True, "actual": [0, 1]}]),
    ]
    response = driver.interpret(req, plan, outcome(steps))
    assert response.status == RunStatus.ERROR
    assert response.tests[0].error.startswith("Process crashed (SIGSEGV: segmentation fault")
    assert response.meta["timings_ms"]["compile"] == 300.0


def test_compile_failure_and_fatal_entry():
    driver = DRIVERS[CodeLanguage.JAVA]
    req = request("java")
    plan = driver.plan(req)
    steps = [
        {
            "name": "compile",
            "exit_code": 1,
            "wall_ms": 300.0,
            "stdout": "",
            "stderr": "/tmp/work/Solution.java:3: error: ';' expected\n",
        }
    ]
    response = driver.interpret(req, plan, outcome(steps))
    assert response.status == RunStatus.COMPILE_ERROR
    assert response.compile_output == "Solution.java:3: error: ';' expected"
    py = DRIVERS[CodeLanguage.PYTHON]
    preq = request("python")
    fatal = [
        harness_step("tests:visible", [], fatal={"t": "fatal", "kind": "entry", "message": "Function 'two_sum' is not defined"})
    ]
    response = py.interpret(preq, py.plan(preq), outcome(fatal))
    assert response.status == RunStatus.COMPILE_ERROR and "two_sum" in response.compile_output
    runtime_fatal = [
        harness_step("tests:visible", [], fatal={"t": "fatal", "kind": "runtime", "message": "NameError: x"}),
        harness_step("tests:hidden", [], fatal={"t": "fatal", "kind": "runtime", "message": "NameError: x"}),
    ]
    response = py.interpret(preq, py.plan(preq), outcome(runtime_fatal))
    assert response.status == RunStatus.ERROR and all(t.error == "NameError: x" for t in response.tests)


def test_run_mode_statuses():
    driver = DRIVERS[CodeLanguage.PYTHON]
    req = request("python", mode="run")
    plan = driver.plan(req)
    base = {
        "name": "run",
        "stdout": "out /tmp/work/x\n",
        "stderr": "",
        "exit_code": 0,
        "signal": None,
        "wall_ms": 12.4,
        "max_rss_kb": 9000,
    }
    ok = driver.interpret(req, plan, outcome([{"name": "compile", "exit_code": 0, "wall_ms": 1.0}, base]))
    assert ok.status == RunStatus.PASSED and ok.stdout == "out /tmp/work/x\n" and ok.runtime_ms == 12 and ok.memory_kb == 9000
    timeout = driver.interpret(
        req,
        plan,
        outcome([{"name": "compile", "exit_code": 0}, {**base, "timed_out": True, "exit_code": 137, "signal": "SIGKILL"}]),
    )
    assert timeout.status == RunStatus.TIMEOUT and "time limit exceeded" in timeout.stderr
    oom = driver.interpret(
        req,
        plan,
        outcome([{"name": "compile", "exit_code": 0}, {**base, "exit_code": 1, "stderr": "Traceback...\nMemoryError\n"}]),
    )
    assert oom.status == RunStatus.MEMORY_EXCEEDED
    segv = driver.interpret(
        req, plan, outcome([{"name": "compile", "exit_code": 0}, {**base, "exit_code": 139, "signal": "SIGSEGV"}])
    )
    assert segv.status == RunStatus.ERROR and "SIGSEGV" in segv.stderr
    flood = driver.interpret(
        req, plan, outcome([{"name": "compile", "exit_code": 0}, {**base, "output_limit_exceeded": True, "exit_code": 137}])
    )
    assert flood.status == RunStatus.ERROR and flood.truncated


def test_container_level_failures():
    driver = DRIVERS[CodeLanguage.PYTHON]
    req = request("python", mode="run")
    plan = driver.plan(req)
    cases = [
        (ContainerOutcome(runner=None, image="i", deadline_exceeded=True), RunStatus.TIMEOUT),
        (ContainerOutcome(runner=None, image="i", oom_killed=True), RunStatus.MEMORY_EXCEEDED),
        (ContainerOutcome(runner=None, image="i", output_overflow=True), RunStatus.ERROR),
        (ContainerOutcome(runner=None, image="i", error="no runner result"), RunStatus.ERROR),
        (ContainerOutcome(runner={"protocol": 1, "ok": False, "error": "boom", "steps": []}, image="i"), RunStatus.ERROR),
    ]
    for oc, status in cases:
        assert driver.interpret(req, plan, oc).status == status


def test_large_values_are_previewed():
    driver = DRIVERS[CodeLanguage.PYTHON]
    big = list(range(5000))
    req = request("python", tests=[{"id": "big", "args": [[1, 2], 3], "expected": big}])
    plan = driver.plan(req)
    response = driver.interpret(
        req, plan, outcome([harness_step("tests:visible", [{"t": "result", "i": 0, "ok": True, "actual": big}])])
    )
    assert response.status == RunStatus.PASSED
    assert isinstance(response.tests[0].actual, str) and "truncated" in response.tests[0].actual
    assert response.truncated is True


def test_sql_interpretation():
    driver = DRIVERS[CodeLanguage.SQL]
    req = ExecuteRequest.model_validate(
        {
            "request_id": "r",
            "language": "sql",
            "code": "SELECT 1",
            "mode": "tests",
            "tests": [{"id": "a", "expected_rows": [[1, "x"]]}],
        }
    )
    plan = driver.plan(req)
    good = driver.interpret(
        req,
        plan,
        outcome(
            [
                harness_step(
                    "tests:visible",
                    [{"t": "result", "i": 0, "ok": True, "actual": {"columns": ["a", "b"], "rows": [[1.0, "x"]]}}],
                )
            ]
        ),
    )
    assert good.status == RunStatus.PASSED and good.tests[0].actual == [[1.0, "x"]]
    bad = driver.interpret(
        req, plan, outcome([harness_step("tests:visible", [{"t": "result", "i": 0, "ok": True, "actual": {"rows": [[2, "x"]]}}])])
    )
    assert bad.status == RunStatus.FAILED


def test_node_compile_output_cleanup():
    driver = DRIVERS[CodeLanguage.TYPESCRIPT]
    raw = (
        ":2\nfunction f(a: number {\n                     ^\n\nSyntaxError [ERR_INVALID_TYPESCRIPT_SYNTAX]: Expected ','\n"
        "    at parseTypeScript (node:internal/modules/typescript:72:36)\n    at node:internal/main/run_main_module:33:47 {\n"
        "  code: 'ERR_INVALID_TYPESCRIPT_SYNTAX'\n}\n\nNode.js v24.21.0\n"
    )
    cleaned = driver.clean_compile_output(raw)
    assert cleaned.startswith("solution.ts:2\nfunction f(a: number {")
    assert "node:internal" not in cleaned and "code:" not in cleaned and "Node.js v" not in cleaned
