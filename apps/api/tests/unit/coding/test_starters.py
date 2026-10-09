"""Deterministic starter code follows the curated conventions."""

from __future__ import annotations

import ast

import pytest

from app.domain.enums import CodeLanguage
from app.seed.loaders import entry_point_name
from app.services.coding.starters import FUNCTION_LANGUAGES, starter_for, starters_for

from .conftest import seeds

ALL_TYPES_SIG = {
    "function_name": "mixed_types",
    "params": [
        {"name": "a", "type": "int"},
        {"name": "b", "type": "long[]"},
        {"name": "c", "type": "string[][]"},
        {"name": "d", "type": "bool"},
    ],
    "return_type": "float[]",
}


@pytest.mark.parametrize("slug", [s for s, seed in seeds().items() if seed.signature is not None])
def test_starters_for_every_curated_signature_define_the_entry_point(slug: str) -> None:
    seed = seeds()[slug]
    signature = seed.signature.model_dump(mode="json", exclude_none=True)
    starters = starters_for(signature, description=seed.title)
    assert set(starters) == {lang.value for lang in FUNCTION_LANGUAGES}
    for lang, code in starters.items():
        assert entry_point_name(seed.signature.function_name, lang) + "(" in code
    ast.parse(starters["python"])
    assert "class Solution" in starters["java"] and "class Solution" in starters["cpp"]
    assert starters["go"].startswith("package main")


def test_type_mapping_matches_curated_conventions() -> None:
    java = starter_for(ALL_TYPES_SIG, CodeLanguage.JAVA)
    assert "public double[] mixedTypes(int a, long[] b, String[][] c, boolean d)" in java
    assert "return new double[0];" in java
    go = starter_for(ALL_TYPES_SIG, CodeLanguage.GO)
    assert "func mixedTypes(a int, b []int64, c [][]string, d bool) []float64" in go
    cpp = starter_for(ALL_TYPES_SIG, CodeLanguage.CPP)
    assert "vector<double> mixedTypes(int a, vector<long long>& b, vector<vector<string>>& c, bool d)" in cpp
    ts = starter_for(ALL_TYPES_SIG, CodeLanguage.TYPESCRIPT)
    assert "function mixedTypes(a: number, b: number[], c: string[][], d: boolean): number[]" in ts
    py = starter_for(ALL_TYPES_SIG, CodeLanguage.PYTHON)
    assert "def mixed_types(a: int, b: list[int], c: list[list[str]], d: bool) -> list[float]:" in py
    js = starter_for(ALL_TYPES_SIG, CodeLanguage.JAVASCRIPT)
    assert "@param {string[][]} c" in js and "function mixedTypes(a, b, c, d)" in js


def test_sql_and_unknown_types_are_rejected() -> None:
    with pytest.raises(ValueError):
        starter_for(ALL_TYPES_SIG, CodeLanguage.SQL)
    with pytest.raises(ValueError):
        starter_for({**ALL_TYPES_SIG, "return_type": "map"}, CodeLanguage.PYTHON)
