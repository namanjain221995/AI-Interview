"""Live STT-leg harness (opt-in: ``-m live``): replays recorded answers in real
time through the runtime's own audio pipeline - Silero segmentation, ordered
segment finals, throttled partials, adaptive end of turn (provisional end +
commit on the final text, merge-back when speech resumes) and the glossary
correction - against the real Whisper replicas, without the engine, LLM or
TTS. Cheap enough to compare segmentation / scheduling / endpointing::

    uv run pytest tests/integration/realtime/test_stt_harness.py -m live -s

Per clip it prints whether the turn ended while the candidate still had more
to say (``CUT``), the end-of-turn reason, the STT wait after end of turn, the
tail segment, every final's latency, partial counts, the word error rate
against the reference text (segment finals, after the glossary, and a
single whole-answer pass) and the transcript.

``STT_HARNESS_CLIPS`` selects clips; ``STT_HARNESS_DIR`` adds a directory with
its own ``clips.json`` + WAVs (e.g. answers with inserted hesitation pauses);
``STT_HARNESS_PARTIALS=0`` disables partials; ``STT_HARNESS_POLICY=legacy``
ends turns at the provisional decision only (the behaviour before 2026-10-07);
``STT_HARNESS_FULL_PASS=0`` skips the whole-answer transcription.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.ai.base import CallContext
from app.ai.ledger import NullLedger
from app.ai.registry import RealAIServices
from app.core.config import get_settings
from app.domain.enums import ModelTask
from app.realtime import endpointing
from app.realtime import runtime as runtime_module
from app.realtime.audio.pcm import SAMPLE_RATE, bytes_to_samples, pcm16_to_wav, wav_to_pcm16
from app.realtime.audio.segmenter import AudioSegmenter, SegmentClosed, SpeechStarted
from app.realtime.audio.vad import create_vad
from app.realtime.endpointing import Endpointer, EndpointParams
from app.realtime.glossary import correct_transcript
from app.realtime.stt import PartialScheduler, TurnTranscriber, build_whisper_prompt, stt_has_spare_capacity

from .conftest import CLIPS, load_clip, silence

pytestmark = [pytest.mark.live, pytest.mark.integration, pytest.mark.timeout(2400)]

DEFAULT_CLIPS = ("intro", "kubernetes_strong", "cost_vague", "cost_specific", "incident", "generic_medium")
FRAME = SAMPLE_RATE // 10  # 100 ms, like the browser
VOCABULARY = ["AWS", "EKS", "Kubernetes", "Karpenter", "Terraform", "Prometheus", "Grafana", "Argo CD"]
TERMS = ("Karpenter", "Prometheus", "Grafana", "Argo CD", "Terraform", "EKS", "Kubernetes", "Cost Explorer")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / max(1, len(ref))


def term_hits(reference: str, hypothesis: str) -> tuple[int, int]:
    """(technical terms of the reference found verbatim in the hypothesis, terms in the reference)."""
    expected = [t for t in TERMS if t.lower() in reference.lower()]
    return sum(1 for t in expected if t.lower() in hypothesis.lower()), len(expected)


def load_extra(directory: str) -> dict[str, dict[str, Any]]:
    meta = json.loads((Path(directory) / "clips.json").read_text())
    for name, item in meta.items():
        pcm, rate = wav_to_pcm16((Path(directory) / f"{name}.wav").read_bytes())
        assert rate == SAMPLE_RATE
        item["samples"] = bytes_to_samples(pcm)
    return meta


def make_segmenter(settings: Any) -> AudioSegmenter:
    """The runtime's segmenter; ``STT_HARNESS_PAUSE`` / ``_EAGER_PAUSE`` /
    ``_EAGER_AFTER`` / ``_MAX_SEGMENT`` (ms) override its settings for experiments."""
    env = os.environ
    if not any(k in env for k in ("STT_HARNESS_PAUSE", "STT_HARNESS_EAGER_PAUSE", "STT_HARNESS_EAGER_AFTER")):
        return runtime_module.build_segmenter(settings)
    return AudioSegmenter(
        create_vad(settings.vad_engine, settings.vad_model_path),
        speech_threshold=settings.vad_speech_threshold,
        barge_in_threshold=settings.vad_barge_in_threshold,
        barge_in_min_speech_ms=settings.barge_in_min_speech_ms,
        segment_pause_ms=int(env.get("STT_HARNESS_PAUSE", settings.segment_pause_ms)),
        eager_pause_ms=int(env.get("STT_HARNESS_EAGER_PAUSE", runtime_module.EAGER_SEGMENT_PAUSE_MS)),
        eager_after_ms=int(env.get("STT_HARNESS_EAGER_AFTER", runtime_module.EAGER_SEGMENT_AFTER_MS)),
        max_segment_ms=int(env.get("STT_HARNESS_MAX_SEGMENT", runtime_module.MAX_SEGMENT_MS)),
    )


def last_voiced_ms(samples: np.ndarray) -> int:
    last = 0
    for index in range(0, samples.size, FRAME):
        frame = samples[index : index + FRAME].astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(frame)))) if frame.size else 0.0
        if rms > 0 and 20 * np.log10(rms) > -42.0:
            last = (index + frame.size) * 1000 // SAMPLE_RATE
    return last


async def replay(  # noqa: PLR0915
    stt: Any, settings: Any, name: str, clip: np.ndarray, reference: str, *, partials: bool, legacy: bool
) -> dict[str, Any]:
    """One answer in real time; returns the turn's STT/endpointing measurements."""
    segmenter = make_segmenter(settings)
    endpointer = Endpointer(EndpointParams.from_settings(settings))
    commit_policy = getattr(endpointing, "commit_required_ms", None) if not legacy else None
    scheduler = PartialScheduler(
        base_interval_ms=settings.stt_partial_interval_ms,
        min_audio_ms=settings.stt_partial_min_audio_ms,
        enabled=partials,
    )
    submitted: dict[int, float] = {}
    transcriber = TurnTranscriber(
        lambda: stt,
        context=CallContext(task=ModelTask.STT, session_id="stt-harness"),
        prompt=lambda so_far: build_whisper_prompt(
            question="Tell me about your recent work.", vocabulary=VOCABULARY, answer_so_far=so_far
        ),
        language=settings.whisper_language or None,
    )
    lead_in = 400
    audio = np.concatenate((silence(lead_in), clip, silence(4500)))
    speech_until_ms = lead_in + last_voiced_ms(clip)
    partial_tasks: list[asyncio.Task[Any]] = []
    stats = {"partials": 0, "partials_skipped": 0, "merged_back": 0}

    async def run_partial(samples: np.ndarray) -> None:
        t0 = time.perf_counter()
        result = await transcriber.partial(samples)
        scheduler.finished(
            latency_ms=(time.perf_counter() - t0) * 1000 if result else None, skipped=result is None
        )
        stats["partials" if result else "partials_skipped"] += 1

    def submit(seg: Any) -> None:
        job = transcriber.submit(
            seg.samples,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            speech_ms=seg.speech_ms,
            vad_prob=seg.mean_prob,
        )
        submitted[job.index] = time.monotonic()

    started = time.monotonic()
    next_at = started
    end_at: float | None = None  # provisional end of turn
    end_audio_ms: int | None = None
    committed_at: float | None = None
    reason = None
    capture_start: int | None = None
    finals_task: asyncio.Task[bool] | None = None
    for i in range(0, audio.size, FRAME):
        events = segmenter.process(audio[i : i + FRAME])
        for event in events:
            if isinstance(event, SpeechStarted):
                if capture_start is None:
                    capture_start = event.segment_start_ms
                elif end_at is not None and committed_at is None:
                    # Speech resumed before the turn was committed: the turn continues.
                    stats["merged_back"] += 1
                    end_at, end_audio_ms, finals_task = None, None, None
            elif isinstance(event, SegmentClosed):
                submit(event)
                scheduler.reset_segment()
        if capture_start is not None and end_at is None:
            decision = endpointer.decide(
                transcriber.text_with_partial(),
                silence_ms=segmenter.silence_ms if not segmenter.in_speech else 0,
                turn_ms=segmenter.now_ms - capture_start,
                speaking=segmenter.in_speech,
                **({"settled": transcriber.pending == 0} if commit_policy else {}),
            )
            if decision.end:
                tail = segmenter.flush()  # like SessionRuntime.end_turn
                if tail is not None:
                    submit(tail)
                end_at, end_audio_ms, reason = time.monotonic(), segmenter.now_ms, decision.reason
                if commit_policy is None:
                    committed_at = end_at
                    break
                finals_task = asyncio.create_task(transcriber.wait_finals(timeout=25))
        if end_at is not None and committed_at is None and finals_task is not None and finals_task.done():
            required = commit_policy(transcriber.text(), endpointer.params)  # type: ignore[misc]
            if (segmenter.silence_ms if not segmenter.in_speech else 0) >= required:
                committed_at = time.monotonic()
                break
        if partials and segmenter.segment_open:
            open_ms = segmenter.now_ms - (segmenter.open_segment_start_ms or 0)
            now = time.monotonic()
            if scheduler.due(
                now=now, open_audio_ms=open_ms, finals_pending=transcriber.pending
            ) and stt_has_spare_capacity(stt):
                scheduler.started(now=now, open_audio_ms=open_ms)
                partial_tasks.append(asyncio.create_task(run_partial(segmenter.open_segment_audio())))
        next_at += FRAME / SAMPLE_RATE
        await asyncio.sleep(max(0.0, next_at - time.monotonic()))
    assert end_at is not None and committed_at is not None, f"{name}: no end of turn"
    speech_end_at = started + segmenter.last_speech_end_ms / 1000
    await transcriber.wait_finals(timeout=60)
    done_at = time.monotonic()
    for task in partial_tasks:
        task.cancel()
    tail = transcriber.segments[-1]
    text = transcriber.text()
    corrected = text
    if os.environ.get("STT_HARNESS_GLOSSARY", "1") != "0":
        corrected = correct_transcript(text, VOCABULARY)
    full_text = None
    if os.environ.get("STT_HARNESS_FULL_PASS", "1") != "0":
        full = await stt.transcribe(pcm16_to_wav(clip), language=settings.whisper_language or None)
        full_text = full.text
    hits, expected_terms = term_hits(reference, corrected)
    return {
        "clip": name,
        "cut": end_audio_ms is not None and end_audio_ms < speech_until_ms,
        "reason": reason,
        "silence_ms": round((end_at - speech_end_at) * 1000),
        "commit_ms": round((committed_at - end_at) * 1000),
        "stt_wait_ms": round((done_at - end_at) * 1000),
        "segments": len(transcriber.segments),
        "tail_audio_ms": tail.duration_ms,
        "tail_latency_ms": round(tail.latency_ms),
        "tail_lead_ms": round((end_at - submitted[tail.index]) * 1000),
        "finals": [(s.duration_ms, round(s.latency_ms)) for s in transcriber.segments],
        "wer": round(wer(reference, text), 3),
        "wer_corrected": round(wer(reference, corrected), 3),
        "wer_full_pass": round(wer(reference, full_text), 3) if full_text is not None else None,
        "terms": f"{hits}/{expected_terms}",
        "text": corrected,
        "full_text": full_text,
        **stats,
    }


async def test_live_stt_leg() -> None:
    settings = get_settings()
    env = os.environ
    clips: dict[str, tuple[np.ndarray, str]] = {}
    for name in [c for c in env.get("STT_HARNESS_CLIPS", ",".join(DEFAULT_CLIPS)).split(",") if c]:
        clips[name] = (load_clip(name), CLIPS[name]["text"])
    if env.get("STT_HARNESS_DIR"):
        for name, item in load_extra(env["STT_HARNESS_DIR"]).items():
            clips[name] = (item["samples"], item["text"])
    partials = env.get("STT_HARNESS_PARTIALS", "1") != "0"
    legacy = env.get("STT_HARNESS_POLICY") == "legacy"
    label = env.get("STT_HARNESS_LABEL", "stt")
    real = RealAIServices(settings, redis_client=None, ledger=NullLedger())
    rows: list[dict[str, Any]] = []
    try:
        for name, (samples, reference) in clips.items():
            row = await replay(real.stt, settings, name, samples, reference, partials=partials, legacy=legacy)
            rows.append(row)
            print(
                f"\n[{label}] {name:18s} {'CUT ' if row['cut'] else 'ok  '}{row['reason']:14s} "
                f"silence={row['silence_ms']:5d} commit=+{row['commit_ms']} merged={row['merged_back']} "
                f"stt_wait={row['stt_wait_ms']:5d} tail={row['tail_audio_ms']}ms->{row['tail_latency_ms']}ms "
                f"segs={row['segments']} wer={row['wer']:.3f}/{row['wer_corrected']:.3f} "
                f"full={row['wer_full_pass']} terms={row['terms']} partials={row['partials']}/{row['partials_skipped']}"
            )
            print(f"[{label}]   text: {row['text']}")
            if row["full_text"] is not None and row["full_text"] != row["text"]:
                print(f"[{label}]   full: {row['full_text']}")
            await asyncio.sleep(1.0)
    finally:
        await real.aclose()
    waits = [r["stt_wait_ms"] for r in rows]
    total_ref = sum(len(words(clips[r["clip"]][1])) for r in rows)

    def weighted(key: str) -> float:
        return sum((r[key] or 0) * len(words(clips[r["clip"]][1])) for r in rows) / max(1, total_ref)

    print(
        f"\n[{label}] cut-offs={sum(r['cut'] for r in rows)}/{len(rows)} stt_wait p50={statistics.median(waits):.0f} "
        f"max={max(waits)} | WER segments={weighted('wer'):.3f} corrected={weighted('wer_corrected'):.3f} "
        f"full-pass={weighted('wer_full_pass'):.3f} | terms "
        f"{sum(int(r['terms'].split('/')[0]) for r in rows)}/{sum(int(r['terms'].split('/')[1]) for r in rows)}"
    )
    assert rows
