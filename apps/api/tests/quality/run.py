"""CLI for the interview-quality harness (wraps the live pytest test, which
owns the throwaway database / Redis / storage fixtures).

    cd apps/api
    uv run python -m tests.quality.run                       # all eight personas, judge on
    uv run python -m tests.quality.run --personas scoring    # strong, mid, vague, weak
    uv run python -m tests.quality.run --personas strong,injector --no-judge --max-turns 10
    uv run python -m tests.quality.run --personas scoring --reports --out /tmp/quality
    uv run python -m tests.quality.run --merge /tmp/quality/quality-*.json --tag merged

The models are shared with production: run the smallest persona set that answers
the question at hand. Results (JSON scorecard + transcripts) go to ``--out``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


async def _rejudge(card) -> None:  # noqa: ANN001 - Scorecard
    """Judge saved transcripts again (one main-model call per persona; no database needed)."""
    from app.ai.ledger import MemoryLedger
    from app.ai.registry import RealAIServices
    from app.core.config import get_settings

    from .harness import job_facts
    from .judge import judge_run
    from .personas import PERSONAS, judge_resume_digest

    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    try:
        for item in card.personas:
            persona = PERSONAS[item.run.persona]
            try:
                item.verdict = await judge_run(
                    services,
                    item.run,
                    job_facts=job_facts(),
                    resume=judge_resume_digest(persona.profile_doc()),
                )
                item.judge_error = None
            except Exception as exc:  # noqa: BLE001
                item.judge_error = f"{type(exc).__name__}: {exc}"[:300]
            print(f"[rejudge] {item.run.persona}: judge {item.judge}")
    finally:
        await services.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--personas", default="all", help="comma list, 'all' or 'scoring'")
    parser.add_argument(
        "--max-turns", type=int, default=12, help="safety cap on candidate turns per interview"
    )
    parser.add_argument(
        "--no-judge", action="store_true", help="skip the LLM judge (deterministic checks only)"
    )
    parser.add_argument("--reports", action="store_true", help="also generate report narratives per session")
    parser.add_argument("--no-speculate", action="store_true", help="do not emulate the quick assessment")
    parser.add_argument("--out", type=Path, default=None, help="output directory for results")
    parser.add_argument("--tag", default="run", help="label used in output file names")
    parser.add_argument(
        "--capture", type=Path, default=None, help="append every turn-decision prompt to this JSONL file"
    )
    parser.add_argument("--no-assert", action="store_true", help="exit 0 even when the scorecard fails")
    parser.add_argument(
        "--merge", nargs="+", type=Path, help="merge result JSON files into one scorecard (no model calls)"
    )
    parser.add_argument(
        "--rescore", action="store_true", help="with --merge: re-run the deterministic checks"
    )
    parser.add_argument(
        "--rejudge", action="store_true", help="with --merge: re-run the LLM judge on the saved transcripts"
    )
    args = parser.parse_args(argv)
    if args.merge:
        from .scorecard import load_results, write_outputs

        os.environ["QUALITY_TAG"] = args.tag
        card = load_results(args.merge, rescore=args.rescore)
        if args.rejudge:
            import asyncio

            asyncio.run(_rejudge(card))
        json_path, text_path = write_outputs(card, args.out or args.merge[0].parent)
        print(card.summary())
        print(f"\nresults: {json_path}\ntranscripts: {text_path}")
        return 0 if (card.passed or args.no_assert) else 1

    os.environ["QUALITY_PERSONAS"] = args.personas
    os.environ["QUALITY_MAX_TURNS"] = str(args.max_turns)
    os.environ["QUALITY_JUDGE"] = "0" if args.no_judge else "1"
    os.environ["QUALITY_REPORTS"] = "1" if args.reports else "0"
    os.environ["QUALITY_SPECULATE"] = "0" if args.no_speculate else "1"
    os.environ["QUALITY_ASSERT"] = "0" if args.no_assert else "1"
    os.environ["QUALITY_TAG"] = args.tag
    if args.out is not None:
        os.environ["QUALITY_OUT"] = str(args.out)
    if args.capture is not None:
        os.environ["QUALITY_CAPTURE"] = str(args.capture.resolve())

    import pytest

    test = Path(__file__).with_name("test_interview_quality.py")
    return int(pytest.main([str(test), "-m", "live", "-s", "-q", "-p", "no:cacheprovider"]))


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
