"""``build_demo_report(session_id)``: the complete report documents of one
session, for samples, demos and debugging.

    uv run python -m app.services.reports.demo <session_id> [--no-llm] [--persist] [--out report.json]
    uv run python -m app.services.reports.demo --latest

Without ``--persist`` nothing is written: scores are computed in memory and
the narratives come from the main model (or the deterministic fallback with
``--no-llm``). With ``--persist`` the full ``report.generate`` pipeline runs
(new report versions, HTML artifacts, release per policy).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.errors import NotFound
from app.db.models import InterviewSession, Report
from app.db.session import session_scope
from app.domain.enums import SessionStatus
from app.services.reports.assembly import assemble_report_data
from app.services.reports.narratives import generate_narratives
from app.services.reports.rendering import render_report_html
from app.services.reports.service import build_contents, generate_reports
from app.services.scoring.persistence import compute_session_scores


async def latest_completed_session_id() -> uuid.UUID | None:
    async with session_scope() as db:
        return await db.scalar(
            select(InterviewSession.id)
            .where(InterviewSession.status == SessionStatus.COMPLETED.value)
            .order_by(InterviewSession.ended_at.desc().nulls_last(), InterviewSession.created_at.desc())
            .limit(1)
        )


async def build_demo_report(
    session_id: uuid.UUID | str,
    *,
    use_llm: bool = True,
    persist: bool = False,
    include_html: bool = False,
) -> dict[str, Any]:
    """``{"session_id", "candidate", "recruiter", ...}`` for one session.

    ``persist=False`` (default) writes nothing; ``persist=True`` runs the
    real pipeline and returns the stored report contents.
    """
    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    if persist:
        result = await generate_reports(sid, reason="demo", use_model=use_llm)
        async with session_scope() as db:
            rows = (
                await db.scalars(select(Report).where(Report.id.in_(list(result.reports.values()))))
            ).all()
            contents = {r.type: r.content for r in rows}
        out: dict[str, Any] = {
            "session_id": str(sid),
            "persisted": True,
            "generation": result.as_dict(),
            "candidate": contents.get("candidate"),
            "recruiter": contents.get("recruiter"),
        }
    else:
        async with session_scope() as db:
            computation = await compute_session_scores(db, sid)
            data = await assemble_report_data(db, computation)
        narratives = await generate_narratives(data, use_model=use_llm)
        candidate, recruiter = build_contents(data, narratives)
        now = datetime.now(UTC).isoformat()
        candidate["generated_at"] = recruiter["generated_at"] = now
        out = {
            "session_id": str(sid),
            "persisted": False,
            "narrative_source": narratives.source,
            "narrative_latency_ms": narratives.latency_ms,
            "candidate": candidate,
            "recruiter": recruiter,
        }
    if include_html:
        meta = {"version": "demo", "report_id": "demo", "generated_at": datetime.now(UTC).isoformat()}
        out["html"] = {
            "candidate": render_report_html("candidate", out["candidate"], meta),
            "recruiter": render_report_html("recruiter", out["recruiter"], meta),
        }
    return out


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("session_id", nargs="?", help="Interview session id")
    parser.add_argument("--latest", action="store_true", help="Use the most recently completed session")
    parser.add_argument("--no-llm", action="store_true", help="Deterministic narratives (no model calls)")
    parser.add_argument(
        "--persist", action="store_true", help="Run the real pipeline (writes report rows/artifacts)"
    )
    parser.add_argument("--out", type=Path, help="Write the JSON here instead of stdout")
    parser.add_argument("--html-dir", type=Path, help="Also write candidate.html / recruiter.html here")
    args = parser.parse_args(argv)
    from app.core.config import get_settings
    from app.core.lifecycle import init_ai_registry, shutdown_ai_registry

    session_id = args.session_id
    if args.latest:
        latest = await latest_completed_session_id()
        if latest is None:
            print("No completed session found.", file=sys.stderr)
            return 2
        session_id = str(latest)
    if not session_id:
        parser.error("session_id or --latest is required")
    if not args.no_llm:
        settings = get_settings()
        await init_ai_registry(strict=False, is_test=settings.app_env == "test")
    try:
        report = await build_demo_report(
            session_id, use_llm=not args.no_llm, persist=args.persist, include_html=bool(args.html_dir)
        )
    except NotFound:
        print(f"Session {session_id} not found.", file=sys.stderr)
        return 2
    finally:
        if not args.no_llm:
            await shutdown_ai_registry()
    html = report.pop("html", None)
    if args.html_dir and html:
        args.html_dir.mkdir(parents=True, exist_ok=True)
        for name, body in html.items():
            (args.html_dir / f"{name}.html").write_text(body, encoding="utf-8")
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
