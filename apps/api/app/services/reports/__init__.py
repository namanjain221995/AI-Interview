"""Interview reports (ENGINE_DESIGN.md section 11).

* :func:`generate_reports` — the ``report.generate`` pipeline (deterministic
  assembly, candidate + recruiter narratives, versioned ``reports`` rows,
  HTML artifacts, release per policy, ``report.ready`` event).
* :func:`release_candidate_report` — reviewer release (after_review/never).
* :func:`get_released_candidate_report` — newest released candidate report
  (for the candidate portal).
* :func:`build_demo_report` — complete report documents of one session
  (imported lazily, so ``python -m app.services.reports.demo`` does not load
  the module twice).
"""

from typing import Any

from app.services.reports.queries import get_released_candidate_report
from app.services.reports.service import GeneratedReports, generate_reports, release_candidate_report

__all__ = [
    "GeneratedReports",
    "build_demo_report",
    "generate_reports",
    "get_released_candidate_report",
    "release_candidate_report",
]


def __getattr__(name: str) -> Any:
    if name == "build_demo_report":
        from app.services.reports.demo import build_demo_report

        return build_demo_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
