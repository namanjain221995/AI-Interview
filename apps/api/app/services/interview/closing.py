"""Closing utterances.

At a natural end (time, sufficient evidence, stopping condition) the engine
streams a short model-written closing (``closing_messages``) that may briefly
acknowledge the candidate's last words; templates are used when the model is
unavailable and for abrupt ends (candidate ended, max duration, policy
termination, technical failure). Closings never evaluate the candidate."""

from __future__ import annotations

import hashlib

from app.domain.enums import CandidateReportMode, SessionEndReason
from app.services.interview.prompts import CLOSING
from app.services.interview.state import InterviewState

_REASON_TEXT = {
    SessionEndReason.SUFFICIENT_EVIDENCE: "we have covered everything planned for this round",
    SessionEndReason.TIME_LIMIT: "the time for this round is up",
    SessionEndReason.STOPPING_CONDITION: "this round has reached its end",
    SessionEndReason.CANDIDATE_ENDED: "the candidate chose to end the interview",
    SessionEndReason.DISCONNECT_TIMEOUT: "the connection was lost",
    SessionEndReason.POLICY_TERMINATION: "the session is ending under the interview policy",
    SessionEndReason.TECHNICAL_FAILURE: "a technical problem ended the session",
    SessionEndReason.CANCELLED: "the interview was cancelled",
}

#: End reasons where a spoken closing makes sense at all.
SPOKEN_REASONS = frozenset(
    {
        SessionEndReason.SUFFICIENT_EVIDENCE,
        SessionEndReason.TIME_LIMIT,
        SessionEndReason.STOPPING_CONDITION,
        SessionEndReason.CANDIDATE_ENDED,
        SessionEndReason.POLICY_TERMINATION,
        SessionEndReason.TECHNICAL_FAILURE,
    }
)
#: Natural ends where the closing is model-written (others use templates).
GENERATED_REASONS = frozenset(
    {SessionEndReason.SUFFICIENT_EVIDENCE, SessionEndReason.TIME_LIMIT, SessionEndReason.STOPPING_CONDITION}
)


def _pick(options: list[str], seed: str) -> str:
    return options[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)]  # noqa: S324 - variation only


def report_note(state: InterviewState) -> str | None:
    if state.candidate_report_mode == CandidateReportMode.IMMEDIATE.value:
        return "you'll be able to see a short feedback summary in the portal shortly"
    return None


def template_closing(state: InterviewState, reason: SessionEndReason) -> str:
    first = state.candidate_first_name
    name = f", {first}" if first else ""
    note = report_note(state)
    next_steps = "The hiring team will review our conversation and follow up with you about next steps."
    if note:
        next_steps += " You'll also be able to see a short feedback summary in the portal shortly."
    if reason == SessionEndReason.TIME_LIMIT:
        opener = _pick(
            [
                f"That brings us to the end of our time{name}. Thank you for walking me through your experience today.",
                f"We're out of time{name}, so let's wrap up here. Thank you for talking me through your work today.",
            ],
            state.session_id,
        )
    elif reason == SessionEndReason.CANDIDATE_ENDED:
        opener = f"Understood{name}, we'll stop here. Thank you for your time today."
    elif reason == SessionEndReason.POLICY_TERMINATION:
        return f"We need to end the session here{name}, in line with the interview policy you agreed to. The hiring team will follow up with you about next steps."
    elif reason == SessionEndReason.TECHNICAL_FAILURE:
        return f"I'm sorry{name}, we've run into a technical problem and need to stop the session here. The hiring team will be in touch about next steps."
    else:
        opener = _pick(
            [
                f"That's everything I wanted to cover{name}. Thank you for taking the time to talk with me today.",
                f"That covers everything for this round{name}. Thanks for walking me through your work today.",
            ],
            state.session_id,
        )
    return f"{opener} {next_steps} Have a great rest of your day."


def closing_prompt(state: InterviewState, reason: SessionEndReason, last_answer: str | None) -> str:
    return CLOSING.render(
        interviewer_name=state.interviewer_name,
        first_name=state.candidate_first_name,
        job_title=(state.job_summary or {}).get("title") or "the",
        reason=_REASON_TEXT.get(reason, "the interview is ending"),
        last_answer=(last_answer or "")[-600:] or None,
        report_note=report_note(state),
    )


def acceptable_closing(text: str) -> bool:
    from app.services.interview.speech import style_issues

    lowered = text.lower()
    if "?" in text:
        return False
    if any(issue in ("praise", "critique") for issue in style_issues(text)):
        return False  # "Thank you for that detailed explanation" judges the candidate
    if len(text.split()) > 80 or len(text.split()) < 8:
        return False
    if any(
        p in lowered
        for p in ("score", "great answer", "you did well", "you passed", "you failed", "strong candidate")
    ):
        return False
    return "thank" in lowered


__all__ = [
    "GENERATED_REASONS",
    "SPOKEN_REASONS",
    "acceptable_closing",
    "closing_prompt",
    "report_note",
    "template_closing",
]
