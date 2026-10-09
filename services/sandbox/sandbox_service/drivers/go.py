"""Go driver (Go 1.27 runtime image, read-only pre-warmed GOCACHE).

Files: go.mod (from the image), solution.go (candidate, forced into package
main), sbx_init.go (caps goroutine stacks so runaway recursion reports a
stack overflow instead of exhausting memory) and, in tests mode, the harness
package sandbox/sbxharness (already compiled in the image's GOCACHE) plus a
generated entry.go: ``func main() { sbxharness.Run(twoSum) }``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from ..models import CodeLanguage, ExecuteRequest
from ..signature import Signature
from .base import WORKDIR, Driver, find_declared, strip_comments_c_like

_PACKAGE = re.compile(r"^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_MAIN = re.compile(r"^func\s+main\s*\(\s*\)", re.MULTILINE)


def ensure_package_main(code: str) -> str:
    """Make the file ``package main`` (keeping line numbers when a clause exists)."""
    scan = strip_comments_c_like(code)
    match = _PACKAGE.search(scan)
    if match is None:
        return "package main; " + code
    if match.group(1) == "main":
        return code
    start, end = match.span(1)
    return code[:start] + "main" + code[end:]


def rename_main(code: str) -> str:
    scan = strip_comments_c_like(code)
    for match in reversed(list(_MAIN.finditer(scan))):
        code = code[: match.start()] + "func sbxCandidateMain()" + code[match.end() :]
    return code


class GoDriver(Driver):
    language = CodeLanguage.GO
    runtime = "go"
    source_name = "solution.go"
    compile_floor_mb = 320
    baseline_mb = 32
    memory_patterns: ClassVar[tuple[str, ...]] = ("runtime: out of memory", "fatal error: out of memory")

    def entry_name(self, code: str, sig: Signature) -> str:
        scan = strip_comments_c_like(code)
        found = find_declared(scan, sig.entry_candidates(sig.camel_name), r"^func\s+{name}\s*[\[(]")
        return found or sig.camel_name

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        code = ensure_package_main(req.code)
        max_stack = max(16, min(256, req.memory_limit_mb // 2)) << 20
        files: list[dict[str, Any]] = [
            {
                "path": "sbx_init.go",
                "content": (
                    f'package main\n\nimport sbxdebug "runtime/debug"\n\nfunc init() {{ sbxdebug.SetMaxStack({max_stack}) }}\n'
                ),
            }
        ]
        copies = [{"src": "/opt/sandbox/go/go.mod", "dst": "go.mod"}]
        if sig is not None:
            # The harness provides main(); a candidate main() used for local testing is renamed.
            code = rename_main(code)
            entry = self.entry_name(code, sig)
            files.append(
                {
                    "path": "entry.go",
                    "content": (
                        f'package main\n\nimport sbxharness "sandbox/sbxharness"\n\nfunc main() {{ sbxharness.Run({entry}) }}\n'
                    ),
                }
            )
            copies.append({"src": "/opt/sandbox/go/sbxharness/harness.go", "dst": "sbxharness/harness.go"})
        files.insert(0, {"path": self.source_name, "content": code})
        return files, copies

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": ["go", "build", "-ldflags=-s -w", "-o", "prog", "."]}

    def env(self, req: ExecuteRequest) -> dict[str, str]:
        return {"GOMEMLIMIT": f"{req.memory_limit_mb}MiB", "GOTRACEBACK": "single"}

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        return {"argv": [f"{WORKDIR}/prog"], "stdin": req.stdin or ""}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": [f"{WORKDIR}/prog"]}

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        assert sig is not None
        return {"entry_candidates": sig.entry_candidates(sig.camel_name)}

    def clean_compile_output(self, text: str) -> str:
        lines = [line for line in self.clean(text).splitlines() if line.strip() not in ("# sandbox", "# command-line-arguments")]
        return "\n".join(line[2:] if line.startswith("./") else line for line in lines)
