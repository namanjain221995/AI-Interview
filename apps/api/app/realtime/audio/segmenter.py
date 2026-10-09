"""Speech segmentation over a VAD stream (REALTIME_DESIGN.md section 3).

Audio time is the sample clock of the received stream (16 kHz), so the
segmenter behaves identically whether frames arrive in real time or in a
burst (reconnect, tests).

* Speech start: ``start_windows`` (3) consecutive windows with probability >=
  ``speech_threshold``; the segment starts ``preroll_ms`` (300 ms) earlier,
  never overlapping the previous segment.
* While speaking, windows above ``speech_threshold - 0.15`` keep the speech
  alive (Silero's recommended hysteresis).
* Segment boundaries never cut words: a pause-closed segment keeps
  ``tail_ms`` after the last speech window and the next one may reuse that
  silent tail as pre-roll; a segment that reaches ``max_segment_ms`` is split
  only at a window of its second half where the VAD heard no speech or the
  level dropped ``QUIET_DB_BELOW_SPEECH`` below the loudest (a pause between
  words) - with none yet it may grow to ``HARD_MAX_FACTOR`` times the limit
  before it is split at its lowest-energy window.
* Silence >= ``segment_pause_ms`` (350 ms) closes the segment
  (:class:`SegmentClosed` + :class:`SpeechEnded`). Once the open segment holds
  ``eager_after_ms`` of speech, a shorter pause (``eager_pause_ms``) already
  closes it: long answers are cut into phrases at natural pauses, so each
  phrase is transcribed while the candidate keeps talking and the segment left
  at the end of the answer is short (Whisper latency grows ~0.18 s per second
  of audio here). Segments reaching ``max_segment_ms`` are split at the
  lowest-energy window of their second half, and the remainder continues as
  the next segment.
* :class:`HighSpeech` fires once per episode when probability >=
  ``barge_in_threshold`` has been sustained for ``barge_in_min_speech_ms``
  (dips below ``speech_threshold`` reset the episode). The runtime uses it
  for barge-in while the interviewer speaks.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.realtime.audio.pcm import PcmRingBuffer, ms_to_samples, rms_dbfs, samples_to_ms
from app.realtime.audio.vad import WINDOW, VadStream

HYSTERESIS = 0.15
HARD_MAX_FACTOR = 1.5
QUIET_DB_BELOW_SPEECH = 25.0  # a window this far below the segment's loudest is a pause
WINDOW_MS_INT = int(WINDOW * 1000 / 16000)  # 32 ms: the shortest pause the VAD can see


@dataclass(slots=True)
class SpeechStarted:
    t_ms: int  # first confirmed speech window
    segment_start_ms: int  # including pre-roll


@dataclass(slots=True)
class SpeechEnded:
    t_ms: int  # end of the last speech window (silence started here)


@dataclass(slots=True)
class SegmentClosed:
    index: int
    start_ms: int
    end_ms: int
    samples: np.ndarray
    reason: Literal["pause", "max_length", "flush"]
    speech_ms: int  # voiced duration inside the segment
    mean_prob: float = 0.0  # mean VAD speech probability of the voiced windows

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(slots=True)
class HighSpeech:
    t_ms: int
    sustained_ms: int


@dataclass(slots=True)
class VadFrame:
    """Diagnostic snapshot after a ``process`` call."""

    t_ms: int
    prob: float
    in_speech: bool
    silence_ms: int


SegmenterEvent = SpeechStarted | SpeechEnded | SegmentClosed | HighSpeech


class AudioSegmenter:
    def __init__(
        self,
        vad: VadStream,
        *,
        speech_threshold: float = 0.5,
        barge_in_threshold: float = 0.8,
        barge_in_min_speech_ms: int = 280,
        start_windows: int = 3,
        segment_pause_ms: int = 350,
        eager_pause_ms: int | None = None,
        eager_after_ms: int = 3000,
        max_segment_ms: int = 9000,
        preroll_ms: int = 300,
        tail_ms: int = 150,
        ring_seconds: float = 40.0,
    ) -> None:
        self.vad = vad
        self.speech_threshold = speech_threshold
        self.release_threshold = max(0.05, speech_threshold - HYSTERESIS)
        self.barge_in_threshold = barge_in_threshold
        self.barge_in_min_speech_ms = barge_in_min_speech_ms
        self.start_windows = max(1, start_windows)
        self.segment_pause = ms_to_samples(segment_pause_ms)
        eager = segment_pause_ms if eager_pause_ms is None else min(eager_pause_ms, segment_pause_ms)
        self.eager_pause = ms_to_samples(max(WINDOW_MS_INT, eager))
        self.eager_after = ms_to_samples(max(0, eager_after_ms))
        self.max_segment = ms_to_samples(max(1000, max_segment_ms))
        self.hard_max_segment = int(self.max_segment * HARD_MAX_FACTOR)
        self.preroll = ms_to_samples(preroll_ms)
        self.tail = ms_to_samples(tail_ms)
        capacity = max(
            ms_to_samples(ring_seconds * 1000), self.max_segment + self.preroll + ms_to_samples(5000)
        )
        self.ring = PcmRingBuffer(capacity)
        self._pending = np.zeros(0, dtype=np.int16)
        self._processed = 0  # samples that went through the VAD
        self.in_speech = False
        self._run = 0
        self._run_start = 0
        self._seg_start: int | None = None
        self._seg_speech = 0  # voiced samples in the open segment
        self._prob_sum = 0.0  # VAD probability over the open segment's voiced windows
        self._prob_n = 0
        self._last_speech_end = 0
        self._prev_seg_end = 0
        self._prev_closed_on_pause = False  # its tail after the last speech is silence
        self._segment_index = 0
        self._levels: deque[tuple[int, float, float]] = deque()  # (window start, dBFS, VAD probability)
        self._high = 0
        self._high_fired = False
        self.last_prob = 0.0
        self.windows = 0

    # ----------------------------------------------------------- properties
    @property
    def now_ms(self) -> int:
        return samples_to_ms(self._processed)

    @property
    def received_ms(self) -> int:
        return samples_to_ms(self.ring.total)

    @property
    def segment_open(self) -> bool:
        return self._seg_start is not None

    @property
    def open_segment_start_ms(self) -> int | None:
        return None if self._seg_start is None else samples_to_ms(self._seg_start)

    @property
    def silence_ms(self) -> int:
        """Time since the last voiced window (0 while speaking)."""
        if self.in_speech and self._last_speech_end >= self._processed:
            return 0
        if self._last_speech_end == 0 and not self.in_speech:
            return samples_to_ms(self._processed)
        return samples_to_ms(max(0, self._processed - self._last_speech_end))

    @property
    def last_speech_end_ms(self) -> int:
        return samples_to_ms(self._last_speech_end)

    def snapshot(self) -> VadFrame:
        return VadFrame(
            t_ms=self.now_ms, prob=self.last_prob, in_speech=self.in_speech, silence_ms=self.silence_ms
        )

    def open_segment_audio(self) -> np.ndarray:
        if self._seg_start is None:
            return np.zeros(0, dtype=np.int16)
        return self.ring.read(self._seg_start, self._processed)

    def open_segment_speech_ms(self) -> int:
        return samples_to_ms(self._seg_speech)

    def read_ms(self, start_ms: int, end_ms: int | None = None) -> np.ndarray:
        end = None if end_ms is None else ms_to_samples(end_ms)
        return self.ring.read(ms_to_samples(start_ms), end)

    # -------------------------------------------------------------- control
    def reset_speech(self) -> None:
        """Forget speech state (keeps the clock and buffered audio)."""
        self.in_speech = False
        self._run = 0
        self._seg_start = None
        self._seg_speech = 0
        self._prob_sum, self._prob_n = 0.0, 0
        self._levels.clear()
        self._high = 0
        self._high_fired = False
        self._prev_seg_end = self._processed
        self.vad.reset()

    def flush(self) -> SegmentClosed | None:
        """Close the open segment now (manual end of turn, max utterance)."""
        if self._seg_start is None:
            self.in_speech = False
            self._run = 0
            return None
        end = self._processed
        seg = self._close(end, "flush")
        self._prev_closed_on_pause = False
        self.in_speech = False
        self._run = 0
        return seg

    # ------------------------------------------------------------ processing
    def process(self, samples: np.ndarray) -> list[SegmenterEvent]:
        events: list[SegmenterEvent] = []
        if samples.size == 0:
            return events
        self.ring.write(samples)
        data = np.concatenate((self._pending, samples)) if self._pending.size else samples
        offset = 0
        while data.size - offset >= WINDOW:
            window = data[offset : offset + WINDOW]
            offset += WINDOW
            self._window(window, events)
        self._pending = data[offset:].copy() if offset < data.size else np.zeros(0, dtype=np.int16)
        return events

    def _window(self, window: np.ndarray, events: list[SegmenterEvent]) -> None:
        start = self._processed
        end = start + WINDOW
        prob = self.vad.process(window)
        self.last_prob = prob
        self.windows += 1
        self._processed = end

        # Barge-in candidate tracking (independent of segmentation).
        if prob >= self.barge_in_threshold:
            self._high += WINDOW
            if not self._high_fired and samples_to_ms(self._high) >= self.barge_in_min_speech_ms:
                self._high_fired = True
                events.append(HighSpeech(t_ms=samples_to_ms(end), sustained_ms=samples_to_ms(self._high)))
        elif prob < self.speech_threshold:
            self._high = 0
            self._high_fired = False

        if self._seg_start is not None:
            self._levels.append((start, rms_dbfs(window), prob))

        if not self.in_speech:
            if prob >= self.speech_threshold:
                if self._run == 0:
                    self._run_start = start
                self._run += 1
                if self._run >= self.start_windows:
                    self.in_speech = True
                    self._last_speech_end = end
                    if self._seg_start is None:
                        floor = self._prev_seg_end - (self.tail if self._prev_closed_on_pause else 0)
                        seg_start = max(floor, self._run_start - self.preroll, self.ring.oldest)
                        self._seg_start = seg_start
                        self._seg_speech = 0
                        self._prob_sum, self._prob_n = 0.0, 0
                        self._levels.clear()
                        events.append(
                            SpeechStarted(
                                t_ms=samples_to_ms(self._run_start), segment_start_ms=samples_to_ms(seg_start)
                            )
                        )
                    self._seg_speech += end - self._run_start
                    self._prob_sum += prob
                    self._prob_n += 1
            else:
                self._run = 0
                if self._seg_start is not None and end - self._last_speech_end >= self._pause_needed():
                    self._close_on_pause(events)
            return

        # In speech.
        if prob >= self.release_threshold:
            self._last_speech_end = end
            self._seg_speech += WINDOW
            self._prob_sum += prob
            self._prob_n += 1
        else:
            if end - self._last_speech_end >= self._pause_needed():
                self.in_speech = False
                self._run = 0
                self._close_on_pause(events)
                return
            # Short dip: silence accumulates but the segment stays open.
            self.in_speech = True
        if self._seg_start is not None and end - self._seg_start >= self.max_segment:
            self._split(events)

    def _pause_needed(self) -> int:
        """Silence (samples) that closes the open segment: the eager pause once
        the segment already holds ``eager_after`` of audio up to its last speech."""
        if self._seg_start is not None and self._last_speech_end - self._seg_start >= self.eager_after:
            return self.eager_pause
        return self.segment_pause

    def _close_on_pause(self, events: list[SegmenterEvent]) -> None:
        end = min(self._processed, self._last_speech_end + self.tail)
        seg = self._close(end, "pause")
        self._prev_closed_on_pause = True
        self.in_speech = False
        if seg is not None:
            events.append(seg)
        events.append(SpeechEnded(t_ms=samples_to_ms(self._last_speech_end)))

    def _split(self, events: list[SegmenterEvent]) -> None:
        """Split a segment that reached ``max_segment`` (see module docstring)."""
        assert self._seg_start is not None
        lo = self._seg_start + self.max_segment // 2
        hi = self._processed - ms_to_samples(200)
        candidates = [(level, s, prob) for s, level, prob in self._levels if lo <= s <= hi]
        loudest = max((level for level, _, _ in candidates), default=0.0)
        quiet = [
            (level, s)
            for level, s, prob in candidates
            if prob < self.release_threshold or level <= loudest - QUIET_DB_BELOW_SPEECH
        ]
        if quiet:
            _, split_start = min(quiet)
            split = split_start + WINDOW // 2
        elif self._processed - self._seg_start < self.hard_max_segment:
            return  # continuous speech so far: wait for a pause between words
        elif candidates:
            _, split_start, _ = min(candidates)
            split = split_start + WINDOW // 2
        else:
            split = self._processed
        seg = self._close(split, "max_length")
        self._prev_closed_on_pause = False
        if seg is not None:
            events.append(seg)
        # Continue the utterance as a new segment from the split point (levels
        # are rebuilt from the windows that follow).
        self._seg_start = split
        self._seg_speech = max(0, self._processed - split)
        self._prob_sum, self._prob_n = self.last_prob, 1

    def _close(self, end: int, reason: Literal["pause", "max_length", "flush"]) -> SegmentClosed | None:
        start = self._seg_start
        self._seg_start = None
        self._levels.clear()
        if start is None or end <= start:
            return None
        samples = self.ring.read(start, end)
        speech = self._seg_speech
        mean_prob = self._prob_sum / self._prob_n if self._prob_n else 0.0
        self._seg_speech = 0
        self._prob_sum, self._prob_n = 0.0, 0
        self._prev_seg_end = end
        index = self._segment_index
        self._segment_index += 1
        return SegmentClosed(
            index=index,
            start_ms=samples_to_ms(start),
            end_ms=samples_to_ms(end),
            samples=samples,
            reason=reason,
            speech_ms=samples_to_ms(min(speech, end - start)),
            mean_prob=round(mean_prob, 3),
        )
