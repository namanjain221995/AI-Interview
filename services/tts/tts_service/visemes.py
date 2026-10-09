"""Phoneme timings -> Oculus viseme timelines.

Kokoro (StyleTTS 2) predicts a duration for every phoneme token it speaks. When
the ONNX graph exposes that output (the Docker image adds it at build time, see
``scripts/add_duration_output.py``) the timeline uses those exact timings.
Otherwise :func:`estimate_spans` estimates them from a per-symbol duration table
calibrated against the model's own durations (30 interview sentences, three
voices) and aligns them to the audible part of the waveform, anchoring
punctuation to the silent gaps found in it.

Either way :func:`viseme_cues` then gates the timeline with the audio: the mouth
is closed (``sil``) before the first and after the last audible frame and during
pauses, and vowel openings are scaled by stress and loudness.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tts_service.audio import FRAME_SECONDS

VISEMES = ("sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U")

STRESS_MARKS = frozenset("ˈˌ")
# Tokens that modify the phone before them: length, nasalisation, aspiration,
# palatalisation and misaki intonation arrows.
MODIFIERS = frozenset("\u02d0\u0303\u02b0\u02b2\u2192\u2193\u2197\u2198")  # length, tilde, h, j, 4 arrows
SENTENCE_MARKS = frozenset(".!?…")
CLAUSE_MARKS = frozenset(",;:—")
PAUSE_MARKS = SENTENCE_MARKS | CLAUSE_MARKS
OPENING_MARKS = frozenset('"“(')
CLOSING_MARKS = frozenset("”)")

_VISEME_GROUPS = {
    "PP": "pbmβɸ",
    "FF": "fvʋ",
    "TH": "θð",
    "DD": "tdɾʈɖcɟʔT",
    "kk": "kɡqxɣχŋɰʁ",
    "CH": "ʃʒʧʤçɕʂʝʨʥꭧ",
    "SS": "szʦʣS",
    "nn": "nlɲɳɴʎ",
    "RR": "ɹrɻɽɚ",
    "aa": "aɑæɐʌIW",
    "E": "eɛəɜᵊœɤA",
    "I": "iɪɨᵻj",
    "O": "oɔɒøOQY",
    "U": "uʊɯwɥyᵝ",
}
VISEME_OF: dict[str, str] = {
    symbol: viseme for viseme, symbols in _VISEME_GROUPS.items() for symbol in symbols
}
# "h" has no mouth shape of its own: it takes the shape of the vowel it precedes.
ASPIRATE = "h"
VOWELS = frozenset("aɑæɐʌeɛəɜᵊœɤiɪɨᵻoɔɒøuʊɯyAIOQWYɚ")
REDUCED_VOWELS = frozenset("əᵻɐᵊɨ")
_DIPHTHONG_ONSETS = frozenset("aeoɔəɛ")
_DIPHTHONG_GLIDES = frozenset("ɪʊ")
_VOWEL_VISEMES = frozenset({"aa", "E", "I", "O", "U"})

# fmt: off
_BASE_WEIGHT = {
    "sil": 1.0, "PP": 1.0, "FF": 0.9, "TH": 0.8, "DD": 0.6, "kk": 0.55, "CH": 0.85, "SS": 0.75,
    "nn": 0.6, "RR": 0.7, "aa": 1.0, "E": 0.9, "I": 0.85, "O": 0.95, "U": 0.9,
}
# fmt: on

# Mean model duration per token in ms at speed 1.0 (StyleTTS 2 gives stress and
# length marks and word gaps durations of their own), excluding pre-pause tokens.
# fmt: off
_DURATION_MS = {
    " ": 50, "ˈ": 60, "ˌ": 40, "ː": 40, "ɪ": 55, "t": 45, "ə": 45, "ɹ": 50, "d": 40, "k": 50, "n": 45,
    "s": 60, "ʊ": 40, "ɛ": 50, "a": 55, "l": 45, "u": 30, "j": 30, "w": 30, "m": 50, "i": 45, "e": 65,
    "ð": 35, "p": 55, "æ": 45, "z": 50, "ɐ": 40, "b": 45, "ɔ": 40, "ʌ": 45, "v": 45, "h": 40, "f": 45,
    "ʃ": 55, "o": 55, "ɚ": 75, "ɑ": 35, "ŋ": 40, "θ": 50, "ʒ": 80, "ɡ": 50, "ɒ": 50, "ɾ": 60, "ɜ": 50,
    "ᵻ": 70, ",": 50, ";": 65, ":": 65, "—": 80, ".": 80, "!": 80, "?": 80, "…": 100,
}
# fmt: on
_DEFAULT_VOWEL_MS = 50
_DEFAULT_CONSONANT_MS = 45
_DEFAULT_MARK_MS = 10
# The model stretches the last phone before a pause (and of the utterance).
_PRE_PAUSE_FACTOR = 2.0
# Gaps between phones this short are audible onset the phone owns, not silence.
_MAX_ONSET_GAP = 0.1
# Pieces shorter than this are folded into their neighbour (too short to animate).
_MIN_PIECE = 0.02


@dataclass(frozen=True, slots=True)
class PhonemeSpan:
    """One phoneme token and when it is spoken, in seconds from the start of the audio."""

    symbol: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Cue:
    """A viseme becoming active at ``time`` seconds."""

    time: float
    viseme: str
    weight: float


@dataclass(frozen=True, slots=True)
class VisemeCue:
    """Wire form of a cue: ``t_ms`` milliseconds from the start of the audio."""

    t_ms: int
    viseme: str
    weight: float


@dataclass(slots=True)
class _Piece:
    start: float
    end: float
    viseme: str
    weight: float


@dataclass(slots=True)
class _Phone:
    symbol: str
    viseme: str | None
    start: float
    end: float
    stress: int = 0
    glide: bool = False
    pause: bool = False


def is_phone(symbol: str) -> bool:
    """True for tokens that are articulated (not punctuation, spaces, stress or length marks)."""
    return (
        symbol not in PAUSE_MARKS
        and symbol not in STRESS_MARKS
        and symbol not in MODIFIERS
        and symbol not in OPENING_MARKS
        and symbol not in CLOSING_MARKS
        and not symbol.isspace()
    )


def has_phones(phonemes: str) -> bool:
    return any(is_phone(symbol) for symbol in phonemes)


# ------------------------------------------------------------------ estimation


def _duration_ms(symbol: str) -> float:
    if symbol in _DURATION_MS:
        return _DURATION_MS[symbol]
    if symbol in VOWELS:
        return _DEFAULT_VOWEL_MS
    if is_phone(symbol):
        return _DEFAULT_CONSONANT_MS
    if symbol in PAUSE_MARKS:
        return _DURATION_MS["."]
    return _DEFAULT_MARK_MS


def _match_pauses(
    expected: Sequence[float], pauses: Sequence[tuple[float, float]], tolerance: float
) -> list[tuple[int, int]]:
    """Monotonic matching of expected pause times to detected pauses.

    Dynamic programming over (punctuation unit, detected pause) pairs: a match
    scores ``1 - distance / tolerance``; either side may stay unmatched. Returns
    (unit index, pause index) pairs.
    """
    k, m = len(expected), len(pauses)
    if k == 0 or m == 0:
        return []
    centres = [(start + end) / 2 for start, end in pauses]
    score = np.zeros((k + 1, m + 1))
    move = np.zeros((k + 1, m + 1), dtype=np.int8)  # 0 skip unit, 1 skip pause, 2 match
    for i in range(1, k + 1):
        for j in range(1, m + 1):
            best, step = score[i - 1, j], 0
            if score[i, j - 1] > best:
                best, step = score[i, j - 1], 1
            distance = abs(expected[i - 1] - centres[j - 1])
            if distance < tolerance and score[i - 1, j - 1] + 1 - distance / tolerance > best:
                best, step = score[i - 1, j - 1] + 1 - distance / tolerance, 2
            score[i, j], move[i, j] = best, step
    pairs: list[tuple[int, int]] = []
    i, j = k, m
    while i > 0 and j > 0:
        if move[i, j] == 2:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move[i, j] == 1:
            j -= 1
        else:
            i -= 1
    return pairs[::-1]


def estimate_spans(
    phonemes: str,
    onset: float,
    offset: float,
    pauses: Sequence[tuple[float, float]],
    total: float,
) -> list[PhonemeSpan]:
    """Estimate when each phoneme token is spoken.

    Tokens get durations from the calibrated table (the phone before a pause
    lengthened), punctuation is anchored to the detected ``pauses`` it matches,
    and every stretch between anchors is scaled onto its share of the audible
    region ``[onset, offset]``. Tokens before the first phone sit at ``onset``;
    trailing punctuation fills ``[offset, total]``.
    """
    symbols = list(phonemes)
    n = len(symbols)
    speech = [i for i, symbol in enumerate(symbols) if is_phone(symbol)]
    if not speech:
        return [PhonemeSpan(symbol, onset, onset) for symbol in symbols]
    first, last = speech[0], speech[-1]
    offset = max(offset, onset)

    weights = np.array([_duration_ms(symbol) for symbol in symbols], dtype=np.float64)
    for i in speech:
        j = i + 1
        while j < n and (symbols[j] in MODIFIERS or symbols[j] in CLOSING_MARKS):
            j += 1
        while j < n and (symbols[j].isspace() or symbols[j] in OPENING_MARKS):
            j += 1
        if j >= n or symbols[j] in PAUSE_MARKS:
            weights[i] *= _PRE_PAUSE_FACTOR

    # Punctuation units inside the speech: a pause mark plus the spaces/marks around it.
    units: list[tuple[int, int]] = []
    i = first
    while i <= last:
        if symbols[i] in PAUSE_MARKS:
            j = i
            while j <= last and not is_phone(symbols[j]) and symbols[j] not in STRESS_MARKS:
                j += 1
            units.append((i, j))
            i = j
        else:
            i += 1

    anchors: list[tuple[int, int, float, float]] = []
    if units and pauses:
        cumulative = np.concatenate([[0.0], np.cumsum(weights[first : last + 1])])
        span = offset - onset
        expected = [
            onset + span * (cumulative[a - first] + cumulative[b - first]) / 2 / cumulative[-1]
            for a, b in units
        ]
        tolerance = max(0.3, 0.2 * span)
        for unit_index, pause_index in _match_pauses(expected, pauses, tolerance):
            a, b = units[unit_index]
            anchors.append((a, b, *pauses[pause_index]))

    starts = np.full(n, onset)
    ends = np.full(n, onset)
    segments: list[tuple[int, int, float, float]] = []
    cursor_index, cursor_time = first, onset
    for a, b, pause_start, pause_end in anchors:
        segments.append((cursor_index, a, cursor_time, pause_start))
        segments.append((a, b, pause_start, pause_end))
        cursor_index, cursor_time = b, pause_end
    segments.append((cursor_index, last + 1, cursor_time, offset))
    if last + 1 < n:
        segments.append((last + 1, n, offset, max(offset, total)))

    for a, b, seg_start, seg_end in segments:
        if b <= a:
            continue
        cumulative = np.concatenate([[0.0], np.cumsum(weights[a:b])])
        scale = (seg_end - seg_start) / cumulative[-1] if cumulative[-1] > 0 else 0.0
        starts[a:b] = seg_start + cumulative[:-1] * scale
        ends[a:b] = seg_start + cumulative[1:] * scale
    return [PhonemeSpan(symbols[i], float(starts[i]), float(ends[i])) for i in range(n)]


# ------------------------------------------------------------------ timeline


def _merge_phones(spans: Sequence[PhonemeSpan]) -> list[_Phone]:
    """Fold marks into phones: stress marks, word gaps and opening quotes into the
    phone after them, length/modifier marks and closing quotes into the phone
    before; t+ʃ and d+ʒ become one affricate."""
    phones: list[_Phone] = []
    carry: float | None = None
    stress = 0
    for span in spans:
        symbol = span.symbol
        if symbol in STRESS_MARKS:
            stress = max(stress, 2 if symbol == "ˈ" else 1)
            carry = span.start if carry is None else carry
            continue
        if symbol.isspace() or symbol in OPENING_MARKS:
            carry = span.start if carry is None else carry
            continue
        if symbol in MODIFIERS or symbol in CLOSING_MARKS:
            if phones:
                phones[-1].end = max(phones[-1].end, span.end)
            elif carry is None:
                carry = span.start
            continue
        start = span.start if carry is None else carry
        carry = None
        if symbol in PAUSE_MARKS:
            phones.append(_Phone(symbol, "sil", start, span.end, pause=True))
            continue
        previous = phones[-1] if phones else None
        if (
            previous is not None
            and (previous.symbol, symbol) in (("t", "ʃ"), ("d", "ʒ"))
            and abs(previous.end - start) < 1e-6
        ):
            previous.symbol += symbol
            previous.viseme = "CH"
            previous.end = span.end
            continue
        glide = (
            symbol in _DIPHTHONG_GLIDES
            and stress == 0
            and previous is not None
            and previous.symbol in _DIPHTHONG_ONSETS
            and abs(previous.end - start) < 1e-6
        )
        phone = _Phone(symbol, VISEME_OF.get(symbol), start, span.end, glide=glide)
        if symbol in VOWELS:
            phone.stress, stress = stress, 0
        phones.append(phone)
    if carry is not None and phones and spans:
        phones[-1].end = max(phones[-1].end, spans[-1].end)

    resolved: list[_Phone] = []
    for index, phone in enumerate(phones):
        if phone.viseme is None and phone.symbol == ASPIRATE:
            following = phones[index + 1] if index + 1 < len(phones) else None
            if following is not None and following.symbol in VOWELS and following.viseme:
                phone.viseme = following.viseme
                phone.glide = True  # weaker version of the vowel shape
            else:
                phone.viseme = "E"
        if phone.viseme is None:  # symbol outside the table: extend the previous phone
            if resolved:
                resolved[-1].end = phone.end
            continue
        resolved.append(phone)
    return resolved


def _mean_level(levels: NDArray[np.float64], start: float, end: float, frame_seconds: float) -> float:
    lo = max(0, int(start / frame_seconds))
    hi = min(len(levels), max(lo + 1, int(np.ceil(end / frame_seconds))))
    if hi <= lo:
        return -120.0
    return float(levels[lo:hi].mean())


def _weight(phone: _Phone, level_db: float) -> float:
    viseme = phone.viseme or "sil"
    weight = _BASE_WEIGHT[viseme]
    if viseme in _VOWEL_VISEMES and phone.symbol in VOWELS:
        if phone.symbol in REDUCED_VOWELS:
            weight *= 0.6
        elif phone.stress == 1:
            weight *= 0.9
        elif phone.stress == 0:
            weight *= 0.8
    if phone.glide:
        weight *= 0.7
    if viseme not in ("sil", "PP", "FF"):  # closures stay strong; openings follow loudness
        loudness = min(1.0, max(0.0, (level_db + 30.0) / 24.0))
        weight *= 0.65 + 0.35 * loudness
    return round(min(1.0, max(0.2, weight)), 2)


def _subtract(start: float, end: float, holes: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(start, end)]
    for hole_start, hole_end in holes:
        next_pieces: list[tuple[float, float]] = []
        for piece_start, piece_end in pieces:
            if hole_end <= piece_start or hole_start >= piece_end:
                next_pieces.append((piece_start, piece_end))
                continue
            if piece_start < hole_start:
                next_pieces.append((piece_start, hole_start))
            if hole_end < piece_end:
                next_pieces.append((hole_end, piece_end))
        pieces = next_pieces
    return pieces


def viseme_cues(
    spans: Sequence[PhonemeSpan],
    levels: NDArray[np.float64],
    onset: float,
    offset: float,
    pauses: Sequence[tuple[float, float]],
    total: float,
    frame_seconds: float = FRAME_SECONDS,
) -> list[Cue]:
    """Viseme timeline for one clip, in seconds from its first sample.

    ``onset``/``offset`` bound the audible region and ``pauses`` are the silent
    gaps inside it; all of them force ``sil``. The result starts at 0, is
    strictly increasing in time, never repeats a viseme back to back and ends
    with ``sil`` at ``offset``.
    """
    total = max(total, offset)
    silences = [(0.0, onset), *pauses, (offset, total)]
    silences = [(start, end) for start, end in silences if end > start]

    pieces = [_Piece(start, end, "sil", 1.0) for start, end in silences]
    for phone in _merge_phones(spans):
        start, end = max(0.0, phone.start), min(total, phone.end)
        if end <= start:
            continue
        for piece_start, piece_end in _subtract(start, end, silences):
            # Loudness is measured over the audible part only (the piece), not the
            # silence a pre-pause phone is stretched into.
            level = _mean_level(levels, piece_start, piece_end, frame_seconds)
            weight = 1.0 if phone.pause else _weight(phone, level)
            pieces.append(_Piece(piece_start, piece_end, phone.viseme or "sil", weight))
    pieces.sort(key=lambda piece: (piece.start, piece.end))

    timeline: list[_Piece] = []
    for piece in pieces:
        if timeline:
            previous_end = timeline[-1].end
            gap = piece.start - previous_end
            if gap > 1e-9:
                if gap <= _MAX_ONSET_GAP and piece.viseme != "sil":
                    piece.start = previous_end  # audible onset before the phone's own start
                else:
                    timeline.append(_Piece(previous_end, piece.start, "sil", 1.0))
            elif gap < -1e-9:
                piece.start = previous_end
                if piece.end <= piece.start:
                    continue
        timeline.append(piece)

    # Fold pieces too short to animate into the previous piece, then merge repeats.
    merged: list[_Piece] = []
    for piece in timeline:
        if merged:
            previous = merged[-1]
            if previous.viseme == piece.viseme:
                previous.end, previous.weight = piece.end, max(previous.weight, piece.weight)
                continue
            if piece.end - piece.start < _MIN_PIECE and piece.viseme != "sil":
                previous.end = piece.end
                continue
        merged.append(piece)
    if not merged:
        return [Cue(0.0, "sil", 1.0)]
    if merged[0].start > 0:
        merged.insert(0, _Piece(0.0, merged[0].start, "sil", 1.0))
    if merged[-1].viseme != "sil":
        merged.append(_Piece(merged[-1].end, total, "sil", 1.0))
    return [Cue(piece.start, piece.viseme, piece.weight) for piece in merged]
