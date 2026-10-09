from __future__ import annotations

import base64
import json

import pytest
import respx

from app.ai.base import AITimeout, AIUnavailable
from app.ai.clients.ocr import UnlimitedOcrClient, collapse_repetition, is_degenerate, strip_layout_tags

BASE = "http://ocr.test/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

REAL_OUTPUT = """ result
title [33, 46, 275, 102]JOHN SMITH
text [34, 116, 495, 157]john.smith@example.com | +1 555 0100
title [37, 203, 277, 251]EXPERIENCE
text [35, 272, 637, 310]Senior Backend Engineer, Acme Corp (2019 - present)
text [35, 343, 761, 382]- Built a payments platform handling 10,000 requests per second"""

CHATTY_OUTPUT = """. The image contains only a single, continuous horizontal line. According to Rule 2, such lines must be ignored.

[Empty String]
text [43, 143, 273, 274]JANE DOE
text [44, 375, 624, 509]Senior Software Engineer"""


def completion(content: str, finish: str = "stop") -> dict:
    return {
        "choices": [{"index": 0, "message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
    }


@pytest.fixture
def ocr(endpoint_factory, ledger) -> UnlimitedOcrClient:
    return UnlimitedOcrClient(
        endpoint_factory("ocr", BASE, retries=1),
        model="ocr-model",
        prompt="OCR",
        max_tokens=777,
        ledger=ledger,
    )


def test_strip_layout_tags_on_real_output() -> None:
    assert strip_layout_tags(REAL_OUTPUT) == (
        "JOHN SMITH\njohn.smith@example.com | +1 555 0100\nEXPERIENCE\n"
        "Senior Backend Engineer, Acme Corp (2019 - present)\n"
        "- Built a payments platform handling 10,000 requests per second"
    )


def test_chatter_before_first_block_is_dropped() -> None:
    assert strip_layout_tags(CHATTY_OUTPUT) == "JANE DOE\nSenior Software Engineer"


def test_continuation_lines_and_untagged_output_kept() -> None:
    raw = "text [1, 2, 3, 4]First line of a paragraph\nthat continues here\ntable [5, 6, 7.5, 8]A | B"
    assert strip_layout_tags(raw) == "First line of a paragraph\nthat continues here\nA | B"
    assert strip_layout_tags("plain text output\nsecond line") == "plain text output\nsecond line"


def test_degenerate_detection() -> None:
    looping = "\n".join(["text [1, 2, 3, 4]Skills: Python"] * 3 + ["Python, Go"] * 20)
    assert is_degenerate(strip_layout_tags(looping))
    tail_loop = "Summary of experience. " + "and the and the " * 40
    assert is_degenerate(tail_loop)
    assert is_degenerate("Intro " + "abc " * 35, finish_reason="length")
    assert not is_degenerate(strip_layout_tags(REAL_OUTPUT))
    assert not is_degenerate("Name\n" + "-" * 400 + "\nEnd")  # separators are not loops
    table = "\n".join(f"row {i} | 0 | 0 | 0" for i in range(30))
    assert not is_degenerate(table)


def test_collapse_repetition() -> None:
    text = "Header\n" + "Python, Go\n" * 10 + "Footer"
    assert collapse_repetition(text) == "Header\nPython, Go\nFooter"
    assert collapse_repetition("Intro " + "xyz " * 30).count("xyz") <= 2


async def test_request_body_and_ok_result(ocr: UnlimitedOcrClient, ledger) -> None:
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").respond(200, json=completion(REAL_OUTPUT))
        result = await ocr.ocr_image(PNG, mime_type="image/png")
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "ocr-model"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 777
    image_part, text_part = body["messages"][0]["content"]
    assert image_part["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert text_part == {"type": "text", "text": "OCR"}
    assert result.status == "ok"
    assert result.text.startswith("JOHN SMITH")
    assert result.raw == REAL_OUTPUT
    assert ledger.records[-1].endpoint == "ocr" and ledger.records[-1].completion_tokens == 120


async def test_degenerate_and_empty_statuses(ocr: UnlimitedOcrClient) -> None:
    loop = "text [1, 2, 3, 4]Experience\n" + "Python developer\n" * 40
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(200, json=completion(loop, finish="length"))
        result = await ocr.ocr_image(PNG)
    assert result.status == "degenerate"
    assert result.text == "Experience\nPython developer"
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(200, json=completion("[Empty String]"))
        assert (await ocr.ocr_image(PNG)).status == "empty"


async def test_rejected_image_returns_error_status(ocr: UnlimitedOcrClient) -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(400, json={"error": {"message": "image too large"}})
        result = await ocr.ocr_image(PNG)
    assert result.status == "error"
    assert "image too large" in result.raw


async def test_transport_failures_raise(ocr: UnlimitedOcrClient) -> None:
    import httpx

    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(503)
        with pytest.raises(AIUnavailable):
            await ocr.ocr_image(PNG)
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(AITimeout):
            await ocr.ocr_image(PNG)
        assert route.call_count == 1  # long generations are not re-run


async def test_input_validation(ocr: UnlimitedOcrClient) -> None:
    assert (await ocr.ocr_image(b"")).status == "empty"
    assert (await ocr.ocr_image(PNG, mime_type="application/pdf")).status == "error"
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").respond(200, json=completion("text [1, 2, 3, 4]x"))
        await ocr.ocr_image(PNG, mime_type="image/jpg", prompt="Custom")
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["messages"][0]["content"][1]["text"] == "Custom"


async def test_context_overflow_retries_with_smaller_budget(ocr: UnlimitedOcrClient) -> None:
    import httpx

    message = (
        "This model's maximum context length is 8192 tokens. However, you requested 777 output tokens and "
        "your prompt contains at least 7700 input tokens, for a total of at least 8477 tokens."
    )
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").mock(
            side_effect=[
                httpx.Response(400, json={"error": {"message": message}}),
                httpx.Response(200, json=completion("text [1, 2, 3, 4]Page text")),
            ]
        )
        result = await ocr.ocr_image(PNG)
    assert result.status == "ok" and result.text == "Page text"
    assert json.loads(route.calls[1].request.content)["max_tokens"] == 8192 - 7700 - 16


async def test_context_overflow_without_room_is_error(ocr: UnlimitedOcrClient) -> None:
    message = (
        "maximum context length is 8192 tokens. However, your prompt contains at least 8100 input tokens"
    )
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").respond(400, json={"error": {"message": message}})
        result = await ocr.ocr_image(PNG)
    assert result.status == "error"
    assert route.call_count == 1
