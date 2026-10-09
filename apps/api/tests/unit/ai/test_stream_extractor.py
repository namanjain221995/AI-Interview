"""Property tests for StreamingJsonFieldExtractor with random objects and
random chunkings (a fixed seed keeps failures reproducible)."""

from __future__ import annotations

import json
import random

import pytest

from app.ai.base import AIOutputInvalid
from app.ai.mock import minimal_payload
from app.ai.structured import StreamingJsonFieldExtractor
from app.schemas.ai import TurnDecision

ALPHABET = (
    list("abcdefghijklmnopqrstuvwxyz ABCXYZ0123456789.,!?:;-_'()[]{}")
    + ['"', "\\", "\n", "\t", "\r", "\b", "\f", "/", "é", "ü", "—", "€", "中"]
    + ["\U0001f600", "\U0001f680", "\U0001d11e"]  # astral: surrogate pairs when ensure_ascii=True
    + ["\x01", "\x1f"]
)


def random_text(rng: random.Random, max_len: int = 60) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, max_len)))


def random_value(rng: random.Random, depth: int = 0):
    kind = rng.randint(0, 6 if depth < 3 else 3)
    if kind == 0:
        return random_text(rng, 20)
    if kind == 1:
        return rng.randint(-1000, 1000)
    if kind == 2:
        return rng.choice([True, False, None, 3.25, -0.5e-3])
    if kind == 3:
        return '"speech": "decoy"'
    if kind == 4:
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    obj = {random_text(rng, 8): random_value(rng, depth + 1) for _ in range(rng.randint(0, 3))}
    if rng.random() < 0.5:
        obj["speech"] = "nested decoy {" + random_text(rng, 5)  # must never be picked up
    return obj


def random_document(rng: random.Random, speech: str) -> str:
    fields: list[tuple[str, object]] = [
        (f"k{i}_{random_text(rng, 4)}", random_value(rng)) for i in range(rng.randint(0, 5))
    ]
    fields = [(k.replace("speech", "sp"), v) for k, v in fields]
    position = rng.choice([0, 0, len(fields), rng.randint(0, len(fields))])
    fields.insert(position, ("speech", speech))
    obj = dict(fields)
    style = rng.randint(0, 3)
    ensure_ascii = rng.random() < 0.5
    if style == 0:
        text = json.dumps(obj, ensure_ascii=ensure_ascii)
    elif style == 1:
        text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=2)
    elif style == 2:
        text = json.dumps(obj, ensure_ascii=ensure_ascii, separators=(",", ":"))
    else:  # vLLM guided decoding style: newline before commas
        parts = [f"{json.dumps(k)} : {json.dumps(v, ensure_ascii=ensure_ascii)}" for k, v in obj.items()]
        text = "{\n  " + "\n,\n  ".join(parts) + "\n}"
    if rng.random() < 0.2:
        text = "```json\n" + text + "\n```"
    if rng.random() < 0.1:
        text = '<think>\nmaybe {"speech": "no"}\n</think>\n' + text
    return text


def chunk(text: str, rng: random.Random) -> list[str]:
    mode = rng.randint(0, 2)
    if mode == 0:
        return list(text)  # one character at a time
    chunks, i = [], 0
    while i < len(text):
        size = rng.randint(1, 4 if mode == 1 else 40)
        chunks.append(text[i : i + size])
        i += size
    return chunks


@pytest.mark.parametrize("seed", range(40))
def test_random_documents_and_chunkings(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(50):
        speech = random_text(rng)
        document = random_document(rng, speech)
        extractor = StreamingJsonFieldExtractor("speech")
        out: list[str] = []
        done_at: int | None = None
        consumed = 0
        for piece in chunk(document, rng):
            out.append(extractor.feed(piece))
            consumed += len(piece)
            if extractor.done and done_at is None:
                done_at = consumed
        assert "".join(out) == speech, document
        assert extractor.value == speech
        assert extractor.found and extractor.done
        assert extractor.raw_text == document
        # "done" fires on the chunk that carries the closing quote of the value
        closing = document.index(json.dumps(speech, ensure_ascii=False)[0:1])  # sanity: a quote exists
        assert done_at is not None and done_at > closing


@pytest.mark.parametrize("seed", range(10))
def test_incremental_output_is_prefix_monotonic(seed: int) -> None:
    rng = random.Random(1000 + seed)
    speech = 'Hello "there" \\ path\\to\nnew\tline \U0001f600 café'
    document = json.dumps({"speech": speech, "action": "ask_question"}, ensure_ascii=True)
    extractor = StreamingJsonFieldExtractor()
    so_far = ""
    for piece in chunk(document, rng):
        so_far += extractor.feed(piece)
        assert speech.startswith(so_far)
    assert so_far == speech


def test_surrogate_pair_split_across_every_boundary() -> None:
    document = '{"speech": "a\\ud83d\\ude00b"}'
    for cut in range(1, len(document)):
        extractor = StreamingJsonFieldExtractor()
        first = extractor.feed(document[:cut])
        second = extractor.feed(document[cut:])
        assert first + second == "a\U0001f600b", cut
        assert "\ud83d" not in first  # a lone high surrogate is never emitted


def test_lone_surrogates_become_replacement_char() -> None:
    extractor = StreamingJsonFieldExtractor()
    assert extractor.feed('{"speech": "x\\ud83dy\\ude00z"}') == "x�y�z"


def test_done_not_set_until_closing_quote_and_state_after() -> None:
    extractor = StreamingJsonFieldExtractor()
    assert extractor.feed('{"spe') == ""
    assert not extractor.found
    assert extractor.feed('ech" : "Hi') == "Hi"
    assert extractor.found and not extractor.done
    assert extractor.feed(" there\\") == " there"
    assert extractor.feed('"') == '"'
    assert not extractor.done
    assert extractor.feed('!", "action": "x"') == "!"
    assert extractor.done
    assert not extractor.object_closed
    assert extractor.feed("}") == ""
    assert extractor.object_closed


def test_field_absent_or_not_a_string() -> None:
    extractor = StreamingJsonFieldExtractor()
    assert extractor.feed('{"action": "ask", "speech": 42, "x": {"speech": "nested"}}') == ""
    assert not extractor.found
    assert extractor.object_closed


def test_first_occurrence_wins_and_other_fields_work() -> None:
    extractor = StreamingJsonFieldExtractor("reason")
    text = '{"speech": "a", "reason": "because \\"x\\"", "reason": "second"}'
    assert extractor.feed(text) == 'because "x"'


def test_finalize_validates_full_turn_decision() -> None:
    payload = minimal_payload(TurnDecision)
    payload["speech"] = 'Could you describe the "hardest" bug you fixed?'
    document = json.dumps(payload, indent=1)
    extractor = StreamingJsonFieldExtractor()
    spoken = "".join(extractor.feed(c) for c in chunk(document, random.Random(7)))
    assert spoken == payload["speech"]
    decision = extractor.finalize(TurnDecision).value
    assert decision.speech == spoken


def test_finalize_raises_on_invalid_object() -> None:
    extractor = StreamingJsonFieldExtractor()
    extractor.feed('{"speech": "hi", "action": "dance"}')
    with pytest.raises(AIOutputInvalid):
        extractor.finalize(TurnDecision)
