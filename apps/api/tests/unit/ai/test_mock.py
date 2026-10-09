from __future__ import annotations

import inspect
import json

import numpy as np
import pytest
from pydantic import BaseModel

import app.schemas.ai as ai_schemas
from app.ai.base import (
    AIOutputInvalid,
    AIServices,
    AIUnavailable,
    CallContext,
    EmbeddingClient,
    LLMClient,
    OcrClient,
    OcrResult,
    RerankerClient,
    SttClient,
    TranscriptionResult,
    TtsProvider,
)
from app.ai.mock import MockAIServices, minimal_instance, minimal_payload
from app.ai.registry import get_ai
from app.ai.structured import StreamingJsonFieldExtractor
from app.domain.enums import AnswerQuality, ModelTask, TurnAction
from app.schemas.ai import AnswerAssessment, InterviewPlan, TurnDecision

TURN = CallContext(task=ModelTask.TURN_DECISION)
MSGS = [{"role": "user", "content": "hi"}]

SCHEMAS = sorted(
    (
        obj
        for name, obj in vars(ai_schemas).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj.__module__ == ai_schemas.__name__
        and obj is not ai_schemas.AIModel
    ),
    key=lambda cls: cls.__name__,
)


def test_every_ai_schema_is_covered() -> None:
    assert len(SCHEMAS) >= 40
    assert TurnDecision in SCHEMAS and InterviewPlan in SCHEMAS


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
def test_minimal_instance_is_valid_for_every_schema(schema: type[BaseModel]) -> None:
    instance = minimal_instance(schema)
    assert isinstance(instance, schema)
    payload = minimal_payload(schema)
    assert schema.model_validate_json(json.dumps(payload)) == instance


def test_minimal_payload_respects_constraints() -> None:
    plan = minimal_payload(InterviewPlan)
    assert len(plan["segments"]) == 1  # min_length=1
    assert plan["segments"][0]["time_budget_minutes"] == 0.5  # ge=0.5
    decision = minimal_payload(TurnDecision)
    assert decision["speech"]
    assert decision["action"] == TurnAction.INTRODUCE.value
    assert decision["answer_quality"] is None  # nullable -> null
    assert set(decision) == set(TurnDecision.model_fields)  # every field is required
    assert minimal_payload(ai_schemas.GeneratedQuestionSet)["questions"][0]["text"]


async def test_mock_services_satisfy_protocols(mock_ai: MockAIServices) -> None:
    assert isinstance(mock_ai, AIServices)
    assert isinstance(mock_ai.llm, LLMClient)
    assert isinstance(mock_ai.embeddings, EmbeddingClient)
    assert isinstance(mock_ai.reranker, RerankerClient)
    assert isinstance(mock_ai.stt, SttClient)
    assert isinstance(mock_ai.ocr, OcrClient)
    assert isinstance(mock_ai.tts, TtsProvider)
    assert get_ai() is mock_ai
    health = await mock_ai.health()
    assert set(health) == {"main", "vision", "embedding", "reranker", "whisper", "ocr", "tts"}


async def test_structured_unscripted_returns_minimal_instance(mock_ai: MockAIServices) -> None:
    result = await mock_ai.llm.structured(TurnDecision, MSGS, context=TURN)
    assert isinstance(result.value, TurnDecision)
    assert result.attempts == 1 and not result.used_fallback
    assert mock_ai.llm.calls[-1].method == "structured"
    assert mock_ai.llm.calls[-1].schema is TurnDecision


async def test_structured_scripted_values_sequence_and_retry(mock_ai: MockAIServices) -> None:
    assessment = AnswerAssessment(quality=AnswerQuality.STRONG, confidence=0.9)
    ctx = CallContext(task=ModelTask.ANSWER_ASSESSMENT)
    mock_ai.llm.script(
        ModelTask.ANSWER_ASSESSMENT,
        assessment,
        {"quality": "vague", "confidence": 0.4},
        "garbage",
        assessment,
    )
    first = await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx)
    second = await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx)
    third = await mock_ai.llm.structured(
        AnswerAssessment, MSGS, context=ctx
    )  # "garbage" -> retry -> assessment
    assert first.value == assessment
    assert second.value.quality is AnswerQuality.VAGUE
    assert third.value == assessment and third.attempts == 2
    assert mock_ai.llm.pending(ModelTask.ANSWER_ASSESSMENT) == 0


async def test_structured_callable_exception_and_fallback(mock_ai: MockAIServices) -> None:
    ctx = CallContext(task=ModelTask.ANSWER_ASSESSMENT)
    mock_ai.llm.script(
        ModelTask.ANSWER_ASSESSMENT,
        lambda call: {"quality": "partial", "confidence": len(call.messages) / 10},
    )
    result = await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx)
    assert result.value.confidence == pytest.approx(0.1)
    mock_ai.llm.script(ModelTask.ANSWER_ASSESSMENT, AIUnavailable("main down"))
    with pytest.raises(AIUnavailable):
        await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx)
    mock_ai.llm.script(ModelTask.ANSWER_ASSESSMENT, AIUnavailable("main down"))
    fb = AnswerAssessment(quality=AnswerQuality.ADEQUATE, confidence=0.0)
    result = await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx, fallback=lambda: fb)
    assert result.used_fallback and result.value == fb
    mock_ai.llm.set_default(ModelTask.ANSWER_ASSESSMENT, "{}")
    with pytest.raises(AIOutputInvalid):
        await mock_ai.llm.structured(AnswerAssessment, MSGS, context=ctx, max_attempts=2)


async def test_chat_scripts_and_defaults(mock_ai: MockAIServices) -> None:
    mock_ai.llm.script(None, "wildcard")
    assert (await mock_ai.llm.chat(MSGS, context=TURN)).text == "wildcard"
    assert (await mock_ai.llm.chat(MSGS, context=TURN)).text == "Mock response for turn_decision."
    mock_ai.llm.set_default(ModelTask.TURN_DECISION, {"a": 1})
    assert json.loads((await mock_ai.llm.chat(MSGS, context=TURN)).text) == {"a": 1}
    mock_ai.llm.reset()
    result = await mock_ai.llm.chat(MSGS, context=TURN, json_schema=AnswerAssessment.model_json_schema())
    AnswerAssessment.model_validate_json(result.text)
    assert result.endpoint == "main"
    vision = await mock_ai.llm.chat(MSGS, context=CallContext(task=ModelTask.FRAME_ANALYSIS))
    assert vision.endpoint == "vision"
    assert len(mock_ai.llm.calls_for(ModelTask.TURN_DECISION)) == 1


async def test_stream_yields_chunks_and_supports_extractor(mock_ai: MockAIServices) -> None:
    decision = minimal_instance(TurnDecision).model_copy(
        update={"speech": 'Say "more" about \\that\\ please.'}
    )
    mock_ai.llm.script(ModelTask.TURN_DECISION, decision)
    stream = mock_ai.llm.stream(MSGS, context=TURN)
    extractor = StreamingJsonFieldExtractor()
    chunks, spoken = [], ""
    async for chunk in stream:
        chunks.append(chunk)
        spoken += extractor.feed(chunk)
    assert len(chunks) > 5 and all(len(c) <= 6 for c in chunks)
    assert spoken == decision.speech
    assert extractor.finalize(TurnDecision).value == decision
    assert stream.result is not None and stream.result.text == decision.model_dump_json()


async def test_stream_aclose_and_scripted_error(mock_ai: MockAIServices) -> None:
    mock_ai.llm.script(ModelTask.TURN_DECISION, "a" * 60)
    stream = mock_ai.llm.stream(MSGS, context=TURN)
    assert await stream.__anext__() == "aaaaaa"
    await stream.aclose()
    assert stream.closed
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    mock_ai.llm.script(ModelTask.TURN_DECISION, AIUnavailable("down"))
    with pytest.raises(AIUnavailable):
        async for _ in mock_ai.llm.stream(MSGS, context=TURN):
            pass


async def test_embeddings_similarity_behaviour(mock_ai: MockAIServices) -> None:
    emb = mock_ai.embeddings
    a, b, c, d = await emb.embed(
        [
            "How would you scale a Kubernetes cluster for a traffic spike?",
            "How do you scale Kubernetes clusters during traffic spikes?",
            "Tell me about a conflict with a teammate and how you resolved it.",
            "How would you scale a Kubernetes cluster for a traffic spike?",
        ]
    )
    cos = lambda x, y: float(np.dot(x, y))  # noqa: E731 - vectors are unit length
    assert len(a) == 1024
    assert np.isclose(np.linalg.norm(a), 1.0, atol=1e-5)
    assert cos(a, d) == pytest.approx(1.0, abs=1e-6)
    assert cos(a, b) > 0.75
    assert cos(a, c) < 0.25
    query = await emb.embed_query("scale kubernetes cluster", "Find questions")
    assert cos(query, a) > cos(query, c)
    empty = await emb.embed_one("")
    assert np.isclose(np.linalg.norm(empty), 1.0)


async def test_reranker_token_overlap(mock_ai: MockAIServices) -> None:
    scores = await mock_ai.reranker.rerank(
        "kubernetes autoscaling",
        ["I cooked pasta", "Kubernetes autoscaling with HPA", "kubernetes basics"],
        top_k=2,
    )
    assert [(s.index, s.score) for s in scores] == [(1, 1.0), (2, 0.5)]
    mock_ai.reranker.fail_with = AIUnavailable("down")
    with pytest.raises(AIUnavailable):
        await mock_ai.reranker.rerank("q", ["d"])


async def test_stt_and_ocr_scripts(mock_ai: MockAIServices) -> None:
    mock_ai.stt.script(
        "first answer", TranscriptionResult(text="custom", no_speech_prob=0.1), AIUnavailable("down")
    )
    mock_ai.stt.script_partial("partial words")
    assert (await mock_ai.stt.transcribe(b"x" * 32044)).text == "first answer"
    assert (await mock_ai.stt.transcribe(b"x", partial=True)).text == "partial words"
    assert (await mock_ai.stt.transcribe(b"x")).text == "custom"
    with pytest.raises(AIUnavailable):
        await mock_ai.stt.transcribe(b"x")
    default = await mock_ai.stt.transcribe(b"x" * 32044)
    assert default.text == "This is a mock transcription." and default.duration_s == 1.0
    mock_ai.ocr.script("Page one", OcrResult(text="", status="degenerate"))
    assert (await mock_ai.ocr.ocr_image(b"png")).text == "Page one"
    assert (await mock_ai.ocr.ocr_image(b"png")).status == "degenerate"
    assert (await mock_ai.ocr.ocr_image(b"png")).text == "Mock OCR text"
    assert mock_ai.ocr.calls[0]["mime_type"] == "image/png"


async def test_mock_tts_and_reset(mock_ai: MockAIServices) -> None:
    result = await mock_ai.tts.synthesize("Hello")
    assert result.provider == "mock" and result.duration_ms == 300
    mock_ai.llm.script(ModelTask.TURN_DECISION, "x")
    mock_ai.reset()
    assert mock_ai.llm.pending(ModelTask.TURN_DECISION) == 0
    assert mock_ai.tts.calls == []
