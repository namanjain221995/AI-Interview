"""Which replies are queued for evaluation (S9 and its review): flow replies never, real answers always."""

from __future__ import annotations

from app.domain.enums import TurnAction
from app.services.interview import understanding
from app.services.interview.contracts import CandidateTurn
from app.services.interview.engine import InterviewEngine

from .factories import ask, round_config, state, sufficient

ANSWER = (
    "I would put the service in private subnets behind an ALB, use IAM roles for service accounts "
    "and VPC endpoints for S3."
)


async def _evaluate(s, text: str) -> bool:
    q = s.current_question()
    heard = understanding.understand(
        text, question_text=q.text, awaiting_answer=True, questions_invited=s.candidate_questions_invited
    )
    turn = CandidateTurn(text=text, started_ms=0, ended_ms=9000)
    pending = await InterviewEngine()._accept_answer(s, turn, text, heard.special, None, persist=False)
    return pending.evaluate


def _invited():
    s = state(rc=round_config(), elapsed_s=12 * 60)
    for _ in range(5):
        ask(s, "kubernetes")
    sufficient(s, "kubernetes", "aws")
    ask(s, "kubernetes", action=TurnAction.INVITE_CANDIDATE_QUESTIONS)
    s.candidate_questions_invited = True
    return s


async def test_a_reply_to_the_invitation_is_never_evaluated() -> None:
    assert await _evaluate(_invited(), "What does the on-call rotation look like for this team?") is False


async def test_a_real_answer_after_the_interview_reopened_is_evaluated() -> None:
    s = _invited()
    ask(s, "aws", action=TurnAction.ASK_QUESTION)  # a late evaluation lowered sufficiency: questioning resumed
    assert await _evaluate(s, ANSWER) is True
