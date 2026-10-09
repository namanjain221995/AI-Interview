#!/usr/bin/env python3
"""Copy the canonical job runner into every runtime image build context.

Each runtime image is built from its own directory (docker-compose build
contexts cannot reach a shared parent), so runtimes/<lang>/runner.py are
verbatim copies of runtimes/runner.py. Run after editing the runner; the unit
test ``test_runner_copies_are_in_sync`` fails when a copy drifts.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

RUNTIMES = Path(__file__).resolve().parents[1] / "runtimes"
LANGUAGES = ("python", "node", "java", "go", "cpp")


def main() -> int:
    source = RUNTIMES / "runner.py"
    for lang in LANGUAGES:
        target = RUNTIMES / lang / "runner.py"
        shutil.copyfile(source, target)
        print(f"synced {target.relative_to(RUNTIMES.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
