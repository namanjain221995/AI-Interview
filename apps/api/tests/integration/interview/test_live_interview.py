"""Live: a short interview against the real main model (opt-in: ``-m live``).

Shared production endpoints: one session, a handful of turns. Run with -s to
see each decision and its latency::

    uv run pytest tests/integration/interview/test_live_interview.py -m live -s
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.ai.ledger import MemoryLedger
from app.ai.registry import RealAIServices, set_ai
from app.core.config import get_settings
from app.domain.enums import SessionEndReason
from app.services.interview.adapters import discover_scoring
from app.services.interview.config import EngineConfig
from app.services.interview.contracts import (
    CandidateTurn,
    SessionClosing,
    SpeechDelta,
    UtteranceBegin,
    UtteranceMeta,
)
from app.services.interview.engine import InterviewEngine
from app.services.interview.evaluation import EvaluationService
from app.services.interview.sessions import SessionServiceImpl

from .harness import build_world, round_config_dict

pytestmark = [pytest.mark.live, pytest.mark.integration]

ANSWERS = [
    # (text, speculate) - speculation runs the quick assessment as the realtime runtime would during the pause
    (
        "Yeah, so at my last company I did a lot of cost optimization on AWS and we saved a lot of money overall.",
        True,
    ),
    (
        "Sure. Our monthly AWS bill was about 180 thousand dollars. I moved the stateless services on EKS to spot "
        "instances with Karpenter, right-sized node groups from metrics, and bought a three-year savings plan for the "
        "baseline. Over five months we got to about 115 thousand a month; spot was roughly half of the saving.",
        False,
    ),
    ("Sorry, could you repeat the question?", False),
    ("Hmm, I honestly don't know that one.", False),
    ("Before we go on, what does a typical day look like for the platform team?", False),
]


def _print(label: str, value: Any) -> None:
    print(f"[live-interview] {label}: {value}")


async def _run_turn(
    engine: InterviewEngine, sid: str, text: str, ms: int
) -> tuple[list[Any], float | None, float]:
    started = time.perf_counter()
    first: float | None = None
    events: list[Any] = []
    async for event in engine.handle_candidate_turn(
        sid, CandidateTurn(text=text, started_ms=ms, ended_ms=ms + 8000)
    ):
        if (
            first is None
            and isinstance(event, SpeechDelta | UtteranceBegin)
            and (isinstance(event, SpeechDelta) or event.text)
        ):
            first = (time.perf_counter() - started) * 1000
        events.append(event)
    return events, first, (time.perf_counter() - started) * 1000


async def test_short_live_interview(db_session, redis, storage) -> None:
    services = RealAIServices(get_settings(), redis_client=None, ledger=MemoryLedger())
    previous = set_ai(services)
    try:
        config = round_config_dict(
            round_type="combined",
            name="Combined AI Round",
            competencies=[
                {
                    "key": "kubernetes",
                    "name": "Kubernetes",
                    "weight": 1.5,
                    "focus_areas": ["availability", "scaling", "troubleshooting"],
                },
                {"key": "aws", "name": "AWS", "weight": 1.2, "focus_areas": ["networking", "iam", "cost"]},
                {
                    "key": "cloud_cost_optimization",
                    "name": "Cloud Cost Optimization",
                    "weight": 0.8,
                    "focus_areas": ["measurement"],
                },
                {"key": "ownership", "name": "Ownership", "weight": 0.6, "focus_areas": ["accountability"]},
            ],
        )
        world = await build_world(services, config=config)  # bank rows embedded with the real embedding model
        sessions = SessionServiceImpl(
            config=EngineConfig(closing_reserve_s=75, default_interviewer_name="Maya")
        )
        from app.db.session import session_scope

        async with session_scope() as db:
            sid = str((await sessions.create_or_resume(db, interview_id=world.interview_id)).id)
        engine = InterviewEngine(
            ai=services,
            scoring=discover_scoring(),
            config=EngineConfig(closing_reserve_s=75, default_interviewer_name="Maya"),
        )
        t0 = time.perf_counter()
        await engine.prepare_session(sid)
        state = await engine.load_state(sid)
        _print(
            "prepare (plan + opening + first question)",
            f"{(time.perf_counter() - t0) * 1000:.0f} ms, plan={state.plan_source}",
        )
        _print("plan segments", [(s.key, s.time_budget_minutes) for s in state.plan.segments])
        _print("opening", state.opening_text)
        t0 = time.perf_counter()
        start_events = [e async for e in engine.start(sid)]
        _print("start -> first event", f"{(time.perf_counter() - t0) * 1000:.0f} ms")
        for meta in [e for e in start_events if isinstance(e, UtteranceMeta)]:
            _print("interviewer", f"[{meta.decision.get('action')}] {meta.full_text}")
            await engine.mark_utterance_delivered(sid, meta.utterance_id, spoken_text=meta.full_text)
        latencies: list[float] = []
        ms = 10_000
        for text, speculate in ANSWERS:
            _print("candidate", text)
            if speculate:
                t_spec = time.perf_counter()
                await engine.on_partial_transcript(sid, text, silence_started=True)
                await engine.wait_speculative(sid, timeout=10)
                _print(
                    "speculative prep (quick assessment)", f"{(time.perf_counter() - t_spec) * 1000:.0f} ms"
                )
            events, first_ms, total_ms = await _run_turn(engine, sid, text, ms)
            ms += 20_000
            for meta in [e for e in events if isinstance(e, UtteranceMeta)]:
                d = meta.decision
                llm_first = (d.get("latency") or {}).get("first_speech_ms")
                _print(
                    "interviewer",
                    f"[{d.get('action')} | {d.get('competency_key')} | {d.get('difficulty')} | quality={d.get('answer_quality')}] "
                    f"{meta.full_text}  (first speech {first_ms:.0f} ms from turn start, LLM first speech {llm_first} ms, "
                    f"turn total {total_ms:.0f} ms, deviations={d.get('deviations')})"
                    if first_ms is not None
                    else f"[{d.get('action')}] {meta.full_text} (total {total_ms:.0f} ms)",
                )
                await engine.mark_utterance_delivered(sid, meta.utterance_id, spoken_text=meta.full_text)
            if first_ms is not None and any(isinstance(e, SpeechDelta) for e in events):
                latencies.append(first_ms)
        st = await engine.load_state(sid)
        if st.pending_evaluations:
            t_eval = time.perf_counter()
            result = await EvaluationService(ai=services).evaluate(sid, st.pending_evaluations[0])
            _print(
                "deep evaluation of the first answer",
                f"{result} in {(time.perf_counter() - t_eval) * 1000:.0f} ms",
            )
        closing = [e async for e in engine.end_session(sid, SessionEndReason.CANDIDATE_ENDED)]
        assert isinstance(closing[-1], SessionClosing)
        _print("closing", [e.full_text for e in closing if isinstance(e, UtteranceMeta)])
        if latencies:
            latencies.sort()
            _print("first-speech latency per streamed turn (ms)", [round(x) for x in latencies])
        assert latencies, "at least one streamed decision"
        await engine.drain_background(10)
        await engine.aclose()
    finally:
        set_ai(previous)
        await services.aclose()
