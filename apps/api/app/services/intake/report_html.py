"""Self-contained HTML artifact of the intake report (design contract section 9.1).

Own Jinja environment (autoescape, ``StrictUndefined``) whose loader sees ``app/services/intake/templates`` and the
generic report templates (``intake_report.html.j2`` extends ``base.html.j2``): inline CSS, the same restrictive
Content-Security-Policy meta tag as :mod:`app.services.reports.rendering`, nothing loaded from the network. The
content is passed through :func:`app.services.intake.report.neutral_intake_document` again at render time (stored
content is never modified). PDF export = the browser's "Save as PDF" (no server-side PDF engine).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from app.services.intake.report import neutral_intake_document
from app.services.reports import rendering
from app.services.reports.rendering import CSP, TEMPLATES_DIR

INTAKE_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_STATUS_PILLS = {
    "covered": "pos",
    "scored": "pos",
    "completed": "pos",
    "hands_on": "pos",
    "correct": "pos",
    "supported": "pos",
    "partial": "warn",
    "partially": "warn",
    "partially_correct": "warn",
    "team_level": "warn",
    "conceptual": "warn",
    "uncertain": "warn",
    "incomplete": "warn",
    "insufficient": "warn",
    "missing": "neg",
    "unsupported": "neg",
    "inconsistent": "neg",
    "incorrect": "neg",
    "not_supported": "neg",
    "important": "neg",
    "attention": "warn",
    "info": "accent",
}


def _pill(value: Any) -> str:
    return _STATUS_PILLS.get(str(value or ""), "")


def _score5(value: Any) -> str:
    """A 1-5 score string for display ("3.6", "4"), en dash when missing."""
    if value in (None, ""):
        return "–"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.1f}".rstrip("0").rstrip(".") if number != int(number) else f"{int(number)}"


def _total(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "–"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


_SOURCES = {
    "final_pass_live": "final pass",
    "final_pass_worker": "final pass, re-run from the recording",
    "live_segments": "live recognition segments",
    "typed": "typed",
}


def _source_label(value: Any) -> str:
    return _SOURCES.get(str(value or ""), "unknown")


def _ms(value: Any) -> str:
    try:
        total = int(value) // 1000
    except (TypeError, ValueError):
        return "–"
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


@lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=ChoiceLoader(
            [FileSystemLoader(str(INTAKE_TEMPLATES_DIR)), FileSystemLoader(str(TEMPLATES_DIR))]
        ),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    base = rendering.environment()
    env.filters.update(
        {name: base.filters[name] for name in ("num", "pct", "percent", "label", "duration", "date")}
    )
    env.filters.update(
        {"pill": _pill, "score5": _score5, "total": _total, "ms": _ms, "source_label": _source_label}
    )
    env.globals["CSP"] = CSP
    return env


def render_intake_html(content: dict[str, Any], meta: dict[str, Any]) -> str:
    """HTML of one intake report version (``meta``: version, report_id, generated_at)."""
    document = neutral_intake_document(content)
    return environment().get_template("intake_report.html.j2").render(c=document, meta=meta)


__all__ = ["INTAKE_TEMPLATES_DIR", "environment", "render_intake_html"]
