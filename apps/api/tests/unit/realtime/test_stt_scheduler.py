"""TurnTranscriber (ordered finals, retry, failure retention, partials) and prompts."""

from __future__ import annotations

import asyncio

from app.ai.base import AIUnavailable, CallContext, PartialSkipped, TranscriptionResult
from app.ai.mock import MockStt
from app.domain.enums import ModelTask
from app.realtime.stt import PartialScheduler, TurnTranscriber, build_whisper_prompt

from .conftest import tone

CTX = CallContext(task=ModelTask.STT, session_id="s1")


def transcriber(stt: MockStt, **kwargs) -> TurnTranscriber:
    return TurnTranscriber(lambda: stt, context=CTX, prompt=lambda so_far: f"prompt[{so_far}]", **kwargs)


class DelayStt(MockStt):
    def __init__(self, plan: list[tuple[float, object]]) -> None:
        super().__init__()
        self.plan = list(plan)

    async def transcribe(self, audio: bytes, **kwargs):
        delay, item = self.plan.pop(0) if self.plan else (0.0, "x")
        self.calls.append({"bytes": len(audio), **kwargs})
        await asyncio.sleep(delay)
        if isinstance(item, BaseException):
            raise item
        return TranscriptionResult(text=str(item), no_speech_prob=0.1, duration_s=1.0)


async def test_finals_are_joined_in_segment_order_even_when_completing_out_of_order() -> None:
    stt = DelayStt([(0.08, "We ran EKS"), (0.0, "across three zones."), (0.02, "Karpenter scaled nodes.")])
    finals: list[int] = []
    t = transcriber(stt, on_final=lambda job: finals.append(job.index))
    for i in range(3):
        t.submit(tone(500), start_ms=i * 1000, end_ms=i * 1000 + 500)
    assert await t.wait_finals(timeout=2)
    assert t.text() == "We ran EKS across three zones. Karpenter scaled nodes."
    assert finals != [0, 1, 2]  # completion order differs from segment order
    assert all(call["content_type"] == "audio/wav" for call in stt.calls)
    assert t.confidence() == 0.9
    assert t.speech_ms() == 1500


async def test_prompt_includes_answer_so_far() -> None:
    stt = DelayStt([(0.0, "First part."), (0.0, "Second part.")])
    t = transcriber(stt)
    t.submit(tone(300), start_ms=0, end_ms=300)
    await t.wait_finals()
    t.submit(tone(300), start_ms=400, end_ms=700)
    await t.wait_finals()
    assert stt.calls[0]["prompt"] == "prompt[]"
    assert stt.calls[1]["prompt"] == "prompt[First part.]"


async def test_failed_segment_is_retried_once() -> None:
    stt = DelayStt([(0.0, AIUnavailable("down")), (0.0, "Recovered text.")])
    t = transcriber(stt)
    job = t.submit(tone(300), start_ms=0, end_ms=300)
    await t.wait_finals()
    assert job.status == "done" and job.attempts == 2 and t.text() == "Recovered text."


async def test_persistent_failure_keeps_audio_and_can_be_retried_later() -> None:
    stt = DelayStt([(0.0, AIUnavailable("down")), (0.0, AIUnavailable("down")), (0.0, "Finally.")])
    failures: list[int] = []
    t = transcriber(stt, on_failure=lambda job, exc: failures.append(job.index))
    job = t.submit(tone(300), start_ms=0, end_ms=300)
    await t.wait_finals()
    assert job.status == "failed" and failures == [0]
    assert job.wav.startswith(b"RIFF")  # audio retained
    assert t.text() == ""
    assert await t.retry_failed()
    assert t.text() == "Finally."


async def test_partial_skipped_and_superseded_partials_are_dropped() -> None:
    stt = MockStt()
    stt.script_partial(PartialSkipped("busy"))
    t = transcriber(stt)
    assert await t.partial(tone(1500)) is None
    assert t.partial_text == ""
    stt.script_partial("interim words")
    result = await t.partial(tone(1500))
    assert result is not None and t.partial_text == "interim words"
    assert t.text_with_partial() == "interim words"
    # A final for the segment replaces the partial.
    stt.script("final words.")
    t.submit(tone(500), start_ms=0, end_ms=500)
    assert t.partial_text == ""
    await t.wait_finals()
    assert t.text_with_partial() == "final words."


async def test_aclose_cancels_pending_finals() -> None:
    stt = DelayStt([(5.0, "never")])
    t = transcriber(stt)
    job = t.submit(tone(300), start_ms=0, end_ms=300)
    await asyncio.sleep(0.01)
    await t.aclose()
    assert job.task is not None and job.task.done()


def test_partial_scheduler_adapts_interval() -> None:
    sched = PartialScheduler(base_interval_ms=1500, min_audio_ms=1200)
    assert not sched.due(now=10.0, open_audio_ms=1000, finals_pending=0)  # not enough audio
    assert sched.due(now=10.0, open_audio_ms=1300, finals_pending=0)
    assert not sched.due(now=10.0, open_audio_ms=1300, finals_pending=1)  # finals have priority
    sched.started(now=10.0, open_audio_ms=1300)
    assert not sched.due(now=20.0, open_audio_ms=5000, finals_pending=0)  # one in flight
    sched.finished(latency_ms=None, skipped=True)
    assert sched.interval_ms == 2250
    sched.started(now=20.0, open_audio_ms=5000)
    sched.finished(latency_ms=2000, skipped=False)  # slow -> back off further
    assert sched.interval_ms > 2250
    for _ in range(30):
        sched.finished(latency_ms=100, skipped=False)
    assert sched.interval_ms == 1500  # recovers to the base
    assert sched.interval_ms <= sched.max_interval_ms
    disabled = PartialScheduler(base_interval_ms=1500, min_audio_ms=0, enabled=False)
    assert not disabled.due(now=100.0, open_audio_ms=10_000, finals_pending=0)


def test_whisper_prompt_layout_and_bounds() -> None:
    prompt = build_whisper_prompt(
        question="How did you scale the EKS cluster",
        vocabulary=["Kubernetes", "EKS", "Karpenter", "EKS", "Argo CD"],
        answer_so_far="We used three managed node groups " * 20,
    )
    assert prompt.startswith("Technical terms: Kubernetes, EKS, Karpenter, Argo CD.")
    assert "How did you scale the EKS cluster?" in prompt
    assert prompt.endswith("node groups")
    assert len(prompt) <= 760
    assert build_whisper_prompt(question=None, vocabulary=[], answer_so_far="") == ""


class CheckAwareStt(MockStt):
    """Declares the optional ``no_speech_check`` argument like the Whisper client."""

    async def transcribe(self, audio: bytes, *, no_speech_check: bool | None = None, **kwargs):
        result = await super().transcribe(audio, **kwargs)
        self.calls[-1]["no_speech_check"] = no_speech_check
        if no_speech_check is False:
            result.no_speech_prob = None  # the server skipped its check
        return result


async def test_clear_speech_segments_skip_the_server_silence_check() -> None:
    stt = CheckAwareStt(default_text="Spoken words.")
    t = transcriber(stt)
    t.submit(tone(2000), start_ms=0, end_ms=2000, speech_ms=1800, vad_prob=0.93)  # clear speech
    t.submit(tone(600), start_ms=2000, end_ms=2600, speech_ms=400, vad_prob=0.93)  # too short
    t.submit(tone(2000), start_ms=3000, end_ms=5000, speech_ms=1800, vad_prob=0.6)  # unsure VAD
    assert await t.wait_finals(timeout=2)
    assert [c["no_speech_check"] for c in stt.calls] == [False, None, None]
    # confidence: VAD probability where Whisper's check was skipped, 1 - no_speech_prob elsewhere
    expected = (0.93 * 2000 + 1.0 * 600 + 1.0 * 2000) / 4600  # the mock reports no_speech_prob 0.0
    assert abs(t.confidence() - round(expected, 3)) < 0.002


async def test_clients_without_the_option_never_receive_it() -> None:
    plain = MockStt(default_text="Words.")
    t = transcriber(plain)
    t.submit(tone(2000), start_ms=0, end_ms=2000, speech_ms=1800, vad_prob=0.95)
    assert await t.wait_finals(timeout=2)
    assert t.text() == "Words." and "no_speech_check" not in plain.calls[0]

    class Forwarding(MockStt):  # declares it but its inner client does not take it
        async def transcribe(self, audio: bytes, no_speech_check: bool | None = None, **kwargs):
            if no_speech_check is not None:
                kwargs["no_speech_check"] = no_speech_check
            return await super().transcribe(audio, **kwargs)

    forwarding = Forwarding(default_text="Still transcribed.")
    t = transcriber(forwarding)
    t.submit(tone(2000), start_ms=0, end_ms=2000, speech_ms=1800, vad_prob=0.95)
    assert await t.wait_finals(timeout=2)
    assert t.text() == "Still transcribed." and t.resolved  # retried without the option


async def test_pending_segment_is_never_reported_as_resolved() -> None:
    """Regression (audit): a segment still in flight when the wait times out must
    not be treated as transcribed - retry_failed() used to return True and the
    turn went on without that audio."""
    stt = DelayStt([(0.0, "First part."), (0.6, "slow second part.")])
    t = transcriber(stt)
    t.submit(tone(500), start_ms=0, end_ms=500)
    t.submit(tone(500), start_ms=600, end_ms=1100)
    assert not await t.wait_finals(timeout=0.1)
    assert t.pending == 1 and not t.failed and not t.resolved
    assert await t.retry_failed(timeout=0.1) is False  # still in flight: not resolved
    assert await t.retry_failed(timeout=2) is True  # waited for it
    assert t.text() == "First part, slow second part."  # Whisper's segment-end period dropped


def test_segment_joins_drop_whisper_periods_before_lowercase_continuations() -> None:
    from app.realtime.stt import join_texts

    assert (
        join_texts(
            ["I coordinated the response.", "rotated the certificate.", "and afterwards we added alerts."]
        )
        == "I coordinated the response, rotated the certificate, and afterwards we added alerts."
    )
    assert join_texts(["We used e.g.", "spot pools."]) == "We used e.g. spot pools."  # abbreviation kept
    assert join_texts(["Mostly AWS.", "And then GCP."]) == "Mostly AWS. And then GCP."  # a real sentence end
    assert join_texts(["", "  Text  ", ""]) == "Text"


def test_second_pass_revision_rules() -> None:
    from app.realtime.revision import is_material_revision, word_difference

    live = "I coordinated the response rotated the certificate and afterwards we added automated alerts"
    full = "I coordinated the response, rotated the certificate, and afterwards we added automated expiry alerts."
    assert 0 < word_difference(live, full) < 0.1
    assert word_difference("same words here", "Same words, here.") == 0.0
    assert is_material_revision(
        "we used carpenter for node scaling", "We used Karpenter for node autoscaling.", min_ratio=0.08
    )
    assert not is_material_revision("we used karpenter", "We used Karpenter.", min_ratio=0.08)  # same words
    long_live = "one two three four five six seven eight nine ten"
    assert not is_material_revision(long_live, "one two three", min_ratio=0.08)  # truncated pass never wins


def test_no_preview_once_the_candidate_paused() -> None:
    """Owner report (slow speech-to-text): a preview started in the pause after the last word held the only
    Whisper slot while the answer's final piece waited behind it. A pause has no new words to preview."""
    from app.realtime.stt import PARTIAL_PAUSE_MS, PartialScheduler

    sched = PartialScheduler(base_interval_ms=1000, min_audio_ms=1200)
    assert sched.due(now=10.0, open_audio_ms=1300, finals_pending=0, silence_ms=120)  # mid-sentence gap
    assert not sched.due(now=10.0, open_audio_ms=1300, finals_pending=0, silence_ms=PARTIAL_PAUSE_MS)
