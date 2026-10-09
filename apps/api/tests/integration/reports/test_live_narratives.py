"""Live report generation against the real main model (opt-in: ``-m live``).

Two structured calls (candidate + recruiter narratives) for one realistic
session. Run with ``-s`` to see latencies and samples::

    uv run pytest tests/integration/reports/test_live_narratives.py -m live -s
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.db.models import Report
from app.db.session import session_scope
from app.services.reports import generate_reports
from app.services.reports.narratives import CANDIDATE_BANNED

pytestmark = [pytest.mark.live, pytest.mark.integration]


async def test_live_report_generation(session_factory, storage, redis) -> None:
    fx = await session_factory()
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    previous = set_ai(services)
    try:
        result = await generate_reports(fx.session_id, job_id="live", allow_fallback=False)
    finally:
        set_ai(previous)
        await services.aclose()
    async with session_scope() as db:
        rows = {
            r.type: r
            for r in (await db.scalars(select(Report).where(Report.session_id == fx.session_id))).all()
        }
    cand, rec = rows["candidate"].content, rows["recruiter"].content
    print(f"\n[live] total pipeline: {result.duration_ms:.0f} ms; sources: {result.narrative_source}")
    print(
        f"[live] candidate validation: {cand.get('narrative_source')}; recruiter validation: {rec['narrative_validation']}"
    )
    print("[live] candidate summary:", cand["narrative"]["overall_summary"])
    print("[live] recruiter executive summary:", rec["narrative"]["executive_summary"])
    print(
        "[live] recruiter suggested:",
        rec["narrative"]["suggested_recommendation"],
        "vs",
        rec["recommendation"]["value"],
    )
    print("[live] candidate validation stats:", rec["candidate_narrative_validation"])
    print("[live] strengths:", json.dumps(cand["narrative"]["technical_strengths"])[:2000])
    print("[live] improve:", json.dumps(cand["narrative"]["areas_to_improve"])[:1500])
    print("[live] followup:", rec["narrative"]["areas_for_human_followup"])
    print("[live] highlight:", (cand["narrative"]["strong_answers"] or [{}])[0])
    assert result.narrative_source == {"candidate": "model", "recruiter": "model"}
    evidence_ids = {str(e) for e in fx.evidence_ids}
    cited = [eid for p in cand["narrative"]["technical_strengths"] for eid in p["evidence_ids"]]
    assert set(cited) <= {e["id"] for e in rec["evidence"]}
    assert cited, "the model should cite evidence for strengths"
    assert set(cited) & evidence_ids or cited
    text = json.dumps(cand["narrative"])
    assert not CANDIDATE_BANNED.search(text)
    assert rec["narrative"]["competency_findings"]
