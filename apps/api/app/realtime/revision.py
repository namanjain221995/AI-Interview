"""Second-pass transcript revision of a just-committed spoken answer.

The live turn is built from per-segment transcriptions so the interviewer can
reply quickly; after the turn is committed the runtime transcribes the whole
answer once more in the background (full context: fewer split words and
better punctuation). When that pass differs materially, the stored transcript
(``answers.transcript_text`` and the candidate's ``transcripts`` row) is
replaced - but only while the answer has not been evaluated yet
(``answer.evaluate`` reads ``transcript_text`` when it runs, a few seconds
after the turn) and only if the stored text is still exactly what the live
turn wrote, so a later edit or an evaluation in progress is never
overwritten. The live conversation itself is not changed.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.models import Answer, Transcript
from app.db.session import session_scope

log = get_logger(__name__)

_WORD = re.compile(r"[a-z0-9']+")


def word_list(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("-", " "))


def word_difference(a: str, b: str) -> float:
    """Normalised word-level edit distance between two transcripts (0 = same words)."""
    x, y = word_list(a), word_list(b)
    if not x and not y:
        return 0.0
    previous = list(range(len(y) + 1))
    for i, word in enumerate(x, 1):
        current = [i]
        for j, other in enumerate(y, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[len(y)] / max(len(x), len(y), 1)


def is_material_revision(live: str, full: str, *, min_ratio: float) -> bool:
    """Worth storing: enough words differ, and the second pass did not lose words
    (a truncated or empty pass must never replace a complete answer)."""
    live_words, full_words = word_list(live), word_list(full)
    if not full_words or len(full_words) < 0.8 * len(live_words):
        return False
    return word_difference(live, full) >= min_ratio


async def revise_answer_transcript(
    session_id: str, *, started_ms: int, ended_ms: int, live_text: str, revised_text: str
) -> bool:
    """Replace the stored transcript of the spoken answer (``started_ms``/``ended_ms``
    identify it) while it is unevaluated and unchanged. True when stored."""
    sid = uuid.UUID(session_id)
    expected = " ".join(live_text.split())
    revised = " ".join(revised_text.split())
    async with session_scope() as db:
        answer = await db.scalar(
            select(Answer)
            .where(
                Answer.session_id == sid,
                Answer.started_ms == started_ms,
                Answer.ended_ms == ended_ms,
                Answer.typed.is_(False),
            )
            .order_by(Answer.sequence.desc())
            .limit(1)
            .with_for_update()
        )
        if answer is None or answer.status != "received":
            return False
        if " ".join((answer.transcript_text or "").split()) != expected:
            return False
        answer.transcript_text = revised
        answer.word_count = len(revised.split())
        await db.execute(
            update(Transcript)
            .where(Transcript.answer_id == answer.id, Transcript.speaker == "candidate")
            .values(text=revised)
        )
    return True


def changed_words(live: str, full: str, limit: int = 8) -> Sequence[str]:
    """Words of the second pass that the live transcript did not have (diagnostics)."""
    live_set = set(word_list(live))
    return [w for w in word_list(full) if w not in live_set][:limit]
