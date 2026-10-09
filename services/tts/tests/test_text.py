from __future__ import annotations

import pytest

from tts_service.text import TECH_TERMS, normalize_for_speech


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # markdown
        ("**Great answer!** Let's go.", "Great answer! Let's go."),
        ("That is *really* _quite_ ~~bad~~ good.", "That is really quite bad good."),
        ("Use `git rebase` here.", "Use git rebase here."),
        ("See [the docs](https://example.com/docs) now.", "See the docs now."),
        ("![diagram](img.png) Look at it.", "diagram Look at it."),
        ("## Next steps\n- Review the PR\n- Ship it", "Next steps. Review the PR. Ship it."),
        ("1. First question\n2) Second question", "First question. Second question."),
        ("> quoted advice", "quoted advice"),
        ("Intro\n\n---\n\nOutro", "Intro. Outro"),
        ("| Name | Score |\n|---|---|\n| Ana | 9 |", "Name, Score. Ana, 9."),
        ("```python\nx = 1\n```\nDone", "x = 1. Done"),
        ("Line one<br>line two", "Line one line two"),
        ("snake_case_name and __init__ stay", "snake case name and init stay"),
        # control characters, emoji, unicode
        ("Thanks!! 😀 Great 👍🏽.", "Thanks! Great."),
        ("zero\u200bwidth\u0007 bell\u00ad", "zerowidth bell"),
        ("tab\tseparated\r\nlines", "tab separated lines"),
        (
            "It’s “quoted” — «really»…",
            'It\'s “quoted” — "really"…',
        ),  # curly double quotes are in Kokoro's vocabulary
        ("Wait... what?!", "Wait… what?"),
        ("ﬁne Ｆｕｌｌ", "fine Full"),
        ("Acme™ and Foo®", "Acme and Foo"),
        ("Fish &amp; chips", "Fish and chips"),
        ("Go → now ↗", "Go to now"),
    ],
)
def test_markup_and_unicode(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kafka, e.g. for events.", "Kafka, for example for events."),
        ("E.g., this one.", "for example, this one."),
        ("Queues, i.e. buffers.", "Queues, that is buffers."),
        ("Apples, pears, etc. are fine.", "Apples, pears, et cetera are fine."),
        ("Apples, pears, etc. Next point.", "Apples, pears, et cetera. Next point."),
        ("We like apples, pears, etc.", "We like apples, pears, et cetera."),
        ("Postgres vs. MySQL vs Mongo", "Post-gress versus MySQL versus Mongo"),
        ("Open VS Code now", "Open VS Code now"),
        ("approx. 5 people", "approximately 5 people"),
        (
            "Dr. Smith and Mrs. Jones met Mr. Lee and Ms. Wu with Prof. Kim.",
            "Doctor Smith and Missus Jones met Mister Lee and Miz Wu with Professor Kim.",
        ),
        ("Come w/ me, not w/o a plan.", "Come with me, not without a plan."),
        ("The U.S. market", "The US market"),
        ("We moved to the U.S. Then we grew.", "We moved to the US. Then we grew."),
        ("Based in the U.K.", "Based in the UK."),
    ],
)
def test_abbreviations(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Revenue grew 50% and margin 3.5 %.", "Revenue grew 50 percent and margin 3 point 5 percent."),
        ("It costs $100.", "It costs 100 dollars."),
        ("It costs $1.", "It costs 1 dollar."),
        ("Lunch is $3.50 or $1.01.", "Lunch is 3 dollars and 50 cents or 1 dollar and 1 cent."),
        ("Raised $1.5M and €2bn.", "Raised 1 point 5 million dollars and 2 billion euros."),
        ("About £20 or €1,000.", "About 20 pounds or 1,000 euros."),
        ("Salary $120k.", "Salary 120 thousand dollars."),
        ("Pi is 3.14.", "Pi is 3 point 1 4."),
        ("Score was 9.5. Next.", "Score was 9 point 5. Next."),
        ("Use Python 3.12.1 now", "Use Python 3 point 12 point 1 now"),
        ("Server 192.168.1.10 is up", "Server 192 dot 168 dot 1 dot 10 is up"),
        ("Meet at 3:30 pm or 10:00 a.m. or 9:05.", "Meet at 3 30 PM or 10 AM or 9 oh 5."),
        ("At 12:00 sharp", "At 12 o'clock sharp"),
        (
            "A 1920x1080 screen is 2x faster, even 1.5x.",
            "A 1920 by 1080 screen is 2 times faster, even 1 point 5 times.",
        ),
        (
            "Latency 200ms then 1ms and 5 µs and 3ns",
            "Latency 200 milliseconds then 1 millisecond and 5 microseconds and 3 nanoseconds",
        ),
        (
            "Store 5GB, 1 MB and 2TB at 10 GB/s over 100Mbps",
            "Store 5 gigabytes, 1 megabyte and 2 terabytes at 10 gigabytes per second over 100 megabits per second",
        ),
        ("We have 100k users.", "We have 100 thousand users."),
        (
            "In the 1990s and the 2000s and the '80s and her 30s",
            "In the nineteen nineties and the two thousands and the eighties and her thirties",
        ),
        ("The 1800s and 2010s", "The eighteen hundreds and twenty tens"),
        ("5-10 years, 2–3 days", "5 to 10 years, 2 to 3 days"),
        ("Dated 2024-01-15", "Dated 2024-01-15"),
        ("Our #1 priority, ~5 people", "Our number 1 priority, approximately 5 people"),
        (
            "It is -5 degrees and 30°C, 451°F, 90°",
            "It is -5 degrees and 30 degrees Celsius, 451 degrees Fahrenheit, 90 degrees",
        ),
        ("2½ hours and ½ day", "2 and a half hours and one half day"),
    ],
)
def test_numbers_units_and_symbols(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Your solution is O(n^2).", "Your solution is O of n squared."),
        ("Can you reach O(n log n) or O(1)?", "Can you reach O of n log n or O of 1?"),
        ("Θ(n) and Ω(1)", "theta of n and omega of 1"),
        ("n² and n³ and 2^n", "n squared and n cubed and 2 to the power of n"),
        (
            "If x <= 5 and y >= 2 and z != 3",
            "If x less than or equal to 5 and y greater than or equal to 2 and z not equal to 3",
        ),
        ("if a == b -> return; x => y", "if a equals b to return; x to y"),
        ("x < 5 and y > 2", "x less than 5 and y greater than 2"),
        ("a ≈ b ± c ≤ d", "a approximately b plus or minus c less than or equal to d"),
        ("x * 2 and 2*3 and 3 × 4", "x times 2 and 2 times 3 and 3 times 4"),
        ("R&D and Q & A", "R and D and Q and A"),
        ("CI/CD, UI/UX, TCP/IP, I/O, A/B tests", "CI CD, UI UX, TCP IP, I O, A-B tests"),
        ("and/or 24/7 a 50/50 split", "and or 24 7 a fifty-fifty split"),
    ],
)
def test_code_and_operators(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("We run PostgreSQL and Postgres.", "We run Post-gress Q L and Post-gress."),
        ("A RESTful SaaS on PaaS", "A restful sass on pass"),
        ("Deploy to k8s or K8s", "Deploy to Kubernetes or Kubernetes"),
        ("C#, F#, .NET and ASP.NET", "C sharp, F sharp, dot net and A S P dot net"),
        ("Node.js, Next.js and vue.js", "Node J S, Next J S and vue J S"),
        ("A regex, two regexes, an enum, enums", "A redge-ex, two redge-exes, an e-num, e-nums"),
        ("Read stdin, write stdout and stderr.", "Read standard in, write standard out and standard error."),
        ("Redis for IoT", "Reddis for I O T"),
        ("Make it idempotent; idempotency matters.", "Make it eye-dem-potent; eye-dem-potency matters."),
        ("Run pytest.", "Run py-test."),
        ("myregex and Enumerate stay", "myregex and Enumerate stay"),
    ],
)
def test_tech_terms(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


def test_tech_table_is_small_and_meaning_preserving() -> None:
    assert len(TECH_TERMS) <= 40
    for term, spoken in TECH_TERMS.items():
        assert spoken and spoken != term
        assert normalize_for_speech(f"We use {term} daily.") == f"We use {spoken} daily."


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Check https://www.example.com/careers/eng?ref=x now.",
            "Check example dot com slash careers slash eng now.",
        ),
        ("Visit www.acme.io.", "Visit acme dot io."),
        ("Mail jobs@example.com today.", "Mail jobs at example dot com today."),
    ],
)
def test_urls_and_emails(raw: str, expected: str) -> None:
    assert normalize_for_speech(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "😀😀", "***", "\u200b\u200d", "---", "|---|"])
def test_nothing_speakable(raw: str) -> None:
    assert normalize_for_speech(raw) == ""


def test_plain_text_is_untouched() -> None:
    text = "Tell me about a time you disagreed with a teammate, and how you resolved it."
    assert normalize_for_speech(text) == text


def test_non_english_skips_english_rules_but_strips_markup() -> None:
    assert normalize_for_speech("**Hola** 50% e.g. 3.5 😀", english=False) == "Hola 50% e.g. 3.5"


@pytest.mark.parametrize(
    "raw",
    [
        "**Great answer!** Let's talk about *distributed systems*, e.g. Kafka & Redis.",
        "Your solution is O(n^2); can you get it to O(n log n)?",
        "We grew 50% YoY — revenue hit $1.5M in the 1990s… or the '80s?",
        "Meet at 3:30 p.m. with Dr. Smith in the U.S. office.",
        "Use C#, .NET and Node.js for the CI/CD pipeline and A/B tests.",
        "Latency dropped from 200ms to 1.5ms, a 10x improvement at ~5 GB/s.",
        "Version 3.12.1 at 192.168.1.10, 5-10 years, 2–3 days, #1 priority.",
    ],
)
def test_idempotent(raw: str) -> None:
    once = normalize_for_speech(raw)
    assert normalize_for_speech(once) == once
