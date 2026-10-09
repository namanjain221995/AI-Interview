from __future__ import annotations

import pytest

from app.realtime.chunker import SentenceChunker


def run(text: str, step: int = 3, **kwargs) -> list[str]:
    chunker = SentenceChunker(**kwargs)
    out: list[str] = []
    for i in range(0, len(text), step):
        out += chunker.feed(text[i : i + step])
    out += chunker.flush()
    return out


@pytest.mark.parametrize("step", [1, 2, 5, 17, 1000])
def test_sentences_are_split_at_ends_regardless_of_delta_size(step: int) -> None:
    text = "Thanks, Jane. How did you size the node groups? Walk me through the trade-offs you considered!"
    assert run(text, step) == [
        "Thanks, Jane.",
        "How did you size the node groups?",
        "Walk me through the trade-offs you considered!",
    ]


def test_abbreviations_initials_and_decimals_do_not_split() -> None:
    text = (
        "We used e.g. Karpenter and i.e. spot pools. Dr. Smith reviewed it with J. Doe at 9 a.m. today. "
        "Error rates fell from 3.5 to 0.4 percent on Python 3.12 services. Next question please."
    )
    chunks = run(text, 4)
    assert chunks[0] == "We used e.g. Karpenter and i.e. spot pools."
    assert "Dr. Smith reviewed it with J. Doe at 9 a.m. today." in chunks[1]
    assert any("3.5 to 0.4 percent on Python 3.12 services." in c for c in chunks)
    assert chunks[-1].endswith("Next question please.")
    assert "".join(chunks).count(".") == text.count(".")


def test_trailing_period_waits_for_next_character_when_ambiguous() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("The value was 3.") == []
    assert chunker.feed("5 percent. Then") == ["The value was 3.5 percent."]
    assert chunker.flush() == ["Then"]
    chunker = SentenceChunker()
    assert chunker.feed("We used e.") == []  # could become "e.g."
    assert chunker.feed("g. spot instances.") == ["We used e.g. spot instances."]


def test_unambiguous_sentence_end_is_emitted_without_waiting() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Thanks for that.") == ["Thanks for that."]
    assert chunker.feed(" What changed?") == []  # short later sentence is held for merging
    assert chunker.flush() == ["What changed?"]


def test_first_chunk_breaks_early_at_comma_after_16_chars() -> None:
    chunker = SentenceChunker()
    out = chunker.feed("You mentioned the migration to EKS, how long did")
    assert out == ["You mentioned the migration to EKS,"]
    assert chunker.feed(" it take") == []  # later chunks wait for the sentence end
    assert chunker.feed(" overall?") == ["how long did it take overall?"]
    assert chunker.flush() == []


def test_first_comma_before_16_chars_does_not_break() -> None:
    chunker = SentenceChunker()
    assert chunker.feed("Okay, so tell me") == []
    assert chunker.feed(" more.") == ["Okay, so tell me more."]
    chunker = SentenceChunker()
    assert chunker.feed("That makes sense, and the next") == ["That makes sense,"]


def test_first_chunk_hard_breaks_near_64_chars_at_a_word_boundary() -> None:
    text = "So tell me how you would approach designing a highly available payment service across three regions with strict consistency"
    chunks = run(text, 7)
    assert len(chunks[0]) <= 64
    assert not chunks[0].endswith(" ")
    assert " ".join(chunks) == text


@pytest.mark.parametrize("step", [1, 4, 9, 1000])
def test_forced_first_cut_prefers_a_phrase_boundary(step: int) -> None:
    text = "Your resume mentions designing a production EKS platform for ten thousand users across three clusters."
    chunks = run(text, step)
    assert chunks[0] == "Your resume mentions designing a production EKS platform"
    assert chunks[1] == "for ten thousand users across three clusters."


def test_second_chunk_is_bounded_by_the_first_chunk_length() -> None:
    """After a short first chunk the second one stays short enough to be
    synthesized while the first plays (no gap), cut at a phrase boundary."""
    text = (
        "Thanks, Jane. Could you walk me through how you decided which workloads were safe "
        "to move to spot instances and what you did about interruptions?"
    )
    chunks = run(text, 5)
    assert chunks[0] == "Thanks, Jane."
    assert len(chunks[1]) <= 60 and chunks[1] == "Could you walk me through how you decided"
    assert chunks[2].startswith("which workloads were safe")
    assert " ".join(chunks) == text


def test_later_chunks_are_bounded_by_max_chars() -> None:
    sentence = "First sentence here. "
    long_tail = "word " * 120
    chunks = run(sentence + long_tail, 11)
    assert chunks[0] == "First sentence here."
    assert all(len(c) <= 220 for c in chunks[1:])
    assert " ".join(chunks).split() == (sentence + long_tail).split()


def test_short_later_sentences_merge_with_the_next() -> None:
    chunks = run("Great. Okay. Right. Now let's talk about monitoring and alerting.", 3)
    assert chunks[0] == "Great."
    assert chunks[1] == "Okay. Right. Now let's talk about monitoring and alerting."


def test_flush_emits_everything_and_resets() -> None:
    chunker = SentenceChunker()
    chunker.feed("No terminal punctuation")
    assert chunker.flush() == ["No terminal punctuation"]
    assert chunker.flush() == []


def test_closing_quotes_stay_with_sentence() -> None:
    chunks = run('She said "ship it." Then we did.', 2)
    assert chunks == ['She said "ship it."', "Then we did."]


def test_ellipsis_followed_by_lowercase_does_not_split() -> None:
    chunks = run("Well... let me think about that for a second. Sure.", 3)
    assert chunks[0] == "Well... let me think about that for a second."
