"""Structural code analysis (nested loops, hash maps, recursion, sorting...)."""

from __future__ import annotations

import pytest

from app.domain.enums import CodeLanguage
from app.services.coding.analysis import analyze, describe_changes, features_from_dict

NESTED = {
    "python": "def f(a):\n    for i in range(len(a)):\n        for j in range(i + 1, len(a)):\n            pass\n",
    "javascript": "function f(a) {\n  for (let i = 0; i < a.length; i++) {\n    for (let j = i + 1; j < a.length; j++) {}\n  }\n}\n",
    "typescript": "function f(a: number[]): void {\n  for (const x of a) {\n    a.forEach((y) => { console.log(x + y); });\n  }\n}\n",
    "java": "class Solution {\n  int f(int[] a) {\n    for (int i = 0; i < a.length; i++)\n      for (int j = 0; j < a.length; j++) a[i]++;\n    return 0;\n  }\n}\n",
    "go": "package main\n\nfunc f(a []int) int {\n\tfor i := 0; i < len(a); i++ {\n\t\tfor j := i + 1; j < len(a); j++ {\n\t\t}\n\t}\n\treturn 0\n}\n",
    "cpp": "int f(vector<int>& a) {\n  int i = 0;\n  while (i < 3) {\n    for (int j = 0; j < 3; j++) { i++; }\n  }\n  return i;\n}\n",
}

FIB = {
    "python": "def fib(n):\n    if n < 2:\n        return n\n    return fib(n - 1) + fib(n - 2)\n",
    "javascript": "function fib(n) {\n  if (n < 2) return n;\n  return fib(n - 1) + fib(n - 2);\n}\n",
    "typescript": "const fib = (n: number): number => {\n  return n < 2 ? n : fib(n - 1) + fib(n - 2);\n};\n",
    "java": "class Solution {\n  public int fib(int n) {\n    if (n < 2) return n;\n    return fib(n - 1) + fib(n - 2);\n  }\n}\n",
    "go": "package main\n\nfunc fib(n int) int {\n\tif n < 2 {\n\t\treturn n\n\t}\n\treturn fib(n-1) + fib(n-2)\n}\n",
    "cpp": "class Solution {\npublic:\n  int fib(int n) {\n    if (n < 2) return n;\n    return fib(n - 1) + fib(n - 2);\n  }\n};\n",
}


@pytest.mark.parametrize("language", list(NESTED))
def test_nested_loops_detected(language: str) -> None:
    features = analyze(NESTED[language], language)
    assert features.nested_loops, features
    assert features.loop_depth == 2


@pytest.mark.parametrize("language", list(FIB))
def test_recursion_detected(language: str) -> None:
    features = analyze(FIB[language], language)
    assert features.recursion
    assert "fib" in features.functions


@pytest.mark.parametrize(
    ("language", "code", "flag"),
    [
        ("python", "seen = {}\nfor x in a:\n    seen[x] = 1\n", "hash_map"),
        ("python", "from collections import Counter\nc = Counter(a)\n", "hash_map"),
        ("python", "s = set(a)\n", "hash_set"),
        ("python", "b = sorted(a)\n", "sorting"),
        ("python", "import heapq\nheapq.heappush(h, 1)\n", "heap"),
        ("python", "lo, hi = 0, n\nmid = (lo + hi) // 2\n", "binary_search"),
        ("javascript", "const m = new Map();\n", "hash_map"),
        ("javascript", "const s = new Set(a);\n", "hash_set"),
        ("javascript", "a.sort((x, y) => x - y);\n", "sorting"),
        ("java", "Map<Integer, Integer> m = new HashMap<>();\n", "hash_map"),
        ("java", "Set<String> s = new HashSet<>();\n", "hash_set"),
        ("java", "Arrays.sort(a);\n", "sorting"),
        ("java", "PriorityQueue<Integer> pq = new PriorityQueue<>();\n", "heap"),
        ("go", "seen := make(map[int]int)\n", "hash_map"),
        ("go", "seen := map[string]bool{}\n", "hash_set"),
        ("go", "sort.Ints(a)\n", "sorting"),
        ("cpp", "unordered_map<int, int> m;\n", "hash_map"),
        ("cpp", "unordered_set<int> s;\n", "hash_set"),
        ("cpp", "sort(a.begin(), a.end());\n", "sorting"),
        ("cpp", "auto it = lower_bound(a.begin(), a.end(), x);\n", "binary_search"),
    ],
)
def test_data_structures_and_algorithms(language: str, code: str, flag: str) -> None:
    assert getattr(analyze(code, language), flag)


def test_keywords_in_comments_and_strings_are_ignored() -> None:
    js = '// for (;;) { new Map() }\nconst s = "for (let i=0;;) { new Set() }";\n'
    features = analyze(js, "javascript")
    assert features.loops == 0 and not features.hash_map and not features.hash_set
    py = '# for x in y:\n"""\nfor a in b:\n    for c in d:\n"""\nx = "sorted(a)"\n'
    features = analyze(py, "python")
    assert features.loops == 0 and not features.sorting


def test_python_comprehension_inside_loop_counts_as_nesting() -> None:
    code = "def f(a):\n    for x in a:\n        if any(y == x for y in a):\n            return True\n"
    assert analyze(code, "python").loop_depth == 2


def test_sequential_loops_are_not_nested() -> None:
    code = "def f(a):\n    for x in a:\n        pass\n    for y in a:\n        pass\n"
    features = analyze(code, "python")
    assert features.loops == 2 and features.loop_depth == 1


def test_do_while_is_one_loop() -> None:
    code = "int f(){ int i=0; do { i++; } while (i < 3); return i; }"
    features = analyze(code, "cpp")
    assert features.loops == 1 and features.loop_depth == 1


def test_sql_features() -> None:
    sql = "WITH t AS (SELECT * FROM e)\nSELECT d.name, MAX(e.salary) OVER (PARTITION BY d.id)\nFROM e JOIN d ON e.d = d.id\nWHERE e.salary > (SELECT AVG(salary) FROM e)\nGROUP BY d.name;\n-- JOIN in a comment\n"
    features = analyze(sql, CodeLanguage.SQL)
    assert (
        features.sql_cte
        and features.sql_join
        and features.sql_subquery
        and features.sql_group_by
        and features.sql_window
    )
    assert "JOIN" in features.summary()


def test_malformed_code_never_raises() -> None:
    for language in ("python", "javascript", "java", "go", "cpp", "sql", "typescript"):
        analyze("for (((( {{{{ '''unterminated \"", language)


def test_describe_changes_nested_loop_to_hash_map() -> None:
    before = analyze(NESTED["python"], "python")
    after = analyze(
        "def f(a, t):\n    seen = {}\n    for i, v in enumerate(a):\n        seen[v] = i\n", "python"
    )
    changes = {c.key: c for c in describe_changes(before, after)}
    assert changes["nested_loops"].introduced is False
    assert changes["hash_map"].introduced is True
    assert changes["hash_map"].description == "introduced a hash map"


def test_describe_changes_ignores_language_switch_and_round_trips_dict() -> None:
    py = analyze(NESTED["python"], "python")
    go = analyze(NESTED["go"], "go")
    assert describe_changes(py, go) == []
    assert describe_changes(None, py) == []
    restored = features_from_dict(py.as_dict())
    assert restored is not None and restored.loop_depth == 2 and restored.nested_loops
    assert features_from_dict({}) is None
