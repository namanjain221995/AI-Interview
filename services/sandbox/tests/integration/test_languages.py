"""End-to-end behaviour per language through the HTTP API (needs the running service)."""

from __future__ import annotations

import pytest

from .programs import (
    AVERAGE,
    AVERAGE_SIGNATURE,
    CHECKSUM,
    CHECKSUM_ARGS,
    CHECKSUM_EXPECTED,
    CHECKSUM_SIGNATURE,
    COMPILE_ERROR,
    COMPILE_ERROR_LINE,
    CORRECT,
    LANGUAGES,
    RUNTIME_ERROR,
    RUNTIME_ERROR_TEXT,
    STDIN_PROGRAM,
    TWO_SUM_SIGNATURE,
    TWO_SUM_TESTS,
    WRAP,
    WRAP_EXPECTED,
    WRAP_SIGNATURE,
    WRAP_WORDS,
    WRONG,
)

pytestmark = pytest.mark.integration


def run_tests(sandbox, language: str, code: str, tests=TWO_SUM_TESTS, signature=TWO_SUM_SIGNATURE, **extra):
    return sandbox.execute(language=language, code=code, mode="tests", signature=signature, tests=tests, **extra)


@pytest.mark.parametrize("language", LANGUAGES)
def test_correct_solution_passes(sandbox, language):
    result = run_tests(sandbox, language, CORRECT[language])
    assert result["status"] == "passed", result
    assert (result["passed"], result["total"]) == (4, 4)
    assert [t["id"] for t in result["tests"]] == ["t1", "t2", "h1", "h2"]
    assert [t["hidden"] for t in result["tests"]] == [False, False, True, True]
    assert all(t["passed"] and t["error"] is None for t in result["tests"])
    assert result["tests"][0]["actual"] == [0, 1]
    assert result["meta"]["limits"]["network"] == "none"
    assert result["runtime_ms"] is not None and result["memory_kb"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_wrong_answer_fails(sandbox, language):
    result = run_tests(sandbox, language, WRONG[language])
    assert result["status"] == "failed", result
    assert result["passed"] == 0 and result["total"] == 4
    first = result["tests"][0]
    assert first["passed"] is False and first["actual"] == [0, 0] and first["expected"] == [0, 1]


@pytest.mark.parametrize("language", LANGUAGES)
def test_compile_error_is_reported(sandbox, language):
    result = run_tests(sandbox, language, COMPILE_ERROR[language])
    assert result["status"] == "compile_error", result
    assert result["compile_output"], result
    assert COMPILE_ERROR_LINE[language] in result["compile_output"], result["compile_output"]
    assert "/tmp/work" not in result["compile_output"]
    assert result["tests"] == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_runtime_error_is_reported(sandbox, language):
    result = run_tests(sandbox, language, RUNTIME_ERROR[language])
    assert result["status"] == "error", result
    assert result["passed"] == 0
    first = result["tests"][0]
    assert first["passed"] is False
    assert RUNTIME_ERROR_TEXT[language] in (first["error"] or ""), first


@pytest.mark.parametrize("language", LANGUAGES)
def test_run_mode_with_stdin(sandbox, language):
    result = sandbox.execute(language=language, code=STDIN_PROGRAM[language], mode="run", stdin="2 3\n")
    assert result["status"] == "passed", result
    assert result["stdout"].strip() == "5"
    assert result["exit_code"] == 0


@pytest.mark.parametrize("language", LANGUAGES)
def test_all_signature_types_convert(sandbox, language):
    tests = [{"id": "all", "args": CHECKSUM_ARGS, "expected": CHECKSUM_EXPECTED}]
    result = run_tests(sandbox, language, CHECKSUM[language], tests=tests, signature=CHECKSUM_SIGNATURE)
    assert result["status"] == "passed", result


@pytest.mark.parametrize("language", LANGUAGES)
def test_string_matrix_round_trip(sandbox, language):
    tests = [
        {"id": "w", "args": [WRAP_WORDS, True], "expected": WRAP_EXPECTED},
        {"id": "empty", "args": [["x"], False], "expected": []},
    ]
    result = run_tests(sandbox, language, WRAP[language], tests=tests, signature=WRAP_SIGNATURE)
    assert result["status"] == "passed", result


@pytest.mark.parametrize("language", LANGUAGES)
def test_float_tolerance(sandbox, language):
    tests = [{"id": "avg", "args": [[1.0, 2.0, 4.0]], "expected": 2.333333}]
    result = run_tests(sandbox, language, AVERAGE[language], tests=tests, signature=AVERAGE_SIGNATURE)
    assert result["status"] == "passed", result


def test_unordered_comparison(sandbox):
    signature = dict(TWO_SUM_SIGNATURE, comparison="unordered")
    code = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target - n in seen:\n            return [i, seen[target - n]]\n        seen[n] = i\n"
    result = run_tests(sandbox, "python", code, signature=signature)
    assert result["status"] == "passed", result
    exact = run_tests(sandbox, "python", code)
    assert exact["status"] == "failed"


def test_python_solution_class_and_print_debugging(sandbox):
    code = "class Solution:\n    def twoSum(self, nums, target):\n        print('debug', nums)\n        return [0, 1] if nums[0] + nums[1] == target else [1, 2]\n"
    result = run_tests(sandbox, "python", code, tests=TWO_SUM_TESTS[:2])
    assert result["status"] == "passed", result
    assert "debug [2, 7, 11, 15]" in result["stdout"]


def test_java_list_signature_and_public_class(sandbox):
    code = (
        "import java.util.*;\n\npublic class Solution {\n"
        "    public List<Integer> twoSum(List<Integer> nums, Integer target) {\n"
        "        for (int i = 0; i < nums.size(); i++) for (int j = i + 1; j < nums.size(); j++)\n"
        "            if (nums.get(i) + nums.get(j) == target) return List.of(i, j);\n"
        "        return List.of();\n    }\n}\n"
    )
    result = run_tests(sandbox, "java", code)
    assert result["status"] == "passed", result


def test_java_compact_source_file_run(sandbox):
    result = sandbox.execute(language="java", code='void main() {\n    IO.println("compact " + (40 + 2));\n}\n', mode="run")
    assert result["status"] == "passed", result
    assert result["stdout"].strip() == "compact 42"


def test_typescript_enum_and_types(sandbox):
    code = (
        "enum Mode { Fast = 1, Slow = 2 }\n"
        "interface Pair { a: number; b: number }\n"
        "function twoSum(nums: number[], target: number): number[] {\n"
        "  const p: Pair = { a: 0, b: 0 };\n"
        "  for (let i = 0; i < nums.length; i++) for (let j = i + 1; j < nums.length; j++)\n"
        "    if (nums[i] + nums[j] === target) { p.a = i; p.b = j; return [p.a, p.b * (Mode.Fast as number)]; }\n"
        "  return [];\n}\n"
    )
    result = run_tests(sandbox, "typescript", code)
    assert result["status"] == "passed", result


def test_go_candidate_main_is_ignored_in_tests_mode(sandbox):
    code = CORRECT["go"] + '\nfunc main() {\n\tprintln("local testing")\n}\n'
    result = run_tests(sandbox, "go", code)
    assert result["status"] == "passed", result


def test_cpp_free_function_and_define_int(sandbox):
    code = "#define int long long\nvector<int> twoSum(vector<int>& nums, int target) {\n    for (int i = 0; i < (int)nums.size(); i++)\n        for (int j = i + 1; j < (int)nums.size(); j++)\n            if (nums[i] + nums[j] == target) return {i, j};\n    return {};\n}\n"
    result = run_tests(sandbox, "cpp", code)
    assert result["status"] == "passed", result


def test_cpp_segfault_is_isolated_per_test(sandbox):
    code = (
        "class Solution {\npublic:\n    vector<int> twoSum(vector<int>& nums, int target) {\n"
        "        if (nums.size() == 2) { int* p = nullptr; return {*p}; }\n"
        "        unordered_map<int,int> m;\n        for (int i = 0; i < (int)nums.size(); i++) {\n"
        "            if (m.count(target - nums[i])) return {m[target - nums[i]], i};\n            m[nums[i]] = i;\n        }\n"
        "        return {};\n    }\n};\n"
    )
    result = run_tests(sandbox, "cpp", code)
    assert result["status"] == "error", result
    by_id = {t["id"]: t for t in result["tests"]}
    assert by_id["t1"]["passed"] and by_id["t2"]["passed"] and by_id["h2"]["passed"]
    assert not by_id["h1"]["passed"] and "SIGSEGV" in by_id["h1"]["error"]


def test_missing_entry_point_is_a_compile_error(sandbox):
    result = run_tests(sandbox, "python", "def other(nums, target):\n    return []\n")
    assert result["status"] == "compile_error"
    assert "two_sum" in result["compile_output"]


def test_hidden_test_output_is_not_returned(sandbox):
    code = "def two_sum(nums, target):\n    print('ARGS', nums, target)\n    return [0, 1]\n"
    result = run_tests(sandbox, "python", code)
    assert "ARGS [2, 7, 11, 15] 9" in result["stdout"]
    assert "-8" not in result["stdout"] and "[3, 3]" not in result["stdout"]


SQL_SETUP = (
    "CREATE TABLE employees (id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary INTEGER);"
    "INSERT INTO employees VALUES (1, 'Ann', 'eng', 120), (2, 'Bob', 'eng', 100), (3, 'Cid', 'ops', 90);"
)
SQL_TESTS = [
    {"id": "s1", "expected_rows": [["eng", 120], ["ops", 90]], "ordered": True},
    {
        "id": "s2",
        "setup_sql": "INSERT INTO employees VALUES (4, 'Dee', 'hr', 70);",
        "expected_rows": [["hr", 70], ["eng", 120], ["ops", 90]],
        "hidden": True,
    },
]


def test_sql_correct_and_wrong(sandbox):
    good = "SELECT dept, MAX(salary) AS top FROM employees GROUP BY dept ORDER BY dept;"
    result = sandbox.execute(language="sql", code=good, mode="tests", tests=SQL_TESTS, sql_setup=SQL_SETUP)
    assert result["status"] == "passed", result
    bad = "SELECT dept, MIN(salary) FROM employees GROUP BY dept ORDER BY dept"
    result = sandbox.execute(language="sql", code=bad, mode="tests", tests=SQL_TESTS, sql_setup=SQL_SETUP)
    assert result["status"] == "failed"
    assert result["tests"][0]["actual"] == [["eng", 100], ["ops", 90]]


def test_sql_syntax_error_and_write_attempt(sandbox):
    result = sandbox.execute(language="sql", code="SELEC dept FROM employees", mode="tests", tests=SQL_TESTS, sql_setup=SQL_SETUP)
    assert result["status"] == "compile_error" and "syntax error" in result["compile_output"]
    result = sandbox.execute(language="sql", code="DELETE FROM employees", mode="tests", tests=SQL_TESTS, sql_setup=SQL_SETUP)
    assert result["status"] == "compile_error" and "not authorized" in result["compile_output"]


def test_sql_run_mode_prints_tables(sandbox):
    result = sandbox.execute(
        language="sql", code="SELECT name, salary FROM employees ORDER BY id;", mode="run", sql_setup=SQL_SETUP
    )
    assert result["status"] == "passed", result
    assert "Ann" in result["stdout"] and "(3 rows)" in result["stdout"]


def test_rejected_signature(sandbox):
    result = sandbox.execute(
        language="python",
        code="def f(): pass",
        mode="tests",
        signature={"function_name": "f", "params": [{"name": "x", "type": "map"}], "return_type": "int"},
        tests=[{"id": "a", "args": [1], "expected": 1}],
    )
    assert result["status"] == "rejected" and "not one of" in result["stderr"]
