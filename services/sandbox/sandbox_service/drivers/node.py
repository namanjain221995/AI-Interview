"""JavaScript and TypeScript drivers (Node 24 runtime image).

compile  ``compile.js`` strips TypeScript types (Node's built-in
         module.stripTypeScriptTypes; no type checking), syntax-checks the
         result as CommonJS or ES module and, in tests mode, appends the entry
         footer and the harness so they share the candidate's module scope.
run      ``node --permission`` (fs reads limited to /tmp/work, no writes, no
         child processes or workers) with heap and stack limits.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from ..models import CodeLanguage, ExecuteRequest
from ..signature import Signature
from .base import WORKDIR, Driver

_NODE_INTERNAL = re.compile(r"^\s+at .*(node:internal|/opt/sandbox/|\(node:|\[eval\]).*$\n?", re.MULTILINE)
_NODE_FOOTER = re.compile(r"^Node\.js v\d+\.\d+\.\d+\s*$\n?", re.MULTILINE)
_ERROR_PROPS = re.compile(r"^\s*code: '[A-Z_]+'\n\}\s*$\n?", re.MULTILINE)


class NodeDriver(Driver):
    runtime = "node"
    baseline_mb = 64
    lang_flag: ClassVar[str] = "js"
    memory_patterns: ClassVar[tuple[str, ...]] = (
        "JavaScript heap out of memory",
        "Reached heap limit",
        "Array buffer allocation failed",
        "Fatal process out of memory",
    )

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        files = [{"path": self.source_name, "content": req.code}]
        if sig is not None:
            files.append({"path": "footer.js", "content": self.footer(sig)})
        return files, []

    @staticmethod
    def footer(sig: Signature) -> str:
        """Resolve the entry point inside the candidate's module scope.

        Names come from a validated signature (identifiers only), so they are
        safe to splice into source code.
        """
        lines = [";globalThis.__sandboxEntry = (function __sandboxResolve() {"]
        for name in sig.entry_candidates(sig.camel_name):
            lines.append(
                f"  try {{ if (typeof {name} === 'function' && !/^class[\\s{{]/.test(Function.prototype.toString.call({name}))) "
                f"{{ const __sandboxCall = (args) => {name}(...args); return __sandboxCall; }} }} catch (e) {{}}"
            )
        for name in sig.entry_candidates(sig.camel_name):
            lines.append(
                "  try { if (typeof Solution === 'function' && Solution.prototype && "
                f"typeof Solution.prototype.{name} === 'function') "
                f"{{ const __sandboxCall = (args) => new Solution().{name}(...args); return __sandboxCall; }} }} catch (e) {{}}"
            )
            lines.append(
                "  try { if (typeof Solution === 'function' && "
                f"typeof Solution.{name} === 'function') "
                f"{{ const __sandboxCall = (args) => Solution.{name}(...args); return __sandboxCall; }} }} catch (e) {{}}"
            )
            lines.append(
                "  try { if (typeof module === 'object' && module && module.exports && "
                f"typeof module.exports.{name} === 'function') "
                f"{{ const __sandboxCall = (args) => module.exports.{name}(...args); return __sandboxCall; }} }} catch (e) {{}}"
            )
        lines.append("  return undefined;")
        lines.append("})();")
        return "\n".join(lines) + "\n"

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        argv = [
            "node",
            "--experimental-vm-modules",
            "--no-warnings",
            "--max-old-space-size=192",
            "/opt/sandbox/compile.js",
            self.lang_flag,
            self.source_name,
            "main.js",
        ]
        if sig is not None:
            argv += ["footer.js", "/opt/sandbox/harness.js"]
        return {"argv": argv}

    def node_argv(self, req: ExecuteRequest) -> list[str]:
        argv = [
            "node",
            "--permission",
            f"--allow-fs-read={WORKDIR}/",
            "--allow-fs-read=/dev/stdin",
            "--stack-size=16000",
            f"--max-old-space-size={req.memory_limit_mb}",
        ]
        if self.lang_flag == "ts":
            argv.append("--enable-source-maps")
        return [*argv, "main.js"]

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        return {"argv": self.node_argv(req), "stdin": req.stdin or ""}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": self.node_argv(req)}

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        assert sig is not None
        return {
            "entry_candidates": sig.entry_candidates(sig.camel_name),
            "params": sig.to_payload(),
            "return_type": sig.return_type,
        }

    def clean(self, text: str) -> str:
        text = text.replace(f"file://{WORKDIR}/main.js", self.source_name).replace(f"{WORKDIR}/main.js", self.source_name)
        text = text.replace(f"file://{WORKDIR}/", "").replace(f"{WORKDIR}/", "")
        return text

    def clean_compile_output(self, text: str) -> str:
        text = self.clean(text)
        text = _NODE_INTERNAL.sub("", text)
        text = _NODE_FOOTER.sub("", text)
        text = _ERROR_PROPS.sub("", text)
        # Errors raised through module.stripTypeScriptTypes print ":<line>" without a file name.
        text = re.sub(r"^:(\d+)$", rf"{self.source_name}:\1", text, flags=re.MULTILINE)
        return text.strip("\n")


class JavaScriptDriver(NodeDriver):
    language = CodeLanguage.JAVASCRIPT
    source_name = "solution.js"
    lang_flag = "js"


class TypeScriptDriver(NodeDriver):
    language = CodeLanguage.TYPESCRIPT
    source_name = "solution.ts"
    lang_flag = "ts"
