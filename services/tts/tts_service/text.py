"""Text normalisation for speech.

Kokoro speaks the phonemes espeak-ng produces for the text. Interviewer text is
LLM output: it can carry markdown, emoji, control characters, symbols and
abbreviations that espeak drops, spells out or mispronounces (``1.5`` becomes
"one. five", ``$100`` "dollar one hundred", ``e.g.`` "ee. gee."). The rules
below rewrite those into plain words without changing what is said. Every rule
is covered by tests/test_text.py, and the tech-term table only lists words whose
espeak-ng (en-us) pronunciation was checked to be wrong.

English-specific rules run only for English voices; markup and control-character
stripping runs for every language.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Callable

# ----------------------------------------------------------------- unicode cleanup

_SUPERSCRIPT_POWERS = {"²": "^2", "³": "^3"}
_FRACTIONS = {"½": "one half", "¼": "one quarter", "¾": "three quarters"}
_DROP_SYMBOLS = ("™", "®", "©", "℠")
# fmt: off
_CHAR_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "«": '"', "»": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "−": "-", "―": "—",
}
# fmt: on
_VARIATION_SELECTORS = re.compile("[\ufe00-\ufe0f\U000e0100-\U000e01ef\u200d]")
# Kokoro's vocabulary contains intonation arrows (misaki markers). They must
# never be injected from user text.
_ARROWS = re.compile("[\u2190-\u21ff\u27f0-\u27ff\u2900-\u297f]")


def _clean_unicode(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in _SUPERSCRIPT_POWERS.items():
        text = text.replace(src, dst)
    text = re.sub(r"(\d)\s?½", r"\1 and a half", text)
    for src, dst in _FRACTIONS.items():
        text = text.replace(src, f" {dst} ")
    text = re.sub(r"(\d)\s?°\s?C\b", r"\1 degrees Celsius", text)
    text = re.sub(r"(\d)\s?°\s?F\b", r"\1 degrees Fahrenheit", text)
    text = re.sub(r"(\d)\s?°", r"\1 degrees", text)
    for symbol in _DROP_SYMBOLS:
        text = text.replace(symbol, "")
    text = unicodedata.normalize("NFKC", text)
    text = _VARIATION_SELECTORS.sub("", text)

    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category == "Zs" or ch == "\t":
            out.append(" ")
        elif category in ("Zl", "Zp"):
            out.append("\n")
        elif category[0] == "C":  # control, format (zero-width, bidi), private use, unassigned
            continue
        elif (
            category in ("So", "Me") or 0x1F3FB <= ord(ch) <= 0x1F3FF
        ):  # emoji, pictographs, keycaps, skin tones
            continue
        else:
            out.append(_CHAR_MAP.get(ch, ch))
    return "".join(out)


# ------------------------------------------------------------------------ markdown

_FENCE = re.compile(r"^\s*(?:```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_BLOCKQUOTE = re.compile(r"^\s*(?:>\s?)+")
_BULLET = re.compile(r"^\s*(?:[-*+•▪◦‣]|\d{1,3}[.)])\s+")
_RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")
_REFERENCE_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_INLINE_CODE = re.compile(r"`+([^`]+?)`+")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDERSCORE = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_TERMINAL = ".!?…:;,—"


def _strip_inline_markup(line: str) -> str:
    line = _IMAGE.sub(r"\1", line)
    line = _LINK.sub(r"\1", line)
    line = _REFERENCE_LINK.sub(r"\1", line)
    line = _INLINE_CODE.sub(r"\1", line)
    line = _HTML_TAG.sub(" ", line)
    line = _BOLD.sub(r"\2", line)
    line = _ITALIC_STAR.sub(r"\1", line)
    line = _ITALIC_UNDERSCORE.sub(r"\1", line)
    line = _STRIKE.sub(r"\1", line)
    return line


def _terminate(text: str) -> str:
    """End ``text`` with a full stop unless it already ends with punctuation."""
    stripped = text.rstrip()
    if stripped and stripped[-1] not in _TERMINAL:
        return stripped + "."
    return stripped


def _strip_markdown(text: str) -> str:
    """Remove markdown/HTML markup, keeping the words.

    Headings, list items, table rows and code lines become sentences of their
    own so they are not read as one run-on sentence.
    """
    out = ""
    in_fence = False
    for raw in text.split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            out = _terminate(out)
            continue
        line = raw
        block = in_fence
        if not in_fence:
            if not line.strip() or _RULE.match(line) or _TABLE_SEPARATOR.match(line):
                out = _terminate(out)
                continue
            if _HEADING.match(line):
                line, block = _HEADING.sub("", line), True
            line = _BLOCKQUOTE.sub("", line)
            if _BULLET.match(line):
                line, block = _BULLET.sub("", line), True
            if line.count("|") >= 2:
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                line, block = ", ".join(cell for cell in cells if cell), True
        line = _strip_inline_markup(line).strip()
        if not line:
            continue
        if block:
            out = _terminate(out)
            out = f"{out} {_terminate(line)}"
        else:
            out = f"{out} {line}"
    return out.strip()


# ------------------------------------------------------------------ English rules

Rule = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]

# fmt: off
_TEENS_AND_TENS = {
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
}
# fmt: on
# fmt: off
_DECADE_PLURALS = {
    0: "hundreds", 1: "tens", 2: "twenties", 3: "thirties", 4: "forties", 5: "fifties",
    6: "sixties", 7: "seventies", 8: "eighties", 9: "nineties",
}
# fmt: on


def _decade(match: re.Match[str]) -> str:
    """1990s -> nineteen nineties, 2000s -> two thousands, 1800s -> eighteen hundreds."""
    century, decade = int(match.group(1)), int(match.group(2))
    if century == 20 and decade == 0:
        return "two thousands"
    return f"{_TEENS_AND_TENS[century]} {_DECADE_PLURALS[decade]}"


def _short_decade(match: re.Match[str]) -> str:
    return _DECADE_PLURALS[int(match.group(1))]


def _speak_url(match: re.Match[str]) -> str:
    url = match.group(0)
    core = url.rstrip(".,;:!?)'\"")
    trailing = url[len(core) :]
    core = re.sub(r"^(?:https?://)?(?:www\.)?", "", core, flags=re.IGNORECASE)
    host, _, path = core.partition("/")
    host = re.split(r"[?#:]", host, maxsplit=1)[0]
    spoken = " dot ".join(part for part in host.split(".") if part)
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    segments = [seg for seg in path.split("/") if seg][:3]
    for segment in segments:
        spoken += " slash " + re.sub(r"[-_.]+", " ", segment)
    return spoken + trailing


def _speak_email(match: re.Match[str]) -> str:
    local, domain = match.group(1), match.group(2)
    return f"{local.replace('.', ' dot ')} at {domain.replace('.', ' dot ')}"


def _sentence_end_follows(match: re.Match[str]) -> bool:
    rest = match.string[match.end() :]
    return not rest.strip() or re.match(r"\s+[\"'(]*[A-Z]", rest) is not None


def _etc(match: re.Match[str]) -> str:
    return "et cetera." if _sentence_end_follows(match) else "et cetera"


def _dotted_acronym(match: re.Match[str]) -> str:
    letters = match.group(1).replace(".", "").upper()
    return letters + "." if _sentence_end_follows(match) else letters


_CURRENCY_NAMES = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "€": ("euro", "euros", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
}
# fmt: off
_SCALES = {
    "k": "thousand", "K": "thousand", "thousand": "thousand",
    "m": "million", "M": "million", "mn": "million", "million": "million",
    "b": "billion", "B": "billion", "bn": "billion", "billion": "billion",
    "tn": "trillion", "trillion": "trillion",
}
# fmt: on


def _currency(match: re.Match[str]) -> str:
    one, many, sub_one, sub_many = _CURRENCY_NAMES[match.group(1)]
    whole, fraction, scale = match.group(2), match.group(3), match.group(4)
    if scale:
        amount = f"{whole}.{fraction}" if fraction else whole
        return f"{amount} {_SCALES[scale]} {many}"
    if fraction and len(fraction) == 2:
        unit = one if whole == "1" else many
        cents = int(fraction)
        if cents == 0:
            return f"{whole} {unit}"
        return f"{whole} {unit} and {cents} {sub_one if cents == 1 else sub_many}"
    if fraction:
        return f"{whole}.{fraction} {many}"
    return f"{whole} {one if whole == '1' else many}"


def _clock(match: re.Match[str]) -> str:
    hour, minute, meridiem = match.group(1), match.group(2), match.group(3)
    suffix = f" {meridiem.upper()}M" if meridiem else ""
    if minute == "00":
        return f"{hour}{suffix}" if meridiem else f"{hour} o'clock"
    if minute.startswith("0"):
        return f"{hour} oh {minute[1]}{suffix}"
    return f"{hour} {minute}{suffix}"


def _unit(singular: str, plural: str) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        number = match.group(1)
        return f"{number} {singular if number == '1' else plural}"

    return replace


_BYTE_UNITS = {"K": "kilobyte", "M": "megabyte", "G": "gigabyte", "T": "terabyte", "P": "petabyte"}
_BIT_RATES = {"K": "kilobits", "M": "megabits", "G": "gigabits"}


def _bytes(match: re.Match[str]) -> str:
    number, prefix = match.group(1), match.group(2)
    name = _BYTE_UNITS[prefix]
    spoken = f"{number} {name if number == '1' else name + 's'}"
    return spoken + " per second" if match.group(3) else spoken


def _decimal(match: re.Match[str]) -> str:
    return f"{match.group(1)} point {' '.join(match.group(2))}"


def _slash_pair(match: re.Match[str]) -> str:
    left, right = match.group(1), match.group(2)
    # A lone "A" is read as the article ("uh"); hyphenated it is read as a letter.
    if left == "A" or right == "A":
        return f"{left}-{right}"
    return f"{left} {right}"


_BIG_O_NAMES = {"O": "O", "Θ": "theta", "Ω": "omega"}

# Whole-word replacements for terms espeak-ng (en-us) was checked to mispronounce.
TECH_TERMS: dict[str, str] = {
    "PostgreSQL": "Post-gress Q L",
    "Postgres": "Post-gress",
    "RESTful": "restful",
    "SaaS": "sass",
    "PaaS": "pass",
    "k8s": "Kubernetes",
    "K8s": "Kubernetes",
    "C#": "C sharp",
    "F#": "F sharp",
    "ASP.NET": "A S P dot net",
    ".NET": "dot net",
    "regex": "redge-ex",
    "Regex": "Redge-ex",
    "regexes": "redge-exes",
    "enum": "e-num",
    "enums": "e-nums",
    "stdin": "standard in",
    "stdout": "standard out",
    "stderr": "standard error",
    "Redis": "Reddis",
    "IoT": "I O T",
    "idempotent": "eye-dem-potent",
    "idempotency": "eye-dem-potency",
    "pytest": "py-test",
}
_TECH_PATTERN = re.compile(
    r"(?<![\w.#+])("
    + "|".join(re.escape(term) for term in sorted(TECH_TERMS, key=len, reverse=True))
    + r")(?![\w#+])"
)

_ENGLISH_RULES: list[Rule] = [
    # Links and addresses first: they contain dots, slashes and digits.
    (re.compile(r"\b(?:https?://|www\.)[^\s<>()\[\]\"']+", re.IGNORECASE), _speak_url),
    (re.compile(r"\b([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)\b"), _speak_email),
    # Abbreviations whose dots espeak reads as sentence breaks.
    (re.compile(r"(?<![\w.])e\.\s?g\.(?=[\s,;:)]|$)", re.IGNORECASE), "for example"),
    (re.compile(r"(?<![\w.])i\.\s?e\.(?=[\s,;:)]|$)", re.IGNORECASE), "that is"),
    (re.compile(r"(?<![\w.])etc\.(?=[\s,;:)\"']|$)", re.IGNORECASE), _etc),
    (re.compile(r"(?<![\w.])vs\.?(?=\s)"), "versus"),
    (re.compile(r"(?<![\w.])approx\.(?=\s)", re.IGNORECASE), "approximately"),
    (re.compile(r"(?<![\w.])Dr\.(?=\s+[A-Z])"), "Doctor"),
    (re.compile(r"(?<![\w.])Mrs\.(?=\s+[A-Z])"), "Missus"),
    (re.compile(r"(?<![\w.])Mr\.(?=\s+[A-Z])"), "Mister"),
    (re.compile(r"(?<![\w.])Ms\.(?=\s+[A-Z])"), "Miz"),
    (re.compile(r"(?<![\w.])Prof\.(?=\s+[A-Z])"), "Professor"),
    (re.compile(r"(?<!\S)w/o(?=\s)", re.IGNORECASE), "without"),
    (re.compile(r"(?<!\S)w/(?=\s)", re.IGNORECASE), "with"),
    # U.S. -> US, a.m. -> AM (keeping a sentence-final full stop).
    (re.compile(r"(?<![\w.])((?:[A-Za-z]\.){2,})(?=[\s,;:!?)\"']|$)"), _dotted_acronym),
    (_TECH_PATTERN, lambda m: TECH_TERMS[m.group(1)]),
    (re.compile(r"\b([A-Za-z][\w-]*)\.js\b"), r"\1 J S"),
    # Complexity and powers: O(n^2) -> O of n squared.
    (
        re.compile(r"(?<!\w)([OΘΩ])\(\s*([^()]{1,40}?)\s*\)"),
        lambda m: f"{_BIG_O_NAMES[m.group(1)]} of {m.group(2)}",
    ),
    (re.compile(r"\s*\^\s*2(?!\d)"), " squared"),
    (re.compile(r"\s*\^\s*3(?!\d)"), " cubed"),
    (re.compile(r"\s*\^\s*\(?\s*(-?[\w.]+)\s*\)?"), r" to the power of \1"),
    # Money, percentages, clock times.
    (
        re.compile(
            r"([$€£])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2})(?!\d))?"
            r"(?:\s?(k|K|thousand|m|M|mn|million|b|B|bn|billion|tn|trillion)\b)?"
        ),
        _currency,
    ),
    (re.compile(r"(\d)\s?%"), r"\1 percent"),
    (re.compile(r"%"), " percent"),
    (re.compile(r"\b(\d{1,2}):([0-5]\d)(?::[0-5]\d)?(?:\s?([AaPp])\.?\s?[Mm]\b)?"), _clock),
    # Dimensions, multipliers and units after numbers.
    (re.compile(r"\b(\d+)\s?x\s?(\d+)\b"), r"\1 by \2"),
    (re.compile(r"\b(\d+(?:\.\d+)?)x\b"), r"\1 times"),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s?ms\b"), _unit("millisecond", "milliseconds")),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s?[µμ]s\b"), _unit("microsecond", "microseconds")),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s?ns\b"), _unit("nanosecond", "nanoseconds")),
    (re.compile(r"\b(\d+(?:\.\d+)?)\s?([KMGTP])B(/s)?\b"), _bytes),
    (
        re.compile(r"\b(\d+(?:\.\d+)?)\s?([KMG])bps\b"),
        lambda m: f"{m.group(1)} {_BIT_RATES[m.group(2)]} per second",
    ),
    (re.compile(r"\b(\d+(?:\.\d+)?)k\b"), r"\1 thousand"),
    # Decades: 1990s -> nineteen nineties, '80s -> eighties.
    (re.compile(r"\b(1[1-9]|20)(\d)0s\b"), _decade),
    (re.compile(r"(?<![\w'])'?([1-9])0s\b"), _short_decade),
    # Numbers with dots: IP addresses, versions, decimals (espeak reads "1.5" as "one. five").
    (re.compile(r"\b\d+(?:\.\d+){3,}\b"), lambda m: m.group(0).replace(".", " dot ")),
    (re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b"), r"\1 point \2 point \3"),
    (re.compile(r"(?<![\d.])(\d+)\.(\d+)(?!\d|\.\d)"), _decimal),
    (re.compile(r"(\d)\s?–\s?(\d)"), r"\1 to \2"),
    (re.compile(r"(?<![\d\-./:])(\d{1,4})-(\d{1,4})(?![\d\-./:])"), r"\1 to \2"),
    (re.compile(r"#\s?(\d+)"), r"number \1"),
    (re.compile(r"~\s?(?=\d)"), "approximately "),
    # Operators and arrows (code-flavoured speech).
    (re.compile(r"\s*(?:<=|≤)\s*"), " less than or equal to "),
    (re.compile(r"\s*(?:>=|≥)\s*"), " greater than or equal to "),
    (re.compile(r"\s*(?:!=|≠)\s*"), " not equal to "),
    (re.compile(r"\s*==\s*"), " equals "),
    (re.compile(r"\s*(?:->|=>|→|⇒)\s*"), " to "),
    (re.compile(r"\s*<\s*"), " less than "),
    (re.compile(r"\s*>\s*"), " greater than "),
    (re.compile(r"\s*≈\s*"), " approximately "),
    (re.compile(r"\s*±\s*"), " plus or minus "),
    (re.compile(r"\s+\*\s+|(?<=\d)\*(?=\d)|\s*×\s*"), " times "),
    (re.compile(r"\s*&\s*"), " and "),
    # Slashes: CI/CD -> C I C D, A/B -> A-B, and/or, 24/7.
    (re.compile(r"\b([A-Z]{1,6})/([A-Z]{1,6})\b"), _slash_pair),
    (re.compile(r"\band/or\b", re.IGNORECASE), "and or"),
    (re.compile(r"\b24/7\b"), "24 7"),
    (re.compile(r"\b50/50\b"), "fifty-fifty"),
]


def _expand_english(text: str) -> str:
    for pattern, replacement in _ENGLISH_RULES:
        text = pattern.sub(replacement, text)
    return text


# --------------------------------------------------------------------------- tidy

_SPACED_DASH = re.compile(r"\s+[-–—]+\s+|\s*—\s*")
_REPEATED_TERMINALS = re.compile(r"([!?])[!?]+")
_ELLIPSIS = re.compile(r"\.{3,}")
_REPEATED_MARKS = re.compile(r"([,;:])(?:\s*[,;:])+")
_SPACE_BEFORE_MARK = re.compile(r"\s+([,.;:!?…])")
_STRAY_MARKUP = re.compile(r"[*_`~|\\^{}\[\]<>]+")
_WHITESPACE = re.compile(r"\s+")
_LEADING_MARKS = re.compile(r"^[\s,.;:!?…—]+")


def _tidy(text: str) -> str:
    text = _ARROWS.sub(" ", text)
    text = _SPACED_DASH.sub(" — ", text)
    text = _ELLIPSIS.sub("…", text)
    text = _REPEATED_TERMINALS.sub(r"\1", text)
    text = _STRAY_MARKUP.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    text = _SPACE_BEFORE_MARK.sub(r"\1", text)
    text = _REPEATED_MARKS.sub(r"\1", text)
    text = _LEADING_MARKS.sub("", text)
    return text.strip()


def normalize_for_speech(text: str, *, english: bool = True) -> str:
    """Return ``text`` rewritten for speech synthesis.

    Markup, emoji and control characters are removed for every language; the
    English rules (abbreviations, symbols, number formats, tech terms) only run
    when ``english`` is true. The result may be empty when nothing speakable
    remains.
    """
    text = _clean_unicode(text)
    text = _strip_markdown(text)
    if english:
        text = _expand_english(text)
    return _tidy(text)
