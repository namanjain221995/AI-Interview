"""C++ driver (GCC 14, -std=c++17 -O2, precompiled prelude).

The translation unit is ``#line 1 "solution.cpp"`` + candidate code, plus in
tests mode a generated ``signed main()`` that parses the JSON arguments into
the signature's C++ types, calls ``Solution().method(...)`` (or a free
function when no Solution class exists) on a fresh instance per test and
reports the JSON-encoded return value. Compiler diagnostics keep the
candidate's own line numbers.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from ..models import CodeLanguage, ExecuteRequest
from ..signature import Signature
from .base import WORKDIR, Driver, find_declared, strip_comments_c_like

# Must stay identical to CXXFLAGS in runtimes/cpp/Dockerfile (the PCH is only used when they match).
CXX_FLAGS = ["-std=c++17", "-O2", "-pipe", "-fmax-errors=20", "-fdiagnostics-color=never"]
HARNESS_OBJECT = "/opt/sandbox/cpp/sandbox_harness.o"

CPP_TYPES = {
    "int": "int",
    "long": "long long",
    "float": "double",
    "bool": "bool",
    "string": "std::string",
    "int[]": "std::vector<int>",
    "long[]": "std::vector<long long>",
    "float[]": "std::vector<double>",
    "string[]": "std::vector<std::string>",
    "bool[]": "std::vector<bool>",
    "int[][]": "std::vector<std::vector<int>>",
    "string[][]": "std::vector<std::vector<std::string>>",
}
_SOLUTION_CLASS = re.compile(r"\b(?:class|struct)\s+Solution\b")
_HAS_MAIN = re.compile(r"\bmain\s*\(")


def generate_glue(sig: Signature, method: str, use_class: bool) -> str:
    n = len(sig.params)
    decls = "\n".join(
        f"            auto sbx_a{k} = sbx::from_json<{CPP_TYPES[p.type]}>(sbx_t.args[{k}]);" for k, p in enumerate(sig.params)
    )
    args = ", ".join(f"sbx_a{k}" for k in range(n))
    if use_class:
        make = "            auto sbx_sol = std::make_unique<Solution>();\n"
        call = f"sbx_sol->{method}({args})"
    else:
        make = ""
        call = f"{method}({args})"
    return f"""#line 1 "sandbox_harness.cpp"
signed main() {{
    sbx::Harness sbx_h;
    if (!sbx_h.load()) return 1;
    sbx_h.ready();
    for (const sbx::Test& sbx_t : sbx_h.tests) {{
        sbx_h.begin(sbx_t);
        auto sbx_t0 = std::chrono::steady_clock::now();
        try {{
            if (sbx_t.args.size() != {n}u) throw sbx::ParseError("expected {n} argument(s)");
{decls}
{make}            sbx_t0 = std::chrono::steady_clock::now();
            auto sbx_res = {call};
            double sbx_ms = sbx::Harness::elapsed_ms(sbx_t0);
            sbx_h.result(sbx_t, sbx::to_json(sbx_res), sbx_ms);
        }} catch (...) {{
            sbx_h.exception(sbx_t, std::current_exception(), sbx::Harness::elapsed_ms(sbx_t0));
        }}
    }}
    sbx_h.done();
    return 0;
}}
"""


class CppDriver(Driver):
    language = CodeLanguage.CPP
    runtime = "cpp"
    source_name = "solution.cpp"
    compile_floor_mb = 448
    baseline_mb = 16
    memory_patterns: ClassVar[tuple[str, ...]] = ("std::bad_alloc",)

    def translation_unit(self, req: ExecuteRequest, sig: Signature | None) -> str:
        if sig is None:
            return f'#line 1 "{self.source_name}"\n{req.code}\n'
        scan = strip_comments_c_like(req.code)
        use_class = bool(_SOLUTION_CLASS.search(scan))
        method = find_declared(scan, sig.entry_candidates(sig.camel_name), r"\b{name}\s*\(") or sig.camel_name
        rename_main = bool(_HAS_MAIN.search(scan))
        parts = []
        if rename_main:
            parts.append("#define main sbx_candidate_main")
        parts.append(f'#line 1 "{self.source_name}"')
        parts.append(req.code)
        if rename_main:
            parts.append("#undef main")
        parts.append(generate_glue(sig, method, use_class))
        return "\n".join(parts)

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return [{"path": "main.cpp", "content": self.translation_unit(req, sig)}], []

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        argv = ["g++", *CXX_FLAGS, "-I/opt/sandbox/cpp", "-include", "sandbox_prelude.hpp", "-o", "prog", "main.cpp"]
        if sig is not None:
            argv.append(HARNESS_OBJECT)
        return {"argv": argv}

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        return {"argv": [f"{WORKDIR}/prog"], "stdin": req.stdin or ""}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": [f"{WORKDIR}/prog"]}
