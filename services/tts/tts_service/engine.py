"""Kokoro-82M synthesis on CPU (kokoro-onnx + onnxruntime).

One ONNX Runtime session per worker: concurrent ``run`` calls on a shared session
would split one intra-op thread pool, while separate sessions give every
synthesis its own ``TTS_THREADS`` threads. Phonemisation goes through
kokoro-onnx's tokenizer (espeak-ng, serialised by its process-wide lock).
"""

from __future__ import annotations

import logging
import queue
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import onnxruntime as ort
from kokoro_onnx import Kokoro
from kokoro_onnx.chunker import split_phonemes
from kokoro_onnx.config import MAX_PHONEME_LENGTH, SAMPLE_RATE

from tts_service.audio import (
    FRAME_SECONDS,
    apply_fades,
    audible_bounds,
    frame_levels,
    quiet_runs,
    resample,
    to_pcm16,
)
from tts_service.config import Settings
from tts_service.logging_setup import get_logger
from tts_service.visemes import (
    CLAUSE_MARKS,
    SENTENCE_MARKS,
    Cue,
    PhonemeSpan,
    VisemeCue,
    estimate_spans,
    has_phones,
    viseme_cues,
)

log = get_logger(__name__)

TimingSource = Literal["model", "estimated"]

# espeak-ng language per Kokoro voice prefix (first letter of the voice id).
VOICE_LANGUAGES: dict[str, tuple[str, str]] = {
    "a": ("en-us", "American English"),
    "b": ("en-gb", "British English"),
    "e": ("es", "Spanish"),
    "f": ("fr-fr", "French"),
    "h": ("hi", "Hindi"),
    "i": ("it", "Italian"),
    "j": ("ja", "Japanese"),
    "p": ("pt-br", "Brazilian Portuguese"),
    "z": ("cmn", "Mandarin Chinese"),
}
ENGLISH_LANGUAGES = frozenset({"en-us", "en-gb"})

# Silence kept around the audible region of each synthesised clip: almost none
# before speech (time to first sound), a pause after it that depends on how the
# text ends, so consecutive clips of one utterance join naturally.
LEAD_SECONDS = 0.02
TAIL_SENTENCE_SECONDS = 0.22
TAIL_CLAUSE_SECONDS = 0.12
TAIL_OPEN_SECONDS = 0.06
# Pauses kokoro-onnx tops up after marks inside one clip (needs model timings).
SENTENCE_PAUSE_SECONDS = 0.25
CLAUSE_PAUSE_SECONDS = 0.1


class EngineError(RuntimeError):
    """The engine cannot synthesise (not loaded, bad model files, inference failure)."""


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    id: str
    language: str
    language_name: str
    gender: Literal["female", "male"]

    @property
    def english(self) -> bool:
        return self.language in ENGLISH_LANGUAGES


@dataclass(slots=True)
class Synthesis:
    pcm16: bytes
    sample_rate: int
    duration_ms: int
    visemes: list[VisemeCue]
    phonemes: str
    voice: str
    synthesis_seconds: float = 0.0
    timing: TimingSource | None = None
    audible_ms: tuple[int, int] | None = field(default=None)

    @property
    def audio_seconds(self) -> float:
        return len(self.pcm16) / 2 / self.sample_rate if self.sample_rate else 0.0

    @property
    def nbytes(self) -> int:
        """Approximate memory held by this result (for the cache's byte budget)."""
        return len(self.pcm16) + 2 * len(self.phonemes) + 64 * len(self.visemes) + 256


class Engine(Protocol):
    model_name: str

    @property
    def timing_source(self) -> TimingSource | None: ...

    def load(self) -> None: ...

    def warm_up(self, text: str, voice: str) -> float: ...

    def voices(self) -> list[VoiceInfo]: ...

    def synthesize(self, text: str, voice: str, speed: float, sample_rate: int) -> Synthesis: ...


def voice_info(voice_id: str) -> VoiceInfo | None:
    if (
        len(voice_id) < 3
        or voice_id[2] != "_"
        or voice_id[0] not in VOICE_LANGUAGES
        or voice_id[1] not in "fm"
    ):
        return None
    language, name = VOICE_LANGUAGES[voice_id[0]]
    return VoiceInfo(voice_id, language, name, "female" if voice_id[1] == "f" else "male")


def cgroup_cpu_limit(path: Path = Path("/sys/fs/cgroup/cpu.max")) -> float | None:
    """CPU quota of this container in cores (cgroup v2), None when unlimited or unknown."""
    try:
        quota, period = path.read_text().split()[:2]
    except (OSError, ValueError):
        return None
    if quota == "max":
        return None
    try:
        return int(quota) / int(period)
    except (ValueError, ZeroDivisionError):
        return None


def tail_seconds(phonemes: str) -> float:
    mark = phonemes.rstrip()[-1:]
    if mark in SENTENCE_MARKS:
        return TAIL_SENTENCE_SECONDS
    if mark in CLAUSE_MARKS:
        return TAIL_CLAUSE_SECONDS
    return TAIL_OPEN_SECONDS


@dataclass(slots=True)
class Clip:
    """One synthesised batch, trimmed, with its timeline relative to its first sample."""

    audio: np.ndarray
    cues: list[Cue]
    timing: TimingSource | None
    audible: tuple[float, float] | None


def render_clip(
    audio: np.ndarray,
    sample_rate: int,
    phonemes: str,
    spans: Sequence[PhonemeSpan],
    *,
    speed: float = 1.0,
    lead: float = LEAD_SECONDS,
) -> Clip:
    """Trim raw model output to its audible region plus pads and build its visemes."""
    levels = frame_levels(audio, sample_rate)
    bounds = audible_bounds(levels)
    if bounds is None:
        return Clip(np.zeros(0, dtype=np.float32), [], None, None)
    onset, offset = bounds
    total = len(audio) / sample_rate
    pauses = quiet_runs(levels, onset, offset)
    timing: TimingSource = "model" if spans else "estimated"
    if not spans:
        spans = estimate_spans(phonemes, onset, offset, pauses, total)
    cues = viseme_cues(spans, levels, onset, offset, pauses, total, FRAME_SECONDS)

    start = max(0.0, onset - lead)
    end = offset + tail_seconds(phonemes) / max(speed, 0.1)
    first, last = round(start * sample_rate), round(end * sample_rate)
    clip = audio[first : min(last, len(audio))].astype(np.float32, copy=False)
    if last > len(audio):
        clip = np.concatenate([clip, np.zeros(last - len(audio), dtype=np.float32)])
    duration = len(clip) / sample_rate

    shifted: list[Cue] = []
    for cue in cues:
        t = min(max(cue.time - start, 0.0), duration)
        if shifted and t <= shifted[-1].time:
            shifted[-1] = Cue(shifted[-1].time, cue.viseme, cue.weight)  # later cue wins the instant
        else:
            shifted.append(Cue(t, cue.viseme, cue.weight))
    deduped: list[Cue] = []
    for cue in shifted:
        if deduped and deduped[-1].viseme == cue.viseme:
            continue
        deduped.append(cue)
    return Clip(clip, deduped, timing, (onset - start, offset - start))


def join_clips(
    clips: Sequence[Clip], sample_rate: int
) -> tuple[np.ndarray, list[Cue], tuple[float, float] | None]:
    """Concatenate clips, offsetting and merging their timelines."""
    pieces: list[np.ndarray] = []
    cues: list[Cue] = []
    offset = 0.0
    first_audible: float | None = None
    last_audible: float | None = None
    for clip in clips:
        if len(clip.audio) == 0:
            continue
        for cue in clip.cues:
            t = offset + cue.time
            if cues and cues[-1].viseme == cue.viseme:
                continue
            if cues and t <= cues[-1].time:
                cues[-1] = Cue(cues[-1].time, cue.viseme, cue.weight)
                continue
            cues.append(Cue(t, cue.viseme, cue.weight))
        if clip.audible is not None:
            if first_audible is None:
                first_audible = offset + clip.audible[0]
            last_audible = offset + clip.audible[1]
        pieces.append(clip.audio)
        offset += len(clip.audio) / sample_rate
    if not pieces:
        return np.zeros(0, dtype=np.float32), [], None
    audible = (
        (first_audible, last_audible) if first_audible is not None and last_audible is not None else None
    )
    return np.concatenate(pieces), cues, audible


def to_ms_cues(cues: Sequence[Cue], duration_ms: int) -> list[VisemeCue]:
    """Round cue times to whole milliseconds, keeping them strictly increasing and in range."""
    out: list[VisemeCue] = []
    for cue in cues:
        t = min(max(round(cue.time * 1000), 0), duration_ms)
        if out and out[-1].viseme == cue.viseme:
            continue
        if out and t <= out[-1].t_ms:
            out[-1] = VisemeCue(out[-1].t_ms, cue.viseme, cue.weight)  # later cue wins the instant
            if len(out) > 1 and out[-2].viseme == out[-1].viseme:
                out.pop()
            continue
        out.append(VisemeCue(t, cue.viseme, cue.weight))
    if not out:
        out.append(VisemeCue(0, "sil", 1.0))
    if out[-1].viseme != "sil":
        if out[-1].t_ms < duration_ms:
            out.append(VisemeCue(duration_ms, "sil", 1.0))
        else:
            out[-1] = VisemeCue(out[-1].t_ms, "sil", 1.0)
    return out


def empty_synthesis(voice: str, sample_rate: int) -> Synthesis:
    return Synthesis(b"", sample_rate, 0, [VisemeCue(0, "sil", 1.0)], "", voice)


class KokoroEngine:
    """Kokoro-82M on onnxruntime CPU with a pool of independent sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model_name = settings.model_name
        self._pool: queue.Queue[Kokoro] = queue.Queue()
        self._styles: dict[str, np.ndarray] = {}
        self._voices: dict[str, VoiceInfo] = {}
        self._timing: TimingSource | None = None
        self._loaded = False

    @property
    def timing_source(self) -> TimingSource | None:
        return self._timing

    def _session_options(self) -> ort.SessionOptions:
        options = ort.SessionOptions()
        options.intra_op_num_threads = self._settings.threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        options.add_session_config_entry(
            "session.intra_op.allow_spinning", "1" if self._settings.allow_spinning else "0"
        )
        return options

    def load(self) -> None:
        model_path, voices_path = Path(self._settings.model_path), Path(self._settings.voices_path)
        for path in (model_path, voices_path):
            if not path.is_file():
                raise EngineError(f"model file not found: {path}")
        logging.getLogger("phonemizer").setLevel(logging.ERROR)

        with np.load(voices_path) as archive:
            styles = {name: np.asarray(archive[name], dtype=np.float32) for name in archive.files}
        instances: list[Kokoro] = []
        for _ in range(self._settings.workers):
            session = ort.InferenceSession(
                str(model_path), sess_options=self._session_options(), providers=["CPUExecutionProvider"]
            )
            instances.append(Kokoro.from_session(session, str(voices_path)))

        from phonemizer.backend import EspeakBackend  # after kokoro-onnx pointed phonemizer at its espeak-ng

        supported = set(EspeakBackend.supported_languages())
        voices: dict[str, VoiceInfo] = {}
        skipped: list[str] = []
        for name in sorted(styles):
            info = voice_info(name)
            if info is None or info.language not in supported:
                skipped.append(name)
                continue
            voices[name] = info
        if self._settings.default_voice not in voices:
            raise EngineError(f"default voice {self._settings.default_voice!r} is not available")

        self._styles = styles
        self._voices = voices
        self._timing = "model" if all(instance.has_timings for instance in instances) else "estimated"
        for instance in instances:
            self._pool.put(instance)
        self._loaded = True
        cpu_limit = cgroup_cpu_limit()
        if cpu_limit is not None and self._settings.workers * self._settings.threads > cpu_limit:
            log.warning(
                "tts.cpu_oversubscribed",
                cpu_limit=cpu_limit,
                workers=self._settings.workers,
                threads=self._settings.threads,
                hint="concurrent syntheses will be CPU-throttled; lower TTS_THREADS or raise the CPU limit",
            )
        log.info(
            "tts.engine.loaded",
            model=str(model_path),
            sessions=len(instances),
            threads=self._settings.threads,
            voices=len(voices),
            skipped_voices=skipped,
            viseme_timing=self._timing,
            onnxruntime=ort.__version__,
        )

    def voices(self) -> list[VoiceInfo]:
        return list(self._voices.values())

    def warm_up(self, text: str, voice: str) -> float:
        """Run one synthesis on every session; returns the slowest latency in seconds."""
        if not self._loaded:
            raise EngineError("engine not loaded")
        instances = [self._pool.get() for _ in range(self._settings.workers)]
        slowest = 0.0
        try:
            for instance in instances:
                result = self._synthesize(instance, text, voice, 1.0, SAMPLE_RATE)
                slowest = max(slowest, result.synthesis_seconds)
        finally:
            for instance in instances:
                self._pool.put(instance)
        return slowest

    def synthesize(self, text: str, voice: str, speed: float, sample_rate: int) -> Synthesis:
        if not self._loaded:
            raise EngineError("engine not loaded")
        instance = self._pool.get()
        try:
            return self._synthesize(instance, text, voice, speed, sample_rate)
        finally:
            self._pool.put(instance)

    def _synthesize(self, kokoro: Kokoro, text: str, voice: str, speed: float, sample_rate: int) -> Synthesis:
        started = time.perf_counter()
        info = self._voices[voice]
        phonemes = " ".join(kokoro.tokenizer.phonemize(text, info.language).split())
        if not has_phones(phonemes):
            return empty_synthesis(voice, sample_rate)
        style = self._styles[voice]
        clips: list[Clip] = []
        for batch in split_phonemes(phonemes, MAX_PHONEME_LENGTH):
            if not has_phones(batch):
                continue
            audio, model_rate, timings = kokoro.create_timed(
                batch,
                style,
                speed=speed,
                is_phonemes=True,
                trim=False,
                sentence_pause=SENTENCE_PAUSE_SECONDS,
                clause_pause=CLAUSE_PAUSE_SECONDS,
            )
            spans = [PhonemeSpan(t.phoneme, t.start, t.end) for t in timings]
            clips.append(
                render_clip(np.asarray(audio, dtype=np.float32), model_rate, batch, spans, speed=speed)
            )

        audio, cues, audible = join_clips(clips, SAMPLE_RATE)
        if len(audio) == 0:
            return empty_synthesis(voice, sample_rate)
        audio = apply_fades(audio, SAMPLE_RATE)
        if sample_rate != SAMPLE_RATE:
            audio = resample(audio, SAMPLE_RATE, sample_rate)
        pcm16 = to_pcm16(audio)
        duration_ms = round(len(audio) * 1000 / sample_rate)
        timing = next((clip.timing for clip in clips if clip.timing), None)
        return Synthesis(
            pcm16=pcm16,
            sample_rate=sample_rate,
            duration_ms=duration_ms,
            visemes=to_ms_cues(cues, duration_ms),
            phonemes=phonemes,
            voice=voice,
            synthesis_seconds=time.perf_counter() - started,
            timing=timing,
            audible_ms=(round(audible[0] * 1000), round(audible[1] * 1000)) if audible else None,
        )
