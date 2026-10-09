"""Streaming sentence chunker for interviewer speech (REALTIME_DESIGN.md section 5).

Text deltas from the engine are cut into TTS chunks:

* at sentence ends (``.``, ``?``, ``!`` followed by whitespace), protecting
  abbreviations ("e.g.", "Dr.", "vs."), initials ("J. Smith"), decimals and
  versions ("3.5", "Python 3.12") and ellipses;
* the FIRST chunk may break at a comma once it has >= ``first_min_chars``
  (16) characters, or hard-break near ``first_max_chars`` (64) - before a
  phrase-starting word ("for", "which", "and", ...) when there is one, else
  at a word boundary - to cut time-to-first-audio (Kokoro takes ~0.6 s for 40
  characters vs ~1.5 s for 90 here; the LLM streams ~200 characters/s);
* the SECOND chunk holds at most ``3 x len(first chunk)`` characters (clamped
  to ``second_min_max_chars``..``second_max_chars``), so it is synthesized
  while the first one plays and no gap opens after a short first chunk;
* later chunks hold up to ``max_chars`` (220); very short later sentences are
  merged with the next one so TTS calls stay efficient.

A ``.`` at the very end of the buffer is undecided until the next character
arrives (it might be "3." of "3.5"); :meth:`flush` emits everything left.
"""

from __future__ import annotations

import re

ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "eg",
        "ie",
        "inc",
        "ltd",
        "co",
        "corp",
        "approx",
        "dept",
        "est",
        "fig",
        "no",
        "nos",
        "vol",
        "u.s",
        "u.k",
        "a.m",
        "p.m",
        "mt",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "cf",
        "al",
        "ph.d",
        "b.sc",
        "m.sc",
        "resp",
        "min",
        "max",
        "avg",
    }
)
_CLOSERS = "\"')]}”’"
_SPACE = re.compile(r"\s+")
# Words that usually start a new phrase: a forced cut just before one keeps
# the prosody of both halves natural.
PHRASE_STARTERS: frozenset[str] = frozenset(
    {
        "and",
        "but",
        "or",
        "so",
        "because",
        "which",
        "that",
        "who",
        "where",
        "when",
        "while",
        "if",
        "for",
        "with",
        "without",
        "from",
        "about",
        "across",
        "after",
        "before",
        "during",
        "how",
        "what",
        "why",
        "whether",
        "including",
        "especially",
        "since",
        "until",
        "unless",
        "although",
        "though",
    }
)


def _normalize(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


class SentenceChunker:
    def __init__(
        self,
        *,
        first_min_chars: int = 16,
        first_max_chars: int = 64,
        second_min_max_chars: int = 60,
        second_max_chars: int = 140,
        max_chars: int = 220,
        min_later_chars: int = 20,
    ) -> None:
        self.first_min_chars = first_min_chars
        self.first_max_chars = first_max_chars
        self.second_min_max_chars = second_min_max_chars
        self.second_max_chars = max(second_min_max_chars, second_max_chars)
        self.max_chars = max_chars
        self.min_later_chars = min_later_chars
        self._buf = ""
        self._held = ""  # short later sentence waiting to be merged
        self.emitted = 0
        self._first_len = 0

    @property
    def pending_text(self) -> str:
        return _normalize(f"{self._held} {self._buf}")

    def reset(self) -> None:
        self._buf = ""
        self._held = ""
        self.emitted = 0
        self._first_len = 0

    # ------------------------------------------------------------------ api
    def feed(self, text: str) -> list[str]:
        if not text:
            return []
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        out = self._drain(final=True)
        rest = _normalize(f"{self._held} {self._buf}")
        self._buf = ""
        self._held = ""
        if rest:
            out.append(self._emit(rest))
        return out

    # ------------------------------------------------------------- internal
    def _emit(self, chunk: str) -> str:
        if self.emitted == 0:
            self._first_len = len(chunk)
        self.emitted += 1
        return chunk

    def _limit(self, pending_first: bool) -> int:
        """Maximum size of the chunk being built (when no sentence end comes)."""
        if pending_first:
            return self.first_max_chars
        if self.emitted == 1:
            return min(self.second_max_chars, max(self.second_min_max_chars, 3 * self._first_len))
        return self.max_chars

    def _push(self, out: list[str], sentence: str) -> None:
        sentence = _normalize(sentence)
        if not sentence:
            return
        if self.emitted == 0 and not out:
            out.append(self._emit(sentence))
            return
        candidate = _normalize(f"{self._held} {sentence}") if self._held else sentence
        if len(candidate) < self.min_later_chars:
            self._held = candidate
            return
        self._held = ""
        out.append(self._emit(candidate))

    def _first_pending(self, out: list[str]) -> bool:
        return self.emitted == 0 and not out

    def _drain(self, *, final: bool) -> list[str]:
        out: list[str] = []
        while True:
            pending_first = self._first_pending(out)
            limit = self._limit(pending_first)
            idx = self._find_boundary(final=final)
            if idx is not None and idx + 1 <= limit:
                sentence, self._buf = self._buf[: idx + 1], self._buf[idx + 1 :]
                self._push(out, sentence)
                continue
            # No sentence end within this chunk's size: cut early (whole text that
            # arrived at once is cut the same way as streamed deltas).
            cut: int | None = None
            if pending_first:
                cut = self._first_early_cut()
            elif len(self._buf) >= limit:
                cut = self._soft_cut(limit)
            if cut is None:
                return out
            sentence, self._buf = self._buf[:cut], self._buf[cut:]
            self._push(out, sentence)

    def _first_early_cut(self) -> int | None:
        buf = self._buf
        # Comma break between first_min_chars and first_max_chars (the comma must
        # be followed by a space so "1,000" is never split).
        pos = buf.find(",", max(0, self.first_min_chars - 1))
        while pos != -1 and pos < self.first_max_chars:
            if pos + 1 < len(buf) and buf[pos + 1].isspace():
                return pos + 1
            if pos + 1 >= len(buf):
                break  # undecided until the next character
            pos = buf.find(",", pos + 1)
        if len(buf) >= self.first_max_chars:
            return self._soft_cut(self.first_max_chars, prefer_comma=False, floor=self.first_min_chars)
        return None

    def _soft_cut(self, limit: int, *, prefer_comma: bool = True, floor: int | None = None) -> int:
        """Cut position <= ``limit``: after a comma, else before a phrase-starting
        word, else at the last space (each at least ``floor`` characters in)."""
        window = self._buf[:limit]
        floor = limit // 3 if floor is None else floor
        if prefer_comma:
            comma = window.rfind(", ")
            if comma >= floor:
                return comma + 1
        phrase = self._phrase_cut(window, floor)
        if phrase is not None:
            return phrase
        space = window.rfind(" ")
        if space >= floor:
            return space
        return limit

    @staticmethod
    def _phrase_cut(window: str, floor: int) -> int | None:
        """Position of the last space before a phrase-starting word (not at the end)."""
        best: int | None = None
        for match in re.finditer(r" ([A-Za-z]+)(?=\s)", window):
            if match.start() >= floor and match.group(1).lower() in PHRASE_STARTERS:
                best = match.start()
        return best

    def _find_boundary(self, *, final: bool) -> int | None:
        buf = self._buf
        n = len(buf)
        i = 0
        while i < n:
            ch = buf[i]
            if ch in ".?!":
                # Extend over repeated punctuation and closing quotes/brackets.
                j = i
                while j + 1 < n and buf[j + 1] in ".?!":
                    j += 1
                k = j
                while k + 1 < n and buf[k + 1] in _CLOSERS:
                    k += 1
                if k + 1 >= n:
                    if (final or self._unambiguous_end(i, j)) and self._is_boundary(i, j):
                        return k
                    return None
                if buf[k + 1].isspace() and self._is_boundary(i, j):
                    return k
                i = k + 1
                continue
            i += 1
        return None

    def _unambiguous_end(self, start: int, end: int) -> bool:
        """A sentence end at the very end of the buffer that cannot become an
        abbreviation, initial or decimal with the next delta ("more." or "?")."""
        buf = self._buf
        head = buf[:start]
        if head.count('"') % 2 == 1 or head.count("(") > head.count(")") or head.count("“") > head.count("”"):
            return False  # a closing quote/bracket may follow in the next delta
        punct = buf[start : end + 1]
        if "?" in punct or "!" in punct:
            return True
        if punct != ".":
            return False
        token_start = start
        while token_start > 0 and buf[token_start - 1].isalpha():
            token_start -= 1
        token = buf[token_start:start].lower()
        before = buf[token_start - 1] if token_start > 0 else " "
        return len(token) >= 2 and before != "." and token not in ABBREVIATIONS

    def _is_boundary(self, start: int, end: int) -> bool:
        buf = self._buf
        punct = buf[start : end + 1]
        if "?" in punct or "!" in punct:
            return True
        if len(punct) >= 3:  # ellipsis: a trailing-off pause, not a sentence end
            nxt = buf[end + 1 :].lstrip()[:1]
            return bool(nxt) and nxt.isupper()
        # Single (or double) period: inspect the token before it.
        prev = start - 1
        if prev >= 0 and buf[prev].isdigit():
            nxt = buf[end + 1 : end + 2]
            if nxt.isdigit():
                return False  # decimal / version
        token_start = start
        while token_start > 0 and (buf[token_start - 1].isalnum() or buf[token_start - 1] == "."):
            token_start -= 1
        token = buf[token_start:start].lower().strip(".")
        if not token:
            return True
        if token in ABBREVIATIONS:
            return False
        if len(token) == 1 and token.isalpha() and token != "i" and buf[token_start].isupper():  # noqa: S105
            return False  # initial: "J. Smith"
        return not ("." in token and all(len(p) <= 2 for p in token.split(".")))  # "U.S.A."
