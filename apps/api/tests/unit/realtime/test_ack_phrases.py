"""Acknowledgements are spoken by Kokoro: only phrases its phonemizer pronounces as words."""

from __future__ import annotations

import re

from app.realtime.acknowledgements import ACKNOWLEDGEMENT_PHRASES, PROMPT_PHRASES, SOFT_ACKNOWLEDGEMENTS
from app.services.interview.prompts import SPEECH_RULES

#: Spelled out letter by letter by the voice (measured 2026-10-13: "Mm-hmm." -> "em-em-hum", "Mhm." -> "em-aitch-em").
SPELLED_OUT = re.compile(r"\b(?:m+-?h+m+|mhm|mm|uh-?huh|hmm+)\b", re.IGNORECASE)


def test_no_acknowledgement_is_spelled_out_by_the_voice() -> None:
    for phrase in (*ACKNOWLEDGEMENT_PHRASES, *SOFT_ACKNOWLEDGEMENTS, *PROMPT_PHRASES):
        assert not SPELLED_OUT.search(phrase), phrase
    assert SOFT_ACKNOWLEDGEMENTS <= set(ACKNOWLEDGEMENT_PHRASES)


def test_the_interviewer_is_told_not_to_write_such_sounds() -> None:
    assert '"mm-hmm"' in SPEECH_RULES and "spells them out" in " ".join(SPEECH_RULES.split())
