"""Intake HTML artifact (INTAKE_INTERVIEW.md 9.1): CSP, autoescape, self-contained, every section rendered."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from html import unescape

from app.services.intake.report import assemble_intake_report, report_meta
from app.services.intake.report_html import render_intake_html
from app.services.reports.rendering import CSP

from .eval_fixtures import evaluate_demo
from .test_report_assembly import _report_inputs

META = report_meta(uuid.uuid4(), 3, datetime(2026, 10, 9, 12, 0, tzinfo=UTC))


def _html(variant: str = "complete") -> str:
    content = assemble_intake_report(_report_inputs(evaluate_demo(variant)))
    return render_intake_html(content, META)


def test_csp_and_no_external_resources() -> None:
    html = _html()
    assert f'content="{CSP}"' in unescape(html)
    assert not re.search(r"<script", html, re.I)
    assert not re.search(r"<link\b", html, re.I)
    assert not re.search(r"(src|href)\s*=\s*['\"]?https?://", html, re.I)
    assert "Report v3" in html


def test_every_section_is_rendered() -> None:
    html = _html()
    for heading in (
        "A. Candidate summary", "B. Overall", "C. Competencies", "D. Résumé verification", "E. Technical ability",
        "F. Technical reading", "G. Structured answer", "H. Trainability", "I. Integrity observations",
        "J. Training plan", "K. Human review",
    ):  # fmt: skip
        assert heading in html
    assert "AI recommendation" in html and "A human reviewer makes the decision." in html
    assert "pronunciation: not assessed" in html
    assert "0.4 × Drill A + 0.6 × Drill B" in html
    assert "Signals are observations, not determinations." in html


def test_candidate_text_is_escaped() -> None:
    evaluated = evaluate_demo()
    inputs = _report_inputs(evaluated)
    inputs.inputs.activities["read_relay"].transcript_final = "<script>alert(1)</script> overfitting"
    html = render_intake_html(assemble_intake_report(inputs), META)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_insufficient_variant_renders() -> None:
    html = _html("incomplete")
    assert "Insufficient evidence" in html and "Partial total" in html
    assert "The speaking exercise was not completed." in html
