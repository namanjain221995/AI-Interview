"""HTML report artifacts (Jinja2, autoescaped, self-contained).

The generated files embed all CSS, load nothing from the network and carry
a restrictive Content-Security-Policy meta tag, so they are safe to archive,
download and print ("Save as PDF"). Model-written text is rendered in
neutral wording (:mod:`app.services.reports.wording`) even when the content
was stored before a wording rule existed; the content passed in is not
modified.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.services.reports.wording import neutral_candidate_document, neutral_recruiter_document
from app.services.scoring.topics import humanize_topic

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def _num(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "–"


def _pct(value: Any, scale: float = 100.0) -> str:
    """Bar width (0-100) for a value on ``scale``."""
    try:
        return f"{max(0.0, min(100.0, float(value) / scale * 100.0)):.1f}"
    except (TypeError, ValueError):
        return "0"


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "–"


def _label(value: Any) -> str:
    """Sentence-case label with acronyms kept ("iam" -> "IAM", "no_answer" -> "No answer")."""
    text = humanize_topic(str(value or ""))
    return text[:1].upper() + text[1:] if text else ""


def _topic(value: Any) -> str:
    return humanize_topic(str(value or ""))


def _duration(seconds: Any) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "–"
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def _date(value: Any) -> str:
    if not value:
        return "–"
    try:
        dt = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except ValueError:
        return str(value)
    return dt.strftime("%d %b %Y, %H:%M UTC")


def _band(score: Any, scale: float = 100.0) -> str:
    """CSS class for a score band."""
    try:
        ratio = float(score) / scale
    except (TypeError, ValueError):
        return "band-none"
    if ratio >= 0.75:
        return "band-high"
    if ratio >= 0.6:
        return "band-mid"
    if ratio >= 0.45:
        return "band-low"
    return "band-weak"


@lru_cache(maxsize=1)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(
        {
            "num": _num,
            "pct": _pct,
            "percent": _percent,
            "label": _label,
            "duration": _duration,
            "date": _date,
            "band": _band,
            "topic": _topic,
        }
    )
    env.globals["CSP"] = CSP
    return env


def _context(content: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    evidence = {e["id"]: e for e in content.get("evidence", [])}
    questions = {q["id"]: q for q in content.get("questions", [])}
    return {"c": content, "meta": meta, "evidence_by_id": evidence, "questions_by_id": questions}


def render_recruiter_html(content: dict[str, Any], meta: dict[str, Any]) -> str:
    content = neutral_recruiter_document(content)
    return environment().get_template("recruiter.html.j2").render(**_context(content, meta))


def render_candidate_html(content: dict[str, Any], meta: dict[str, Any]) -> str:
    content = neutral_candidate_document(content)
    return environment().get_template("candidate.html.j2").render(**_context(content, meta))


def render_report_html(report_type: str, content: dict[str, Any], meta: dict[str, Any]) -> str:
    if report_type == "candidate":
        return render_candidate_html(content, meta)
    return render_recruiter_html(content, meta)
