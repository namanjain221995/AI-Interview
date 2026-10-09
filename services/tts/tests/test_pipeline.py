from __future__ import annotations

import numpy as np
import pytest

from tts_service.engine import (
    LEAD_SECONDS,
    TAIL_CLAUSE_SECONDS,
    TAIL_OPEN_SECONDS,
    TAIL_SENTENCE_SECONDS,
    Clip,
    join_clips,
    render_clip,
    tail_seconds,
    to_ms_cues,
    voice_info,
)
from tts_service.visemes import Cue, PhonemeSpan, VisemeCue

SR = 24000


def speechlike(lead: float, parts: list[tuple[float, bool]], tail: float) -> np.ndarray:
    """Silence, then alternating voiced (noise-modulated tone) / silent parts, then silence."""
    rng = np.random.default_rng(7)
    chunks = [np.zeros(round(lead * SR), dtype=np.float32)]
    for seconds, voiced in parts:
        n = round(seconds * SR)
        if voiced:
            t = np.arange(n) / SR
            chunks.append(
                (0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * rng.random(n))).astype(np.float32)
            )
        else:
            chunks.append(np.zeros(n, dtype=np.float32))
    chunks.append(np.zeros(round(tail * SR), dtype=np.float32))
    return np.concatenate(chunks)


def test_tail_depends_on_final_punctuation() -> None:
    assert tail_seconds("həlˈoʊ.") == TAIL_SENTENCE_SECONDS
    assert tail_seconds("wˈʌt?") == TAIL_SENTENCE_SECONDS
    assert tail_seconds("wˈɛl,") == TAIL_CLAUSE_SECONDS
    assert tail_seconds("ænd ") == TAIL_OPEN_SECONDS


def test_voice_info_from_kokoro_ids() -> None:
    heart = voice_info("af_heart")
    assert heart is not None and heart.language == "en-us" and heart.gender == "female" and heart.english
    emma = voice_info("bm_george")
    assert emma is not None and emma.language == "en-gb" and emma.gender == "male"
    dora = voice_info("ef_dora")
    assert dora is not None and dora.language == "es" and not dora.english
    assert voice_info("xx_unknown") is None and voice_info("af") is None and voice_info("aq_x") is None


def test_render_clip_trims_to_audible_region_and_aligns_visemes() -> None:
    audio = speechlike(0.35, [(0.5, True), (0.25, False), (0.6, True)], 0.5)
    clip = render_clip(audio, SR, "həlˈoʊ, wˈɜːld.", [])
    assert clip.timing == "estimated"
    assert clip.audible is not None
    onset, offset = clip.audible
    assert onset == pytest.approx(LEAD_SECONDS, abs=0.006)  # 350 ms of leading silence removed
    assert offset == pytest.approx(LEAD_SECONDS + 1.35, abs=0.01)
    assert len(clip.audio) / SR == pytest.approx(offset + TAIL_SENTENCE_SECONDS, abs=0.002)
    cues = clip.cues
    assert cues[0].time == 0.0 and cues[0].viseme == "sil"
    assert cues[1].time == pytest.approx(onset, abs=0.006) and cues[1].viseme != "sil"
    assert cues[-1].viseme == "sil" and cues[-1].time == pytest.approx(offset, abs=0.006)
    pause_start = onset + 0.5
    assert any(c.viseme == "sil" and abs(c.time - pause_start) < 0.01 for c in cues)
    after_pause = [c for c in cues if c.time > pause_start + 0.1]
    assert after_pause[0].time == pytest.approx(onset + 0.75, abs=0.01)  # "w" starts when sound resumes


def test_render_clip_uses_model_timings_when_given() -> None:
    audio = speechlike(0.3, [(0.4, True)], 0.4)
    spans = [
        PhonemeSpan("m", 0.3, 0.4),
        PhonemeSpan("ˈ", 0.4, 0.45),
        PhonemeSpan("ɑ", 0.45, 0.7),
        PhonemeSpan(".", 0.7, 0.9),
    ]
    clip = render_clip(audio, SR, "mˈɑ.", spans)
    assert clip.timing == "model"
    assert [(round(c.time, 3), c.viseme) for c in clip.cues] == [
        (0.0, "sil"),
        (0.02, "PP"),
        (0.12, "aa"),
        (0.42, "sil"),
    ]


def test_render_clip_pads_when_model_tail_is_short_and_handles_silence() -> None:
    audio = speechlike(0.05, [(0.3, True)], 0.01)
    clip = render_clip(audio, SR, "ɑ.", [])
    assert len(clip.audio) / SR == pytest.approx(0.02 + 0.3 + TAIL_SENTENCE_SECONDS, abs=0.006)
    silent = render_clip(np.zeros(SR, dtype=np.float32), SR, "ɑ.", [])
    assert len(silent.audio) == 0 and silent.cues == [] and silent.audible is None


def test_slower_speech_gets_longer_tail() -> None:
    audio = speechlike(0.1, [(0.3, True)], 0.6)
    normal = render_clip(audio, SR, "ɑ.", [], speed=1.0)
    slow = render_clip(audio, SR, "ɑ.", [], speed=0.8)
    assert len(slow.audio) > len(normal.audio)


def test_join_clips_offsets_and_merges_timelines() -> None:
    a = Clip(
        np.zeros(SR // 2, dtype=np.float32),
        [Cue(0.0, "sil", 1), Cue(0.02, "aa", 1), Cue(0.3, "sil", 1)],
        "model",
        (0.02, 0.3),
    )
    b = Clip(
        np.zeros(SR // 4, dtype=np.float32),
        [Cue(0.0, "sil", 1), Cue(0.02, "PP", 1), Cue(0.2, "sil", 1)],
        "model",
        (0.02, 0.2),
    )
    audio, cues, audible = join_clips([a, b], SR)
    assert len(audio) == SR // 2 + SR // 4
    assert [(round(c.time, 3), c.viseme) for c in cues] == [
        (0.0, "sil"),
        (0.02, "aa"),
        (0.3, "sil"),
        (0.52, "PP"),
        (0.7, "sil"),
    ]
    assert audible == pytest.approx((0.02, 0.7))
    empty_audio, empty_cues, none = join_clips([], SR)
    assert len(empty_audio) == 0 and empty_cues == [] and none is None


def test_to_ms_cues_rounds_dedupes_and_terminates() -> None:
    cues = [
        Cue(0.0, "sil", 1.0),
        Cue(0.0204, "aa", 0.9),
        Cue(0.0206, "PP", 1.0),
        Cue(0.2, "PP", 1.0),
        Cue(0.5, "E", 0.8),
    ]
    out = to_ms_cues(cues, 450)
    assert out == [
        VisemeCue(0, "sil", 1.0),
        VisemeCue(20, "aa", 0.9),
        VisemeCue(21, "PP", 1.0),
        VisemeCue(450, "sil", 1.0),
    ]
    assert to_ms_cues([], 100) == [VisemeCue(0, "sil", 1.0)]
    times = [
        c.t_ms for c in to_ms_cues([Cue(i / 3000, "aa" if i % 2 else "PP", 1.0) for i in range(30)], 1000)
    ]
    assert times == sorted(set(times))


def test_cgroup_cpu_limit(tmp_path) -> None:
    from tts_service.engine import cgroup_cpu_limit

    limited = tmp_path / "cpu.max"
    limited.write_text("800000 100000\n")
    assert cgroup_cpu_limit(limited) == 8.0
    unlimited = tmp_path / "unlimited"
    unlimited.write_text("max 100000\n")
    assert cgroup_cpu_limit(unlimited) is None
    assert cgroup_cpu_limit(tmp_path / "missing") is None
