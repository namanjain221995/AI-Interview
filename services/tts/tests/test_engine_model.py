"""Tests against the real Kokoro model (marker ``model``; skipped when the files are absent)."""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import BASE_MODEL, TIMED_MODEL, VOICES
from tts_service.audio import audible_bounds, frame_levels, quiet_runs
from tts_service.config import Settings
from tts_service.engine import KokoroEngine, Synthesis
from tts_service.text import normalize_for_speech
from tts_service.visemes import VISEMES, estimate_spans, is_phone

pytestmark = pytest.mark.model

SENTENCE = "Could you walk me through how you would design a rate limiter for a public API?"
CORPUS = [
    "Thanks for joining us today. Let's get started.",
    "Tell me about a project you are especially proud of, and why.",
    "Interesting. Why did you choose that approach over a simpler one?",
    "Imagine the database becomes the bottleneck. What would you do first?",
]


def make_engine(model_path, workers: int = 1) -> KokoroEngine:
    engine = KokoroEngine(
        Settings(model_path=model_path, voices_path=VOICES, workers=workers, threads=8, log_json=False)
    )
    engine.load()
    engine.warm_up("Hello there.", "af_heart")
    return engine


@pytest.fixture(scope="module")
def base_engine() -> KokoroEngine:
    return make_engine(BASE_MODEL)


@pytest.fixture(scope="module")
def timed_engine() -> KokoroEngine:
    if not TIMED_MODEL.is_file():
        pytest.skip("durations model missing: run scripts/add_duration_output.py")
    return make_engine(TIMED_MODEL)


def decode(synthesis: Synthesis) -> np.ndarray:
    return np.frombuffer(synthesis.pcm16, dtype="<i2").astype(np.float32) / 32767.0


def assert_well_formed(synthesis: Synthesis, tolerance_ms: float = 10) -> None:
    samples = decode(synthesis)
    assert synthesis.duration_ms == round(len(samples) * 1000 / synthesis.sample_rate)
    cues = synthesis.visemes
    assert all(cue.viseme in VISEMES and 0.0 < cue.weight <= 1.0 for cue in cues)
    times = [cue.t_ms for cue in cues]
    assert times == sorted(set(times)) and times[0] == 0 and times[-1] <= synthesis.duration_ms
    assert all(a.viseme != b.viseme for a, b in zip(cues, cues[1:], strict=False))
    assert cues[0].viseme == "sil" and cues[-1].viseme == "sil"
    # The timeline spans exactly the audible region of the audio actually returned.
    bounds = audible_bounds(frame_levels(samples, synthesis.sample_rate))
    assert bounds is not None and synthesis.audible_ms is not None
    start_ms, end_ms = bounds[0] * 1000, bounds[1] * 1000
    assert abs(start_ms - synthesis.audible_ms[0]) <= tolerance_ms
    assert abs(end_ms - synthesis.audible_ms[1]) <= tolerance_ms
    assert start_ms <= 20 + tolerance_ms  # leading silence trimmed (time to first sound)
    first_open = next(cue for cue in cues if cue.viseme != "sil")
    assert abs(first_open.t_ms - start_ms) <= 5 + tolerance_ms
    assert abs(cues[-1].t_ms - end_ms) <= 5 + tolerance_ms
    assert synthesis.duration_ms - end_ms <= 260  # bounded tail


@pytest.mark.parametrize("engine_name", ["timed_engine", "base_engine"])
def test_synthesis_is_well_formed(engine_name: str, request: pytest.FixtureRequest) -> None:
    engine: KokoroEngine = request.getfixturevalue(engine_name)
    synthesis = engine.synthesize(normalize_for_speech(SENTENCE), "af_heart", 1.0, 24000)
    assert synthesis.timing == ("model" if engine_name == "timed_engine" else "estimated")
    assert 2500 < synthesis.duration_ms < 8000
    assert synthesis.phonemes.startswith("kʊd")
    assert synthesis.synthesis_seconds / (synthesis.duration_ms / 1000) < 1.0
    assert_well_formed(synthesis)


def test_model_variant_detection(base_engine: KokoroEngine, timed_engine: KokoroEngine) -> None:
    assert base_engine.timing_source == "estimated"
    assert timed_engine.timing_source == "model"


def test_duration_output_does_not_change_audio(base_engine: KokoroEngine, timed_engine: KokoroEngine) -> None:
    text = "Tell me about yourself"  # no internal punctuation: no pause top-up on either path
    a = decode(base_engine.synthesize(text, "af_heart", 1.0, 24000))
    b = decode(timed_engine.synthesize(text, "af_heart", 1.0, 24000))
    assert len(a) == len(b)
    assert float(np.abs(a - b).max()) < 1e-3


def test_estimated_timings_track_model_timings(timed_engine: KokoroEngine) -> None:
    """The heuristic aligner, run on real audio, stays close to the model's own phoneme timings."""
    kokoro = timed_engine._pool.get()
    try:
        errors: list[float] = []
        for text in CORPUS:
            phonemes = kokoro.tokenizer.phonemize(text, "en-us")
            audio, sr, timings = kokoro.create_timed(
                phonemes, "af_heart", 1.0, is_phonemes=True, trim=False, sentence_pause=0, clause_pause=0
            )
            levels = frame_levels(audio, sr)
            bounds = audible_bounds(levels)
            assert bounds is not None
            pauses = quiet_runs(levels, *bounds)
            estimated = estimate_spans("".join(t.phoneme for t in timings), *bounds, pauses, len(audio) / sr)
            for truth, guess in zip(timings, estimated, strict=True):
                if is_phone(truth.phoneme):
                    errors.append(abs(truth.start - guess.start))
    finally:
        timed_engine._pool.put(kokoro)
    mean_ms = 1000 * float(np.mean(errors))
    assert mean_ms < 100, f"mean onset error {mean_ms:.0f} ms"


def test_long_text_is_split_into_batches(timed_engine: KokoroEngine) -> None:
    text = " ".join([CORPUS[1], CORPUS[2], CORPUS[3]] * 4)[:990]
    synthesis = timed_engine.synthesize(normalize_for_speech(text), "af_heart", 1.0, 24000)
    assert len(synthesis.phonemes) > 510  # more than one model pass
    assert synthesis.duration_ms > 30000
    assert_well_formed(synthesis)


def test_speed_and_sample_rate(timed_engine: KokoroEngine) -> None:
    text = normalize_for_speech(CORPUS[0])
    slow = timed_engine.synthesize(text, "af_heart", 0.8, 24000)
    fast = timed_engine.synthesize(text, "af_heart", 1.25, 24000)
    assert slow.duration_ms > fast.duration_ms * 1.3
    narrow = timed_engine.synthesize(text, "af_heart", 1.0, 16000)
    wide = timed_engine.synthesize(text, "af_heart", 1.0, 24000)
    assert narrow.sample_rate == 16000 and len(narrow.pcm16) == pytest.approx(len(wide.pcm16) * 2 / 3, abs=4)
    assert abs(narrow.duration_ms - wide.duration_ms) <= 1
    # Analysis runs at 24 kHz; band-limiting to 8 kHz makes soft high-frequency
    # onsets (the "th" of "Thanks") quieter, so the measured start can move later.
    assert_well_formed(narrow, tolerance_ms=30)


@pytest.mark.parametrize("voice", ["am_michael", "bf_emma", "af_bella"])
def test_other_voices(timed_engine: KokoroEngine, voice: str) -> None:
    synthesis = timed_engine.synthesize("Thanks, that was a clear answer.", voice, 1.0, 24000)
    assert synthesis.voice == voice and synthesis.duration_ms > 1000
    assert_well_formed(synthesis)


def test_text_without_phones_gives_empty_audio(timed_engine: KokoroEngine) -> None:
    synthesis = timed_engine.synthesize("…", "af_heart", 1.0, 24000)
    assert synthesis.pcm16 == b"" and synthesis.duration_ms == 0 and synthesis.visemes[0].viseme == "sil"


def test_voices_and_languages(timed_engine: KokoroEngine) -> None:
    voices = {voice.id: voice for voice in timed_engine.voices()}
    assert {"af_heart", "af_bella", "am_michael", "bf_emma"} <= set(voices)
    assert voices["bf_emma"].language == "en-gb"
