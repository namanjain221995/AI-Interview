"""Oculus viseme timelines for lip-sync.

``text_to_visemes`` maps English text to visemes with grapheme/digraph rules
(no dictionary): vowels are held longer than consonants, punctuation becomes
a closed-mouth pause, and the timeline is scaled to the real audio duration
when one is given. ``phonemes_to_visemes`` does the same for an IPA phoneme
string (e.g. from a G2P front end such as misaki, used by Kokoro).

Viseme ids: sil PP FF TH DD kk CH SS nn RR aa E I O U.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.ai.base import VisemeCue

OCULUS_VISEMES: tuple[str, ...] = (
    "sil",
    "PP",
    "FF",
    "TH",
    "DD",
    "kk",
    "CH",
    "SS",
    "nn",
    "RR",
    "aa",
    "E",
    "I",
    "O",
    "U",
)
VOWEL_VISEMES = frozenset({"aa", "E", "I", "O", "U"})

# Natural durations (ms) before scaling to the audio length.
BASE_DURATION_MS: dict[str, int] = {
    "PP": 70,
    "FF": 80,
    "TH": 75,
    "DD": 55,
    "kk": 60,
    "CH": 85,
    "SS": 85,
    "nn": 60,
    "RR": 65,
    "aa": 125,
    "E": 105,
    "I": 95,
    "O": 120,
    "U": 110,
}

WEIGHTS: dict[str, float] = {
    "sil": 1.0,
    "PP": 1.0,
    "FF": 0.9,
    "TH": 0.8,
    "DD": 0.7,
    "kk": 0.6,
    "CH": 0.8,
    "SS": 0.7,
    "nn": 0.6,
    "RR": 0.7,
    "aa": 1.0,
    "E": 0.9,
    "I": 0.8,
    "O": 0.9,
    "U": 0.85,
}

PAUSE_MS: dict[str, int] = {
    ",": 140,
    ";": 200,
    ":": 200,
    ".": 300,
    "!": 300,
    "?": 300,
    "…": 320,
    "—": 160,
    "–": 120,
    "(": 80,
    ")": 80,
    "\n": 250,
}
TRAILING_SILENCE_MS = 80

# Longest-match grapheme rules; values are viseme sequences.
_TRIGRAPHS: dict[str, tuple[str, ...]] = {
    "tch": ("CH",),
    "sch": ("SS", "kk"),
    "igh": ("aa", "I"),
    "eau": ("O",),
    "ough": ("O",),
    "augh": ("aa",),
    "eigh": ("E",),
}
_DIGRAPHS: dict[str, tuple[str, ...]] = {
    "th": ("TH",),
    "sh": ("CH",),
    "ch": ("CH",),
    "ph": ("FF",),
    "wh": ("U",),
    "ck": ("kk",),
    "ng": ("nn",),
    "qu": ("kk", "U"),
    "gh": (),
    "ee": ("I",),
    "ea": ("I",),
    "ie": ("I",),
    "ei": ("E",),
    "ai": ("E",),
    "ay": ("E",),
    "ey": ("E",),
    "oa": ("O",),
    "oo": ("U",),
    "ou": ("aa", "U"),
    "ow": ("O",),
    "oi": ("O", "I"),
    "oy": ("O", "I"),
    "au": ("aa",),
    "aw": ("aa",),
    "ue": ("U",),
    "ew": ("U",),
    "ui": ("U",),
    "ll": ("nn",),
    "ss": ("SS",),
    "tt": ("DD",),
    "dd": ("DD",),
    "pp": ("PP",),
    "bb": ("PP",),
    "mm": ("PP",),
    "ff": ("FF",),
    "nn": ("nn",),
    "rr": ("RR",),
    "zz": ("SS",),
    "gg": ("kk",),
    "cc": ("kk",),
}
_WORD_START: dict[str, tuple[str, ...]] = {"kn": ("nn",), "wr": ("RR",), "gn": ("nn",), "ps": ("SS",)}
_SINGLE: dict[str, tuple[str, ...]] = {
    "a": ("aa",),
    "b": ("PP",),
    "c": ("kk",),
    "d": ("DD",),
    "e": ("E",),
    "f": ("FF",),
    "g": ("kk",),
    "h": (),
    "i": ("I",),
    "j": ("CH",),
    "k": ("kk",),
    "l": ("nn",),
    "m": ("PP",),
    "n": ("nn",),
    "o": ("O",),
    "p": ("PP",),
    "q": ("kk",),
    "r": ("RR",),
    "s": ("SS",),
    "t": ("DD",),
    "u": ("U",),
    "v": ("FF",),
    "w": ("U",),
    "x": ("kk", "SS"),
    "y": ("I",),
    "z": ("SS",),
}
_VOWEL_LETTERS = frozenset("aeiouy")
_SOFTENERS = frozenset("eiy")

_SYMBOL_WORDS = {
    "%": "percent",
    "&": "and",
    "+": "plus",
    "@": "at",
    "$": "dollars",
    "#": "number",
    "=": "equals",
}
_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.,]\d+)*|[,;:.!?…—–()\n]|[%&+@$#=]")

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]  # fmt: skip
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_to_words(value: int) -> str:
    """English words for 0 <= value < 10**12 (digit-by-digit beyond)."""
    if value < 0:
        return "minus " + number_to_words(-value)
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        return _ONES[hundreds] + " hundred" + ("" if rest == 0 else " " + number_to_words(rest))
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if value >= size:
            if value >= size * 1000:
                break
            major, rest = divmod(value, size)
            return number_to_words(major) + f" {name}" + ("" if rest == 0 else " " + number_to_words(rest))
    return " ".join(_ONES[int(d)] for d in str(value))


def _expand_number(token: str) -> list[str]:
    if "." in token and token.replace(".", "").isdigit() and token.count(".") == 1:
        whole, frac = token.split(".")
        return [*number_to_words(int(whole or "0")).split(), "point", *(_ONES[int(d)] for d in frac)]
    digits = token.replace(",", "")
    if digits.isdigit():
        return number_to_words(int(digits)).split() if len(digits) <= 12 else [_ONES[int(d)] for d in digits]
    return [_ONES[int(d)] for d in token if d.isdigit()]


def word_to_visemes(word: str) -> list[str]:
    """Rule-based grapheme -> viseme mapping for one English word."""
    w = word.lower().replace("'", "")
    out: list[str] = []
    i = 0
    n = len(w)
    while i < n:
        if i == 0 and w[:2] in _WORD_START:
            out.extend(_WORD_START[w[:2]])
            i += 2
            continue
        matched = False
        for size, table in ((4, _TRIGRAPHS), (3, _TRIGRAPHS), (2, _DIGRAPHS)):
            chunk = w[i : i + size]
            if len(chunk) == size and chunk in table:
                if chunk == "mb" and i + 2 == n:
                    out.append("PP")
                else:
                    out.extend(table[chunk])
                i += size
                matched = True
                break
        if matched:
            continue
        ch = w[i]
        nxt = w[i + 1] if i + 1 < n else ""
        if ch == "c" and nxt in _SOFTENERS:
            out.append("SS")
        elif ch == "g" and nxt in _SOFTENERS and i > 0:
            out.append("CH")
        elif (
            ch == "e"
            and i == n - 1
            and n > 2
            and w[i - 1] not in _VOWEL_LETTERS
            and any(c in _VOWEL_LETTERS for c in w[:-1])
        ):
            pass  # silent final e ("make", "time")
        elif ch == "y" and i == 0 and n > 1:
            out.append("I")
        else:
            out.extend(_SINGLE.get(ch, ()))
        i += 1
    if not out and w:
        out.append("E")  # every spoken word opens the mouth at least once
    return out


def _tokens(text: str) -> Iterable[tuple[str, str]]:
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token in PAUSE_MS:
            yield "pause", token
        elif token in _SYMBOL_WORDS:
            yield "word", _SYMBOL_WORDS[token]
        elif token[0].isdigit():
            for word in _expand_number(token):
                yield "word", word
        else:
            yield "word", token


def _timeline(units: list[tuple[str, float]], duration_ms: int | None) -> list[VisemeCue]:
    merged: list[tuple[str, float]] = []
    for viseme, dur in units:
        if dur <= 0:
            continue
        if merged and merged[-1][0] == viseme:
            merged[-1] = (viseme, merged[-1][1] + dur)
        else:
            merged.append((viseme, float(dur)))
    if not merged:
        return [VisemeCue(t_ms=0, viseme="sil", weight=WEIGHTS["sil"])]
    if merged[-1][0] != "sil":
        merged.append(("sil", float(TRAILING_SILENCE_MS)))
    total = sum(dur for _, dur in merged)
    scale = duration_ms / total if duration_ms and duration_ms > 0 else 1.0
    cues: list[VisemeCue] = []
    t = 0.0
    for viseme, dur in merged:
        start = round(t)
        t += dur * scale
        if cues and start <= cues[-1].t_ms:
            continue  # collapsed by compression: keep the earlier cue
        cues.append(VisemeCue(t_ms=start, viseme=viseme, weight=WEIGHTS[viseme]))
    if cues[-1].viseme != "sil":
        end = round(total * scale)
        cues.append(VisemeCue(t_ms=max(cues[-1].t_ms + 1, end - 1), viseme="sil", weight=WEIGHTS["sil"]))
    return cues


def text_to_visemes(text: str, duration_ms: int | None = None) -> list[VisemeCue]:
    """Viseme cues (start times in ms, strictly increasing) for ``text``.
    With ``duration_ms`` the timeline is stretched/compressed to the audio."""
    units: list[tuple[str, float]] = []
    for kind, token in _tokens(text):
        if kind == "pause":
            units.append(("sil", PAUSE_MS[token]))
        else:
            units.extend((v, BASE_DURATION_MS[v]) for v in word_to_visemes(token))
    return _timeline(units, duration_ms)


def estimate_duration_ms(text: str, speed: float = 1.0) -> int:
    """Natural speaking duration implied by the viseme durations."""
    cues = text_to_visemes(text)
    if len(cues) < 2:
        return 0
    natural = cues[-1].t_ms + TRAILING_SILENCE_MS
    return round(natural / max(speed, 0.1))


# ------------------------------------------------------------------- IPA

_IPA_MULTI: dict[str, tuple[str, ...]] = {
    "tʃ": ("CH",),
    "dʒ": ("CH",),
    "aɪ": ("aa", "I"),
    "aʊ": ("aa", "U"),
    "eɪ": ("E", "I"),
    "oʊ": ("O", "U"),
    "əʊ": ("E", "U"),
    "ɔɪ": ("O", "I"),
}
_IPA: dict[str, tuple[str, ...]] = {
    "p": ("PP",), "b": ("PP",), "m": ("PP",),
    "f": ("FF",), "v": ("FF",),
    "θ": ("TH",), "ð": ("TH",),
    "t": ("DD",), "d": ("DD",), "ɾ": ("DD",),
    "k": ("kk",), "g": ("kk",), "ɡ": ("kk",), "x": ("kk",), "q": ("kk",),
    "ʃ": ("CH",), "ʒ": ("CH",), "ʧ": ("CH",), "ʤ": ("CH",), "ç": ("CH",),
    "s": ("SS",), "z": ("SS",),
    "n": ("nn",), "l": ("nn",), "ɫ": ("nn",), "ŋ": ("nn",), "ɲ": ("nn",),
    "ɹ": ("RR",), "r": ("RR",), "ɻ": ("RR",), "ɚ": ("RR",), "ɝ": ("RR",),
    "w": ("U",), "j": ("I",),
    "ɑ": ("aa",), "a": ("aa",), "æ": ("aa",), "ʌ": ("aa",), "ɐ": ("aa",), "ɒ": ("O",),
    "ɛ": ("E",), "e": ("E",), "ə": ("E",), "ɜ": ("E",), "ᵊ": ("E",),
    "ɪ": ("I",), "i": ("I",), "ᵻ": ("I",), "ɨ": ("I",), "y": ("I",),
    "ɔ": ("O",), "o": ("O",),
    "ʊ": ("U",), "u": ("U",), "ʉ": ("U",),
    # misaki (Kokoro G2P) diphthong capitals
    "A": ("E", "I"), "I": ("aa", "I"), "W": ("aa", "U"), "Y": ("O", "I"), "O": ("O", "U"), "Q": ("E", "U"),
}  # fmt: skip
_IPA_IGNORED = frozenset("ˈˌʰʲʷ̩̯̃ʔh ")


def phonemes_to_visemes(phonemes: str, duration_ms: int | None = None) -> list[VisemeCue]:
    """Viseme cues for an IPA phoneme string (stress marks ignored, ``ː``
    lengthens the previous vowel, punctuation pauses)."""
    units: list[tuple[str, float]] = []
    i = 0
    n = len(phonemes)
    while i < n:
        pair = phonemes[i : i + 2]
        if pair in _IPA_MULTI:
            units.extend((v, BASE_DURATION_MS[v]) for v in _IPA_MULTI[pair])
            i += 2
            continue
        ch = phonemes[i]
        i += 1
        if ch in PAUSE_MS:
            units.append(("sil", PAUSE_MS[ch]))
        elif ch == "ː" and units and units[-1][0] in VOWEL_VISEMES:
            viseme, dur = units[-1]
            units[-1] = (viseme, dur * 1.5)
        elif ch in _IPA:
            units.extend((v, BASE_DURATION_MS[v]) for v in _IPA[ch])
        # anything else (stress, length on consonants, unknown symbols) is ignored
    return _timeline(units, duration_ms)
