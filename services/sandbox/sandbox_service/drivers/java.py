"""Java driver (Temurin 25 runtime image).

compile  javac on the candidate's file only (the harness/launcher are
         precompiled in the image), with C1 + SerialGC + the image's AOT cache.
run      the precompiled SandboxLauncher finds the entry point (any public
         class name, compact source files) / SandboxHarness calls the method by
         reflection. Heap = the requested memory limit; non-heap overhead is
         covered by ``baseline_mb``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from ..models import CodeLanguage, ExecuteRequest
from ..signature import Signature
from .base import Driver, strip_comments_c_like

# Must stay identical to the training flags in runtimes/java/Dockerfile (AOT cache compatibility).
JAVAC_JVM_FLAGS = ["-J-XX:+UseSerialGC", "-J-XX:TieredStopAtLevel=1", "-J-XX:-UsePerfData", "-J-Xss8m", "-J-Xmx256m"]
JAVAC_AOT_CACHE = "/opt/sandbox/java/javac.aot"
HARNESS_JAR = "/opt/sandbox/java/harness.jar"
_PUBLIC_TYPE = re.compile(
    r"^public\s+(?:(?:final|abstract|sealed|non-sealed|strictfp|static)\s+)*(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)


def public_type_name(code: str) -> str | None:
    match = _PUBLIC_TYPE.search(strip_comments_c_like(code))
    return match.group(1) if match else None


class JavaDriver(Driver):
    language = CodeLanguage.JAVA
    runtime = "java"
    source_name = "Main.java"
    compile_floor_mb = 384
    baseline_mb = 96
    memory_patterns: ClassVar[tuple[str, ...]] = ("java.lang.OutOfMemoryError",)

    def file_name(self, req: ExecuteRequest) -> str:
        name = public_type_name(req.code)
        if name:
            return f"{name}.java"
        return "Solution.java" if req.mode == "tests" else "Main.java"

    def files(self, req: ExecuteRequest, sig: Signature | None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return [{"path": self.file_name(req), "content": req.code}], []

    def compile_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {
            "argv": [
                "javac",
                *JAVAC_JVM_FLAGS,
                f"-J-XX:AOTCache={JAVAC_AOT_CACHE}",
                "-encoding",
                "UTF-8",
                "-proc:none",
                "-g",
                "-nowarn",
                "-d",
                "classes",
                self.file_name(req),
            ]
        }

    @staticmethod
    def jvm_flags(req: ExecuteRequest) -> list[str]:
        return [
            "-XX:+UseSerialGC",
            "-XX:-UsePerfData",
            "-XX:+DisableAttachMechanism",
            "-Xshare:auto",
            f"-Xmx{req.memory_limit_mb}m",
            "-Xss32m",
            "-XX:MaxMetaspaceSize=128m",
            "-XX:ReservedCodeCacheSize=48m",
            "-Dfile.encoding=UTF-8",
            "-Dstdout.encoding=UTF-8",
            "-Dstderr.encoding=UTF-8",
            "-Djava.io.tmpdir=/tmp",
            "-cp",
            f"{HARNESS_JAR}:classes",
        ]

    def run_step(self, req: ExecuteRequest) -> dict[str, Any]:
        preferred = self.file_name(req)[: -len(".java")]
        return {"argv": ["java", *self.jvm_flags(req), "SandboxLauncher", "classes", preferred], "stdin": req.stdin or ""}

    def harness_step(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        return {"argv": ["java", *self.jvm_flags(req), "SandboxHarness"]}

    def harness_input(self, req: ExecuteRequest, sig: Signature | None) -> dict[str, Any]:
        assert sig is not None
        return {
            "class": "Solution",
            "entry_candidates": sig.entry_candidates(sig.camel_name),
            "params": sig.to_payload(),
            "return_type": sig.return_type,
        }
