"""Replay of the owner's live intake session f1dc3a1b (2026-10-10) through the intake conductor (mock AI): audio
checks and questions about the interview are answered and never consume the pending item, a mid-sentence fragment
gets a soft nudge (no new question), unclear one-word answers get a deterministic clarification that credits the
candidate with nothing they did not say, and real answers move the plan on as before."""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Answer, Transcript
from app.db.session import session_scope
from app.domain.enums import ModelTask, SessionStatus
from app.services.intake import prompts as P
from app.services.interview import understanding as U
from app.services.interview.contracts import TimerEvent, UtteranceMeta

from . import live_helpers
from .live_helpers import Driver, build_intake_world, collect, create_session, item_keys, metas, texts

intake_env = live_helpers.intake_env  # the shared fixture

pytestmark = pytest.mark.integration

MECHANICS_Q = (
    "Could you share a specific technical detail you observed about how the system handled retries or "
    "deduplication?"
)
INVENTED = re.compile(r"\b(?:you meant|what you were about to say|you were saying|about to say)\b", re.I)


async def start(env, **world_kwargs):
    world = await build_intake_world(**world_kwargs)
    sid = await create_session(env, world)
    engine = env["engine"]
    await engine.prepare_session(sid)
    events = await collect(engine.start(sid))
    return world, sid, events


def only_meta(events: list) -> UtteranceMeta:
    found = metas(events)
    assert len(found) == 1, [m.full_text for m in found]
    return found[0]


async def answer_rows(sid: str) -> list[Answer]:
    async with session_scope() as db:
        rows = await db.execute(
            select(Answer).where(Answer.session_id == uuid.UUID(sid)).order_by(Answer.sequence)
        )
        return list(rows.scalars().all())


async def candidate_lines(sid: str) -> list[str]:
    async with session_scope() as db:
        rows = await db.execute(
            select(Transcript)
            .where(Transcript.session_id == uuid.UUID(sid), Transcript.speaker == "candidate")
            .order_by(Transcript.seq)
        )
        return [r.text for r in rows.scalars().all()]


def model_calls(env) -> int:
    return len(env["mock_ai"].llm.calls_for(ModelTask.TURN_DECISION))


async def pending(env, sid: str) -> tuple[str | None, str]:
    state = await env["engine"].load_state(sid)
    ik = state.intake
    item = ik.current()
    return ik.current_item, item.status if item is not None else ""


async def test_live_transcript_replay_keeps_items_pending_until_a_real_answer(intake_env) -> None:  # noqa: PLR0915
    env = intake_env
    env["model"].speech.update(
        {
            "resume.C1.ownership": "What did you personally build in that project, versus the team?",
            "resume.C1.mechanics": MECHANICS_Q,
        }
    )
    _, sid, events = await start(env)
    assert texts(events)[1] == P.ITEM_TEMPLATES["greeting.intro"]
    driver = Driver(env, sid)
    calls = model_calls(env)

    # 1. "Okay, so can you hear?" - was swallowed as the self-introduction.
    meta = only_meta(await driver.say("Okay, so can you hear?", seconds=4, typed=False))
    assert meta.decision["flow"] == "audio_check" and meta.question_id is None
    assert meta.full_text.split(". ")[0] + "." in U.AUDIO_ACKS
    assert meta.full_text.endswith(P.REINVITE["greeting.intro"]) and "90 seconds" not in meta.full_text
    assert await pending(env, sid) == ("greeting.intro", "asked")

    # 2. "Can you hear me?" - was swallowed as the project answer.
    meta = only_meta(await driver.say("Can you hear me?", seconds=3, typed=False))
    assert meta.decision["flow"] == "audio_check"
    assert meta.full_text.endswith(P.REINVITE["greeting.intro"])
    assert await pending(env, sid) == ("greeting.intro", "asked")

    # 3. "But first answer me, like, am I able to do it or not?" - was swallowed as the first résumé answer.
    meta = only_meta(
        await driver.say("But first answer me, like, am I able to do it or not?", seconds=4, typed=False)
    )
    assert meta.decision["flow"] == "process_question"
    assert meta.full_text == f"{U.PROCESS_REPLIES['ability']} {P.REINVITE['greeting.intro']}"
    assert await pending(env, sid) == ("greeting.intro", "asked")
    assert await answer_rows(sid) == [] and model_calls(env) == calls  # nothing consumed, no model call

    # 4. The real introduction moves the plan on.
    events = await driver.say(
        "I'm Heet, I work at TechSara as an AI engineer and I build retrieval chatbots on a local LLM.",
        seconds=40,
        typed=False,
    )
    assert item_keys(events)[-1] == "greeting.project"
    assert texts(events)[-1] == P.ITEM_TEMPLATES["greeting.project"]
    rows = await answer_rows(sid)
    assert [r.transcript_text for r in rows] == [
        "I'm Heet, I work at TechSara as an AI engineer and I build retrieval chatbots on a local LLM."
    ]

    # 5. "Okay, so am I properly audible?" - only the 4th check was answered live, after three items were lost.
    meta = only_meta(await driver.say("Okay, so am I properly audible?", seconds=4, typed=False))
    assert meta.decision["flow"] == "audio_check"
    assert meta.full_text.endswith(P.REINVITE["greeting.project"])
    assert await pending(env, sid) == ("greeting.project", "asked")

    # 6. "Can you repeat the question?" - repeated, not consumed.
    events = await driver.say("Can you repeat the question?", seconds=3, typed=False)
    assert texts(events)[-1].endswith(P.ITEM_TEMPLATES["greeting.project"])
    assert await pending(env, sid) == ("greeting.project", "asked")

    # 7. The project answer: the résumé stage starts with a model-phrased item.
    events = await driver.say(
        "I built a retrieval chatbot on a local LLM which can answer based on our organization's specific data "
        "set, and we deploy it with a CI/CD pipeline on AWS with another team member.",
        seconds=50,
        typed=False,
    )
    assert item_keys(events)[-1] == "resume.C1.ownership"
    calls = model_calls(env)

    # 8. "so for that" (a pause mid-sentence) - was cut off with a new clarification question.
    meta = only_meta(await driver.say("so for that", seconds=4, typed=False))
    assert meta.decision["flow"] == "incomplete" and meta.full_text in U.NUDGES
    assert "?" not in meta.full_text and meta.question_id is None
    assert await pending(env, sid) == ("resume.C1.ownership", "asked")

    # 9. "Yeah, that's it." - a deterministic clarification, never a model guess.
    meta = only_meta(await driver.say("Yeah, that's it.", seconds=3, typed=False))
    assert meta.full_text.startswith(P.UNCLEAR_BRIEF_LEAD)
    assert meta.full_text.endswith("What did you personally build in that project, versus the team?")
    assert not INVENTED.search(meta.full_text) and model_calls(env) == calls
    assert await pending(env, sid) == ("resume.C1.ownership", "asked")

    # 10. A real answer: the mechanics rung (the résumé claim's retries / deduplication topic).
    events = await driver.say(
        "I researched what can be improved, suggested it to him, and we both worked on deploying it to AWS.",
        seconds=30,
        typed=False,
    )
    assert item_keys(events)[-1] == "resume.C1.mechanics" and texts(events)[-1].endswith(MECHANICS_Q)
    calls = model_calls(env)

    # 11. "Exactly." - live, the model asked "what you meant by exactly regarding the retries or deduplication".
    meta = only_meta(await driver.say("Exactly.", seconds=2, typed=False))
    assert meta.full_text == f"{P.UNCLEAR_BRIEF_LEAD} {MECHANICS_Q}"
    assert not INVENTED.search(meta.full_text) and "exactly" not in meta.full_text.lower()
    assert model_calls(env) == calls  # the clarification of an unclear answer never goes to the model
    assert await pending(env, sid) == ("resume.C1.mechanics", "asked")

    # 12. "implemented that scenario." - an answer: the plan moves on.
    events = await driver.say("implemented that scenario.", seconds=3, typed=False)
    assert item_keys(events)[-1] == "resume.C1.stress"

    # The transcript keeps every candidate line; only real answers (and the answers after a clarification)
    # became answer rows.
    lines = await candidate_lines(sid)
    for line in (
        "Okay, so can you hear?",
        "Can you hear me?",
        "so for that",
        "Okay, so am I properly audible?",
    ):
        assert line in lines
    answers = [r.transcript_text for r in await answer_rows(sid)]
    assert not any("hear" in a.lower() or a == "so for that" for a in answers), answers
    assert answers[-2:] == ["Exactly.", "implemented that scenario."]


async def test_process_questions_readiness_and_job_questions_before_the_last_part(intake_env) -> None:
    env = intake_env
    _, sid, _ = await start(env)
    driver = Driver(env, sid)

    meta = only_meta(await driver.say("Should I start?", seconds=2, typed=False))
    assert meta.full_text == f"Yes, please go ahead. {P.REINVITE['greeting.intro']}"
    meta = only_meta(await driver.say("How long should I speak?", seconds=2, typed=False))
    assert meta.full_text.startswith("About a minute and a half is plenty")  # the intro's 90 s soft limit
    meta = only_meta(await driver.say("Let me think.", seconds=2, typed=False))
    assert meta.full_text == P.WAIT and meta.decision["flow"] == "thinking"
    meta = only_meta(await driver.say("What are the next steps?", seconds=2, typed=False))
    assert meta.full_text.startswith(U.PROCESS_REPLIES["next_steps"])  # approved wording, answered now
    # A job / program question is answered now, like a human interviewer would (approved information only), and the
    # question is asked again; the item stays pending.
    meta = only_meta(await driver.say("What is the salary for this program?", seconds=2, typed=False))
    assert meta.full_text == live_helpers.ASIDE_SPEECH
    assert meta.decision["action"] == "answer_candidate_question" and meta.decision["intake"]["move"] == "answer_question"
    assert await pending(env, sid) == ("greeting.intro", "asked")
    # "Okay" twice gets "go ahead"; the third time the question is rephrased (ordinary unclear path).
    first = only_meta(await driver.say("Okay.", seconds=2, typed=False))
    assert first.full_text in U.READY_REPLIES and first.decision["flow"] == "readiness"
    second = only_meta(await driver.say("Okay.", seconds=2, typed=False))
    assert second.full_text.startswith(U.READY_AGAIN)
    third = only_meta(await driver.say("Okay.", seconds=2, typed=False))
    assert "flow" not in third.decision and third.full_text.startswith(P.UNCLEAR_BRIEF_LEAD)
    assert await pending(env, sid) == ("greeting.intro", "asked")
    # Flow turns never write answer rows; the deferred job question and the third "okay" went the ordinary way.
    assert [r.transcript_text for r in await answer_rows(sid)] == [
        "What is the salary for this program?",
        "Okay.",
    ]


async def test_a_question_to_the_interviewer_is_answered_and_the_question_asked_again(intake_env) -> None:
    """Owner, 2026-10-17: "What's your name?" mid-interview was taken as the answer and the interview moved on."""
    env = intake_env
    _, sid, _ = await start(env)
    driver = Driver(env, sid)
    calls = model_calls(env)

    meta = only_meta(await driver.say("Sorry, what's your name?", seconds=2, typed=False))
    assert model_calls(env) == calls + 1  # the model answers, not a fixed line
    assert meta.full_text == live_helpers.ASIDE_SPEECH
    assert meta.decision["action"] == "answer_candidate_question"
    assert meta.decision["intake"]["item_key"] == "greeting.intro"
    prompt = env["model"].prompts[-1]
    assert "answer_question (item_key greeting.intro)" in prompt and "friendly human interviewer" in prompt
    assert "The waiting question" in prompt  # it asks the pending question again
    assert "APPROVED PROGRAM INFORMATION" in prompt and "twelve weeks" in prompt
    assert await pending(env, sid) == ("greeting.intro", "asked")

    # The real answer afterwards is the self-introduction, as before.
    events = await driver.say(
        "I'm Priya, a data analyst at Infosys for two years, mostly SQL and Python dashboards for sales teams.",
        seconds=20, typed=False,
    )  # fmt: skip
    assert metas(events)
    assert (await pending(env, sid))[0] != "greeting.intro"
    assert [r.transcript_text for r in await answer_rows(sid)][-1].startswith("I'm Priya")


async def test_an_unapproved_number_in_a_mid_interview_answer_is_flagged(intake_env) -> None:
    env = intake_env
    env["model"].speech["aside"] = "The stipend is fifty thousand rupees a month. So, tell me about yourself?"
    _, sid, _ = await start(env)
    meta = only_meta(await Driver(env, sid).say("Is there a stipend for this program?", seconds=2, typed=False))
    assert "intake:unapproved_money" in meta.decision["intake"]["deviations"]
    assert (await env["engine"].load_state(sid)).intake.template_next  # the next model utterance uses its template


async def test_an_audio_check_while_paused_is_not_kept_as_the_answer(intake_env) -> None:
    env = intake_env
    _, sid, _ = await start(env)
    engine = env["engine"]
    await collect(engine.handle_timer(sid, TimerEvent(kind="disconnect", elapsed_s=0)))
    driver = Driver(env, sid)
    assert await driver.say("Hello, can you hear me?", seconds=3, typed=False) == []
    state = await engine.load_state(sid)
    assert state.status is SessionStatus.PAUSED and not state.intake.pending_candidate_turn
    assert await answer_rows(sid) == []
    resumed = await collect(engine.resume(sid, reconnect=True))
    assert texts(resumed)[-1].endswith(P.ITEM_TEMPLATES["greeting.intro"])  # re-asked, not consumed


async def test_an_audio_check_over_an_activity_instruction_is_answered_before_re_explaining(
    intake_env,
) -> None:
    from app.services.interview.contracts import ActivityStarted

    from .live_helpers import first

    env = intake_env
    _, sid, _ = await start(env)
    await env["engine"].intake_jump(sid, "reading")
    events = await collect(env["engine"].resume(sid, reconnect=False))
    started = first(events, ActivityStarted)
    assert started is not None
    driver = Driver(env, sid)
    events = await driver.say("Sorry, can you hear me?", seconds=3, typed=False)
    again = first(events, ActivityStarted)
    assert again is not None and again.activity["activity_id"] == started.activity["activity_id"]
    assert texts(events)[-1].startswith(P.AUDIO_REEXPLAIN_LEAD)
    assert await answer_rows(sid) == []  # transcript only, never the reading item's answer
