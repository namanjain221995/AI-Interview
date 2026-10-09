"""Signature naming conventions and deterministic starter code.

Follows the curated problems' conventions (``samples/coding``): Python uses
the snake_case function name at top level; JavaScript/TypeScript a top-level
lowerCamelCase function; Java and C++ a ``Solution`` class method (C++ takes
vectors by reference); Go a top-level function in ``package main``.

Used by the catalog loader (problems may omit ``starter_code`` for any
function language), by generated problems and by coding questions created
through the API without starter code for some languages, so every supported
language always has something that compiles in the sandbox.

This module only depends on :mod:`app.domain.enums`, so the loader, the
validator CLI and the coding service can all import it without cycles
(``app.services.coding.starters`` re-exports it).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.enums import CodeLanguage

#: Languages every function-style coding problem supports.
FUNCTION_LANGUAGES: tuple[CodeLanguage, ...] = (
    CodeLanguage.PYTHON,
    CodeLanguage.JAVASCRIPT,
    CodeLanguage.TYPESCRIPT,
    CodeLanguage.JAVA,
    CodeLanguage.GO,
    CodeLanguage.CPP,
)

_PY = {
    "int": "int",
    "long": "int",
    "float": "float",
    "bool": "bool",
    "string": "str",
    "int[]": "list[int]",
    "long[]": "list[int]",
    "float[]": "list[float]",
    "string[]": "list[str]",
    "bool[]": "list[bool]",
    "int[][]": "list[list[int]]",
    "string[][]": "list[list[str]]",
}
_TS = {
    "int": "number",
    "long": "number",
    "float": "number",
    "bool": "boolean",
    "string": "string",
    "int[]": "number[]",
    "long[]": "number[]",
    "float[]": "number[]",
    "string[]": "string[]",
    "bool[]": "boolean[]",
    "int[][]": "number[][]",
    "string[][]": "string[][]",
}
_JAVA = {
    "int": "int",
    "long": "long",
    "float": "double",
    "bool": "boolean",
    "string": "String",
    "int[]": "int[]",
    "long[]": "long[]",
    "float[]": "double[]",
    "string[]": "String[]",
    "bool[]": "boolean[]",
    "int[][]": "int[][]",
    "string[][]": "String[][]",
}
_GO = {
    "int": "int",
    "long": "int64",
    "float": "float64",
    "bool": "bool",
    "string": "string",
    "int[]": "[]int",
    "long[]": "[]int64",
    "float[]": "[]float64",
    "string[]": "[]string",
    "bool[]": "[]bool",
    "int[][]": "[][]int",
    "string[][]": "[][]string",
}
_CPP = {
    "int": "int",
    "long": "long long",
    "float": "double",
    "bool": "bool",
    "string": "string",
    "int[]": "vector<int>",
    "long[]": "vector<long long>",
    "float[]": "vector<double>",
    "string[]": "vector<string>",
    "bool[]": "vector<bool>",
    "int[][]": "vector<vector<int>>",
    "string[][]": "vector<vector<string>>",
}

_PY_DEFAULT = {"int": "0", "long": "0", "float": "0.0", "bool": "False", "string": '""'}
_JS_DEFAULT = {"int": "0", "long": "0", "float": "0", "bool": "false", "string": '""'}
_JAVA_DEFAULT = {"int": "0", "long": "0L", "float": "0.0", "bool": "false", "string": '""'}
_GO_DEFAULT = {"int": "0", "long": "0", "float": "0", "bool": "false", "string": '""'}
_CPP_DEFAULT = {"int": "0", "long": "0", "float": "0.0", "bool": "false", "string": '""'}

SUPPORTED_TYPES = frozenset(_PY)


# =============================================================================
# Naming
# =============================================================================


def to_lower_camel(name: str) -> str:
    """``top_k_frequent`` -> ``topKFrequent``."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def entry_point_name(function_name: str, language: CodeLanguage | str) -> str:
    """Name of the candidate entry point for ``language``: the snake_case name
    for Python, lowerCamelCase everywhere else."""
    return function_name if CodeLanguage(language) == CodeLanguage.PYTHON else to_lower_camel(function_name)


# =============================================================================
# Starter code
# =============================================================================


def _is_array(type_name: str) -> bool:
    return type_name.endswith("[]")


def _comment(description: str | None, prefix: str) -> str:
    text = (description or "Write your solution here.").strip().splitlines()[0][:110]
    return f"{prefix} {text}"


def starter_for(
    signature: Mapping[str, Any], language: CodeLanguage | str, *, description: str | None = None
) -> str:
    """Starter code for ``language`` (raises ``ValueError`` for SQL or bad signatures)."""
    lang = CodeLanguage(language)
    if lang == CodeLanguage.SQL:
        raise ValueError("SQL problems have no function signature")
    name = str(signature["function_name"])
    params = [(str(p["name"]), str(p["type"])) for p in signature.get("params", [])]
    ret = str(signature["return_type"])
    for _, type_name in [*params, ("return", ret)]:
        if type_name not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported signature type {type_name!r}")
    entry = entry_point_name(name, lang)

    if lang == CodeLanguage.PYTHON:
        args = ", ".join(f"{p}: {_PY[t]}" for p, t in params)
        default = "[]" if _is_array(ret) else _PY_DEFAULT[ret]
        return f"def {entry}({args}) -> {_PY[ret]}:\n    {_comment(description, '#')}\n    return {default}\n"

    if lang == CodeLanguage.JAVASCRIPT:
        doc = "\n".join(f" * @param {{{_TS[t]}}} {p}" for p, t in params)
        args = ", ".join(p for p, _ in params)
        default = "[]" if _is_array(ret) else _JS_DEFAULT[ret]
        return (
            f"/**\n{doc}\n * @return {{{_TS[ret]}}}\n */\nfunction {entry}({args}) {{\n"
            f"  {_comment(description, '//')}\n  return {default};\n}}\n"
        )

    if lang == CodeLanguage.TYPESCRIPT:
        args = ", ".join(f"{p}: {_TS[t]}" for p, t in params)
        default = "[]" if _is_array(ret) else _JS_DEFAULT[ret]
        return f"function {entry}({args}): {_TS[ret]} {{\n  {_comment(description, '//')}\n  return {default};\n}}\n"

    if lang == CodeLanguage.JAVA:
        args = ", ".join(f"{_JAVA[t]} {p}" for p, t in params)
        if _is_array(ret):
            base = _JAVA[ret][: _JAVA[ret].index("[")]
            dims = _JAVA[ret].count("[]")
            default = f"new {base}[0]" + "[]" * (dims - 1)
        else:
            default = _JAVA_DEFAULT[ret]
        return (
            "import java.util.*;\n\nclass Solution {\n"
            f"    public {_JAVA[ret]} {entry}({args}) {{\n"
            f"        {_comment(description, '//')}\n        return {default};\n    }}\n}}\n"
        )

    if lang == CodeLanguage.GO:
        args = ", ".join(f"{p} {_GO[t]}" for p, t in params)
        default = f"{_GO[ret]}{{}}" if _is_array(ret) else _GO_DEFAULT[ret]
        return (
            f"package main\n\nfunc {entry}({args}) {_GO[ret]} {{\n"
            f"\t{_comment(description, '//')}\n\treturn {default}\n}}\n"
        )

    # C++
    def cpp_param(p: str, t: str) -> str:
        return f"{_CPP[t]}& {p}" if _is_array(t) else f"{_CPP[t]} {p}"

    args = ", ".join(cpp_param(p, t) for p, t in params)
    default = "{}" if _is_array(ret) else _CPP_DEFAULT[ret]
    includes = (
        "#include <string>\n#include <vector>\n"
        if any(t == "string" or "string" in t for _, t in [*params, ("r", ret)])
        else "#include <vector>\n"
    )
    return (
        f"{includes}using namespace std;\n\nclass Solution {{\npublic:\n"
        f"    {_CPP[ret]} {entry}({args}) {{\n"
        f"        {_comment(description, '//')}\n        return {default};\n    }}\n}};\n"
    )


def starters_for(
    signature: Mapping[str, Any],
    languages: Iterable[CodeLanguage | str] | None = None,
    *,
    description: str | None = None,
) -> dict[str, str]:
    """Starter code for every function language (or ``languages``), keyed by language value."""
    langs = list(languages) if languages else list(FUNCTION_LANGUAGES)
    return {
        CodeLanguage(lang).value: starter_for(signature, lang, description=description)
        for lang in langs
        if CodeLanguage(lang) != CodeLanguage.SQL
    }


__all__ = [
    "FUNCTION_LANGUAGES",
    "SUPPORTED_TYPES",
    "entry_point_name",
    "starter_for",
    "starters_for",
    "to_lower_camel",
]
